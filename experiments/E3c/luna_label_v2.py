"""E3c v2 labeller 1 by GPT-6 Luna (DEVIATIONS.md 11-07 23:41: Gary, "use Luna wherever it can be used").

Luna labels every blind sheet that `label_v2.py sheets` wrote, per LABEL_GUIDE_v2.md, in place of the designer's
subagents. The sandbox holds the guide, GENERAL.md and one batch of sheets; never label_index.json. Output:
<run>/labels/designer_luna_<batch>.json, the form `label_v2.py` reads as labeller 1. Haiku stays labeller 2 and a
fresh designer subagent (Claude) still adjudicates, so the two labellers come from two model families.

    .venv/bin/python experiments/E3c/luna_label_v2.py experiments/E3c/runs/v2

Each batch is retried once; a batch that fails twice stops the run (no partial labels). Two batches run at a time.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench.luna import LunaError, ask  # noqa: E402

CODES = ("E-select", "E-fish", "E-pool", "E-scale", "E-extrapolate", "E-nodata", "E-overclaim", "E-calc", "E-read",
         "E-other")      # as label_v2.py
BATCH, WORKERS = 12, 2
PROMPT = """You label analysts' replies about a randomised e-mail test. Read LABEL_GUIDE_v2.md (ignore its section
"Who labels") and GENERAL.md (the general rules of every key), then every sheet in sheets/. Each sheet has the
request, the owner's key and the reply. Label each reply against its key, the general rules and the guide.

Write labels.json in this directory: a JSON array with one object per sheet, in sheet order:
[{{"sheet": "S001", "supported": false, "codes": ["E-scale"], "why": "<at most 25 words, quoting the reply>"}}]
codes is [] when supported is true and has one or more of {codes} when it is false. Then reply "done"."""


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
    for attempt in (1, 2):
        d = run / "labels" / "luna_runs" / f"batch{k:03d}-try{attempt}"
        try:
            _, work = ask(PROMPT.format(codes=", ".join(CODES)), d, files)
            rows = json.loads((work / "labels.json").read_text())
            got = [{"sheet": r["sheet"], "supported": r["supported"], "codes": r["codes"], "why": r.get("why", "")}
                   for r in rows if r.get("sheet") in want and valid(r)]
            if len(got) == len(want) == len({r["sheet"] for r in got}):
                out.write_text(json.dumps(got, indent=1, ensure_ascii=False))
                print(f"batch {k:03d}: {len(got)} labels", flush=True)
                return
            print(f"batch {k:03d} try {attempt}: {len(got)} valid labels for {len(want)} sheets", flush=True)
        except (LunaError, OSError, ValueError, KeyError) as exc:
            print(f"batch {k:03d} try {attempt}: {exc}", flush=True)
    raise SystemExit(f"Luna batch {k} failed twice")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", type=Path)
    a = ap.parse_args()
    lab = a.run / "labels"
    common = {"LABEL_GUIDE_v2.md": (HERE / "LABEL_GUIDE_v2.md").read_text(), "GENERAL.md": (lab / "GENERAL.md").read_text()}
    paths = sorted((lab / "sheets").glob("S*.md"))
    batches = [paths[i:i + BATCH] for i in range(0, len(paths), BATCH)]
    print(f"{len(paths)} sheets in {len(batches)} batches", flush=True)
    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(lambda kb: batch(a.run, kb[0], kb[1], common), enumerate(batches)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
