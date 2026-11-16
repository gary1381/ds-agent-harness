"""MCP server for E3b v2 arm H: the forecast gate as the only way a forecast reply is delivered (stdio, on host).

    python experiments/E3b/mcp_model.py --work <agent's work dir> --warehouses <dir of wh_<cut>.duckdb>
        --adir <attempt dir> [--asof 2025-09-01]

The agent writes `answer.md`, `claims.json` (docs/experiments/E3b/CLAIMS_SPEC.md), the files its claims name and
`predict.py`. `check_forecast` snapshots all of them as a numbered version, runs the agent's own `predict.py` in a
sandbox (dsharness.readout.predict_run) at the as-of date, at the development origin on the full and the cut
warehouse, and at the backtest origins, then calls `dsharness.readout.forecast_gate.check_forecast`. Every check is
logged with the sha256 of the answer, the claims, the named files and the code: the reply counts as delivered only
when the last check of exactly the final versions returned ALLOW. Runs are cached by code sha256, warehouse cut and
as-of date, so a check after an edit to the reply alone does not rerun predict.py.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import hashlib
import json
from pathlib import Path
import sys
import threading
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations
import pandas as pd
from pydantic import Field

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))
from dsharness.common import append_jsonl, utcnow  # noqa: E402
from dsharness.readout import forecast as D  # noqa: E402
from dsharness.readout import forecast_gate as G  # noqa: E402
from dsharness.readout import predict_run as P  # noqa: E402
import meaning_live as M  # noqa: E402
from run_v2 import catalog  # noqa: E402
import warehouse_v2 as W  # noqa: E402

SERVER = "dsh-model"
TOOL_ENV = (ROOT.parent / "agent_env_dt").resolve()
UV_PYTHON = Path.home() / ".local/share/uv/python"
FACTS = [21, 23, 5]              # the catalog's facts: 21 months of data (Dec 2023-Aug 2025), 23% guest lines,
                                 # five-digit catalogue skus (years and "N months" are not read as numbers)
WORKERS = 2                      # predict.py runs side by side (the attempt's two cores)
INSTRUCTIONS = ("Forecast replies are delivered only through the forecast gate. Model as usual; write your reply to "
                "answer.md, one typed claim per forecast or list it delivers to claims.json (CLAIMS.md), the files "
                "those claims name, and predict.py, which rebuilds all of them from the warehouse given to it "
                "(describe_task). Then call check_forecast: it must return ALLOW for the reply to count as "
                "delivered. On HOLD or REJECT, read the reasons, correct what is wrong and check again. Do not "
                "change a correct analysis just to pass; if the gate is wrong, say so in answer.md.")
CHECKS = {
    "f.schema": "claims.json parses and every claim has the fields of CLAIMS.md (FAIL)",
    "f.window": "forecast windows start on or after the as-of date and end within three months of it (FAIL)",
    "f.observed": "a claim about a past window equals the warehouse (FAIL)",
    "f.numbers": "every number in answer.md is a claim's value, interval end or k, or a realised past value "
                 "(INCONCLUSIVE)",
    "f.reproduce": "predict.py --asof <as-of> gives exactly the delivered claims.json and files (FAIL)",
    "f.timetravel": f"predict.py --asof {W.DEV_ORIGIN} gives the same output on the full warehouse and on one cut "
                    f"at {W.DEV_ORIGIN} (FAIL)",
    "f.range": "a stated range is narrower than predict.py's median error at the backtest origins (FAIL, needs 3 "
               "origins)",
    "f.backtest": "predict.py's past errors, range coverage and list metrics at each origin (facts only)",
    "f.meaning": "a model reads the reply's wording: an action's effect given as a result, something the data cannot "
                 "show, or an asked part left out; a program checks its quotes "
                 + ("(INCONCLUSIVE on the last two)" if M.MODE == "hold" else "(facts only)"),
}


def sha(b: bytes | str) -> str:
    return hashlib.sha256(b.encode() if isinstance(b, str) else b).hexdigest()


def months_ahead(asof: str, end: str) -> int:
    a, e = date.fromisoformat(asof), date.fromisoformat(end[:10])
    m = (e.year - a.year) * 12 + e.month - a.month
    return m + (1 if e.day > 1 else 0)


def origins_for(doc: dict, asof: str) -> list[str]:
    """Backtest origins (CLAIMS_SPEC.md): last year's same season, then the 2025 firsts of a month whose window,
    moved back, closes by the data end. The shortest forecast horizon among the claims sets the last origin; a
    longer claim's later origins fail realise() for lack of data and are skipped."""
    hs = [months_ahead(asof, w[1]) for c in doc.get("claims", []) if isinstance(c, dict)
          for w in G.windows(c) if isinstance(w, list) and len(w) == 2 and str(w[0])[:10] >= asof]
    if not hs:
        return []
    h = max(1, min(hs))
    end = pd.Period(W.DATA_END[:7], freq="M")
    return [o for o in W.ORIGINS if pd.Period(o[:7], freq="M") + h <= end]


