"""E3b v2: GPT-6 Luna's blind review of the built evaluation before the pre-registration is frozen.

Luna sees the requests, the agents' catalog and CLAIMS.md, the answer key, the labelling guide, the truth script,
the gate (checks and server) and the runner's prompts: code and documents only, no data, no attempt.

    .venv/bin/python experiments/E3b/review_v2.py --out experiments/E3b/runs/review_v2
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))
from dsharness.bench.luna import ask  # noqa: E402
from run_v2 import HARNESS, PROMPT, catalog, claims_md  # noqa: E402

FILES = {"bank_v2.yaml": HERE / "bank_v2.yaml", "keys_v2.yaml": HERE / "keys_v2.yaml",
         "LABEL_GUIDE_v2.md": HERE / "LABEL_GUIDE_v2.md", "truth_v2.py": HERE / "truth_v2.py",
         "type_numbers.py": HERE / "type_numbers.py", "forecast_gate.py": ROOT / "src/dsharness/readout/forecast_gate.py",
         "mcp_model.py": HERE / "mcp_model.py", "predict_run.py": ROOT / "src/dsharness/readout/predict_run.py",
         "DESIGN.md": ROOT / "docs/experiments/E3b/DESIGN.md"}
REVIEW = """You review an evaluation of AI data scientists before its plan is frozen. Agents (Claude Code and Codex) get a
UK gift wholesaler's order lines up to 2025-08-31 (`DATA_CATALOG.md`), one request from `bank_v2.yaml`, and must write
`answer.md`, typed claims (`CLAIMS.md`) and `predict.py`. Arm N works alone; arm H delivers only through a forecast
gate (`forecast_gate.py`, served by `mcp_model.py`, running `predict.py` sandboxed by `predict_run.py`). The prompts
are in `PROMPTS.md`. After the census, `type_numbers.py` types numbers left without a claim, `truth_v2.py` realises
every claim on the sealed months (Sep-Nov 2025) and sets program errors, and labellers judge meaning with
`LABEL_GUIDE_v2.md` and the key `keys_v2.yaml`. `DESIGN.md` (its v2 sections) gives the plan and your earlier review.

Check, concretely, quoting lines:
1. The key, request by request: is the supported reply right and reachable from this data? Is anything stricter
   than a competent analyst could meet, or too lenient? Is a likely error missing? Are the estimands executable and
   do they match the request?
2. The program rules (`program` in the key, and their code in truth_v2.py): does the code do what the key says? Are
   the gross-error, baseline, list and coverage rules sound and hard to game (e.g. by stating no range, no claim, or
   a huge range)?
3. Fairness between arms: can arm N avoid errors by typing less, or arm H be pushed into errors by the gate's
   reasons? Does the post-hoc typing close the gap?
4. The gate: anything that steers agents to a wrong answer, holds correct replies, or can be passed without doing
   the work? Is the sandbox for predict.py sound?
5. Anything else that would make the result misleading.

Write REVIEW.md: first a table `id | verdict (agree / change / unsure) | why`, one row per request (42 rows); then
numbered findings on points 2-5, each marked BLOCKER or MINOR, at most 900 words. Reply with a one-paragraph
summary."""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    files = {k: p.read_text() for k, p in FILES.items()}
    files.update({"DATA_CATALOG.md": catalog(), "CLAIMS.md": claims_md(),
                  "PROMPTS.md": "# Prompt (both arms; {harness} is empty in arm N)\n\n" + PROMPT
                                + "\n\n# {harness} in arm H\n" + HARNESS + "\n"})
    text, _ = ask(REVIEW, a.out, files, timeout_s=3600)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
