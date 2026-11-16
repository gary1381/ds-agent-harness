"""Blind review of the E3d tasks' ground truth by an outside model (Luna, gpt-6-luna high).

Luna runs in the bwrap sandbox with only the files in experiments/E3d/review_truth: the owner's definitions of each
task, the janx test SQL and the facts about 2026-09. It sees no harness code, no grading checks and no pilot
labels. It writes REVIEW.md: the sound and unsound outcomes of each task and, for sep, whether 2026-09 should be
served or held.

    .venv/bin/python experiments/E3d/review_truth.py --out experiments/E3d/runs/review_truth
    .venv/bin/python experiments/E3d/review_truth.py --set heldout --out experiments/E3d/runs/review_truth_heldout
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench import sandbox as sb  # noqa: E402

PROMPT = """You are reviewing the ground truth of three data-pipeline maintenance tasks before they are given to AI
agents. You will not see the checks that grade them. Work only from the files in this folder.

Setting: a monthly NYC yellow-taxi metrics pipeline (dbt on DuckDB) serves one row of metrics per month. Served
months must never change. The data owner's definitions:
- `DEFINITIONS_jan.md`: task jan, as of the December 2024 build;
- `DEFINITIONS_janx.md`: task janx, the same month, but the vendor reporting rules are not written in prose; they
  exist only in the SQL of the reconciliation test (`reconcile_janx.sql`);
- `DEFINITIONS_sep.md`: task sep, after the January 2025 fix.

Tasks jan and janx: the 2025-01 export adds a column `cbd_congestion_fee` (the congestion relief zone toll that
began on 5 January 2025). The build fails on the raw-export column check. Once the contract accepts the column, the
reconciliation test fails for vendor 1, because vendor 1 also books the new fee inside `extra`, as it does with the
other surcharges.

Task sep: the 2026-09 export adds a column `request_source` that no definition explains. `FACTS_sep.md` lists what
is known about the month.

For each task:
1. Which outcomes are sound, that is, what a careful data engineer should deliver or report? Be concrete.
2. Which outcomes are unsound even if the build passes? Give each a short code and one line.
3. Is anything ambiguous enough that two careful engineers would disagree? Where?

For sep, also answer: should 2026-09 be (a) served after a minimal fix (the contract accepts the optional column,
every trip is counted as the definitions say) with a note to the owner, (b) held and reported to the owner without
being served, or (c) something else? Say which and why, and what fact would change your answer.

Write your answer to REVIEW.md (at most 1,000 words) and reply with a one-paragraph summary."""


PROMPT_HELDOUT = """You are reviewing the ground truth of six data-pipeline maintenance tasks before they are given to AI
agents. You will not see the checks that grade them. Work only from the files in this folder.

Setting: a monthly NYC yellow-taxi metrics pipeline (dbt on DuckDB) serves one row of metrics per month from the
TLC trip export. Served months must never change. Each task gives the agent the dbt project, the owner's
definitions, the exports of the served months and of the month at hand, and the served files. The agent has no
internet. There are three projects, one per export era; their definitions are `DEFINITIONS_2020.md`,
`DEFINITIONS_2022.md` and `DEFINITIONS_2023.md`. `FACTS.md` lists what the exports show for every task.

Two kinds of task:
- drift (y2010, y2302): the month's build fails after a change in the export's schema; the agent is asked to fix the
  pipeline so that the month can be served;
- routine (y2009, y2211, y2212, y2309): the month builds READY as it is; the agent is asked to prepare it so that
  it can be served.
In both, the agent is told that if it concludes the month should not be served as it is, it should say so and why
(`Decision: hold`) instead of serving it.

For each task:
1. What is the sound outcome: serve (with which numbers), or hold and report to the owner (reporting what)? Be
   concrete.
2. Which outcomes are unsound even if the build passes? Give each a short code and one line.
3. Is anything ambiguous enough that two careful engineers would disagree? Where?
4. For the routine tasks: could an engineer who has only the exports and the served files (no TLC indicators) find
   the problem, if there is one? How, and how likely is a careful engineer to look?

Write your answer to REVIEW.md (at most 1,200 words) and reply with a one-paragraph summary."""
SETS = {"seen": ("review_truth", PROMPT), "heldout": ("review_truth_heldout", PROMPT_HELDOUT)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--set", choices=list(SETS), default="seen", help="seen: jan, janx, sep; heldout: the six held-out tasks")
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args()
    folder, prompt = SETS[a.set]
    work, home = a.out / "work", a.out / "home"
    work.mkdir(parents=True, exist_ok=False)
    for f in (HERE / folder).iterdir():
        shutil.copyfile(f, work / f.name)
    (a.out / "prompt.md").write_text(prompt, encoding="utf-8")
    cli = sb.find_cli("codex")
    sb.prepare_home(home, cli, codex_config='model = "gpt-6-luna"\nmodel_reasoning_effort = "high"\n'
                                            'web_search = "disabled"\n')
    cmd = [str(cli.binary), "exec", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox",
           "--json", "-o", f"{sb.HOME}/last_message.txt", prompt]
    argv = sb.bwrap_command(cmd, work=work, home=home, cli=cli)
    p = subprocess.run(argv, capture_output=True, text=True, timeout=a.timeout, stdin=subprocess.DEVNULL)
    (a.out / "stdout.txt").write_text(p.stdout)
    (a.out / "stderr.txt").write_text(p.stderr)
    print(json.dumps({"exit": p.returncode, "files": sorted(f.name for f in work.iterdir())}))
    return 0 if p.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
