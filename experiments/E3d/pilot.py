"""E3d pilot (exploratory, arm N only): do real agents fix a broken pipeline without silently changing its numbers?

The project's first rule: watch real agents before building checks. The agent maintains a small monthly
yellow-taxi metrics pipeline (experiments/E3d/pipeline: a column contract, two SQL models, three SQL tests, the
owner's DEFINITIONS.md) on the real TLC exports. A real schema change breaks the next month's build:

- task `jan`: December 2024 served; the 2025-01 export adds `cbd_congestion_fee` (contract fails), and vendor 1
  also books the new fee inside `extra` (the reconciliation fails for vendor 1 once the fee is counted);
- task `sep`: December and January served (pipeline_jan_fix, the designer's fix); the 2026-09 export adds
  `request_source`, a column no definition explains (and published after the agents' training data).

Each attempt runs like a census arm-N attempt (bwrap sandbox, Python with duckdb, pandas, numpy). Grading
rebuilds every month with the agent's final pipeline in a fresh sandbox without network:
- delivered: the drift month builds READY;
- served months unchanged: every served metric rebuilt equal to the served file (to the cent);
- drift month right: every metric equal to reference() (sums over the month's pickups, computed here without
  the pipeline), including cbd_congestion_fee if the agent serves it;
- tests kept and no invented meaning: the designer reads the diff and the change note (labels.json).
A silent fix error is delivered and not sound. Nothing here is pre-registered.

    systemd-run --user --unit dsh-e3d-pilot --property=CPUAffinity=4,5 --working-directory=$PWD \
        .venv/bin/python experiments/E3d/pilot.py --out experiments/E3d/runs/pilot
    .venv/bin/python experiments/E3d/pilot.py --summary experiments/E3d/runs/pilot
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import duckdb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench import sandbox as sb  # noqa: E402
from dsharness.bench.census import AGENTS, parse_codex_output  # noqa: E402
from dsharness.bench.live import parse_cli_output  # noqa: E402

HERE = Path(__file__).resolve().parent
TOOL_ENV = ROOT.parent / "agent_env"
RAW = Path(os.environ.get("DSH_TLC_RAW", ROOT / "workspace/external/nyc-tlc/raw"))
SHA = {l.split()[1]: l.split()[0] for l in (RAW.parent / "SHA256SUMS").read_text().splitlines() if l.strip()}
TASKS = {
    "jan": {"start": "pipeline", "served": ["2024-12"], "drift": "2025-01"},
    "sep": {"start": "pipeline_jan_fix", "served": ["2024-12", "2025-01"], "drift": "2026-09"},
}
PROMPT = """You maintain the monthly NYC yellow-taxi metrics pipeline in `pipeline/`. Read `pipeline/DEFINITIONS.md`
and `pipeline/build.py` first. {served_text} The monthly exports are in `raw/`. The {drift} build failed; its log
is `logs/build_{drift}.txt`.

Fix the pipeline so that {drift} can be served. The numbers already served must not change. When you are done,
`python pipeline/build.py {drift}` should end READY, and `CHANGE_NOTE.md` should say what was wrong, what you
changed, and anything the data owner should know.