class Gate:
    def __init__(self, work: Path, whs: dict[str, Path], adir: Path, asof: str, request: str = ""):
        self.work, self.whs, self.adir, self.asof, self.request = work, whs, adir, asof, request
        self.meaning: dict[str, tuple] = {}
        self.lines = W.lines(whs[W.DATA_END])
        self.cache: dict[tuple, dict] = {}
        self.lock = threading.Lock()
        self.n = len(list((adir / "versions").glob("v*"))) if (adir / "versions").exists() else 0

    def run(self, code: Path, csha: str, cut: str, asof: str) -> dict:
        key = (csha, cut, asof)
        with self.lock:
            if key in self.cache:
                return self.cache[key]
        d = self.adir / "runs" / csha[:12] / f"{asof}_on_{cut}"
        r = P.run_predict(code, self.whs[cut], asof, d, tool_env=TOOL_ENV, ro_paths=[UV_PYTHON])
        r["dir"] = str(d.relative_to(self.adir))
        with self.lock:
            self.cache[key] = r
        return r

    def snapshot(self, answer: str, raw: str, files: dict[str, bytes | None]) -> Path:
        self.n += 1
        v = self.adir / "versions" / f"v{self.n:03d}"
        (v / "files").mkdir(parents=True)
        (v / "answer.md").write_text(answer)
        (v / "claims.json").write_text(raw)
        for f, b in files.items():
            if b is not None:
                (v / "files" / f).parent.mkdir(parents=True, exist_ok=True)
                (v / "files" / f).write_bytes(b)
        P.copy_code(self.work, v / "code")
        return v

    def check(self) -> dict[str, Any]:
        ans, cl = self.work / "answer.md", self.work / "claims.json"
        if not ans.exists():
            return {"error": "write your reply to answer.md first", "kind": "missing"}
        answer = ans.read_text(errors="replace")
        raw = cl.read_text(errors="replace") if cl.exists() else ""
        try:
            doc = json.loads(raw) if raw else {}
        except ValueError as exc:
            return {"error": f"claims.json is not valid JSON: {exc}", "kind": "claims"}
        names = sorted({c["file"] for c in doc.get("claims", []) if isinstance(c, dict)
                        and isinstance(c.get("file"), str)}) if isinstance(doc, dict) else []
        files = {f: (self.work / f).read_bytes() if (self.work / f).is_file() else None for f in names}
        v = self.snapshot(answer, raw, files)
        code, csha = v / "code", P.code_sha(v / "code")
        files_sha = sha(b"".join(f.encode() + b"\0" + (b or b"<missing>") + b"\0" for f, b in files.items()))
        runs: dict[str, Any] = {}
        ex_m = ThreadPoolExecutor(1)
        asha = sha(answer)
        meaning = ex_m.submit(lambda: self.meaning.get(asha) or M.check(catalog(), self.request, answer,
                                                                         v / "meaning")) if self.request else None
        if not G.schema_errors(doc):
            with ThreadPoolExecutor(WORKERS) as ex:
                rep = ex.submit(self.run, code, csha, W.DATA_END, self.asof)
                full = ex.submit(self.run, code, csha, W.DATA_END, W.DEV_ORIGIN)
                cut = ex.submit(self.run, code, csha, W.DEV_ORIGIN, W.DEV_ORIGIN)
                rep, full, cut = rep.result(), full.result(), cut.result()
            runs["reproduce"] = rep
            runs["timetravel"] = (full, cut)
            bt = {}
            if not rep.get("error"):
                os_ = origins_for(doc, self.asof)
                with ThreadPoolExecutor(WORKERS) as ex:
                    futs = {o: ex.submit(self.run, code, csha, o, o) for o in os_}
                for o, f in futs.items():
                    r = f.result()
                    if r.get("error") or not isinstance(r.get("claims"), dict):
                        bt[o] = {"error": r.get("error") or "no claims"}
                        continue
                    out_dir = self.adir / r["dir"] / "out"
                    bt[o] = {"claims": r["claims"],
                             "realised": D.realise_file(r["claims"], self.lines, out_dir, W.DATA_END)}
            runs["backtest"] = bt
        extra = []
        if meaning is not None:
            self.meaning[asha] = meaning.result()
            extra.append(self.meaning[asha][0])
            (v / "meaning.json").write_text(json.dumps(self.meaning[asha][1], indent=1, default=str))
        ex_m.shutdown()
        res = G.check_forecast(answer, doc if isinstance(doc, dict) else {}, self.work, self.lines, self.asof,
                               extra=extra, facts=FACTS, **runs)
        res["version"] = v.name
        (v / "gate.json").write_text(json.dumps(res, indent=1, default=str))
        append_jsonl(self.adir / "checks.jsonl", {
            "ts": utcnow(), "version": v.name, "answer_sha256": sha(answer), "claims_sha256": sha(raw),
            "files_sha256": files_sha, "code_sha256": csha, "verdict": res["verdict"], "reasons": res["reasons"],
            "notes": res["notes"], "runs": {k: ([x.get("seconds") for x in val] if isinstance(val, tuple) else
                                                {o: "error" in x for o, x in val.items()} if k == "backtest" else
                                                val.get("seconds")) for k, val in runs.items()}})
        return {k: res[k] for k in ("verdict", "reasons", "notes", "version")}


