"""E3b v2 labeller 1 by GPT-6 Luna (LABEL_GUIDE_v2.md, "Who labels"; Gary 11-07: "use Luna wherever it can be used").

Luna labels every blind sheet that `label_v2.py sheets` wrote, through the OpenAI API (DEVIATIONS 11-08: every model
call by API key, within the DS Harness budget). Each request holds the guide, GENERAL.md and one batch of sheets
inline; never label_index.json, the key file or any truth. Output: <run>/labels/designer_luna_<batch>.json, the
form `label_v2.py` reads as labeller 1. Haiku is labeller 2 and GPT-6.1 Terra adjudicates, so the two labellers and
the adjudicator come from different model families. Luna is also the Codex agent in the census: agreement is reported by agent.

    .venv/bin/python experiments/E3b/luna_label_v2.py experiments/E3b/runs/v2

Each batch is retried once; a batch that fails twice stops the run (no partial labels). Two batches run at a time.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench import openai_api  # noqa: E402

CODES = ("E-causal", "E-nodata", "E-read", "E-define", "E-extrapolate", "E-overclaim", "E-calc", "E-other")  # as label_v2.py
BATCH, WORKERS = 12, 2
PROMPT = """You label analysts' replies to requests at a UK gift wholesaler (the data ends 2025-08-31; the requests ask
about September to November 2025). Below are LABEL_GUIDE_v2.md (ignore its sections "Who labels" and "Untyped
numbers"), GENERAL.md (the general rules of every key) and the sheets. Each sheet has the request, the owner's key,
reference figures about the past, the files the reply delivers (name, columns, first rows) and the reply. Label each
reply's meaning against its key, the general rules and the guide. Do not judge whether a forecast will come true: a
program checks accuracy separately.

Reply with ONLY a JSON array, one object per sheet, in sheet order:
[{{"sheet": "S001", "supported": false, "codes": ["E-causal"], "why": "<at most 25 words, quoting the reply>"}}]
codes is [] when supported is true and has one or more of {codes} when it is false.

{files}"""


def valid(row: dict) -> bool:
    codes = row.get("codes")
    return (isinstance(row.get("supported"), bool) and isinstance(codes, list) and all(c in CODES for c in codes)
            and bool(codes) != row["supported"])


def batch(run: Path, k: int, sheets: list[Path], common: dict[str, str]) -> None:
    out = run / "labels" / f"designer_luna_{k:03d}.json"
    if out.exists():
        return
    want = {p.stem for p in sheets}
    files = {**common, **{f"sheets/{p.name}": p.read_text(encoding="utf-8") for p in sheets}}
    text = "\n\n".join(f"=== {name} ===\n{body.strip()}" for name, body in files.items())
    for attempt in (1, 2):
        d = run / "labels" / "luna_runs" / f"batch{k:03d}-try{attempt}"
        try:
            res, _ = openai_api.ask(PROMPT.format(codes=", ".join(CODES), files=text), model="gpt-6-luna",
                                    effort="high", log_dir=d, purpose="E3b labels (Luna)")
            m = re.search(r"\[.*\]", res, re.S)
            rows = json.loads(m.group(0)) if m else []
            got = [{"sheet": r["sheet"], "supported": r["supported"], "codes": r["codes"], "why": r.get("why", "")}
                   for r in rows if r.get("sheet") in want and valid(r)]
            if len(got) == len(want) == len({r["sheet"] for r in got}):
                out.write_text(json.dumps(got, indent=1, ensure_ascii=False))
                print(f"batch {k:03d}: {len(got)} labels", flush=True)
                return
            print(f"batch {k:03d} try {attempt}: {len(got)} valid labels for {len(want)} sheets", flush=True)
        except (openai_api.OpenAIAPIError, OSError, ValueError, KeyError, TypeError) as exc:
            print(f"batch {k:03d} try {attempt}: {exc}", flush=True)
    raise SystemExit(f"Luna batch {k} failed twice")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", type=Path)
    a = ap.parse_args()
    lab = a.run / "labels"
    common = {"LABEL_GUIDE_v2.md": (HERE / "LABEL_GUIDE_v2.md").read_text(),
              "GENERAL.md": (lab / "GENERAL.md").read_text()}
    paths = sorted((lab / "sheets").glob("S*.md"))
    batches = [paths[i:i + BATCH] for i in range(0, len(paths), BATCH)]
    print(f"{len(paths)} sheets in {len(batches)} batches", flush=True)
    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(lambda kb: batch(a.run, kb[0], kb[1], common), enumerate(batches)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
