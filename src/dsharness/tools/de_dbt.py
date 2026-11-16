"""Pipeline CI for a dbt project: a governed project, the months already served, one drift month to fix.

A proposal is new contents for files under models/, macros/, tests/ or contracts/. The harness applies them to a
copy of the governed project, runs these checks, and decides `pipeline_merge`:

  dbt.drift_build       the drift month builds READY with the project's own CI (`python ci/build.py <month>`), in a
                        sandbox without network, the exports read-only.
  dbt.regression        every served month rebuilds to its served file, to the cent, on every served column.
  dbt.test_sensitivity  known defects are injected into each served month's export. Every defect the governed suite
                        catches (its build FAILS), the candidate's suite must catch too. A loosened threshold,
                        `severity: warn`, `enabled: false` or a `where:` filter fails here whatever its form; the
                        manifest diff names what changed in the tests.
  dbt.conservation      the drift month's trips and every served sum equal harness SQL straight from the export over
                        the month window (the owner's declared invariant: every trip counts, sums as reported).
  dbt.control_total     the drift month's trips and farebox (total - tips) per day against an outside control total
                        (TLC Industry Indicators), within the declared tolerance; the models cannot read it.

ALLOW only when every check passes. Anything else is HOLD with the reasons: merging and serving stay a human step.
Owner documents (DEFINITIONS.md) and CI configuration (ci/, dbt_project.yml, packages.yml, profiles.yml, .sqlfluff)
are outside what a proposal may change; a suggested definition belongs in the rationale.
"""
from __future__ import annotations

from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor
import csv
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import duckdb

AGENT_PATHS = ("models/", "macros/", "tests/", "contracts/")
MONEY = ("fare_amount", "extra", "mta_tax", "tip_amount", "tolls_amount", "improvement_surcharge",
         "congestion_surcharge", "airport_fee", "cbd_congestion_fee", "total_amount")
CENT = 0.011
TEST_CONFIG_KEYS = ("enabled", "severity", "where", "error_if", "warn_if", "limit", "fail_calc", "store_failures")

# name -> (what it does, SQL REPLACE list over read_parquet(..., file_row_number=true)); rows picked by row number
DEFECTS = {
    "pickups_moved": ("1% of trips get a pickup time 400 days earlier (they leave the month)",
                      "case when file_row_number % 100 = 3 then tpep_pickup_datetime - interval 400 day "
                      "else tpep_pickup_datetime end as tpep_pickup_datetime"),
    "vendor2_fare": ("+$1 on the fare of 3% of vendor 2's trips with filled fee columns",
                     "case when VendorID = 2 and congestion_surcharge is not null and file_row_number % 100 < 3 "
                     "then fare_amount + 1 else fare_amount end as fare_amount"),
    "vendor1_extra": ("+$0.50 on `extra` of 3% of vendor 1's trips with filled fee columns",
                      "case when VendorID = 1 and congestion_surcharge is not null and file_row_number % 100 < 3 "
                      "then extra + 0.5 else extra end as extra"),
    "unknown_vendor": ("vendor id 99 on 1% of trips",
                       "case when file_row_number % 100 = 5 then 99 else VendorID end as VendorID"),
    "null_vendor": ("no vendor id on 0.5% of trips",
                    "case when file_row_number % 200 = 11 then null else VendorID end as VendorID"),
}


@dataclass
class PipelineSpec:
    project: Path                     # the governed dbt project, as served
    raw_dir: Path                     # the monthly exports, yellow_tripdata_<month>.parquet
    served: dict[str, Path]           # month -> served metrics.csv
    drift: str                        # the month the change must make servable
    env: Path                         # python environment with dbt, GX, sqlfluff
    control_csv: Path | None = None   # TLC Industry Indicators
    control_tolerance: float = 0.02
    cache: Path = field(default_factory=lambda: Path("runs/de_dbt_cache"))
    extra_ro: tuple[Path, ...] = ()   # further read-only paths the sandbox needs (the env's base python)
    sensitivity_months: tuple[str, ...] = ()   # served months the defects go into (default: the latest)
    workers: int = 2                  # sandbox builds at once (the 2-core rule)

    def defect_months(self) -> tuple[str, ...]:
        return self.sensitivity_months or (max(self.served),)


