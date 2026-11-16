"""E1: Haiku 5.5 labels a random 30% of wrong attempts blind, for Cohen's kappa against the designer.

Each batch runs in the bwrap sandbox in its own empty directory, with no tools. The model sees only
the taxonomy and the blind sheets (question, truth, answer, grade, transcript digest; no agent, arm,
tier or designer label). Sheets go in on stdin (long prompts overflow argv).

    .venv/bin/python experiments/E1/label_sheet.py --run main --blind
    .venv/bin/python experiments/E1/run_blind_labels.py --run main
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench import sandbox as sb  # noqa: E402

MODEL = "claude-haiku-5-5"
BATCH = 8
LABELS = ("E-def", "E-test", "E-refund", "E-dup", "E-unit", "E-time", "E-late", "E-src", "E-join", "E-calc",
          "E-read", "E-fmt", "E-proc", "E-other")

TABLE_E1 = """| label | meaning |
|---|---|
| E-def | used a different metric definition than the official one |
| E-test | test accounts not excluded |
| E-refund | refunds handled wrong (gross vs net, wrong month) |
| E-dup | duplicates counted, or legitimate identical rows removed |
| E-unit | unit error (fen vs CNY, x100) |
| E-time | wrong period, month boundary, as-of date or timezone |
| E-late | incomplete (late-arriving) data treated as complete |
| E-src | wrong source (raw or staging instead of certified), other than the above |
| E-join | join fan-out or grain error |
| E-calc | aggregation or arithmetic error (e.g. average of averages) |
| E-read | misread the question (answered a different slice or quantity) |
| E-fmt | answer shape (missing keys, extra rows, wrong value column) |"""
TABLE_E3A = """| label | meaning |
|---|---|
| E-def | used a different metric definition than the official one (net for gross, guest orders counted as customers, average order value over all lines, an unofficial "new" or "repeat" customer), or merged an item code with its lower-case variant |
| E-test | non-product lines (adjustments, fees, vouchers, test or gift-shop codes, zero or negative price or quantity) kept in a product metric, or product lines excluded wrongly |
| E-refund | cancellation lines (orders starting with C) handled wrong: left in gross sales, netted out of gross, booked in the original order's month, sign flipped |
| E-dup | exact duplicate rows counted twice, or rows that differ merged |
| E-unit | unit error (ratio vs percent, x100 twice, pence vs pounds) |
| E-time | wrong months, quarter or year, month boundary, or a month taken from another timestamp |
| E-late | the partial last month (December 2025) treated as complete |
| E-src | wrong source (raw or staging instead of certified), other than the above |
| E-join | grain error: lines counted as orders, orders as customers, a join that repeats lines |
| E-calc | aggregation or arithmetic error (average of monthly ratios, sum of monthly distinct counts, wrong running-total start, growth base swapped) |
| E-read | misread the question (answered a different slice or quantity: one market for both, a country for a market, top 3 for top 5) |
| E-fmt | answer shape (monthly rows for one total, wrong key column, extra rows) |"""
TABLES = {"e1": TABLE_E1, "e3a": TABLE_E3A}   # e3a: the retail meanings of experiments/E3a/LABEL_NOTES.md
HEAD = """You label why a data analyst's answer was graded wrong. For each sheet below pick exactly ONE
primary label from this fixed list:

{table}
| E-proc | process failure (timeout, no final JSON, tool error, gave up) |
| E-other | none of the above |

Each sheet shows the question, the true values, the submitted rows, the grader's reason and a digest
of the analyst's work. Judge from the evidence on the sheet only.

Reply with ONLY a JSON array, one object per sheet, in sheet order:
[{"sheet": "W001", "label": "E-...", "reason": "<one sentence>"}]

"""


def run_batch(sheets: list[Path], out: Path, k: int, table: str = TABLE_E1, timeout_s: int = 900) -> list[dict]:
    d = out / f"batch{k:02d}"
    work, home = d / "work", d / "home"
    work.mkdir(parents=True, exist_ok=False)
    cli = sb.find_cli("claude")
    sb.prepare_home(home, cli)
    prompt = HEAD.replace("{table}", table) + "\n\n".join(p.read_text(encoding="utf-8") for p in sheets)
    cmd = [str(cli.binary), "-p", "--model", MODEL, "--output-format", "json", "--tools", "",
           "--permission-mode", "dontAsk"]
    argv = sb.bwrap_command(cmd, work=work, home=home, cli=cli)
    p = subprocess.run(argv, input=prompt, capture_output=True, text=True, timeout=timeout_s)
    (d / "stdout.txt").write_text(p.stdout)
    (d / "stderr.txt").write_text(p.stderr)
    text = json.loads(p.stdout).get("result", "") if p.stdout.strip().startswith("{") else ""
    m = re.search(r"\[.*\]", text, re.S)
    rows = json.loads(m.group(0)) if m else []
    want = {s.stem for s in sheets}
    got = [r for r in rows if r.get("sheet") in want and r.get("label") in LABELS]
    if len(got) != len(want):
        raise RuntimeError(f"batch {k}: {len(got)} valid labels for {len(want)} sheets (exit {p.returncode})")
    return got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--runs-root", type=Path, default=ROOT / "experiments/E1/runs")
    ap.add_argument("--world", choices=sorted(TABLES), default="e1", help="which meanings the label table gives")
    a = ap.parse_args()
    lab = a.runs_root / a.run / "labels"
    sheets = sorted((lab / "blind").glob("W*.md"))
    out = lab / "haiku_runs"
    out.mkdir(parents=True, exist_ok=False)
    labels = []
    for k in range(0, len(sheets), BATCH):
        labels += run_batch(sheets[k:k + BATCH], out, k // BATCH, TABLES[a.world])
        print(f"{len(labels)}/{len(sheets)} labelled", flush=True)
    (lab / "haiku_blind.json").write_text(json.dumps(labels, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
