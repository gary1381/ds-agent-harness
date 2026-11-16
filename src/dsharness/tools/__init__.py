"""Governed agent tools: the only surface through which agents touch data, models and pipelines.

Every tool takes a controller-built `ToolContext` (one READY build, one role, one principal) and
returns a JSON-serialisable dict; failures come back as ``{"error", "kind"}``. Role isolation,
budgets, PII scrubbing and the audit log live in `tools.context.governed_tool`. Evidence records
are written here and nowhere else in the library.
"""
from . import analytics, de, ds
from .analytics import (describe_metric, describe_table, list_metrics, query_metric, run_sql, search_catalog,
                        submit_answer)
from .audit import read_audit
from .context import (ACTION_TOOLS, ROLE_TOOLS, ROLES, TOOL_REGISTRY, ToolContext, ToolError, error, make_context)
from .de import pipeline_status, propose_pipeline_change, read_pipeline_file
from .ds import (candidate_subject, feature_subject, list_features, propose_feature, request_final_evaluation,
                 request_release, run_experiment_readout, train_candidate)

_missing = sorted({t for tools in ROLE_TOOLS.values() for t in tools} - set(TOOL_REGISTRY))
if _missing:
    raise ImportError(f"ROLE_TOOLS names tools that are not implemented: {_missing}")


def call_tool(ctx: ToolContext, name: str, arguments: dict | None = None) -> dict:
    """Dispatch by name (MCP server, bench runner). Unknown names are an error result, not an exception."""
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return error("unknown_tool", f"no tool named {name!r}; this role has {ROLE_TOOLS.get(ctx.role)}")
    if arguments is not None and not isinstance(arguments, dict):
        return error("invalid_argument", "arguments must be an object")
    return fn(ctx, **(arguments or {}))


__all__ = [
    "ACTION_TOOLS", "ROLE_TOOLS", "ROLES", "TOOL_REGISTRY", "ToolContext", "ToolError", "call_tool", "make_context",
    "read_audit", "analytics", "de", "ds",
    "list_metrics", "describe_metric", "query_metric", "search_catalog", "describe_table", "run_sql", "submit_answer",
    "list_features", "propose_feature", "train_candidate", "request_final_evaluation", "request_release",
    "pipeline_status", "read_pipeline_file", "propose_pipeline_change",
    "candidate_subject", "feature_subject", "run_experiment_readout",
]
