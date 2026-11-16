"""Audit trail of every agent tool call: ``registry/audit.jsonl`` (append-only JSON lines).

Each line answers "who called what, with which inputs, on which build, and what came back":
ts, principal, role, task_id, build_id, tool, call_index, args_digest (over the full arguments),
redacted args, outcome (ok | error) with the error kind, a small result summary and the duration.

Arguments are redacted, not dropped: long strings keep a prefix plus their digest, answer rows and
pipeline file contents are reduced to counts and digests, and e-mail / phone patterns are scrubbed.
The digest still binds the line to the exact input, so an audit can prove what was submitted
without the log becoming a second copy of the data.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from ..common import append_jsonl, digest, read_jsonl, utcnow
from ..workspace import Workspace
from .context import TOOL_REGISTRY, ToolContext, jsonable, scrub_pii

AUDIT_LOG = "audit.jsonl"
_STR_KEEP = 300
_LIST_KEEP = 20
_SUMMARY_KEYS = ("tier", "verdict", "outcome", "n_rows", "truncated", "candidate_id", "change_id", "status",
                 "feature_digest", "build_id")


def audit_path(ws: Workspace) -> Path:
    return Path(ws.registry) / AUDIT_LOG


def read_audit(ws: Workspace) -> list[dict]:
    return read_jsonl(audit_path(ws))


def redact(value, key: str | None = None):
    """Shrink one argument for the log while keeping a digest of what was elided."""
    if key == "answer_rows" and isinstance(value, list):
        return {"n_rows": len(value), "sha256": digest(jsonable(value))}
    if key == "files" and isinstance(value, dict):
        return {str(p): {"chars": len(c) if isinstance(c, str) else None, "sha256": digest(jsonable(c))}
                for p, c in list(value.items())[:_LIST_KEEP]}
    if isinstance(value, str):
        if len(value) <= _STR_KEEP:
            return value
        return f"{value[:_STR_KEEP]}...[{len(value)} chars, sha256:{digest(value)[:16]}]"
    if isinstance(value, dict):
        return {str(k): redact(v) for k, v in list(value.items())[:_LIST_KEEP]}
    if isinstance(value, (list, tuple)):
        items = [redact(v) for v in list(value)[:_LIST_KEEP]]
        if len(value) > _LIST_KEEP:
            items.append(f"...[{len(value)} items, sha256:{digest(jsonable(list(value)))[:16]}]")
        return items
    return jsonable(value)


def _named_args(tool: str, args: tuple, kwargs: dict) -> dict:
    """Positional arguments by parameter name (ctx excluded), so logs read the same either way."""
    out = dict(kwargs)
    fn = TOOL_REGISTRY.get(tool)
    if fn is not None and args:
        params = [p for p in inspect.signature(inspect.unwrap(fn)).parameters][1:]
        for name, value in zip(params, args):
            out[name] = value
        for i, value in enumerate(args[len(params):]):
            out[f"_extra{i}"] = value
    elif args:
        out["_args"] = list(args)
    return out


def summarize_result(result: dict) -> dict:
    out = {k: result[k] for k in _SUMMARY_KEYS if k in result and not isinstance(result[k], (dict, list))}
    if isinstance(result.get("gate"), dict):
        out["gate"] = result["gate"].get("verdict")
    if isinstance(result.get("rows"), list):
        out["n_rows_returned"] = len(result["rows"])
    if isinstance(result.get("evidence_ids"), list):
        out["evidence_ids"] = result["evidence_ids"][:10]
    out["keys"] = sorted(result)[:30]
    return out


def audit_call(ctx: ToolContext, tool: str, args: tuple, kwargs: dict, result: dict, duration_s: float) -> None:
    named = _named_args(tool, args, kwargs)
    is_error = "error" in result and "kind" in result
    append_jsonl(audit_path(ctx.ws), scrub_pii({
        "ts": utcnow(), "principal": ctx.principal, "role": ctx.role, "task_id": ctx.task_id,
        "build_id": ctx.build_id, "tool": tool, "call_index": ctx.calls_used,
        "args_digest": digest(jsonable(named)), "args": {k: redact(v, k) for k, v in named.items()},
        "outcome": "error" if is_error else "ok", "error_kind": result.get("kind") if is_error else None,
        "error": str(result.get("error"))[:300] if is_error else None,
        "result_summary": summarize_result(result), "duration_ms": round(duration_s * 1000, 1),
    }))
