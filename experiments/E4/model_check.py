"""Draft E4 model on the E3a warehouse: do O1/O2 reproduce the frozen E3a truths of the questions they target?

A sanity check of the onboarding change, not an evaluation (the questions are the ones the change came from).
Needs the H v0.5 source:  DSH_SRC=<tree>/src .venv/bin/python experiments/E4/model_check.py <new empty dir>"""
import json, os, shutil, sys
from pathlib import Path

LIVE = Path(__file__).resolve().parents[2]
sys.path.insert(0, os.environ.get("DSH_SRC") or str(LIVE / "src"))
from dsharness import tools as T                      # noqa: E402
from dsharness.workspace import Workspace             # noqa: E402
base = Workspace(LIVE / "workspace/external/e3a/ws")
root = Path(sys.argv[1])
root.mkdir(parents=True)
for d in ("lake", "sources"):
    (root / d).symlink_to(getattr(base, d), target_is_directory=True)
(root / "custody").mkdir()
shutil.copytree(base.root / "context", root / "context")           # catalog, pipeline: as E3a
shutil.copy(LIVE / "experiments/E4/context/semantic_model.yaml", root / "context/semantic_model.yaml")
ws = Workspace(root).ensure()
sys.path.insert(0, str(LIVE / "experiments/E3a"))
from world import CUTOFF
ctx = T.make_context(ws, CUTOFF, "analyst", "agent:e4-model-check")
truth = json.loads((LIVE / "experiments/E3a/runs/main/truth.json").read_text())
bank = {q["qid"]: q for q in __import__("yaml").safe_load((LIVE / "experiments/E3a/questions/bank.yaml").read_text())}
call = lambda m, dims, months: {"metric": m, "dimensions": dims, "months": months, "filters": None}
cases = [
    ("R-H29", call("gross_product_sales", ["sku"], ["2025-01", "2025-11"]),
     {"months": "sum", "across": {"op": "top", "dimension": "sku", "n": 5}},
     lambda t: [{"sku": k.upper(), "value": v} for k, v in t.items()]),
    ("R-H10", call("gross_product_sales", ["market", "customer_type"], ["2025-06", "2025-08"]),
     {"months": "sum", "across": {"op": "share", "dimension": "customer_type"}, "keep": {"customer_type": "guest"}},
     lambda t: [{"market": {"uk": "UK", "non_uk": "non-UK"}[k], "customer_type": "guest", "value": v} for k, v in t.items()]),
    ("R-H34", call("gross_product_sales", ["customer_type"], ["2025-01", "2025-11"]),
     {"months": "sum", "across": {"op": "share", "dimension": "customer_type"}, "keep": {"customer_type": "guest"}},
     lambda t: [{"customer_type": "guest", "value": t[""] if "" in t else list(t.values())[0]}]),
]
for qid, c, d, rows in cases:
    t = truth[qid]
    r = T.submit_answer(ctx, (bank[qid].get("text") or bank[qid].get("question")), rows(t), metric_call=c, derive=d)
    if "error" in r:
        print(qid, "ERROR", r["error"]); continue
    print(qid, r["tier"], r["gate"]["verdict"], "|", "; ".join(r["reasons"])[:300], "|", r.get("warnings"))
ctx.close()
