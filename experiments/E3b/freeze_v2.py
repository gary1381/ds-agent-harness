"""E3b v2: print the frozen-inputs table for PREREGISTRATION.md (the rows run_census_v2.sh checks before it starts).

Run once, after meaning_live.py's DECIDER and MODE are set from E3c's replay; paste the output under "Frozen inputs".
The served CLAIMS.md and catalog are built by run_v2.py from frozen files; their digests are printed as a check.

    .venv/bin/python experiments/E3b/freeze_v2.py
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "E3b"))

FILES = [
    "experiments/E3b/bank_v2.yaml", "experiments/E3b/keys_v2.yaml", "experiments/E3b/LABEL_GUIDE_v2.md",
    "docs/experiments/E3b/CLAIMS_SPEC.md", "experiments/E3b/run_v2.py", "experiments/E3b/mcp_model.py",
    "experiments/E3b/meaning_live.py", "experiments/E3b/warehouse_v2.py", "experiments/E3b/truth_v2.py",
    "experiments/E3b/type_numbers.py", "experiments/E3b/realise_luna.py", "experiments/E3b/compare_realise.py",
    "experiments/E3b/replay_gate_v2.py", "experiments/E3b/label_v2.py", "experiments/E3b/luna_label_v2.py",
    "experiments/E3b/analyze_v2.py", "experiments/E3b/harm_v2.py", "src/dsharness/readout/forecast.py",
    "src/dsharness/readout/forecast_gate.py", "src/dsharness/readout/predict_run.py",
    "experiments/E3b/review_analysis_v2.py", "experiments/E3b/agent_env_v2.lock",
    "workspace/external/e3a/truth_lines.parquet",         # every warehouse cut is built from it (warehouse_v2.py)
]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> int:
    import meaning_live as ML
    import run_v2 as R
    print(f"meaning check: decider {ML.DECIDER}, mode {ML.MODE}\n")
    print("| file | sha256 |\n|---|---|")
    for f in FILES:
        print(f"| `{f}` | `{sha((ROOT / f).read_bytes())}` |")
    print(f"\nServed CLAIMS.md sha256 `{sha(R.claims_md().encode())}`; catalog sha256 `{sha(R.catalog().encode())}`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
