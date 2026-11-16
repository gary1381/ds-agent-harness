"""E3b v2: GPT-6 Luna's blind review of the post-census pipeline (typing, truth, labels, analysis, harm) before the
freeze: does the code compute what PREREGISTRATION.md says? Code and documents only, no data, no attempt.

    .venv/bin/python experiments/E3b/review_analysis_v2.py --out experiments/E3b/runs/review_analysis_v2
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench.luna import ask  # noqa: E402

FILES = {"PREREGISTRATION.md": ROOT / "docs/experiments/E3b/PREREGISTRATION.md",
         "keys_v2_header.yaml": HERE / "keys_v2.yaml", "LABEL_GUIDE_v2.md": HERE / "LABEL_GUIDE_v2.md",
         "type_numbers.py": HERE / "type_numbers.py", "truth_v2.py": HERE / "truth_v2.py",
         "label_v2.py": HERE / "label_v2.py", "luna_label_v2.py": HERE / "luna_label_v2.py",
         "analyze_v2.py": HERE / "analyze_v2.py", "harm_v2.py": HERE / "harm_v2.py",
         "replay_gate_v2.py": HERE / "replay_gate_v2.py", "test_e3b_analysis.py": ROOT / "tests/test_e3b_analysis.py"}
REVIEW = """You review the analysis code of a pre-registered experiment before it is frozen. Two AI agents (Claude
Code, Codex) answer 40 forecasting requests on a wholesaler's order data, k = 2 times each, in two arms: N alone,
H through a forecast gate. After the census: `type_numbers.py` types untyped numbers, `truth_v2.py` realises claims
on the sealed months and sets program errors, `label_v2.py` / `luna_label_v2.py` make outcome-blind sheets and
labels with adjudication, `analyze_v2.py` computes the metrics and decision rules, `harm_v2.py` compares arm H's
first and final answers, `replay_gate_v2.py` replays the gate on every final. `PREREGISTRATION.md` is the plan.

Check, concretely, quoting lines:
1. Does each metric (R1 and its split, R2, R3, P, C, T), the bootstrap, the paired difference and each decision
   rule in analyze_v2.py match the plan exactly (denominators, which replies count, ties, missing data)?
2. Does truth_v2.py set E-gross, typed E-overclaim, E-calc on past figures and coverage as the plan says? Can a
   reply escape a code it should get, or get one it should not?
3. Labelling: is anything that reveals agent, arm, gate or outcome on a sheet or in a labeller prompt? Is the
   adjudication blind? Is kappa computed as planned?
4. harm_v2.py: does it pick the right first answers, and is the comparison fair (same labellers, same steps)?
5. Bugs, edge cases the tests miss, anything that would make the reported result misleading.

Write REVIEW.md: numbered findings, each marked BLOCKER or MINOR with file and line, at most 900 words. Reply with a
one-paragraph summary."""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    files = {k: p.read_text() for k, p in FILES.items()}
    keys = files["keys_v2_header.yaml"]
    files["keys_v2_header.yaml"] = keys[:keys.index("\nkeys:")] if "\nkeys:" in keys else keys
    text, _ = ask(REVIEW, a.out, files, timeout_s=3600)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
