"""E3a truth build B: an outside model implements the owner definitions from the raw lines alone.

Luna (gpt-6-luna, high) runs in the bwrap sandbox (no repository, no harness code, no pipeline SQL)
with the disguised raw lines, Python with duckdb, and the definitions text from DEFINITIONS.md. It
writes truth_lines.parquet, truth_metrics.csv and NOTES.md. compare_truth.py then compares B with the
governed build A line by line and metric by metric; disagreements are adjudicated by hand.

    .venv/bin/python experiments/E3a/run_truth_b.py --out workspace/external/e3a/truth_b
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench import sandbox as sb  # noqa: E402

HERE = Path(__file__).resolve().parent
RAW = ROOT / "workspace/external/e3a/raw_lines.parquet"
TOOL_ENV = ROOT.parent / "agent_env"               # Python with duckdb/pandas, as in census arm N
METRICS = ("gross_product_sales", "cancelled_value", "net_product_sales", "cancellation_rate", "orders",
           "average_order_value", "units_sold", "active_customers", "new_customers", "repeat_customers")
TASK = """You are a data engineer at an online gift wholesaler. Your working directory holds
`raw_lines.parquet`, the order-system export (columns order_no, sku, item_name, qty, ordered_at,
unit_price, buyer_id, country). Python 3 with duckdb and pandas is available as `python`.

Implement the company's official definitions below exactly, from the raw export only, and write:

1. `truth_lines.parquet`: one row per distinct order line, columns order_no, sku, item_name, qty,
   ordered_at, unit_price, buyer_id, country, amount (qty x unit_price, exact decimal arithmetic, not
   floating point), month ('YYYY-MM'), market ('UK' or 'non-UK'), line_type.
2. `truth_metrics.csv`: columns month, market, metric, value. One row per month from 2023-12 to
   2025-12, per market in ALL / UK / non-UK, per metric in: {metrics}. Leave a rate or average out
   (no row) where it is undefined.
3. `NOTES.md`: every rule you found ambiguous and how you decided, and any data you could not
   classify.

Work only from raw_lines.parquet and the definitions; do not look for other files. When done, reply
with a one-paragraph summary.

# Official definitions

"""


def brief() -> str:
    text = (HERE / "DEFINITIONS.md").read_text(encoding="utf-8")
    body = text[text.index("The company is"):]          # drop the title and the note about this repository
    return TASK.format(metrics=", ".join(METRICS)) + body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--timeout", type=int, default=3600)
    a = ap.parse_args()
    work, home = a.out / "work", a.out / "home"
    work.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(RAW, work / "raw_lines.parquet")
    prompt = brief()
    (a.out / "prompt.md").write_text(prompt, encoding="utf-8")
    cli = sb.find_cli("codex")
    sb.prepare_home(home, cli, codex_config='model = "gpt-6-luna"\nmodel_reasoning_effort = "high"\n'
                                            'web_search = "disabled"\n')
    cmd = [str(cli.binary), "exec", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox",
           "--json", "-o", f"{sb.HOME}/last_message.txt", prompt]
    shim = a.out / "shim_bin"
    shim.mkdir()
    for name in ("python", "python3"):
        (shim / name).symlink_to(TOOL_ENV / "bin/python")
    argv = sb.bwrap_command(cmd, work=work, home=home, cli=cli,
                            ro_paths=[TOOL_ENV, sb.HOME / ".local/share/uv/python"],
                            ro_binds_at=[(shim, "/usr/local/bin")],
                            env={"PATH": f"{TOOL_ENV}/bin:{cli.binary_dir}:/usr/local/bin:/usr/bin:/bin"})
    p = subprocess.run(argv, capture_output=True, text=True, timeout=a.timeout, stdin=subprocess.DEVNULL)
    (a.out / "stdout.txt").write_text(p.stdout)
    (a.out / "stderr.txt").write_text(p.stderr)
    (work / "raw_lines.parquet").unlink(missing_ok=True)
    out = {"exit": p.returncode, "files": sorted(f.name for f in work.iterdir())}
    print(json.dumps(out))
    return 0 if p.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
