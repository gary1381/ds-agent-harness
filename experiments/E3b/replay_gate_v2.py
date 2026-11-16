"""E3b v2: the forecast gate replayed offline on every final reply of both arms (no agent sees it).

After the census, before any sealed month is read: for each attempt's final answer.md, claims.json, named files and
predict.py, run exactly the gate arm H had (mcp_model.Gate, the same warehouses, predict.py runs and meaning check),
into <run>/replay/<agent>_<task>-<arm>-r<rep>/. For arm N this is the gate's detection rate without feedback
(PREREGISTRATION.md, "Also reported"); for arm H it confirms the final verdict. Uses only lines before 2025-09-01.

    systemd-run --user --property=CPUAffinity=4,5 ... .venv/bin/python experiments/E3b/replay_gate_v2.py --run experiments/E3b/runs/v2
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mcp_model as MM  # noqa: E402
from run_v2 import REQUESTS  # noqa: E402
import warehouse_v2 as W  # noqa: E402


GATE_CODE = [HERE / "mcp_model.py", HERE / "meaning_live.py", HERE.parents[1] / "src/dsharness/readout/forecast_gate.py",
             HERE.parents[1] / "src/dsharness/readout/forecast.py", HERE.parents[1] / "src/dsharness/readout/predict_run.py"]


def inputs_sha(work: Path) -> str:
    """The final reply (every file in work/) and the gate's code: a cached result is reused only if both match."""
    h = hashlib.sha256()
    for p in sorted(work.rglob("*")) + GATE_CODE:
        if p.is_file():
            h.update(str(p.relative_to(work) if p.is_relative_to(work) else p.name).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", type=Path, required=True)
    a = ap.parse_args()
    whs = {c: W.path(a.run / "warehouses", c) for c in [W.DATA_END, W.DEV_ORIGIN, *W.ORIGINS]}
    latest = {}
    for line in (a.run / "attempts.jsonl").read_text().splitlines():
        r = json.loads(line)
        latest[(r["agent"], r["task"], r["arm"], r["rep"])] = r
    out = a.run / "replay"
    out.mkdir(exist_ok=True)
    rows = []
    for (agent, task, arm, rep) in sorted(latest):
        name = f"{agent}_{task}-{arm}-r{rep}"
        work = a.run / "attempts" / agent / f"{task}-{arm}-r{rep}" / "work"
        res_f = out / name / "result.json"
        if not (work / "answer.md").exists():
            continue
        sha = inputs_sha(work)
        if res_f.exists() and json.loads(res_f.read_text()).get("inputs_sha256") == sha:
            rows.append(json.loads(res_f.read_text()))
            continue
        adir = out / name
        if adir.exists():
            shutil.rmtree(adir)
        adir.mkdir(parents=True)
        res = MM.Gate(work, whs, adir, W.DATA_END, REQUESTS[task]["ask"]).check()
        row = {"agent": agent, "task": task, "arm": arm, "rep": rep, "verdict": res.get("verdict"),
               "reasons": res.get("reasons"), "notes": res.get("notes"), "error": res.get("error"),
               "inputs_sha256": sha}
        res_f.write_text(json.dumps(row, indent=1))
        rows.append(row)
        print(f"{name}: {row['verdict'] or row['error']}", flush=True)
    (out / "summary.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
