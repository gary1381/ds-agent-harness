"""E3d labels by GPT-6 Luna (DEVIATIONS.md 11-07 23:40: Gary, "use Luna wherever it can be used").

  designer  Luna labels the designer sheets (label_sheet.py) per LABEL_GUIDE.md, in place of the designer's
            subagents. The sandbox holds the guide, one batch of sheets (grouped by task) and the start projects
            the diffs refer to. Output: <run>/labels_designer/labels_luna_<batch>.json, the form --merge reads.
  blind     Luna labels the blind sheets that blind_notes.py wrote (same prompt as Haiku), as a second blind
            labeller. Output: <run>/labels_haiku/luna.json; analyze_dbt.py reports its kappa beside Haiku's.

    .venv/bin/python experiments/E3d/luna_labels.py designer experiments/E3d/runs/census
    .venv/bin/python experiments/E3d/luna_labels.py blind experiments/E3d/runs/census

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
sys.path.insert(0, str(HERE))
from dsharness.bench.luna import LunaError, ask  # noqa: E402
import blind_notes as B  # noqa: E402
from label_sheet import CODES  # noqa: E402
import run_dbt as R  # noqa: E402

BATCH, BLIND_BATCH, WORKERS = 6, 12, 2
SKIP = ("target", "dbt_packages", "logs", "package-lock.yml")

DESIGNER = """You label sheets for a data-pipeline experiment. Read LABEL_GUIDE.md first, then every sheet in
sheets/. The diffs on the sheets are against the start projects in projects/ (each project's DEFINITIONS.md is the
owner's definitions). Label every sheet exactly as the guide says: N sheets names_problem, U sheets unsound + code,
Y sheets names_extreme_trip. Judge U sheets from the diff, not from the note's claims. Ignore the guide's section
"Who labels".

Write labels.json in this directory: a JSON array with one object per sheet in sheets/, in the forms of the guide's
"Output" and "Y sheets" sections, with "why" one sentence. Codes allowed: {codes}. Then reply "done"."""


def projects() -> dict[str, str]:
    files = {}
    for start in sorted({t["start"] for t in R.TASKS.values()}):
        for f in sorted((HERE / start).rglob("*")):
            rel = f.relative_to(HERE / start)
            if f.is_file() and not any(part in SKIP for part in rel.parts):
                files[f"projects/{start}/{rel}"] = f.read_text(encoding="utf-8", errors="replace")
    return files


def valid_designer(row: dict) -> bool:
    s = row.get("sheet", "")
    if s.startswith("N"):
        return isinstance(row.get("names_problem"), bool)
    if s.startswith("Y"):
        return isinstance(row.get("names_extreme_trip"), bool)
    return isinstance(row.get("unsound"), bool) and (row.get("code") in CODES) == row["unsound"]


def designer_batch(run: Path, k: int, sheets: list[Path], common: dict[str, str]) -> None:
    out = run / "labels_designer" / f"labels_luna_{k:02d}.json"
    if out.exists():
        return
    want = {p.stem for p in sheets}
    files = {**common, **{f"sheets/{p.name}": p.read_text(encoding="utf-8") for p in sheets}}
    for attempt in (1, 2):
        d = run / "labels_designer" / "luna_runs" / f"batch{k:02d}-try{attempt}"
        try:
            _, work = ask(DESIGNER.format(codes=", ".join(CODES)), d, files)
            rows = json.loads((work / "labels.json").read_text())
            got = [r for r in rows if r.get("sheet") in want and valid_designer(r)]
            if len(got) == len(want) == len({r["sheet"] for r in got}):
                out.write_text(json.dumps(got, indent=1, ensure_ascii=False))
                print(f"batch {k:02d}: {len(got)} labels", flush=True)
                return
            print(f"batch {k:02d} try {attempt}: {len(got)} valid labels for {len(want)} sheets", flush=True)
        except (LunaError, OSError, ValueError) as exc:
            print(f"batch {k:02d} try {attempt}: {exc}", flush=True)
    raise SystemExit(f"Luna designer batch {k} failed twice")


def designer(run: Path) -> int:
    d = run / "labels_designer"
    index = json.loads((d / "index.json").read_text())
    common = {"LABEL_GUIDE.md": (HERE / "LABEL_GUIDE.md").read_text(), **projects()}
    # batches never mix kinds or tasks, so a batch needs one project and one section of the guide
    groups: dict[tuple[str, str], list[Path]] = {}
    for sid, key in sorted(index.items()):
        groups.setdefault((sid[0], key.split("/")[1].split("-")[0]), []).append(d / "sheets" / f"{sid}.md")
    batches = [g[i:i + BATCH] for g in groups.values() for i in range(0, len(g), BATCH)]
    print(f"{len(index)} sheets in {len(batches)} batches", flush=True)
    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(lambda kb: designer_batch(run, kb[0], kb[1], common), enumerate(batches)))
    return 0


def blind_batch(out: Path, k: int, batch: list[Path]) -> list[dict]:
    f = out / "luna_batches" / f"batch{k:02d}.json"
    if f.exists():
        return json.loads(f.read_text())
    want = {p.stem for p in batch}
    prompt = (B.HEAD.replace("Reply with ONLY a JSON array", "Reply with ONLY a JSON array (no file writes)") +
              "\n\n".join(p.read_text(encoding="utf-8") for p in batch))
    for attempt in (1, 2):
        try:
            text, _ = ask(prompt, out / "luna_runs" / f"batch{k:02d}-try{attempt}")
            m = re.search(r"\[.*\]", text, re.S)
            rows = json.loads(m.group(0)) if m else []
            got = [r for r in rows if r.get("sheet") in want and r.get("names_problem") in ("yes", "no")]
            if len(got) == len(want):
                f.parent.mkdir(exist_ok=True)
                f.write_text(json.dumps(got, indent=1, ensure_ascii=False))
                return got
            print(f"blind batch {k} try {attempt}: {len(got)} valid labels for {len(want)} sheets", flush=True)
        except (LunaError, ValueError) as exc:
            print(f"blind batch {k} try {attempt}: {exc}", flush=True)
    raise SystemExit(f"Luna blind batch {k} failed twice")


def blind(run: Path) -> int:
    out = run / "labels_haiku"
    paths = sorted((out / "sheets").glob("S*.md"))
    batches = [paths[i:i + BLIND_BATCH] for i in range(0, len(paths), BLIND_BATCH)]
    with ThreadPoolExecutor(WORKERS) as ex:
        labels = [row for rows in ex.map(lambda kb: blind_batch(out, *kb), enumerate(batches)) for row in rows]
    (out / "luna.json").write_text(json.dumps(labels, indent=1, ensure_ascii=False))
    print(f"{len(labels)}/{len(paths)} blind labels -> {out / 'luna.json'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=("designer", "blind"))
    ap.add_argument("run", type=Path)
    a = ap.parse_args()
    return {"designer": designer, "blind": blind}[a.cmd](a.run)


if __name__ == "__main__":
    raise SystemExit(main())
