"""CI build of one month of the yellow-taxi metrics:  python ci/build.py 2025-01

1. raw export check (ci/check_raw.py: contracts/yellow_trips.yml and a Great Expectations suite);
2. dbt build of the month into a fresh database: every model and every data test (a failing test stops the build
   and the models after it);
3. sqlfluff lint of models/, tests/ and macros/;
4. if all pass, monthly_metrics is written to out/<month>/metrics.csv and the month is READY.

Reads raw/yellow_tripdata_<month>.parquet (TAXI_RAW_DIR to read elsewhere).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parents[1]
os.environ.setdefault("DO_NOT_TRACK", "1")
os.environ.setdefault("DBT_SEND_ANONYMOUS_USAGE_STATS", "false")
sys.path.insert(0, str(HERE / "ci"))

import check_raw  # noqa: E402


def dbt_build(month: str, raw_dir: Path) -> tuple[bool, list[str]]:
    """dbt build in its own process (so its database connection is closed after); results from run_results.json."""
    db = HERE / "target" / f"{month}.duckdb"
    db.parent.mkdir(exist_ok=True)
    db.unlink(missing_ok=True)
    (HERE / "target" / "run_results.json").unlink(missing_ok=True)
    env = {**os.environ, "TAXI_DB": str(db)}
    p = subprocess.run([sys.executable, "-c", "import sys; from dbt.cli.main import cli; sys.exit(cli())", "build",
                        "--project-dir", str(HERE), "--profiles-dir", str(HERE), "--quiet", "--no-use-colors",
                        "--vars", f"{{month: '{month}', raw_dir: '{raw_dir}'}}"],
                       cwd=HERE, env=env, capture_output=True, text=True)
    rr = HERE / "target" / "run_results.json"
    lines = []
    if rr.exists():
        for r in json.loads(rr.read_text())["results"]:
            kind, name = r["unique_id"].split(".")[0], r["unique_id"].split(".")[2]
            msg = f"  {r['status'].upper():7} {kind} {name}"
            if r["status"] in ("fail", "warn", "error") and r.get("message"):
                msg += f": {r['message']}"
            lines.append(msg)
    if p.returncode != 0 and not lines:
        lines.append("  " + (p.stdout + p.stderr).strip().replace("\n", "\n  "))
    return p.returncode == 0, lines


def lint() -> tuple[bool, str]:
    p = subprocess.run([sys.executable, "-m", "sqlfluff", "lint", "models", "tests", "macros", "--nocolor"],
                       cwd=HERE, capture_output=True, text=True)
    return p.returncode == 0, p.stdout.strip()


def main() -> int:
    month = sys.argv[1]
    raw_dir = Path(os.environ.get("TAXI_RAW_DIR", HERE / "raw")).resolve()
    problems = check_raw.check(month, raw_dir)
    print(f"raw export check: {'PASS' if not problems else 'FAIL'}")
    for p in problems:
        print(f"  {p}")
    if problems:
        print(f"{month}: FAILED")
        return 1
    ok, lines = dbt_build(month, raw_dir)
    print(f"dbt build: {'PASS' if ok else 'FAIL'}")
    print("\n".join(lines))
    lint_ok, lint_out = (True, "") if os.environ.get("TAXI_CI_SKIP_LINT") else lint()
    print(f"sqlfluff lint: {'PASS' if lint_ok else 'FAIL'}")
    if not lint_ok:
        print("\n".join("  " + ln for ln in lint_out.splitlines() if ln.strip()))
    if not (ok and lint_ok):
        print(f"{month}: FAILED")
        return 1
    import duckdb

    out = HERE / "out" / month
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(HERE / "target" / f"{month}.duckdb"), read_only=True)
    con.execute(f"COPY (SELECT * FROM monthly_metrics) TO '{out / 'metrics.csv'}' (HEADER)")
    print(f"{month}: READY  -> out/{month}/metrics.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