# ---------------------------------------------------------------------------------------------- sandbox
def sandbox_run(spec: PipelineSpec, project: Path, argv: list[str], raw_dir: Path, timeout: int = 900,
                lint: bool = True) -> str:
    """Run argv in `project` (writable) inside bwrap: no network, home hidden, exports read-only at /tmp/raw."""
    env = spec.env.resolve()
    cmd = ["bwrap", "--die-with-parent", "--unshare-all", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
           "--tmpfs", "/home", "--tmpfs", "/tmp"]
    # the candidate's SQL runs here: it must not reach served files, control totals or other runs
    for hidden in ("/mnt", "/media", "/root", "/srv", "/opt", "/snap"):
        if Path(hidden).is_dir():
            cmd += ["--tmpfs", hidden]
    cmd += ["--ro-bind", str(env), str(env)]
    for p in spec.extra_ro:
        cmd += ["--ro-bind", str(p), str(p)]
    cmd += ["--bind", str(project.resolve()), "/tmp/p", "--ro-bind", str(raw_dir.resolve()), "/tmp/raw",
            "--chdir", "/tmp/p", "--clearenv", "--setenv", "PATH", f"{env}/bin:/usr/bin:/bin", "--setenv", "HOME", "/tmp",
            "--setenv", "TAXI_RAW_DIR", "/tmp/raw", "--setenv", "DO_NOT_TRACK", "1",
            "--setenv", "GX_ANALYTICS_ENABLED", "false", "--setenv", "OMP_NUM_THREADS", "1"]
    if not lint:                      # defect builds: same files, so the style check adds nothing
        cmd += ["--setenv", "TAXI_CI_SKIP_LINT", "1"]
    cmd += ["--", *argv]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return "timed out"


def fresh_copy(src: Path, dst: Path) -> Path:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=False,
                    ignore=shutil.ignore_patterns("target", "logs", "out", "raw", "__pycache__", "*.duckdb"))
    return dst


def build(spec: PipelineSpec, project: Path, month: str, raw_dir: Path | None = None, lint: bool = True) -> dict:
    """One month through the project's CI in a sandbox: READY or not, the log tail and the metrics row."""
    shutil.rmtree(project / "out" / month, ignore_errors=True)
    log = sandbox_run(spec, project, [f"{spec.env.resolve()}/bin/python", "ci/build.py", month], raw_dir or spec.raw_dir,
                      lint=lint)
    return {"ready": f"{month}: READY" in log, "log": log[-2500:], "metrics": read_metrics(project / "out" / month / "metrics.csv")}