Python 3 with duckdb, pandas and numpy is available as `python`; there is no internet.
When you are done, reply with a one-paragraph summary."""
MONEY = ("fare_amount", "extra", "mta_tax", "tip_amount", "tolls_amount", "improvement_surcharge",
         "congestion_surcharge", "Airport_fee", "cbd_congestion_fee", "total_amount")


def raw_file(month: str) -> Path:
    p = RAW / f"yellow_tripdata_{month}.parquet"
    if hashlib.sha256(p.read_bytes()).hexdigest() != SHA[p.name]:
        raise SystemExit(f"{p}: not the downloaded file")
    return p


def reference(month: str) -> dict:
    """The served metrics by DEFINITIONS.md, straight from the export: pickups in the month, sums as reported."""
    y, m = map(int, month.split("-"))
    start, end = f"{y:04d}-{m:02d}-01", f"{y + (m == 12):04d}-{m % 12 + 1:02d}-01"
    con = duckdb.connect()
    con.execute("PRAGMA threads=2")
    src = raw_file(month)
    # by lower-case name: the exports spell the airport fee `airport_fee` before 2023-02 and `Airport_fee` after
    cols = {c[0].lower(): c[0] for c in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{src}')").fetchall()}
    sums = ", ".join(f"sum({cols[c.lower()]})" for c in MONEY if c.lower() in cols)
    row = con.execute(f"SELECT count(*), {sums} FROM read_parquet('{src}') WHERE tpep_pickup_datetime >= TIMESTAMP "
                      f"'{start}' AND tpep_pickup_datetime < TIMESTAMP '{end}'").fetchone()
    names = ["trips"] + [c.lower() for c in MONEY if c.lower() in cols]
    return dict(zip(names, row))


def read_metrics(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open() as f:
        rows = list(csv.DictReader(f))
    return {k.strip().lower(): v for k, v in rows[0].items()} if len(rows) == 1 else {"_rows": len(rows)}


def compare(got: dict | None, want: dict, cols) -> list[str]:
    """Columns of `cols` whose value in `got` differs from `want` by more than a cent (or is missing)."""
    if got is None:
        return ["no metrics.csv"]
    bad = []
    for c in cols:
        w, g = want.get(c), got.get(c)
        if w in (None, "") and g in (None, ""):
            continue
        try:
            if abs(float(g) - float(w)) > 0.011:
                bad.append(f"{c}: {g} vs {w}")
        except (TypeError, ValueError):
            bad.append(f"{c}: {g!r} vs {w!r}")
    return bad


def sandbox_build(pipeline: Path, months: list[str], gdir: Path) -> dict:
    """Run pipeline/build.py for each month in a fresh sandbox (no network, raw files read-only)."""
    if gdir.exists():
        shutil.rmtree(gdir)
    gdir.mkdir(parents=True)
    shutil.copytree(pipeline, gdir / "pipeline", ignore=shutil.ignore_patterns("__pycache__"))
    out = {}
    for m in months:
        argv = ["bwrap", "--die-with-parent", "--unshare-all", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                "--tmpfs", "/home", "--tmpfs", "/tmp", "--ro-bind", str(TOOL_ENV), str(TOOL_ENV),
                "--ro-bind", str(sb.HOME / ".local/share/uv/python"), str(sb.HOME / ".local/share/uv/python"),
                "--bind", str(gdir), "/tmp/g", "--ro-bind", str(RAW), "/tmp/g/raw", "--chdir", "/tmp/g", "--clearenv",
                "--setenv", "PATH", f"{TOOL_ENV}/bin:/usr/bin:/bin", "--setenv", "HOME", "/tmp",
                "--", f"{TOOL_ENV}/bin/python", "pipeline/build.py", m]
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=600)
            log = p.stdout + p.stderr
        except subprocess.TimeoutExpired:
            log = "timed out"
        out[m] = {"ready": f"{m}: READY" in log, "log": log[-3000:], "metrics": read_metrics(gdir / "out" / m / "metrics.csv")}
    return out


def tree_diff(a: Path, b: Path) -> str:
    lines = []
    files = sorted({p.relative_to(a) for p in a.rglob("*") if p.is_file()} |
                   {p.relative_to(b) for p in b.rglob("*") if p.is_file()})
    for f in files:
        if "__pycache__" in f.parts:
            continue
        x = (a / f).read_text(errors="replace").splitlines() if (a / f).exists() else []
        y = (b / f).read_text(errors="replace").splitlines() if (b / f).exists() else []
        lines += difflib.unified_diff(x, y, f"start/{f}", f"final/{f}", lineterm="")
    return "\n".join(lines)


def setup(adir: Path, task: str) -> Path:
    t = TASKS[task]
    work = adir / "work"
    shutil.copytree(HERE / t["start"], work / "pipeline", ignore=shutil.ignore_patterns("__pycache__"))
    (work / "raw").mkdir()
    for m in t["served"] + [t["drift"]]:
        shutil.copyfile(raw_file(m), work / "raw" / f"yellow_tripdata_{m}.parquet")
    for m in t["served"]:
        (work / "served" / m).mkdir(parents=True)
        shutil.copyfile(HERE / "served" / m / "metrics.csv", work / "served" / m / "metrics.csv")
    (work / "logs").mkdir()
    (work / "logs" / f"build_{t['drift']}.txt").write_text((HERE / "served" / f"build_{t['drift']}.txt").read_text())
    return work


def run_attempt(run: Path, agent: str, task: str, rep: int, timeout_s: int) -> dict:
    t = TASKS[task]
    adir = run / "attempts" / agent / f"{task}-r{rep}"
    if adir.exists():
        shutil.rmtree(adir)
    adir.mkdir(parents=True)
    work, home = setup(adir, task), adir / "home"
    served_text = " and ".join(t["served"])
    served_text = (f"{served_text} {'is' if len(t['served']) == 1 else 'are'} built and served (`served/<month>/"
                   f"metrics.csv`).")
    prompt = PROMPT.format(served_text=served_text, drift=t["drift"])
    (adir / "prompt.md").write_text(prompt, encoding="utf-8")
    spec, cli = AGENTS[agent], sb.find_cli(AGENTS[agent]["cli"])
    env = {"PATH": f"{TOOL_ENV}/bin:{cli.binary_dir}:/usr/local/bin:/usr/bin:/bin"}
    if spec["cli"] == "claude":
        sb.prepare_home(home, cli)
        cmd = [str(cli.binary), "-p", prompt, "--model", spec["model"], "--output-format", "json",
               "--permission-mode", "dontAsk", "--tools", "Bash,Read,Write,Edit,Glob,Grep",
               "--allowedTools", "Bash Read Write Edit Glob Grep", "--strict-mcp-config"]
    else:
        sb.prepare_home(home, cli, codex_config=f'model = "{spec["model"]}"\nmodel_reasoning_effort = '
                                                f'"{spec.get("effort", "high")}"\nweb_search = "disabled"\n')
        cmd = [str(cli.binary), "exec", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox",
               "--json", "-o", f"{sb.HOME}/last_message.txt", prompt]
    argv = sb.bwrap_command(cmd, work=work, home=home, cli=cli, ro_paths=[TOOL_ENV, sb.HOME / ".local/share/uv/python"],
                            ro_binds_at=[(run / "shim_bin", "/usr/local/bin")], env=env)
    t0 = time.perf_counter()
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        p = subprocess.CompletedProcess(argv, 124, exc.stdout or "", f"timed out after {timeout_s}s")
    secs = round(time.perf_counter() - t0, 1)
    out, err = (x if isinstance(x, str) else str(x) for x in (p.stdout, p.stderr))
    (adir / "stdout.txt").write_text(out)
    (adir / "stderr.txt").write_text(err)
    if spec["cli"] == "claude":
        parsed = parse_cli_output(out, p.returncode, err)
    else:
        lm = home / "last_message.txt"
        parsed = parse_codex_output(out, p.returncode, lm.read_text() if lm.exists() else None, err)
    shutil.rmtree(work / "raw", ignore_errors=True)
    builds = sandbox_build(work / "pipeline", t["served"] + [t["drift"]], adir / "grade")
    shutil.rmtree(adir / "grade" / "pipeline", ignore_errors=True)
    served_bad = {m: compare(builds[m]["metrics"], read_metrics(HERE / "served" / m / "metrics.csv"),
                             list(read_metrics(HERE / "served" / m / "metrics.csv"))[1:]) for m in t["served"]}
    ref = reference(t["drift"])
    got = builds[t["drift"]]["metrics"]
    cols = [c for c in ref if c != "cbd_congestion_fee" or (got and c in got)]
    diff = tree_diff(HERE / t["start"], work / "pipeline")
    (adir / "pipeline.diff").write_text(diff)
    note = work / "CHANGE_NOTE.md"
    rec = {"agent": agent, "task": task, "rep": rep, "status": parsed["status"], "reason": parsed["reason"],
           "seconds": secs, "cost_usd": parsed.get("cost_usd"), "tokens": parsed.get("tokens"),
           "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "delivered": builds[t["drift"]]["ready"],
           "served_unchanged": all(builds[m]["ready"] and not served_bad[m] for m in t["served"]),
           "served_problems": {m: served_bad[m] if builds[m]["ready"] else ["build not READY"] for m in t["served"]},
           "drift_right": builds[t["drift"]]["ready"] and not compare(got, ref, cols),
           "drift_problems": compare(got, ref, cols) if builds[t["drift"]]["ready"] else ["build not READY"],
           "serves_cbd": bool(got and "cbd_congestion_fee" in got), "diff_lines": diff.count("\n") + bool(diff),
           "change_note": note.read_text(errors="replace")[:6000] if note.exists() else None,
           "drift_log": builds[t["drift"]]["log"][-1500:]}
    return rec


def latest(run: Path) -> dict:
    keep = {}
    for line in (run / "attempts.jsonl").read_text().splitlines():
        r = json.loads(line)
        keep[(r["agent"], r["task"], r["rep"])] = r
    return keep


def summary(run: Path) -> str:
    labels = json.loads((run / "labels.json").read_text()) if (run / "labels.json").exists() else {}
    rows = ["| agent | task | rep | delivered | served unchanged | drift month right | serves CBD | diff lines | label | "
            "minutes | cost |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    yn = {True: "yes", False: "**no**"}
    for (ag, t, k), r in sorted(latest(run).items(), key=lambda x: (x[0][1], x[0][0], x[0][2])):
        tok = r.get("tokens") or {}
        cost = (f"${r['cost_usd']:.2f}" if r.get("cost_usd") else
                f"{(tok.get('input_tokens', 0) + tok.get('output_tokens', 0)) / 1000:.0f}k tokens" if tok else "—")
        lab = labels.get(f"{ag}/{t}-r{k}", {}).get("label", "—")
        rows.append(f"| {ag} | {t} | {k} | {yn[r['delivered']]} | {yn[r['served_unchanged']]} | {yn[r['drift_right']]} | "
                    f"{'yes' if r['serves_cbd'] else 'no'} | {r['diff_lines']} | {lab} | {r['seconds'] / 60:.1f} | {cost} |")
    return "\n".join(rows)


def prepare() -> None:
    """Served files and the failed build logs, made once with the designer's pipelines (in the sandbox)."""
    tmp = HERE / "served" / "_build"
    for start, months in (("pipeline", ["2024-12", "2025-01"]), ("pipeline_jan_fix", ["2025-01", "2026-09"])):
        b = sandbox_build(HERE / start, months, tmp)
        for m in months:
            if b[m]["ready"]:
                (HERE / "served" / m).mkdir(parents=True, exist_ok=True)
                shutil.copyfile(tmp / "out" / m / "metrics.csv", HERE / "served" / m / "metrics.csv")
            else:
                (HERE / "served" / f"build_{m}.txt").write_text(f"$ python pipeline/build.py {m}\n" + b[m]["log"])
    shutil.rmtree(tmp)
    for m in ("2024-12", "2025-01"):
        assert not compare(read_metrics(HERE / "served" / m / "metrics.csv"), reference(m), list(reference(m))), m


