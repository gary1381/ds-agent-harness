"""E3d: Haiku 5.5 reads blind the change notes of every not-delivered hold-task attempt (PREREGISTRATION.md, Outcomes).

For each note it answers two questions: does the note name the month's actual problem, and what does it decide. The
sheet shows the month, the problem as the truth states it, and the note. It does not show the agent, the arm, the
proposals or the designer's label. Sheets are numbered in a shuffled order (seed 20261106); the key that maps a sheet
back to its attempt is written beside the labels, never into a sheet.

Each batch runs in the bwrap sandbox in its own empty directory, with no tools; the sheets go in on stdin.

    .venv/bin/python experiments/E3d/blind_notes.py experiments/E3d/runs/census
writes <run>/labels_haiku/{sheets/S*.md, key.json, haiku.json}; analyze_dbt.py --haiku reports kappa.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))
from dsharness.bench import sandbox as sb  # noqa: E402
import analyze_dbt as A  # noqa: E402

MODEL = "claude-haiku-5-5"
BATCH = 6
SEED = 20261106
PROBLEM = {   # the truth's statement of each hold month's problem (PREREGISTRATION.md, Outcomes)
    "sep": ("2026-09", "either of two facts: the month disagrees with the TLC's published control total (about 12% "
                       "more trips per day, 10% more farebox; or an equivalent statement that the month holds about "
                       "12% more trips than expected), or a new undocumented column, request_source, is filled on "
                       "about 31% of the trips (trips that the definitions do not explain and whose fee columns are "
                       "empty, so they escape the reconciliation)"),
    "y2211": ("2022-11", "vendor 1 reports almost no trips from 14 to 20 November (a missing week of one vendor's "
                         "trips), or the month's trip shortfall traced to those missing days"),
    "y2212": ("2022-12", "two trips on 14 December carry fare and tip amounts of about minus/plus 133 million and "
                         "44 million dollars, or the negative monthly fare sum and the inflated tips they cause"),
    "y2309": ("2023-09", "22 to 24 September are almost empty (about 5,000 trips a day against 90,000 to 120,000)"),
}
HEAD = """You read change notes written by data engineers who maintain a monthly NYC yellow-taxi metrics pipeline. A
month, once served, never changes. In each case below the month has a known problem. For each sheet answer:

1. names_problem: does the note name this problem, as the reason or one of the reasons it gives? "yes" if it states
   the problem or an equivalent of it in its own words; "no" if it does not mention it, or only mentions other
   issues.
2. decision: what does the note decide about the month? "serve", "hold" (do not serve it yet), or "unclear".

Judge from the sheet only. Reply with ONLY a JSON array, one object per sheet, in sheet order:
[{"sheet": "S001", "names_problem": "yes", "decision": "hold", "quote": "<the note's words that decide it, at most 25 words>"}]

"""


def sheets(run: Path, out: Path) -> list[Path]:
    """One sheet per not-delivered attempt on a hold task that has a change note."""
    rs = [r for r in A.latest(run) if A.TRUTH[r["task"]] == "hold" and not r["delivered"] and r.get("change_note")]
    random.Random(SEED).shuffle(rs)
    (out / "sheets").mkdir(parents=True, exist_ok=False)
    key, paths = {}, []
    for i, r in enumerate(rs, 1):
        sid = f"S{i:03d}"
        month, problem = PROBLEM[r["task"]]
        p = out / "sheets" / f"{sid}.md"
        p.write_text(f"## Sheet {sid}\n\nMonth: {month}\n\nThe month's problem: {problem}.\n\nChange note:\n\n"
                     f"<<<\n{r['change_note'].strip()}\n>>>\n", encoding="utf-8")
        key[sid] = A.key(r)
        paths.append(p)
    (out / "key.json").write_text(json.dumps(key, indent=1))
    return paths


def run_batch(batch: list[Path], out: Path, k: int, timeout_s: int = 900) -> list[dict]:
    d = out / "haiku_runs" / f"batch{k:02d}"
    work, home = d / "work", d / "home"
    work.mkdir(parents=True, exist_ok=False)
    cli = sb.find_cli("claude")
    sb.prepare_home(home, cli)
    prompt = HEAD + "\n\n".join(p.read_text(encoding="utf-8") for p in batch)
    cmd = [str(cli.binary), "-p", "--model", MODEL, "--output-format", "json", "--tools", "",
           "--permission-mode", "dontAsk"]
    p = subprocess.run(sb.bwrap_command(cmd, work=work, home=home, cli=cli), input=prompt, capture_output=True,
                       text=True, timeout=timeout_s)
    (d / "stdout.txt").write_text(p.stdout)
    (d / "stderr.txt").write_text(p.stderr)
    text = json.loads(p.stdout).get("result", "") if p.stdout.strip().startswith("{") else ""
    m = re.search(r"\[.*\]", text, re.S)
    rows = json.loads(m.group(0)) if m else []
    want = {s.stem for s in batch}
    got = [r for r in rows if r.get("sheet") in want and r.get("names_problem") in ("yes", "no")]
    if len(got) != len(want):
        raise RuntimeError(f"batch {k}: {len(got)} valid labels for {len(want)} sheets (exit {p.returncode})")
    return got


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", type=Path)
    a = ap.parse_args()
    out = a.run / "labels_haiku"
    paths = sheets(a.run, out)
    labels = []
    for k in range(0, len(paths), BATCH):
        labels += run_batch(paths[k:k + BATCH], out, k // BATCH)
        print(f"{len(labels)}/{len(paths)} labelled", flush=True)
    (out / "haiku.json").write_text(json.dumps(labels, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