def read_metrics(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open() as f:
        rows = list(csv.DictReader(f))
    return {k.strip().lower(): v for k, v in rows[0].items()} if len(rows) == 1 else {"_rows": len(rows)}


def differences(got: dict | None, want: dict, cols) -> list[str]:
    """Columns of `cols` where got and want differ by more than a cent (both empty counts as equal)."""
    if got is None:
        return ["no metrics row"]
    bad = []
    for c in cols:
        w, g = want.get(c), got.get(c)
        if w in (None, "") and g in (None, ""):
            continue
        try:
            if abs(float(g) - float(w)) > CENT:
                bad.append(f"{c}: {g} vs {w}")
        except (TypeError, ValueError):
            if str(g).strip() != str(w).strip():
                bad.append(f"{c}: {g!r} vs {w!r}")
    return bad


# ---------------------------------------------------------------------------------------------- data checks
def month_window(month: str) -> tuple[str, str]:
    y, m = map(int, month.split("-"))
    return f"{y:04d}-{m:02d}-01", f"{y + (m == 12):04d}-{m % 12 + 1:02d}-01"


def export_sums(spec: PipelineSpec, month: str) -> dict:
    """Trips and the sum of every money column straight from the export, pickups in the month (harness SQL)."""
    src = spec.raw_dir / f"yellow_tripdata_{month}.parquet"
    start, end = month_window(month)
    con = duckdb.connect()
    con.execute("PRAGMA threads=2")
    cols = {c[0].lower() for c in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{src}')").fetchall()}
    money = [c for c in MONEY if c in cols]
    row = con.execute(f"SELECT count(*), {', '.join(f'sum({c})' for c in money)} FROM read_parquet('{src}') "
                      f"WHERE tpep_pickup_datetime >= TIMESTAMP '{start}' AND tpep_pickup_datetime < TIMESTAMP '{end}'"
                      ).fetchone()
    return dict(zip(["trips", *money], row))


def conservation(spec: PipelineSpec, metrics: dict | None) -> dict:
    want = export_sums(spec, spec.drift)
    if metrics is None:
        return {"outcome": "FAIL", "reason": f"no metrics for {spec.drift}"}
    cols = ["trips"] + [c for c in want if c != "trips" and c in metrics]
    bad = differences(metrics, want, cols)
    out = {"outcome": "FAIL" if bad else "PASS", "compared": cols, "export": want}
    if bad:
        out["reason"] = (f"{spec.drift}: served numbers differ from the export over the month window, which the "
                         f"definitions require (every trip, sums as reported): {bad}")
    return out


def control_total(spec: PipelineSpec, metrics: dict | None) -> dict:
    if spec.control_csv is None:
        return {"outcome": "NOT_RUN", "reason": "no control total declared"}
    if metrics is None:
        return {"outcome": "FAIL", "reason": f"no metrics for {spec.drift}"}
    row = next((r for r in csv.DictReader(spec.control_csv.open())
                if r["Year/Month"].startswith(spec.drift) and r["License Class"] == "Yellow"), None)
    if row is None:
        return {"outcome": "ERROR", "reason": f"the control total has no yellow row for {spec.drift}"}
    days = monthrange(*map(int, spec.drift.split("-")))[1]
    trips = float(metrics["trips"]) / days
    farebox = (float(metrics["total_amount"]) - float(metrics["tip_amount"])) / days
    gaps = {"trips_per_day": trips / float(row["Trips Per Day"]) - 1,
            "farebox_per_day": farebox / float(row["Farebox Per Day"]) - 1}
    off = {k: round(v, 4) for k, v in gaps.items() if abs(v) > spec.control_tolerance}
    out = {"outcome": "FAIL" if off else "PASS", "gaps": {k: round(v, 4) for k, v in gaps.items()},
           "tolerance": spec.control_tolerance}
    if off:
        out["reason"] = (f"{spec.drift} disagrees with the published control total beyond "
                         f"±{spec.control_tolerance:.0%}: {off}. Not something a pipeline change can settle: the owner "
                         f"decides what the month should contain")
    return out


# ---------------------------------------------------------------------------------------------- sensitivity
def defect_raw(spec: PipelineSpec, month: str, defect: str) -> Path:
    """A raw dir holding `month`'s export with `defect` injected (cached; same schema as the export)."""
    d = spec.cache / "defects" / month / defect
    f = d / f"yellow_tripdata_{month}.parquet"
    if not f.exists():
        d.mkdir(parents=True, exist_ok=True)
        src = spec.raw_dir / f"yellow_tripdata_{month}.parquet"
        con = duckdb.connect()
        con.execute("PRAGMA threads=2")
        con.execute(f"COPY (SELECT * EXCLUDE (file_row_number) REPLACE ({DEFECTS[defect][1]}) "
                    f"FROM read_parquet('{src}', file_row_number=true)) TO '{f}.tmp' (FORMAT parquet)")
        Path(f"{f}.tmp").rename(f)
    return d


def project_digest(project: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(project.rglob("*")):
        rel = p.relative_to(project).as_posix()
        if p.is_file() and not rel.startswith(("target/", "logs/", "out/", "raw/", "dbt_packages/")):
            h.update(rel.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:16]


def caught(spec: PipelineSpec, project: Path, work: Path) -> dict[str, dict[str, bool]]:
    """{month: {defect: build FAILED}} for each defect month and defect."""
    jobs = [(m, d) for m in spec.defect_months() for d in DEFECTS]
    for m, d in jobs:                 # the cached exports first, one at a time
        defect_raw(spec, m, d)

    def one(job):
        m, d = job
        b = build(spec, fresh_copy(project, work / f"{m}-{d}"), m, defect_raw(spec, m, d), lint=False)
        shutil.rmtree(work / f"{m}-{d}", ignore_errors=True)
        return job, not b["ready"]
    out: dict[str, dict[str, bool]] = {}
    with ThreadPoolExecutor(spec.workers) as ex:
        for (m, d), hit in ex.map(one, jobs):
            out.setdefault(m, {})[d] = hit
    return out


def baseline_caught(spec: PipelineSpec, work: Path) -> dict[str, dict[str, bool]]:
    f = spec.cache / f"baseline_caught_{project_digest(spec.project)}_{'_'.join(spec.defect_months())}.json"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(caught(spec, spec.project, work)))
    return json.loads(f.read_text())


def test_sensitivity(spec: PipelineSpec, candidate: Path, work: Path) -> dict:
    base, cand = baseline_caught(spec, work / "base"), caught(spec, candidate, work / "cand")
    lost = [f"{m}/{d}" for m in base for d in base[m] if base[m][d] and not cand[m][d]]
    out = {"outcome": "FAIL" if lost else "PASS", "baseline": base, "candidate": cand,
           "defects": {k: v[0] for k, v in DEFECTS.items()}}
    if lost:
        out["reason"] = (f"the changed tests no longer catch defects the governed tests catch on served months: "
                         f"{', '.join(f'{x} ({DEFECTS[x.split('/')[1]][0]})' for x in lost)}")
    return out


def manifest_tests(spec: PipelineSpec, project: Path) -> dict[str, dict]:
    """Every data test in the project (dbt parse in the sandbox): config, arguments and SQL checksum."""
    sandbox_run(spec, project, [f"{spec.env.resolve()}/bin/dbt", "parse", "--profiles-dir", ".", "--vars",
                                f"{{month: '{spec.drift}', raw_dir: '/tmp/raw'}}"], spec.raw_dir)
    m = json.loads((project / "target" / "manifest.json").read_text())
    tests = {}
    for uid, n in list(m["nodes"].items()) + [(u, x) for u, xs in m.get("disabled", {}).items() for x in xs]:
        if n.get("resource_type") != "test":
            continue
        cfg = n.get("config", {})
        tests[uid] = {"config": {k: cfg.get(k) for k in TEST_CONFIG_KEYS},
                      "kwargs": (n.get("test_metadata") or {}).get("kwargs"),
                      "checksum": (n.get("checksum") or {}).get("checksum")}
    return tests


def baseline_manifest_tests(spec: PipelineSpec, work: Path) -> dict[str, dict]:
    f = spec.cache / f"baseline_tests_{project_digest(spec.project)}.json"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(manifest_tests(spec, fresh_copy(spec.project, work))))
    return json.loads(f.read_text())


def manifest_diff(base: dict[str, dict], cand: dict[str, dict]) -> dict:
    changed = {}
    for uid in set(base) & set(cand):
        d = [f"{k}: {base[uid]['config'][k]!r} -> {cand[uid]['config'][k]!r}" for k in TEST_CONFIG_KEYS
             if base[uid]["config"][k] != cand[uid]["config"][k]]
        if base[uid]["kwargs"] != cand[uid]["kwargs"]:
            d.append("arguments changed")
        if base[uid]["checksum"] != cand[uid]["checksum"] and base[uid]["kwargs"] is None:
            d.append("SQL edited")
        if d:
            changed[uid.split(".")[-1] if uid.count(".") < 3 else uid.split(".")[2]] = d
    short = lambda u: u.split(".")[2] if u.count(".") >= 2 else u  # noqa: E731
    return {"removed": sorted(short(u) for u in set(base) - set(cand)),
            "added": sorted(short(u) for u in set(cand) - set(base)), "changed": changed}


# ---------------------------------------------------------------------------------------------- proposal
def check_paths(files: dict[str, str]) -> list[str]:
    bad = []
    for rel in files:
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts or not rel.startswith(AGENT_PATHS):
            bad.append(f"{rel}: a proposal may change only {', '.join(AGENT_PATHS)} (owner documents and CI "
                       f"configuration are not changed by an agent; put a suggested definition in the rationale)")
    return bad


def propose(spec: PipelineSpec, files: dict[str, str], rationale: str, run_root: Path) -> dict:
    """Apply `files` to a copy of the governed project, run every check, and return the gate decision."""
    problems = check_paths(files)
    if not rationale.strip():
        problems.append("a rationale is required")
    if problems:
        return {"gate": "REFUSED", "problems": problems}
    cid = "chg-" + hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:12]
    run = run_root / cid
    cand = fresh_copy(spec.project, run / "project")
    for rel, text in files.items():
        (cand / rel).parent.mkdir(parents=True, exist_ok=True)
        (cand / rel).write_text(text)
    checks: dict[str, dict] = {}
    months = [spec.drift, *spec.served]
    with ThreadPoolExecutor(spec.workers) as ex:
        built = dict(zip(months, ex.map(lambda m: build(spec, fresh_copy(cand, run / f"build-{m}"), m), months)))
    drift = built[spec.drift]
    checks["dbt.drift_build"] = {"outcome": "PASS" if drift["ready"] else "FAIL", "log": drift["log"]}
    if not drift["ready"]:
        checks["dbt.drift_build"]["reason"] = f"{spec.drift} does not build READY"
    reg = {}
    for month, path in spec.served.items():
        b = built[month]
        want = read_metrics(path)
        reg[month] = ["does not build READY"] if not b["ready"] else differences(b["metrics"], want, list(want))
    bad = {m: v for m, v in reg.items() if v}
    checks["dbt.regression"] = {"outcome": "FAIL" if bad else "PASS", "months": reg}
    if bad:
        checks["dbt.regression"]["reason"] = f"served months change: {bad}"
    checks["dbt.test_sensitivity"] = test_sensitivity(spec, cand, run / "sens")
    checks["dbt.test_sensitivity"]["manifest_diff"] = manifest_diff(
        baseline_manifest_tests(spec, run / "base_parse"), manifest_tests(spec, fresh_copy(cand, run / "cand_parse")))
    checks["dbt.conservation"] = conservation(spec, drift["metrics"])
    checks["dbt.control_total"] = control_total(spec, drift["metrics"])
    reasons = [f"{k}: {v['reason']}" for k, v in checks.items() if v["outcome"] != "PASS" and v.get("reason")]
    gate = "ALLOW" if all(v["outcome"] in ("PASS", "NOT_RUN") for v in checks.values()) else "HOLD"
    result = {"change_id": cid, "gate": gate, "reasons": reasons, "checks": checks, "rationale": rationale,
              "files": sorted(files)}
    (run / "result.json").write_text(json.dumps(result, indent=1, default=str))
    return result