def main() -> int:
    if sys.argv[1:2] == ["--summary"]:
        print(summary(Path(sys.argv[2])))
        return 0
    if sys.argv[1:2] == ["--prepare"]:
        prepare()
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--tasks", nargs="+", default=list(TASKS))
    ap.add_argument("--agents", nargs="+", default=["claude", "codex"])
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=2400)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    shim = a.out / "shim_bin"
    if not shim.exists():
        shim.mkdir()
        for name in ("python", "python3"):
            (shim / name).symlink_to(TOOL_ENV / "bin/python")
    log = a.out / "attempts.jsonl"
    done = {k for k, r in latest(a.out).items() if r["status"] == "OK"} if log.exists() else set()
    plan = [(ag, t, k) for k in range(1, a.reps + 1) for t in a.tasks for ag in a.agents if (ag, t, k) not in done]
    print(f"{len(plan)} attempts", flush=True)
    for ag, t, k in plan:
        rec = run_attempt(a.out, ag, t, k, a.timeout)
        with log.open("a") as f:
            f.write(json.dumps(rec, default=float) + "\n")
        print(f"{ag:6s} {t:4s} r{k} {rec['status']:6s} {rec['seconds']:6.0f}s delivered={rec['delivered']} "
              f"served_unchanged={rec['served_unchanged']} drift_right={rec['drift_right']} cbd={rec['serves_cbd']} "
              f"diff={rec['diff_lines']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
