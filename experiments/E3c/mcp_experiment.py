"""MCP server for E3c v2 arm H: the experiment readout gate as the only way a readout is delivered (stdio, on host).

    python experiments/E3c/mcp_experiment.py --design <design.yaml> --work <agent's work dir> --request <request.txt>
        --log <checks.jsonl>

The agent analyses the test with its usual tools and writes `answer.md` and `claims.json` in its work directory.
`check_readout` reads both files as they are on disk, runs `dsharness.readout.experiment` and logs the verdict with
the answer's sha256: the readout counts as delivered only when the last check of the delivered `answer.md` was ALLOW.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations
from pydantic import Field

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.common import append_jsonl, utcnow  # noqa: E402
from dsharness.readout import experiment as X  # noqa: E402

SERVER = "dsh-experiment"
INSTRUCTIONS = ("Readouts of this test are delivered only through the readout gate. Analyse as usual; write your "
                "reply to answer.md and one structured claim per number it states to claims.json (schema: "
                "describe_experiment), then call check_readout. It must return ALLOW for the reply to count as "
                "delivered. On HOLD or REJECT, read the reasons: correct the reply or the claims and check again. "
                "Do not change a correct analysis just to pass; if the gate is wrong, say so in answer.md.")
CLAIM_SCHEMA = {
    "id": "c1",
    "outcome": "one of the outcome columns",
    "estimand": "mean_difference (a - b per customer assigned) | relative_lift (mean a / mean b - 1) | group_mean (a) "
                "| anyway_share (mean b / mean a) | effect_gap (the a - b effect in population minus in population2) "
                "| median_difference | other (not recomputed: role context or caveat only)",
    "groups": "[a, b] (or [a] for group_mean); group keys, or 'any' for both e-mails together",
    "population": "[] for everyone assigned, or conditions like [{\"column\": \"zip_code\", \"op\": \"=\", "
                  "\"value\": \"Rural\"}]; ops = != < <= > >= in between",
    "population2": "effect_gap only",
    "estimate": "the number as your reply states it (a proportion for rates: 0.0766, not 7.66)",
    "low": "95% interval low, or null", "high": "95% interval high, or null",
    "role": "result | exploratory (needs 'searched') | projection (needs 'assumption') | context (a descriptive "
            "number) | caveat (a number given to say what is not the answer, e.g. a per-buyer difference)",
    "direction": "what your reply says about the comparison: higher | lower (the data show a difference in that "
                 "direction) | unclear (a point estimate the interval does not separate from no difference) | none "
                 "(no difference) | null (no direction stated)",
    "searched": "exploratory: how many segments you compared", "scale": "a projection to N customers: the multiplier",
    "horizon_days": "only if the claim is about a period other than the test's window",
    "assumption": "projection: what it assumes",
}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def build_server(design: X.Design, work: Path, request: str, log: Path) -> MCPServer:
    server = MCPServer(name=SERVER, instructions=INSTRUCTIONS)

    def describe_experiment() -> dict[str, Any]:
        return {"table": design.table, "assignment": design.assignment, "groups": design.groups,
                "control": design.control, "outcomes": list(design.outcomes),
                "pre_assignment_columns": list(design.pre_treatment), "outcome_window_days": design.window_days,
                "analysis_plan": {"primary": "each e-mail vs no e-mail, per customer assigned, on every outcome; "
                                             "men's vs women's directly",
                                  "prespecified_modifiers": design.prespecified},
                "claim_schema": CLAIM_SCHEMA,
                "checks": {k: v for k, v in CHECKS.items()},
                "gate": "ALLOW when every check passes; REJECT on a contradicted claim; HOLD otherwise"}

    def estimate_effect(
            outcome: Annotated[str, Field(description="An outcome column")],
            groups: Annotated[list[str], Field(description="[a, b], or [a] for group_mean")],
            estimand: Annotated[str, Field(description="mean_difference, relative_lift, group_mean, anyway_share, "
                                                       "effect_gap or median_difference")] = "mean_difference",
            population: Annotated[list[dict] | None, Field(description="Conditions on columns; none = everyone "
                                                                       "assigned")] = None,
            population2: Annotated[list[dict] | None, Field(description="effect_gap only")] = None,
    ) -> dict[str, Any]:
        con = X.connect(design)
        try:
            r = X.recompute(con, design, {"outcome": outcome, "groups": groups, "estimand": estimand,
                                          "population": population or [], "population2": population2})
        except X.ClaimError as exc:
            return {"error": str(exc), "kind": "claim"}
        finally:
            con.close()
        w = X.where(design, population)[2] | X.where(design, population2)[2]
        out = {"estimand": estimand, "outcome": outcome, "groups": groups,
               **{k: v for k, v in (r or {}).items() if not k.endswith("_linear")}}
        if w & set(design.outcomes) and estimand in X.COMPARISONS:
            out["warning"] = ("this population is selected by an outcome: the comparison is not the effect of "
                              "assignment (role caveat at most)")
        return out

    def check_readout() -> dict[str, Any]:
        ans, cl = work / "answer.md", work / "claims.json"
        if not ans.exists():
            return {"error": "write your reply to answer.md first", "kind": "missing"}
        answer = ans.read_text(errors="replace")
        raw = cl.read_text(errors="replace") if cl.exists() else ""
        try:
            claims = json.loads(raw) if raw else []
        except ValueError as exc:
            return {"error": f"claims.json is not valid JSON: {exc}", "kind": "claims"}
        out = X.check_readout(design, answer, claims, request)
        append_jsonl(log, {"ts": utcnow(), "answer_sha256": sha(answer), "claims_sha256": sha(raw),
                           "n_claims": len(claims) if isinstance(claims, list) else None, "gate": out["gate"],
                           "reasons": out["reasons"]})
        return out

    ro = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
    server.add_tool(describe_experiment, name="describe_experiment",
                    description="The test's design, the owner's analysis plan, the claim schema and the gate's checks.",
                    annotations=ro)
    server.add_tool(estimate_effect, name="estimate_effect",
                    description="An effect from the rows, as the gate computes it: estimate and 95% interval.",
                    annotations=ro)
    server.add_tool(check_readout, name="check_readout",
                    description="Run the readout gate on answer.md and claims.json as they are now; returns ALLOW, "
                                "HOLD or REJECT with the reasons and each claim recomputed.",
                    annotations=ro)
    return server


CHECKS = {
    "exp.data": "the owner's data checks on the table (Great Expectations suite, sample ratio, balance)",
    "exp.claims": "every claim names known columns, groups, an estimand and a role",
    "exp.numbers_covered": "every number in answer.md is a claim, a number of the request or a design fact",
    "exp.recompute": "each claim's estimate (0.5%) and interval ends (10% of the width) match the rows",
    "exp.estimand": "no comparison among customers selected by an outcome (nor group means there given as results), "
                    "unless role caveat",
    "exp.contrast": "'higher'/'lower' agree with the estimate's sign (else REJECT) and the interval excludes zero; "
                    "'none' only where it includes zero (else HOLD)",
    "exp.prespecified": "a subgroup result is in the analysis plan, or marked exploratory with 'searched'",
    "exp.horizon": "nothing beyond the outcome window as a result; projections state their assumption",
    "exp.pooled": "no pooled 'any e-mail' result alone where the two e-mails differ on that outcome (overall or in "
                  "the claim's population)",
}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--design", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--request", type=Path, required=True)
    p.add_argument("--log", type=Path, required=True)
    a = p.parse_args(argv)
    build_server(X.Design.load(a.design), a.work, a.request.read_text(), a.log).run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
