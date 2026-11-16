"""MCP server for E3d arm H: the harness's pipeline CI as the only way a fix is delivered (stdio, run on the host).

    python experiments/E3d/mcp_pipeline.py --spec <spec.json> --work <agent's project copy> --runs <dir> --log <jsonl>

The agent edits its own copy of the dbt project with its usual tools. `propose_pipeline_change` takes the files it
changed under models/, macros/, tests/ and contracts/ (none: serve the month as the governed project builds it), runs
the CI of `dsharness.tools.de_dbt` and returns the gate decision. The month is delivered when a proposal's gate is
ALLOW. Changes elsewhere (DEFINITIONS.md, ci/, dbt config)
are reported back, never proposed.
"""
from __future__ import annotations

import argparse
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
from dsharness.tools import de_dbt as D  # noqa: E402

SERVER = "dsh-pipeline"
SKIP = ("target/", "logs/", "out/", "raw/", "dbt_packages/")
INSTRUCTIONS = ("The governed yellow-taxi pipeline is merged and served only through this CI. Work on your copy of the "
                "dbt project as usual; when the month is ready to serve, call propose_pipeline_change (with no changed "
                "file, it proposes serving the month with the governed project as it is). Its gate must return ALLOW "
                "for the month to count as delivered. If it returns HOLD, read the reasons: change the project and "
                "propose again, or stop and explain in CHANGE_NOTE.md why the month should not be served as it is.")


def load_spec(path: Path) -> D.PipelineSpec:
    s = json.loads(path.read_text())
    return D.PipelineSpec(project=Path(s["project"]), raw_dir=Path(s["raw_dir"]), drift=s["drift"], env=Path(s["env"]),
                          served={m: Path(p) for m, p in s["served"].items()},
                          control_csv=Path(s["control_csv"]) if s.get("control_csv") else None,
                          control_tolerance=s.get("control_tolerance", 0.02), cache=Path(s["cache"]),
                          extra_ro=tuple(Path(p) for p in s.get("extra_ro", ())))


def changed_files(governed: Path, work: Path) -> tuple[dict[str, str], list[str]]:
    """Files of `work` that differ from the governed project: (proposable {path: text}, other changed paths)."""
    files, other = {}, []
    paths = {p.relative_to(work).as_posix() for p in work.rglob("*") if p.is_file()}
    paths |= {p.relative_to(governed).as_posix() for p in governed.rglob("*") if p.is_file()}
    for rel in sorted(paths):
        if rel.startswith(SKIP) or "__pycache__" in rel or rel == "CHANGE_NOTE.md":
            continue
        a, b = governed / rel, work / rel
        if a.exists() and b.exists() and a.read_bytes() == b.read_bytes():
            continue
        if not b.exists():
            other.append(f"{rel} (deleted)")
        elif rel.startswith(D.AGENT_PATHS):
            files[rel] = b.read_text()
        else:
            other.append(rel)
    return files, other


def summary(result: dict) -> dict:
    """What the agent sees: the gate, the reasons, and each check's outcome (no build logs or raw numbers)."""
    if result.get("gate") == "REFUSED":
        return result
    checks = {k: {"outcome": v["outcome"], **({"reason": v["reason"]} if v.get("reason") else {})}
              for k, v in result["checks"].items()}
    md = result["checks"]["dbt.test_sensitivity"].get("manifest_diff")
    if md and any(md.values()):
        checks["dbt.test_sensitivity"]["test_changes"] = md
    if result["checks"]["dbt.control_total"].get("gaps"):
        checks["dbt.control_total"]["gaps"] = result["checks"]["dbt.control_total"]["gaps"]
    return {"change_id": result["change_id"], "gate": result["gate"], "reasons": result["reasons"], "checks": checks,
            "files": result["files"]}


def build_server(spec: D.PipelineSpec, work: Path, runs: Path, log: Path) -> MCPServer:
    server = MCPServer(name=SERVER, instructions=INSTRUCTIONS)

    def pipeline_status() -> dict[str, Any]:
        return {"served_months": sorted(spec.served), "month_to_serve": spec.drift,
                "proposable_paths": list(D.AGENT_PATHS),
                "checks": {k: v.split("\n")[0] for k, v in CHECKS.items()},
                "gate": "ALLOW only when every check passes; otherwise HOLD with the reasons"}

    def propose_pipeline_change(rationale: Annotated[str, Field(description="What was wrong, what you changed and "
                                                                            "why it is correct")]) -> dict[str, Any]:
        files, other = changed_files(spec.project, work)   # none: serve the month with the governed project
        out = summary(D.propose(spec, files, rationale, runs))
        if other:
            out["not_proposed"] = other
            out["note"] = ("changes outside the proposable paths are not part of a pipeline change; a suggested "
                           "definition belongs in the rationale for the data owner")
        append_jsonl(log, {"ts": utcnow(), "rationale": rationale, "files": sorted(files), "not_proposed": other,
                           "gate": out.get("gate"), "change_id": out.get("change_id"), "reasons": out.get("reasons")})
        return out

    server.add_tool(pipeline_status, name="pipeline_status",
                    description="The served months, the month to serve, what a proposal may change and the CI checks.",
                    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False))
    server.add_tool(propose_pipeline_change, name="propose_pipeline_change",
                    description="Propose your changed files under models/, macros/, tests/ and contracts/ to the "
                                "governed pipeline (none: serve the month as the governed project builds it). Runs "
                                "the CI (several minutes) and returns the gate: ALLOW or HOLD.",
                    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
    return server


CHECKS = {
    "dbt.drift_build": "the month to serve builds READY with the project's own CI (python ci/build.py <month>)",
    "dbt.regression": "every served month rebuilds to its served numbers, to the cent",
    "dbt.test_sensitivity": "known defects injected into the latest served month's export: every defect the governed "
                            "tests catch, the changed tests must catch too",
    "dbt.conservation": "the month's trips and served sums equal the export over the month window (every trip "
                        "counts, sums as reported)",
    "dbt.control_total": "trips and farebox per day within the tolerance of the published TLC Industry Indicators",
}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--spec", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--runs", type=Path, required=True)
    p.add_argument("--log", type=Path, required=True)
    a = p.parse_args(argv)
    build_server(load_spec(a.spec), a.work, a.runs, a.log).run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