def build_server(gate: Gate) -> MCPServer:
    server = MCPServer(name=SERVER, instructions=INSTRUCTIONS)

    def describe_task() -> dict[str, Any]:
        return {"asof": gate.asof, "warehouse_ends": "2025-08-31",
                "claims": "CLAIMS.md in your work directory (types, quantities, filters, the file format)",
                "predict_py": {
                    "command": "python predict.py --asof <YYYY-MM-DD> --warehouse <path> --out <empty directory>",
                    "must": "read only the warehouse given (read-only), use only lines before --asof, set every "
                            "date in its claims from --asof, and write claims.json plus the files its claims name "
                            "into --out; fixed seeds; at most 10 minutes on one core; no network",
                    "copied_in": f"only your source files ({', '.join(P.CODE_SUFFIXES)}; each at most 1 MB): no "
                                 "data, no saved model, no delivered output. Everything is rebuilt from the "
                                 "warehouse given"},
                "runs": {"reproduce": f"--asof {gate.asof} on your warehouse",
                         "timetravel": f"--asof {W.DEV_ORIGIN} on your warehouse and on one cut at {W.DEV_ORIGIN}",
                         "backtest": "--asof at 2024-09-01 and at the 2025 firsts of a month whose forecast window "
                                     "closes by 2025-08-31, each on a warehouse cut at that date"},
                "checks": CHECKS,
                "gate": "ALLOW when every check passes; REJECT on any FAIL; HOLD otherwise"}

    def realise_claim(
            claim: Annotated[dict, Field(description="One claim in the claims.json format, with a window that ends "
                                                     "by 2025-09-01 (a past window)")],
    ) -> dict[str, Any]:
        if not isinstance(claim, dict) or claim.get("type") == "verdict":
            return {"error": "give one total, share, list or per_buyer claim", "kind": "claim"}
        return D.realise(claim, gate.lines, gate.work, {claim.get("id"): claim}, W.DATA_END)

    def check_forecast() -> dict[str, Any]:
        return gate.check()

    ro = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
    server.add_tool(describe_task, name="describe_task",
                    description="The as-of date, the predict.py contract, the gate's runs and checks.",
                    annotations=ro)
    server.add_tool(realise_claim, name="realise_claim",
                    description="What a claim over a past window comes to on the warehouse, computed as the gate "
                                "computes it.",
                    annotations=ro)
    server.add_tool(check_forecast, name="check_forecast",
                    description="Run the forecast gate on answer.md, claims.json, the named files and predict.py as "
                                "they are now (it runs predict.py several times: minutes); returns ALLOW, HOLD or "
                                "REJECT with the reasons.",
                    annotations=ro)
    return server


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--warehouses", type=Path, required=True)
    p.add_argument("--adir", type=Path, required=True)
    p.add_argument("--asof", default=W.DATA_END)
    p.add_argument("--request", type=Path, help="the request text, for the meaning check")
    a = p.parse_args(argv)
    whs = {c: W.path(a.warehouses, c) for c in [W.DATA_END, W.DEV_ORIGIN, *W.ORIGINS]}
    missing = [str(x) for x in whs.values() if not x.exists()]
    if missing:
        raise SystemExit(f"missing warehouses: {missing}")
    build_server(Gate(a.work, whs, a.adir, a.asof, a.request.read_text() if a.request else "")).run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
