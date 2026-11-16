"""E3b v2 blind adjudication by GPT-6.1 Terra through the OpenAI API (Gary 2026-11-08; DEVIATIONS 11-08).

Run after `label_v2.py adjudicate <run>`, which writes labels/adjudicate/D###.md: each disagreement's blind sheet
with the two labels unattributed and in random order. Terra is neither labeller's family (Luna is labeller 1, Haiku
labeller 2). It sees one D###.md at a time, the label guide (without "Who labels" and "Untyped numbers") and the
general rules; never the agent, arm, gate verdict, outcome or which labeller wrote what. Each answer is checked like
a label (codes from the list, codes empty exactly when supported); an invalid answer is asked once more, then the
run stops. Writes labels/adjudicate/adjudications.json for `label_v2.py final`.

    .venv/bin/python experiments/E3b/adjudicate_terra.py experiments/E3b/runs/v2
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench import openai_api  # noqa: E402

MODEL, EFFORT = "gpt-6.1-terra", "high"
CODES = ("E-causal", "E-nodata", "E-read", "E-define", "E-extrapolate", "E-overclaim", "E-calc", "E-other")

PROMPT = """You adjudicate one disagreement in a blind labelling study. Analysts answered a forecasting request on a
UK gift wholesaler's order data; two labellers judged whether the reply is supported and disagreed. You did not
label anything before. Decide the label yourself from the guide, the general rules, the owner's key on the sheet and
the reply: the two labels below the sheet are input to weigh, not votes.

LABEL GUIDE
<<<
{guide}
>>>

{general}

SHEET WITH THE TWO LABELS
<<<
{sheet}
>>>

Reply with ONLY one JSON object:
{{"supported": true or false, "codes": [], "why": "<at most 25 words, quoting the reply>"}}
codes is [] when supported is true, and one or more of {codes} when it is false."""


def guide_text() -> str:
    g = (HERE / "LABEL_GUIDE_v2.md").read_text()
    for drop in ("## Who labels", "## Untyped numbers"):
        if drop in g:
            start = g.index(drop)
            nxt = g.find("\n## ", start + 1)
            g = g[:start] + (g[nxt + 1:] if nxt >= 0 else "")
    return g.strip()


def valid(row: dict) -> bool:
    codes = row.get("codes")
    return (isinstance(row.get("supported"), bool) and isinstance(codes, list) and all(c in CODES for c in codes)
            and bool(codes) != row["supported"])


def decide(sheet: str, guide: str, general: str, d: Path) -> dict:
    prompt = PROMPT.format(guide=guide, general=general.strip(), sheet=sheet.strip(), codes=", ".join(CODES))
    for attempt in (1, 2):
        text, _ = openai_api.ask(prompt, model=MODEL, effort=EFFORT, max_output_tokens=8000,
                                 log_dir=d / f"try{attempt}", purpose="E3b adjudication")
        m = re.search(r"\{.*\}", text, re.S)
        try:
            row = json.loads(m.group(0)) if m else {}
        except json.JSONDecodeError:
            row = {}
        if valid(row):
            return {"supported": row["supported"], "codes": row["codes"], "why": str(row.get("why", ""))[:300]}
    raise SystemExit(f"{d.name}: Terra gave no valid label twice")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", type=Path)
    a = ap.parse_args()
    adir = a.run / "labels" / "adjudicate"
    sheets = sorted(adir.glob("D*.md"))
    guide, general = guide_text(), (a.run / "labels" / "GENERAL.md").read_text()
    done_f = adir / "terra_done.json"                 # resumable: decisions already made are kept
    done = json.loads(done_f.read_text()) if done_f.exists() else {}
    for i, p in enumerate(sheets, 1):
        if p.stem not in done:
            done[p.stem] = decide(p.read_text(), guide, general, adir / "terra_runs" / p.stem)
            done_f.write_text(json.dumps(done, indent=1, ensure_ascii=False))
        print(f"{i}/{len(sheets)} adjudicated", flush=True)
    rows = [{"sheet": s, **done[s]} for s in sorted(done) if s in {p.stem for p in sheets}]
    (adir / "adjudications.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    print(f"{len(rows)} adjudications -> {adir / 'adjudications.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
