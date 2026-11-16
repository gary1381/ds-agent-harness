"""Tool context, role isolation and the single choke point every agent-facing tool goes through.

Trust boundary: an agent never builds its own context. The controller (MCP server, bench runner,
demo) calls `make_context`, which pins ONE READY build, the agent's role and principal, a row cap
and a call budget. `governed_tool` wraps every tool function and, in this order:

  1. counts the call against the budget (exhaustion is an explicit error, never a silent stop),
  2. enforces role isolation (`ROLE_TOOLS` is the only allowlist; an analyst cannot train),
  3. contains failures: the agent receives ``{"error": ..., "kind": ...}``, never a traceback,
  4. makes the result JSON-safe and scrubs e-mail addresses / mobile numbers as a last line of
     defence (PII is already blocked upstream by the SQL analyzer and the catalog),
  5. appends one audit line to ``registry/audit.jsonl``.

Calls on one context are serialised by a lock: the MCP SDK runs sync tools in worker threads,
and the cached DuckDB connection must not be used concurrently.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal
import functools
import math
import re
import threading
import time
from uuid import UUID

import duckdb
import numpy as np
import pandas as pd

from ..lake.build import latest_ready_build, load_report, warehouse_path
from ..ml.features import readonly_connection
from ..workspace import CUTOFFS, Workspace

ROLES = ("analyst", "data_scientist", "data_engineer")

# Read-only context tools are shared (a data scientist or engineer needs the catalog too); every
# tool that produces evidence or changes state belongs to exactly one role.
_CONTEXT_TOOLS = ["list_metrics", "describe_metric", "query_metric", "search_catalog", "describe_table", "run_sql"]
ROLE_TOOLS: dict[str, list[str]] = {
    "analyst": [*_CONTEXT_TOOLS, "submit_answer"],
    "data_scientist": ["list_metrics", "query_metric", "search_catalog", "describe_table", "run_sql",
                       "list_features", "propose_feature", "train_candidate", "request_final_evaluation",
                       "request_release"],
    "data_engineer": ["search_catalog", "describe_table", "run_sql",
                      "pipeline_status", "read_pipeline_file", "propose_pipeline_change"],
}
ACTION_TOOLS = {"submit_answer", "list_features", "propose_feature", "train_candidate", "request_final_evaluation",
                "request_release", "pipeline_status", "read_pipeline_file", "propose_pipeline_change"}

MAX_ROWS_LIMIT = 10_000
_PRINCIPAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")
# Namespaces of evidence producers (see policies/gates.yaml). An agent principal inside one of them
# could have its own records counted by the gate as if a verifier had issued them.
RESERVED_PRINCIPAL_PREFIXES = ("verifier:", "evaluator:", "ci:", "experiment:")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_MOBILE = re.compile(r"(?<![0-9A-Za-z])1[3-9]\d{9}(?![0-9A-Za-z])")


class ToolError(Exception):
    """A refusal or invalid request that the agent should see as ``{"error", "kind", ...details}``."""

    def __init__(self, kind: str, message: str, **details):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.details = details


@dataclass
class ToolContext:
    ws: Workspace
    build_id: str
    role: str                     # analyst | data_scientist | data_engineer
    principal: str
    task_id: str | None = None
    max_rows: int = 200
    budget_calls: int = 200
    calls_used: int = field(default=0, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False, compare=False)
    _stack: ExitStack | None = field(default=None, init=False, repr=False, compare=False)
    _con: duckdb.DuckDBPyConnection | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self):
        if self.role not in ROLE_TOOLS:
            raise ValueError(f"unknown role {self.role!r}; known: {list(ROLE_TOOLS)}")
        check_principal(self.principal)
        if type(self.max_rows) is not int or not 1 <= self.max_rows <= MAX_ROWS_LIMIT:
            raise ValueError(f"max_rows must be an int in [1, {MAX_ROWS_LIMIT}], got {self.max_rows!r}")
        if type(self.budget_calls) is not int or self.budget_calls < 1:
            raise ValueError(f"budget_calls must be a positive int, got {self.budget_calls!r}")
        if self.task_id is not None and (not isinstance(self.task_id, str) or not _PRINCIPAL.match(self.task_id)):
            raise ValueError(f"task_id must match {_PRINCIPAL.pattern}, got {self.task_id!r}")
        report = load_report(self.ws, self.build_id)      # FileNotFoundError for an unknown build
        if report["status"] != "READY":
            raise RuntimeError(f"build {self.build_id} is {report['status']}; tools only serve READY builds "
                               f"({report['reasons'][:3]})")
        self._cutoff = report["cutoff"]

    @property
    def cutoff(self) -> str:
        return self._cutoff

    @property
    def con(self) -> duckdb.DuckDBPyConnection:
        """One read-only, locked-down connection per context (opened lazily, closed by `close`)."""
        if self._con is None:
            stack = ExitStack()
            self._con = stack.enter_context(readonly_connection(warehouse_path(self.ws, self.build_id)))
            self._stack = stack
        return self._con

    def close(self) -> None:
        with self._lock:
            if self._stack is not None:
                self._stack.close()
            self._stack, self._con = None, None

    @property
    def budget_remaining(self) -> int:
        return max(0, self.budget_calls - self.calls_used)


def check_principal(principal) -> str:
    if not isinstance(principal, str) or not _PRINCIPAL.match(principal):
        raise ValueError(f"principal must match {_PRINCIPAL.pattern}, got {principal!r}")
    if principal.lower().startswith(RESERVED_PRINCIPAL_PREFIXES):
        raise ValueError(f"principal {principal!r} uses a reserved evidence-producer namespace "
                         f"{RESERVED_PRINCIPAL_PREFIXES}; agents cannot act as verifiers")
    return principal


def resolve_cutoff(cutoff: str) -> str:
    """'train' | 'score' | 'drift' | 'monitor' or an explicit 'YYYY-MM-DD' cutoff date."""
    if cutoff in CUTOFFS:
        return CUTOFFS[cutoff]
    if isinstance(cutoff, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cutoff):
        return cutoff
    raise ValueError(f"cutoff must be one of {sorted(CUTOFFS)} or 'YYYY-MM-DD', got {cutoff!r}")


def make_context(ws: Workspace, cutoff: str, role: str, principal: str, task_id: str | None = None,
                 **kwargs) -> ToolContext:
    """Controller-side: bind an agent to the READY default-pipeline build of `cutoff`.

    Raises (never returns a degraded context) when the build is missing or FAILED."""
    if role not in ROLE_TOOLS:
        raise ValueError(f"unknown role {role!r}; known: {list(ROLE_TOOLS)}")
    build_id = latest_ready_build(ws, resolve_cutoff(cutoff))
    return ToolContext(ws=ws, build_id=build_id, role=role, principal=principal, task_id=task_id, **kwargs)


# --------------------------------------------------------------------------- result hygiene
def jsonable(obj):
    """Plain JSON value: NaN/inf -> None, timestamps -> ISO strings, numpy/pandas scalars unwrapped."""
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        return f if math.isfinite(f) else None
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if obj is pd.NA or obj is pd.NaT:
        return None
    if isinstance(obj, pd.Timestamp):
        return iso_value(obj.to_pydatetime())
    if isinstance(obj, (datetime, date, dtime)):
        return iso_value(obj)
    if isinstance(obj, timedelta):
        return obj.total_seconds()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(obj))} bytes>"
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset, np.ndarray, pd.Series)):
        return [jsonable(v) for v in (sorted(obj, key=str) if isinstance(obj, (set, frozenset)) else obj)]
    if isinstance(obj, pd.DataFrame):
        return [jsonable(r) for r in obj.to_dict("records")]
    return str(obj)


def iso_value(v) -> str:
    """Dates as 'YYYY-MM-DD'; midnight datetimes as their date; other datetimes as ISO-8601."""
    if isinstance(v, datetime):
        if v.tzinfo is None and v.time() == dtime(0):
            return v.date().isoformat()
        return v.isoformat()
    return v.isoformat()


def scrub_pii(obj):
    """Redact e-mail addresses and mainland mobile numbers in every string of a JSON value."""
    if isinstance(obj, str):
        return _MOBILE.sub("<redacted:phone>", _EMAIL.sub("<redacted:email>", obj))
    if isinstance(obj, dict):
        return {scrub_pii(k): scrub_pii(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_pii(v) for v in obj]
    return obj


def error(kind: str, message: str, **details) -> dict:
    return {"error": message, "kind": kind, **details}


# --------------------------------------------------------------------------- the choke point
TOOL_REGISTRY: dict[str, Callable] = {}


def governed_tool(name: str):
    """Register `fn(ctx, ...)` as agent-facing tool `name` behind budget, role, error and audit checks."""
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(ctx: ToolContext, *args, **kwargs) -> dict:
            if not isinstance(ctx, ToolContext):
                raise TypeError(f"{name}: first argument must be a ToolContext built by make_context")
            from .audit import audit_call        # local: audit imports this module
            started = time.perf_counter()
            with ctx._lock:
                ctx.calls_used += 1
                if ctx.calls_used > ctx.budget_calls:
                    result = error("budget_exhausted", f"tool-call budget exhausted ({ctx.budget_calls} calls "
                                   f"for this session); ask the operator for a new session")
                elif name not in ROLE_TOOLS[ctx.role]:
                    result = error("forbidden", f"tool {name!r} is not available to role {ctx.role!r}; "
                                   f"allowed: {ROLE_TOOLS[ctx.role]}")
                else:
                    result = _invoke(fn, name, ctx, args, kwargs)
                result = scrub_pii(jsonable(result))
                if not isinstance(result, dict):
                    result = error("internal", f"tool {name} returned {type(result).__name__}, not an object")
                audit_call(ctx, name, args, kwargs, result, time.perf_counter() - started)
            return result

        wrapper.tool_name = name
        TOOL_REGISTRY[name] = wrapper
        return wrapper
    return decorate


def _invoke(fn, name: str, ctx: ToolContext, args, kwargs) -> dict:
    try:
        return fn(ctx, *args, **kwargs)
    except ToolError as exc:
        return error(exc.kind, exc.message, **exc.details)
    except TypeError as exc:        # wrong/missing arguments from the agent
        return error("invalid_argument", f"{name}: {exc}")
    except (ValueError, KeyError) as exc:
        msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
        return error("invalid_argument", str(msg))
    except duckdb.InterruptException:
        return error("timeout", f"{name}: query interrupted (time limit exceeded)")
    except duckdb.Error as exc:
        return error("query_error", f"{type(exc).__name__}: {str(exc).splitlines()[0][:400]}")
    except FileNotFoundError as exc:
        return error("not_found", str(exc))
    except Exception as exc:        # contained, reported, audited -- never a raw traceback to the agent
        return error("internal", f"{type(exc).__name__}: {str(exc).splitlines()[0][:400] if str(exc) else ''}")


def run_with_timeout(con: duckdb.DuckDBPyConnection, timeout_s: float, fn: Callable):
    """Run `fn()` (which uses `con`) and interrupt the connection if it takes longer than `timeout_s`."""
    timer = threading.Timer(timeout_s, con.interrupt)
    timer.start()
    try:
        return fn()
    finally:
        timer.cancel()
