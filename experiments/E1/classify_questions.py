"""E1: classify each bank question as DIRECT or DERIVED, blind to every result.

DIRECT: the answer is exactly the rows of one governed-metric query (one metric, a month range, filters
and group-by on that metric's dimensions, monthly grain, the metric's own unit), unchanged. DERIVED:
anything that needs arithmetic on those rows (sum over months, unit conversion, difference between
groups, share of a total, growth, ranking) or no governed metric fits. The coverage table
(experiments/E1/coverage.md) says most error labels are caught only "if a governed metric exists", so
decision rule D3 needs this split. Haiku 5.5 does it in the bwrap sandbox with no tools, seeing only
the questions and the metric list; it never sees attempts, labels or tiers.

    .venv/bin/python experiments/E1/classify_questions.py --out experiments/E1/questions/answer_form.json
    # another world: its own metrics, company and units (the E1/E1b prompt is unchanged by default)
    .venv/bin/python experiments/E1/classify_questions.py --questions experiments/E3a/questions/bank.yaml \
        --semantic experiments/E3a/context/semantic_model.yaml --company "an online gift wholesaler" \
        --units "GBP; ratio = 0-1" --conversion "a ratio to a percentage" \
        --workdir experiments/E3a/questions/classify_run --out experiments/E3a/questions/answer_form.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench import census as C  # noqa: E402
from dsharness.bench import sandbox as sb  # noqa: E402

MODEL = "claude-haiku-5-5"

HEAD = """Below are governed metrics of {company} and a list of business questions.

A governed-metric query returns, for ONE metric, one row per month in a month range, optionally
filtered to values of the metric's dimensions and optionally broken down by some of its dimensions.
Values are in the metric's own unit ({units}).

For each question decide:
- DIRECT: the correct answer is exactly the rows of one such query, unchanged (same unit, monthly rows).
- DERIVED: the answer needs any arithmetic on such rows (a total or average over several months, a unit
  conversion such as {conversion}, a difference or ratio between groups or
  metrics, a share of a total, growth, a ranking or top-N), or no governed metric fits.

Reply with ONLY a JSON array, one object per question, in order:
[{{"qid": "...", "form": "DIRECT" | "DERIVED", "metric": "<metric name or null>", "why": "<few words>"}}]

GOVERNED METRICS
"""


DEFAULTS = {"company": "a subscription company", "units": "fen = 1/100 CNY; ratio = 0-1",
            "conversion": "fen to CNY or a ratio to a percentage"}
SEMANTIC = ROOT / "src/dsharness/context/semantic_model.yaml"


def metric_list(semantic: Path = SEMANTIC) -> str:
    m = yaml.safe_load(semantic.read_text(encoding="utf-8"))["metrics"]
    return "\n".join(f"- {k} ({v.get('unit')}): {v.get('description', '').strip()} dimensions: {v.get('dimensions')}"
                     for k, v in m.items())


def build_prompt(qs, semantic: Path = SEMANTIC, **world) -> str:
    return (HEAD.format(**{**DEFAULTS, **world}) + metric_list(semantic) + "\n\nQUESTIONS\n"
            + "\n".join(f"- {q.qid}: {q.question}" for q in qs) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", type=Path, default=ROOT / "experiments/E1/questions/bank.yaml")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--semantic", type=Path, default=SEMANTIC)
    for name, default in DEFAULTS.items():
        ap.add_argument(f"--{name}", default=default)
    ap.add_argument("--workdir", type=Path, default=ROOT / "experiments/E1/questions/classify_run")
    a = ap.parse_args()
    qs = C.load_questions(a.questions)
    prompt = build_prompt(qs, a.semantic, **{k: getattr(a, k) for k in DEFAULTS})
    work, home = a.workdir / "work", a.workdir / "home"
    work.mkdir(parents=True, exist_ok=False)
    cli = sb.find_cli("claude")
    sb.prepare_home(home, cli)
    cmd = [str(cli.binary), "-p", "--model", MODEL, "--output-format", "json", "--tools", "",
           "--permission-mode", "dontAsk"]
    p = subprocess.run(sb.bwrap_command(cmd, work=work, home=home, cli=cli), input=prompt,
                       capture_output=True, text=True, timeout=900)
    text = json.loads(p.stdout).get("result", "") if p.stdout.strip().startswith("{") else ""
    m = re.search(r"\[.*\]", text, re.S)
    rows = json.loads(m.group(0)) if m else []
    got = {r["qid"]: r for r in rows if r.get("form") in ("DIRECT", "DERIVED")}
    missing = [q.qid for q in qs if q.qid not in got]
    if missing:
        raise SystemExit(f"no valid form for {missing} (exit {p.returncode})")
    a.out.write_text(json.dumps([got[q.qid] for q in qs], indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    n = sum(r["form"] == "DERIVED" for r in got.values())
    print(f"{len(got)} questions: {len(got) - n} DIRECT, {n} DERIVED -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
