"""E1 runner: real agents x arms x questions x k, resumable.

    .venv/bin/python experiments/E1/run_census.py --run main            # the pre-registered run
    .venv/bin/python experiments/E1/run_census.py --run smoke --smoke   # mechanics check, not an eval item
    .venv/bin/python experiments/E1/run_census.py --run main --summary  # metrics so far
    # E1b: N, H (this checkout, v0.2) and H1 (the frozen v0.1 snapshot) on the new bank
    .venv/bin/python experiments/E1/run_census.py --runs-root experiments/E1b/runs --run main \
        --questions experiments/E1b/questions/bank.yaml --arms N H H1 --h1-src experiments/E1b/harness_v01/src
    # E3a: another data world (harness v0.3: census.World), defined by a module with WORLD and WS
    .venv/bin/python experiments/E1/run_census.py --runs-root experiments/E3a/runs --run main \
        --questions experiments/E3a/questions/bank.yaml --world experiments/E3a/world.py

Pin to two cores: taskset -c 0,1 (children inherit it).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench import census as C  # noqa: E402
from dsharness.common import read_jsonl  # noqa: E402
from dsharness.workspace import Workspace  # noqa: E402

SMOKE = [C.Question("SMOKE-ctx", "S", "designer", "en", "What was net revenue by region in each month of Q4 2025?",
                    ("month", "region"), "fen", ("2025-10", "2025-12"),
                    truth_sql="SELECT month, region, net_revenue_fen AS value FROM truth_revenue "
                              "WHERE month BETWEEN '2025-10' AND '2025-12'")]


def load_world(path: Path):
    spec = importlib.util.spec_from_file_location("census_world", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.WS, mod.WORLD


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--questions", type=Path, default=ROOT / "experiments/E1/questions/bank.yaml")
    ap.add_argument("--agents", nargs="+", default=list(C.AGENTS))
    ap.add_argument("--arms", nargs="+", default=list(C.ARMS))
    ap.add_argument("--h1-src", type=Path, default=None, help="source tree of the frozen harness for arm H1")
    ap.add_argument("--world", type=Path, default=None,
                    help="module defining WORLD and WS (e.g. experiments/E3a/world.py); default: the synthetic world")
    ap.add_argument("--runs-root", type=Path, default=ROOT / "experiments/E1/runs")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only", nargs="+", default=None, help="question ids to run")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()
    if a.smoke and a.world:
        ap.error("--smoke is the synthetic world's mechanics check; use --only with --world")
    run_dir = a.runs_root / a.run
    if a.summary:
        recs = read_jsonl(run_dir / "attempts.jsonl")
        print(json.dumps(C.summarize(recs), indent=1, default=str))
        return 0
    qs = SMOKE if a.smoke else C.load_questions(a.questions)
    if a.only:
        qs = [q for q in qs if q.qid in set(a.only)]
    ws, kw = Workspace((ROOT / "workspace").resolve()), {}
    if a.world:
        ws, kw["world"] = load_world(a.world)
    census = C.Census(ws, run_dir, qs, a.agents, a.arms, 1 if a.smoke else a.k, workers=a.workers,
                      log=lambda m: print(m, flush=True), h1_src=a.h1_src, **kw)
    census.prepare()
    census.run(limit=a.limit)
    recs = read_jsonl(run_dir / "attempts.jsonl")
    print(json.dumps(C.summarize(recs), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
