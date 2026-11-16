"""Analytics tools: governed metrics first, raw SQL second, and a verification ladder for answers.

Trust boundary: agents ask questions of ONE READY build through a read-only, locked-down
connection. A governed metric (semantic model) is reviewed code, so its numbers are the
oracle. Raw SQL is untrusted input: it is statically analysed before it runs and its results
are always labelled uncertified.

`submit_answer` is where an agent's claim meets evidence. Its ladder:

  1. static analysis of the submitted SQL (or, for a metric-only answer, of the compiled
     metric SQL). Hard violations flag the answer and the SQL is not executed. Soft warnings
     (e.g. governed churn SQL reads the mutable `ended_at`) are reported, never flagged alone;
  2. honesty: re-execute the submitted SQL and compare its rows with the submitted rows;
  3. reconciliation: the agent DECLARES what its numbers mean (`metric_call`); the rows are
     compared with the governed metric for exactly that call, and revenue totals also with the
     Finance close. This is what catches plausible-but-wrong raw-SQL answers. The declared call
     must also BE the question: where the question's own words (never the agent's claims) pin
     the metric, the months, a dimension value or a breakdown, a call that contradicts them
     cannot verify, however well its numbers reconcile (reconcile INCONCLUSIVE with the
     contradictions listed -> CONSISTENT, T3, gate HOLD for human review). Without a
     declaration the harness tries to infer a call from the question; an inferred call can
     flag a mismatch but can never verify (publication requires a declared claim). If nothing
     maps to a governed metric the answer is exploratory: CONSISTENT, gate HOLD (human review).
     Rows that do not fit their own declared claim (missing key columns, repeated keys, no
     numeric value) are a FAIL ("answer does not match its declared claim"), not a harness ERROR:
     the claim is the agent's, so the mismatch is a verdict about the answer. ERROR stays reserved
     for the harness failing to compute its oracle.

Tier: VERIFIED (declared claim reconciles) | CONSISTENT (reproducible, no oracle) | FLAGGED
(any violation or mismatch). Each rung is recorded as evidence (stage answer_publish) and the
gate decision is returned with the tier. This module is the only writer of answer evidence.

Trust label (the user-facing projection of the evidence ladder, docs/DESIGN.zh-CN.md §5.2):
  T0  VERIFIED and reconciled exactly to an independent record (Finance close) for every month
      it covers -- all of them closed;
  T1  VERIFIED on the governed-metric path, but no independent record exists for the metric or
      some month is not closed yet: provisional, re-scored when the record closes;
  T2  cross-checked by a second independent path for a non-governed question -- not issued by
      this reference implementation (it has no such second path yet);
  T3  exploratory (CONSISTENT): reproducible, no oracle;
  FLAGGED answers get no label.
Every submission also yields an answer certificate (docs/answer_certificate.schema.json):
question, term resolution, claim, metric / semantic / SQL digests, build, check outcomes with
evidence ids, tier, trust label and caveats, with harness / model / cost fields left for the
caller. It is returned and appended to registry/answer_certificates.jsonl (append-only).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import functools
import math
import re

import duckdb
import numpy as np
import pandas as pd

from ..common import append_jsonl, digest, read_jsonl, utcnow
from ..context.catalog import Catalog, describe_table as _describe_table, load_catalog, search_catalog as _search
from ..context.semantic import (SemanticModel, compile_metric, coverage, describe_metric as _describe_metric,
                                list_metrics as _list_metrics, load_semantic_model, load_verified_queries,
                                metric_definition_digest, query_metric as _query_metric,
                                query_metric_period as _query_metric_period)
from ..evidence import EvidenceStore, decide
from ..evidence.attest import CERTIFICATE_LOG
from ..evidence.records import iso_utc
from ..lake.build import load_report
from ..verify.reconcile import compare_frames, reconcile_answer_to_metric
from ..verify.sql_static import analyze_sql
from .context import ToolContext, ToolError, governed_tool, iso_value, jsonable, run_with_timeout, scrub_pii
from .derive import DeriveError, apply_derive, compare_derived, normalize_derive
from .shape import shape_contradictions, zh_scan

CHECK_VERSION = "2"             # 2 (H v0.2): answer shape, declared derivations, Chinese dates and names
STAGE = "answer_publish"
QUERY_TIMEOUT_S = 30.0
MAX_SQL_CHARS = 20_000
MAX_ANSWER_ROWS = 2_000
MAX_REEXEC_ROWS = 10_000
MAX_QUESTION_CHARS = 2_000
MAX_CLAIMS = 20
MAX_EXAMPLES = 5
EXACT_REL_TOL = 1e-9            # additive metrics are integers: exact
RATIO_REL_TOL = 1e-6            # ratios/averages: full precision expected, tiny float noise tolerated
_METRIC_CALL_KEYS = {"metric", "dimensions", "months", "filters"}
SUBMIT_HINT = ("If these rows are the answer to the question as asked, submit them unchanged (full precision) with this "
               "metric_call. If the question needs one more step on them (a total or average over the months, a "
               "pooled rate, growth between two months, a share of the total, a difference or ratio between two "
               "groups, a top-N, fen to CNY), submit the result of that step with this metric_call and declare the "
               "step in `derive`; the harness recomputes it.")
_DERIVE_SYNTAX = ("submit_answer.derive declares one arithmetic step on the governed rows: {months: sum|mean|pool|growth|cumsum|change|total, "
                  "across: {op: share|difference|ratio|top, dimension (or 'month'), a, b, n}, keep: {dimension: values the "
                  "question names}, unit: cny|percent, windows: [[first, last], [first, last]] (growth from one period to "
                  "another), divide_by: {metric, filters} (a ratio of two governed metrics: per customer, per order, "
                  "share of customers)}. ")
_DERIVE_EXAMPLE = ("Example: 'Q1 2026 net revenue in CNY' -> metric_call {metric: net_revenue_fen, "
                   "dimensions: [], months: [2026-01, 2026-03]} with derive {months: sum, unit: cny} and one row "
                   "{value: ...}. ")
_DERIVE_TAIL = ("'total' is the metric over the declared months as one period (each customer counted once); "
                "'pool' recomputes a rate over several months from its counts; 'change' is each month's change from "
                "the month before (declare the base month too; it is not reported); 'cumsum' is a running total and keeps "
                "one row per month; a distinct count cannot be summed over months.")
DERIVE_GUIDANCE = _DERIVE_SYNTAX + _DERIVE_EXAMPLE + _DERIVE_TAIL


def derive_guidance(model: SemanticModel) -> str:
    """The derive guidance with the model's own worked example (`derive_example`), if it has one."""
    ex = str(model.spec.get("derive_example") or "").strip()
    return _DERIVE_SYNTAX + (ex + " " if ex else _DERIVE_EXAMPLE) + _DERIVE_TAIL


CERTIFICATE_SCHEMA = "https://dsharness.dev/schemas/answer-certificate/v1"
CERTIFICATES_LOG = CERTIFICATE_LOG                 # registry/answer_certificates.jsonl, append-only
CALLER_FIELDS = ("harness", "model", "cost")       # filled in by the caller; outside the certificate id
ALLOWED_USE = {
    "T0": "reconciled to an independent record: may be used externally (board, regulator) with a named sign-off",
    "T1": "governed, provisional: internal KPIs and dashboards marked provisional; re-scored when the record closes",
    "T2": "cross-checked: internal decision support",
    "T3": "exploratory: not for KPIs or reports; state the assumptions",
    None: "do not use: the answer is wrong, unsafe or unverifiable",
}
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_MIDNIGHT = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T]00:00(:00(\.0+)?)?$")
_MONTH_START = re.compile(r"^(\d{4}-\d{2})-01([ T]00:00(:00(\.0+)?)?)?$")


@functools.cache
def _model_at(path: str | None) -> SemanticModel:
    return load_semantic_model(path)


@functools.cache
def _catalog_at(path: str | None) -> Catalog:
    return load_catalog(path)


def semantic_model(ctx: ToolContext | None = None) -> SemanticModel:
    """The workspace's own model (context/semantic_model.yaml) if it has one, else the built-in one."""
    p = ctx.ws.context_file("semantic_model.yaml") if ctx is not None else None
    return _model_at(str(p) if p else None)


def catalog(ctx: ToolContext | None = None) -> Catalog:
    p = ctx.ws.context_file("catalog.yaml") if ctx is not None else None
    return _catalog_at(str(p) if p else None)


# =========================================================================== context tools
@governed_tool("list_metrics")
def list_metrics(ctx: ToolContext) -> dict:
    """Governed metrics, the build's month coverage and worked examples (runtime-context split only)."""
    model = semantic_model(ctx)
    first, last = coverage(ctx.con, model)
    # a workspace with its own model shows only its own worked examples (none if it has no verified_queries.yaml)
    vq = ctx.ws.context_file("verified_queries.yaml")
    own = ctx.ws.context_file("semantic_model.yaml") is not None
    examples = [] if own and vq is None else [
        {"question": q["question"], "metric_call": q["metric_call"], "sql": q["sql"], "notes": q["notes"]}
        for q in load_verified_queries("context", path=vq, model=model)]
    return {"metrics": _list_metrics(model), "coverage": {"first_month": first, "last_month": last},
            "build_id": ctx.build_id, "cutoff": ctx.cutoff, "semantic_digest": model.digest,
            "examples": examples,
            "guidance": ["Answer with query_metric whenever a governed metric covers the question.",
                         "Submit with submit_answer and declare metric_call = {metric, dimensions, months, filters}.",
                         "The metric_call must be the question asked: its metric, months containing the months "
                         "it names, the values it names as filters, a dimension for every 'by X'. A call that "
                         "contradicts the question is held for human review, however well it reconciles.",
                         derive_guidance(model),
                         "run_sql results are uncertified; use them only when no governed metric fits."]}


@governed_tool("describe_metric")
def describe_metric(ctx: ToolContext, name: str) -> dict:
    return _describe_metric(semantic_model(ctx), name)


@governed_tool("query_metric")
def query_metric(ctx: ToolContext, metric: str, dimensions: list[str] | None = None,
                 months: list[str] | None = None, filters: dict | None = None) -> dict:
    """Governed metric rows (month, <dimensions...>, value). `months` is an inclusive pair."""
    model = semantic_model(ctx)
    call = normalize_metric_call({"metric": metric, "dimensions": dimensions or [], "months": months,
                                  "filters": filters})
    sql = compile_metric(model, **call)
    df = run_with_timeout(ctx.con, QUERY_TIMEOUT_S, lambda: _query_metric(ctx.con, model, **call))
    rows = df.to_dict("records")
    return {"rows": rows[:ctx.max_rows], "n_rows": len(rows), "truncated": len(rows) > ctx.max_rows,
            "columns": list(df.columns), "metric_call": call, "tier": "governed", "sql": sql,
            "semantic_digest": model.digest, "build_id": ctx.build_id,
            "caveats": list(model.metrics[call["metric"]].caveats),
            "submit_hint": SUBMIT_HINT}


@governed_tool("search_catalog")
def search_catalog(ctx: ToolContext, query: str) -> dict:
    hits = _search(catalog(ctx), query)
    return {"results": hits[:20], "n_results": len(hits),
            "note": "certified = silver.* (governed); staging/uncertified tables carry known defects."}


@governed_tool("describe_table")
def describe_table(ctx: ToolContext, table: str) -> dict:
    return _describe_table(catalog(ctx), table)


@governed_tool("run_sql")
def run_sql(ctx: ToolContext, sql: str) -> dict:
    """Statically analysed, read-only SQL. Violations are refused; results are always uncertified."""
    _check_sql_arg(sql)
    rep = analyze_sql(sql, catalog(ctx))
    if not rep.ok:
        raise ToolError("refused", "SQL refused by static analysis", violations=rep.violations,
                        warnings=rep.warnings)
    cols, rows, truncated = _execute(ctx, sql, ctx.max_rows)
    pii = [c for c in cols if c.lower() in catalog(ctx).pii_columns]
    if pii:
        raise ToolError("refused", f"result columns look like personal data: {pii}")
    return {"columns": cols, "rows": [dict(zip(cols, r)) for r in rows], "n_rows": len(rows),
            "truncated": truncated, "tier": "uncertified", "tables": rep.tables, "table_tier": rep.tier,
            "warnings": rep.warnings, "build_id": ctx.build_id,
            "note": "Uncertified result. Prefer query_metric; any number you report must go through submit_answer."}


def _check_sql_arg(sql) -> None:
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("sql must be a non-empty string")
    if len(sql) > MAX_SQL_CHARS:
        raise ValueError(f"sql is longer than {MAX_SQL_CHARS} characters")


def _execute(ctx: ToolContext, sql: str, limit: int) -> tuple[list[str], list[tuple], bool]:
    """Run an analysed SELECT with the row cap applied inside DuckDB (lazy relation + LIMIT), so a
    huge result is never materialised; order of an ORDER BY query is preserved."""
    def go():
        rel = ctx.con.sql(sql)
        return list(rel.columns), rel.limit(limit + 1).fetchall()
    names, rows = run_with_timeout(ctx.con, QUERY_TIMEOUT_S, go)
    return _unique_names(names), rows[:limit], len(rows) > limit


def _unique_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for n in names:
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n}_{seen[n]}")
    return out


# =========================================================================== metric calls
def normalize_metric_call(mc) -> dict:
    """{metric, dimensions: [...], months: (a, b) | None, filters: {...} | None}; raises ValueError."""
    if not isinstance(mc, dict):
        raise ValueError(f"metric_call must be an object {{metric, dimensions, months, filters}}, got {mc!r}")
    unknown = set(mc) - _METRIC_CALL_KEYS
    if unknown:
        raise ValueError(f"metric_call has unknown keys {sorted(unknown)}; allowed: {sorted(_METRIC_CALL_KEYS)}")
    metric = mc.get("metric")
    if not isinstance(metric, str):
        raise ValueError("metric_call.metric must be a metric name (see list_metrics)")
    dims = mc.get("dimensions") or []
    if isinstance(dims, str) or not isinstance(dims, (list, tuple)) or not all(isinstance(d, str) for d in dims):
        raise ValueError(f"metric_call.dimensions must be a list of dimension names, got {dims!r}")
    months = mc.get("months")
    if months is not None:
        if isinstance(months, str) or not isinstance(months, (list, tuple)) or len(months) != 2:
            raise ValueError(f"metric_call.months must be ['YYYY-MM', 'YYYY-MM'] (inclusive), got {months!r}")
        months = (str(months[0]), str(months[1]))
    filters = mc.get("filters") or None
    if filters is not None and not isinstance(filters, dict):
        raise ValueError(f"metric_call.filters must be an object dimension -> value(s), got {filters!r}")
    if filters:
        filters = {k: (sorted(v) if isinstance(v, (list, tuple)) else v) for k, v in filters.items()}
    return {"metric": metric, "dimensions": [d for d in dims if d != "month"], "months": months, "filters": filters}


def _validate_call_on_build(ctx: ToolContext, call: dict) -> str:
    """Compile (validates names/values) and check the months lie in the build's coverage."""
    model = semantic_model(ctx)
    sql = compile_metric(model, **call)
    first, last = coverage(ctx.con, model)
    if call["months"] is not None and (call["months"][0] < first or call["months"][1] > last):
        raise ValueError(f"months {call['months'][0]}..{call['months'][1]} are outside this build's "
                         f"coverage {first}..{last}")
    return sql


def _tolerance(model: SemanticModel, metric: str) -> float:
    raw = model.spec["metrics"][metric]
    if raw.get("type", "simple") != "simple":
        return RATIO_REL_TOL
    agg = model.spec["datasets"][raw["dataset"]]["measures"][raw["measure"]]["agg"]
    return EXACT_REL_TOL if agg in ("sum", "count", "count_distinct") else RATIO_REL_TOL


# =========================================================================== governed-claim matching
# Wording that changes what a number means; inference stops and asks for a declaration instead.
_QUALIFIERS = re.compile(
    r"\b(gross|exclud\w*|excl|without|except|only|cumulative|growth|change[sd]?|share|percent\w*|median|"
    r"ytd|year[- ]to[- ]date|compared?|versus|vs|difference|forecast\w*|predict\w*|project\w*|estimat\w*|"
    r"expected|average monthly|per month|monthly average)\b")
_MONTH_NAMES = {"january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
                "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9,
                "sept": 9, "sep": 9, "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12,
                "dec": 12}
_MN = "|".join(sorted(_MONTH_NAMES, key=len, reverse=True))
_FULL_MONTH_NAMES = [n for n in _MONTH_NAMES if len(n) > 4 or n in ("june", "july")] + ["may"]


_QUALIFIER_REACH = 16            # characters before a generic term in which a qualifier refines it


def match_governed_metrics(model: SemanticModel, text: str) -> list[str]:
    """Governed metrics a question or claim talks about (most specific reading wins).

    A metric named only by one of its generic_terms ("sales", 销售额) is the default reading; a qualifier of a
    metric that suppresses it ("gross", 毛) just before the generic word makes that metric the reading instead
    (H v0.5 F23, E3a R-H08 / R-L40 / R-H14: 毛产品销售额 was read as net sales and one agent switched to net
    because of it). Only just before: "sales excluding cancelled orders" is still the default reading."""
    low = text.lower()
    strong = {name for name, m in model.metrics.items() if any(re.search(t, low) for t in m.terms)}
    default: set[str] = set()
    for name, m in model.metrics.items():
        if name in strong:
            continue
        starts = [mt.start() for t in m.generic_terms for mt in re.finditer(t, low)]
        if starts:
            refined = {r for r, rm in model.metrics.items() if name in rm.suppresses and any(
                re.search(q, low[max(0, i - _QUALIFIER_REACH):i]) for q in rm.qualifiers for i in starts)}
            strong |= refined
            default |= set() if refined else {name}
    hits = strong | default
    for name in strong:             # a default reading is weak: it suppresses nothing
        hits -= set(model.metrics[name].suppresses)
    if {"active_subscribers_eom", "active_subscribers_bom"} <= hits:
        if re.search(r"\b(start|beginning|begin|opening)\b", low):
            hits.discard("active_subscribers_eom")
        elif re.search(r"\b(end|closing)\b", low):
            hits.discard("active_subscribers_bom")
    return sorted(hits)


def _ym(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}"


_ORDINAL = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}


def _scan_months(text: str) -> tuple[list[str], str, bool]:
    """(months the text names explicitly, the text with those mentions removed, names a specific day).
    A backwards range contributes the marker '!'; a specific day contributes no month."""
    zh_found, rest = zh_scan(text)
    low = f" {rest.lower()} "
    found: list[str] = list(zh_found)

    def take(pattern, fn):
        nonlocal low
        for mt in list(re.finditer(pattern, low)):
            found.extend(fn(mt))
        low = re.sub(pattern, " ", low)

    def span(y, a, b):
        return [_ym(y, k) for k in range(a, b + 1)] if a <= b else ["!"]

    take(rf"\b({_MN})\.?\s*(?:to|through|thru|until|-|–)\s*({_MN})\.?,?\s+(\d{{4}})\b",
         lambda mt: span(int(mt[3]), _MONTH_NAMES[mt[1]], _MONTH_NAMES[mt[2]]))
    # E3a R-H07: "each month of 2025 through November" is January to November, not the whole year (H v0.5 F26)
    take(rf"\b(\d{{4}})\s*,?\s+(?:through|thru|up\s+to|until|to)\s+({_MN})\b(?!\.?,?\s+\d{{4}})",
         lambda mt: span(int(mt[1]), 1, _MONTH_NAMES[mt[2]]))
    take(rf"\b({_MN})\.?,?\s+(\d{{4}})\b", lambda mt: [_ym(int(mt[2]), _MONTH_NAMES[mt[1]])])
    take(r"\b(first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter\s+(?:of\s+)?(\d{4})\b",
         lambda mt: span(int(mt[2]), 3 * _ORDINAL[mt[1]] - 2, 3 * _ORDINAL[mt[1]]))
    take(r"\bq([1-4])\s+of\s+(\d{4})\b", lambda mt: span(int(mt[2]), 3 * int(mt[1]) - 2, 3 * int(mt[1])))
    take(r"\b(first|second|1st|2nd)\s+half\s+(?:of\s+)?(\d{4})\b",
         lambda mt: span(int(mt[2]), 6 * _ORDINAL[mt[1]] - 5, 6 * _ORDINAL[mt[1]]))
    take(r"\bh([12])\s*,?\s*(\d{4})\b", lambda mt: span(int(mt[2]), 6 * int(mt[1]) - 5, 6 * int(mt[1])))
    take(r"\b(\d{4})\s*-?\s*h([12])\b", lambda mt: span(int(mt[1]), 6 * int(mt[2]) - 5, 6 * int(mt[2])))
    take(r"\bq([1-4])\s*,?\s*(\d{4})\b", lambda mt: span(int(mt[2]), 3 * int(mt[1]) - 2, 3 * int(mt[1])))
    take(r"\b(\d{4})\s*-?\s*q([1-4])\b", lambda mt: span(int(mt[1]), 3 * int(mt[2]) - 2, 3 * int(mt[2])))
    day = re.search(r"\b\d{4}-\d{2}-\d{2}\b", low) is not None
    take(r"\b\d{4}-\d{2}-\d{2}\b", lambda mt: [])          # a day is not a month scope ("on 2026-05-01")
    take(r"\b(\d{4})-(0[1-9]|1[0-2])\b", lambda mt: [_ym(int(mt[1]), int(mt[2]))])
    take(r"\b(?:in|during|for|of|over|throughout)\s+(?:the\s+year\s+)?(\d{4})\b",
         lambda mt: span(int(mt[1]), 1, 12))
    return found, low, day


def months_from_text(text: str) -> tuple[tuple[str, str] | None, str | None]:
    """((first, last) | None, problem | None). Only contiguous, unambiguous month scopes are returned."""
    found, low, day = _scan_months(text)
    if day:
        return None, "the question names a specific date; declare metric_call.months"
    if "!" in found:
        return None, "a month range in the question runs backwards"
    leftover_names = [n for n in _FULL_MONTH_NAMES if re.search(rf"\b{n}\b", low)
                      and (n != "may" or re.search(r"\bMay\b", text))]
    if leftover_names or re.search(r"\b(19|20)\d{2}\b", low):
        return None, "the question's time scope is ambiguous (month or year without a parseable scope)"
    if not found:
        return None, None
    months = sorted(set(found))
    if any(not (2000 <= int(m[:4]) <= 2100) for m in months):
        return None, "implausible year in the question"
    ys, ms = int(months[0][:4]), int(months[0][5:])
    expected = [_ym(ys + (ms - 1 + k) // 12, (ms - 1 + k) % 12 + 1) for k in range(len(months))]
    if months != expected:
        return None, "the question names non-contiguous months"
    return (months[0], months[-1]), None


# Spellings of dimension values and names in a question, beyond the canonical `snake_case` form.
_VALUE_ALIASES = {"addon": r"add[ -]?ons?", "howto": r"how[ -]?tos?"}
_DIM_PHRASES = {"ticket_category": r"(?:ticket[ _-])?categor(?:y|ies)", "invoice_type": r"invoice[ _-]types?"}
_BREAKDOWN = (r"\b(?:by|per|for each|for every|in each|in every|broken down by|split by|grouped by)\s+"
              r"(?:(?:the|each|every|acquisition|customer|subscription|ticket|invoice|program(?:me)?|campaign|"
              r"randomi[sz]ed)\s+)?")


def _flex(text: str) -> str:
    """Spaces, hyphens and underscores are interchangeable: "non-UK", "non UK", "paid_search"."""
    return re.sub(r"(?:\\ |\\-|_)+", "[ _-]+", re.escape(text))


def _value_pattern(v: str) -> str:
    return _VALUE_ALIASES.get(v) or (_flex(v) + r"(?:e?s|e?d)?")


def _dim_pattern(d: str) -> str:
    return _DIM_PHRASES.get(d) or (re.escape(d).replace("_", "[ _-]") + r"s?")


_ZH_BREAKDOWN = r"(?:各|每个|每一个|按|分)\s*(?:个\s*)?"


# "net revenue including add-ons and refunds", "excluding test accounts", "含退款": words that describe
# what the quantity is made of. Values named only there are not a filter and not a breakdown.
_DEFINITION_CLAUSE = re.compile(r"\b(?:including|incl\.|inclusive of|excluding|excl\.|net of|after|less)\b[^,.;:?()]*|"
                                r"(?:包括|包含|含|扣除|扣掉|减去|不含)[^，。；：？（）,.;:?()]*", re.I)


def _ascii_bounded(pattern: str, text: str) -> str:
    """Latin spellings must stand alone ("uk" is not in "duke"); Chinese has no word boundaries."""
    return (r"(?<![a-z0-9])" if text[:1].isascii() and text[:1].isalnum() else "") + pattern + \
        (r"(?![a-z0-9])" if text[-1:].isascii() and text[-1:].isalnum() else "")


def _named_dimension_values(model: SemanticModel, question: str) -> tuple[dict[str, set[str]], list[set]]:
    """Dimension values the question names, by canonical spelling or by an alias, read left to right
    with the longest spelling winning at each position: "non-UK" never also names "UK", 非英国 never
    英国. One spelling can name a value of several dimensions (market "UK" and country "United
    Kingdom"); `hits` lists, per phrase found, every (dimension, value) it names. Values inside a
    definition clause do not count."""
    low = _DEFINITION_CLAUSE.sub(" ", question).lower()
    spell: dict[str, tuple[set, set]] = {}               # normalised spelling -> ({patterns}, {(dim, value)})
    for d, dim in model.dimensions.items():
        for v in dim.values or ():
            for text, canonical in ((v, True), *((a, False) for a in dim.aliases.get(v, ()))):
                key = re.sub(r"[ _-]+", " ", text.lower())
                pats, owners = spell.setdefault(key, (set(), set()))
                pats.add(_value_pattern(text.lower()) if canonical else _flex(text.lower()))
                owners.add((d, v))
    named: dict[str, set[str]] = {}
    hits: list[set] = []
    if not spell:
        return named, hits
    order = sorted(spell, key=len, reverse=True)
    rx = re.compile("|".join(f"(?P<s{i}>{_ascii_bounded('(?:' + '|'.join(sorted(spell[k][0], key=len, reverse=True)) + ')', k)})"
                             for i, k in enumerate(order)))
    for m in rx.finditer(low):
        owners = spell[order[int(m.lastgroup[1:])]][1]
        hits.append(owners)
        for d, v in owners:
            named.setdefault(d, set()).add(v)
    return named, hits


def _asks_breakdown(model: SemanticModel, d: str, question: str) -> bool:
    if re.search(_BREAKDOWN + _dim_pattern(d) + r"\b", question.lower()):
        return True
    names = model.dimensions[d].names
    return bool(names) and re.search(_ZH_BREAKDOWN + "(?:" + "|".join(map(re.escape, names)) + ")", question) is not None


def claim_contradictions(model: SemanticModel, question: str, declared: dict) -> list[str]:
    """Where the QUESTION's own words pin the metric, the months, a dimension value or a breakdown,
    the declared metric_call must agree; each disagreement is returned as one sentence.

    Only the question is parsed, never the claims: claims are the agent's own words and could
    otherwise dilute the parse (an extra month or metric name switches a check off). The rules
    are deliberately one-sided: the declared call may carry MORE than was asked (a wider month
    range, an extra breakdown) because the asked-for rows are then still in the answer, but it
    may not answer a different metric, other months, other dimension values, or fewer values
    than asked. A parser false positive costs a human review (HOLD), never a REJECT."""
    out: list[str] = []
    asked = match_governed_metrics(model, question)
    if asked and declared["metric"] not in asked:
        out.append(f"the question asks about {asked} but metric_call declares {declared['metric']!r}")
    dm = declared["months"]
    if dm is not None:
        q_months, _ = months_from_text(question)
        if q_months is None:        # non-contiguous or partly ambiguous: every month it does name still counts
            named = sorted(m for m in _scan_months(question)[0] if m != "!")
            q_months = (named[0], named[-1]) if named else None
        # containment, not equality: months_from_text under-reads lists such as "Jan and Feb 2026"
        if q_months and not (dm[0] <= q_months[0] and q_months[1] <= dm[1]):
            out.append(f"the question names months {q_months[0]}..{q_months[1]} but metric_call declares "
                       f"{dm[0]}..{dm[1]}")
    filters = declared["filters"] or {}
    all_named, hits = _named_dimension_values(model, question)
    status: dict[str, tuple[bool, set, set]] = {}
    for d in model.metrics[declared["metric"]].dimensions:
        named = all_named.get(d, set())
        f = filters.get(d)
        fv = set(f) if isinstance(f, list) else ({f} if f is not None else set())
        if d in declared["dimensions"]:
            # a breakdown carries every named value as its own row unless a filter drops it; a filter
            # the question does not ask for narrows the answer
            ok = not fv or (bool(named) and named <= fv)
        else:
            ok = fv == named
        status[d] = (ok, named, fv)

    def honoured_elsewhere(d: str) -> bool:
        """Every phrase naming d also names a value of another dimension the call filters or breaks
        down by, consistently ("UK" read as market UK or as country United Kingdom)."""
        return all(any(d2 != d and status.get(d2, (False,))[0] and (d2 in declared["dimensions"] or filters.get(d2))
                       for d2, _ in hit) for hit in hits if any(dd == d for dd, _ in hit))
    for d, (ok, named, fv) in status.items():
        if ok or (not fv and named and honoured_elsewhere(d)):
            continue
        if not named:
            out.append(f"metric_call filters {d} to {sorted(fv)} but the question names no {d}")
        elif fv:
            out.append(f"the question names {d} {sorted(named)} but metric_call filters {d} to {sorted(fv)}")
        else:
            out.append(f"the question names {d} {sorted(named)} but metric_call neither filters nor breaks down "
                       f"by {d}")
    for d in model.dimensions:
        if d != "month" and d not in declared["dimensions"] and _asks_breakdown(model, d, question):
            out.append(f"the question asks for a breakdown by {d} but metric_call.dimensions is "
                       f"{declared['dimensions']}")
    return out


def infer_metric_call(model: SemanticModel, text: str, rows: list[dict]) -> tuple[dict | None, list[str]]:
    """Best-effort reading of an undeclared answer as a governed metric call.

    Returns (call, notes). Anything ambiguous returns None: the safe failure is "cannot map"
    (INCONCLUSIVE), never a guessed interpretation that could wrongly flag a correct answer."""
    metrics = match_governed_metrics(model, text)
    if not metrics:
        return None, []
    if len(metrics) > 1:
        return None, [f"the question mentions several governed metrics {metrics}; declare metric_call"]
    metric = metrics[0]
    low = text.lower()
    if _QUALIFIERS.search(low):
        return None, [f"the question qualifies {metric} ({_QUALIFIERS.search(low).group(0)!r}); declare metric_call "
                      f"if it is a governed quantity"]
    months, problem = months_from_text(text)
    if problem:
        return None, [problem]
    cols = set().union(*(r.keys() for r in rows)) if rows else set()
    m = model.metrics[metric]
    for d in m.dimensions:
        if d in cols:
            continue
        for v in model.dimensions[d].values or ():
            if re.search(rf"\b{re.escape(v).replace('_', '[ _-]')}s?\b", low):
                return None, [f"the question mentions {d}={v!r} (a filter?); declare metric_call.filters"]
    if months is None:
        if "month" not in cols:
            return None, ["neither the question nor the rows state which months the numbers cover"]
        row_months = {_norm_month(r.get("month")) for r in rows}
        if not all(isinstance(x, str) and _MONTH.match(x) for x in row_months):
            return None, ["answer rows have non-month values in the month column"]
        months = (min(row_months), max(row_months))
    dims = [d for d in m.dimensions if d in cols]
    return {"metric": metric, "dimensions": dims, "months": months, "filters": None}, []


# =========================================================================== answer shaping
class _ShapeError(ValueError):
    pass


def _norm_month(v):
    if isinstance(v, (datetime, date, pd.Timestamp)):
        v = iso_value(v.to_pydatetime() if isinstance(v, pd.Timestamp) else v)
    if isinstance(v, str):
        mt = _MONTH_START.match(v.strip())
        if mt:
            return mt.group(1)
        return v.strip()
    return v


def _is_number(v) -> bool:
    return isinstance(v, (int, float, Decimal, np.integer, np.floating)) and not isinstance(v, (bool, np.bool_))


def shape_for_metric(rows: list[dict], call: dict, metric_name: str) -> tuple[list[dict], list[str]]:
    """Rows -> [{month, <dims>, value}] for reconciliation; raises _ShapeError when they do not fit."""
    keys = ["month", *call["dimensions"]]
    cols: list[str] = []
    for r in rows:
        cols += [c for c in r if c not in cols]
    notes = []
    if "value" in cols:
        vcol = "value"
    elif metric_name in cols:
        vcol = metric_name
    else:
        numeric = [c for c in cols if c not in keys and all(_is_number(r.get(c)) or r.get(c) is None for r in rows)
                   and any(_is_number(r.get(c)) for r in rows)]
        if len(numeric) != 1:
            raise _ShapeError(f"cannot tell which column holds the value: name it 'value' (numeric candidates: "
                              f"{numeric})")
        vcol = numeric[0]
        notes.append(f"value read from column {vcol!r}")
    missing = [d for d in call["dimensions"] if d not in cols]
    if missing:
        raise _ShapeError(f"rows lack the declared dimension columns {missing}")
    fill_month = None
    if "month" not in cols:
        months = call["months"]
        if months is None or months[0] != months[1]:
            raise _ShapeError("rows lack a 'month' column and the claim spans more than one month")
        fill_month = months[0]
        notes.append(f"month {fill_month} taken from the claim (rows have no month column)")
    out = []
    seen: set[tuple] = set()
    for r in rows:
        v = r.get(vcol)
        if v is not None and not _is_number(v):
            raise _ShapeError(f"value column {vcol!r} has a non-numeric value {v!r}; report numbers as JSON numbers")
        row = {"month": fill_month if fill_month else _norm_month(r.get("month")), "value": v}
        for d in call["dimensions"]:
            row[d] = r.get(d)
        key = tuple(None if row[k] is None else str(row[k]).strip() for k in keys)
        if key in seen:
            raise _ShapeError(f"rows repeat the key {dict(zip(keys, key))}: the claim has one row per {keys}")
        seen.add(key)
        out.append(row)
    return out, notes


def ungoverned_keys(rows: list[dict], call: dict, allowed: list[str]) -> list[str]:
    """Columns that key the answer rows (text or whole numbers, more than one value) but are not a dimension the
    declared metric has (E3a R-H29: a right top-5 by item code FAILED as 'rows lack a month column')."""
    cols: list[str] = []
    for r in rows:
        cols += [c for c in r if c not in cols]
    known = {"month", "value", call["metric"], *call["dimensions"], *allowed}
    out = []
    for c in cols:
        vals = [r.get(c) for r in rows if r.get(c) is not None]
        if c in known or not vals or any(isinstance(v, (float, np.floating, Decimal)) for v in vals):
            continue
        if len({str(v).strip() for v in vals}) > 1:
            out.append(c)
    return out


# =========================================================================== honesty (re-execution)
def _canon(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ("null", None)
    if isinstance(v, (bool, np.bool_)):
        return ("bool", bool(v))
    if _is_number(v):
        return ("num", float(v))
    if isinstance(v, (datetime, date, pd.Timestamp)):
        return ("str", iso_value(v.to_pydatetime() if isinstance(v, pd.Timestamp) else v))
    if isinstance(v, str):
        s = v.strip()
        mt = _MIDNIGHT.match(s)
        return ("str", mt.group(1) if mt else s)
    return ("str", str(jsonable(v)))


def _sort_key(row: tuple) -> tuple:
    return tuple((tag, f"{val:.9g}" if tag == "num" else str(val)) for tag, val in row)


def _pair_columns(a_cols: list[str], query_cols: list[str], query_rows: list[tuple]) -> tuple[dict, list[str]] | None:
    """Answer column -> SQL column, or None. Same names pair; one column left on each side pairs too (the answer
    must call the value 'value', the SQL may not: E3a, 23 right answers FAILED on `AS v`); SQL columns left over
    with numbers in every row are helpers the answer may leave out. Anything else is a mismatch."""
    pairs = {c: c for c in a_cols if c in query_cols}
    a_left = [c for c in a_cols if c not in pairs]
    q_left = [c for c in query_cols if c not in pairs.values()]
    notes = []
    if len(a_left) == 1 and len(q_left) == 1:
        pairs[a_left[0]] = q_left[0]
        notes.append(f"answer column {a_left[0]!r} compared with SQL column {q_left[0]!r}")
    elif a_left:
        return None
    elif q_left:
        idx = [query_cols.index(c) for c in q_left]
        if not query_rows or not all(_canon(t[i])[0] == "num" for t in query_rows for i in idx):
            return None
        notes.append(f"SQL helper column(s) {q_left} not in the answer")
    return pairs, notes


def compare_row_multisets(answer_rows: list[dict], query_cols: list[str], query_rows: list[tuple]) -> dict:
    """Order-insensitive row comparison: same columns (see _pair_columns), same rows, numbers within 1e-9
    (relative)."""
    a_cols = sorted(set().union(*(r.keys() for r in answer_rows))) if answer_rows else []
    base = {"n_answer_rows": len(answer_rows), "n_query_rows": len(query_rows), "query_columns": query_cols}
    paired = _pair_columns(a_cols, query_cols, query_rows) if answer_rows else ({c: c for c in query_cols}, [])
    if paired is None:
        return {**base, "outcome": "FAIL", "answer_columns": a_cols,
                "reason": f"answer columns {a_cols} differ from the columns the SQL returns {sorted(query_cols)}"}
    pairs, col_notes = paired
    if col_notes:
        base["column_notes"] = col_notes
    q_cols = sorted(pairs, key=str)
    a = sorted((tuple(_canon(r.get(c)) for c in q_cols) for r in answer_rows), key=_sort_key)
    idx = [query_cols.index(pairs[c]) for c in q_cols]
    q = sorted((tuple(_canon(t[i]) for i in idx) for t in query_rows), key=_sort_key)
    if len(a) != len(q):
        return {**base, "outcome": "FAIL",
                "reason": f"the SQL returns {len(q)} rows but the answer has {len(a)}"}
    mismatches = []
    for ra, rq in zip(a, q):
        for col, (ta, va), (tq, vq) in zip(q_cols, ra, rq):
            same = (ta == tq) and (va == vq or (ta == "num" and math.isclose(va, vq, rel_tol=1e-9, abs_tol=1e-9)))
            if not same:
                mismatches.append({"column": col, "answer": va, "query": vq})
                break
    if mismatches:
        return {**base, "outcome": "FAIL", "n_mismatched_rows": len(mismatches),
                "mismatches": mismatches[:MAX_EXAMPLES],
                "reason": f"{len(mismatches)} answer rows differ from what the SQL returns"}
    if not a:
        return {**base, "outcome": "INCONCLUSIVE", "reason": "the SQL returns no rows and the answer is empty"}
    return {**base, "outcome": "PASS"}


# =========================================================================== finance close
def finance_close_check(con: duckdb.DuckDBPyConnection, rows: list[dict], call: dict,
                        model: SemanticModel | None = None) -> dict:
    """Revenue totals vs the independent Finance close (closed months only). Applies only to a metric
    whose semantic model names the finance close as its independent record."""
    dims = call["dimensions"]
    model = model or semantic_model()
    if not any(m.independent_record for m in model.metrics.values()):
        return {"outcome": "NOT_APPLICABLE", "reason": "no independent record exists for this data"}
    m = model.metrics.get(call["metric"])
    if (m is None or m.independent_record != "silver.finance_closed_revenue" or call["filters"]
            or not set(dims) <= {"region"}):
        return {"outcome": "NOT_APPLICABLE", "reason": "only unfiltered net revenue by month[/region] has an "
                                                       "official record"}
    a, b = call["months"] if call["months"] else ("0000-00", "9999-99")
    fin = con.execute("SELECT month, region, net_revenue_fen FROM silver.finance_closed_revenue "
                      "WHERE month BETWEEN ? AND ?", [a, b]).df()
    if fin.empty:
        return {"outcome": "NOT_APPLICABLE", "reason": f"Finance has not closed any month in {a}..{b}"}
    keys = ["month", *dims]
    expected = fin.groupby(keys, as_index=False)["net_revenue_fen"].sum().rename(columns={"net_revenue_fen": "value"})
    closed = set(expected["month"])
    actual = pd.DataFrame([r for r in rows if r["month"] in closed], columns=[*keys, "value"])
    res = compare_frames(actual, expected, keys, "value", rel_tol=EXACT_REL_TOL)
    return {**res, "months_checked": sorted(closed)}


# =========================================================================== submit_answer
def _validate_answer_rows(rows) -> list[dict]:
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ValueError("answer_rows must be a list of objects (one per result row)")
    if len(rows) > MAX_ANSWER_ROWS:
        raise ValueError(f"answer_rows has {len(rows)} rows; at most {MAX_ANSWER_ROWS} are accepted")
    for r in rows:
        for k, v in r.items():
            if not isinstance(k, str):
                raise ValueError(f"answer row keys must be strings, got {k!r}")
            if not (v is None or isinstance(v, (str, bool, int, float))):
                raise ValueError(f"answer row values must be JSON scalars, got {type(v).__name__} in column {k!r}")
            if isinstance(v, float) and not math.isfinite(v):
                raise ValueError(f"answer row value in column {k!r} is not finite")
    return rows


def _summ(res: dict, keep=("outcome", "reason", "error", "n_expected", "n_actual", "n_compared", "n_mismatches",
                           "mismatches", "n_missing_keys", "missing_keys", "n_extra_keys", "extra_keys",
                           "max_abs_diff", "rel_tol", "months_checked", "n_answer_rows", "n_query_rows",
                           "n_mismatched_rows", "query_columns", "answer_columns", "source")) -> dict:
    out = {k: res[k] for k in keep if k in res}
    for k in ("mismatches", "missing_keys", "extra_keys"):
        if k in out:
            out[k] = out[k][:MAX_EXAMPLES]
    return out


@governed_tool("submit_answer")
def submit_answer(ctx: ToolContext, question: str, answer_rows: list[dict], sql: str | None = None,
                  metric_call: dict | None = None, claims: list[str] | tuple = (), derive: dict | None = None) -> dict:
    """Verify an answer (static -> re-execution -> reconciliation), record evidence, return tier, trust
    label, gate decision and the answer certificate."""
    model = semantic_model(ctx)
    if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION_CHARS:
        raise ValueError(f"question must be a non-empty string of at most {MAX_QUESTION_CHARS} characters")
    rows = _validate_answer_rows(answer_rows)
    if sql is not None:
        _check_sql_arg(sql)
    claims = list(claims or [])
    if len(claims) > MAX_CLAIMS or not all(isinstance(c, str) and len(c) <= 1000 for c in claims):
        raise ValueError(f"claims must be at most {MAX_CLAIMS} strings of at most 1000 characters")
    declared = None
    contradictions: list[str] = []
    if metric_call is not None:
        declared = normalize_metric_call(metric_call)
        _validate_call_on_build(ctx, declared)            # unknown metric/dim/months -> invalid_argument
    derived = None
    if derive is not None:
        if declared is None:
            raise ValueError("derive needs a metric_call: the step is computed from that call's governed rows")
        try:
            derived = normalize_derive(derive, declared["metric"], model.spec, declared)
        except DeriveError as exc:
            raise ValueError(str(exc)) from None
        if derived and derived.get("divide_by"):           # the denominator is a governed call of its own
            _validate_call_on_build(ctx, normalize_metric_call({"metric": derived["divide_by"]["metric"],
                                                                "dimensions": declared["dimensions"],
                                                                "months": declared["months"],
                                                                "filters": derived["divide_by"]["filters"]}))
    text = " ".join([question, *claims])
    reasons: list[str] = []
    warnings: list[str] = []
    flagged = False

    # ---- 1. static analysis -------------------------------------------------------------
    if sql is not None:
        rep = analyze_sql(sql, catalog(ctx))
        static_source = "submitted_sql"
    elif declared is not None:
        rep = analyze_sql(compile_metric(model, **declared), catalog(ctx))
        static_source = "compiled_metric_sql"
    else:
        rep = None
        static_source = None
    if rep is None:
        static = {"outcome": "INCONCLUSIVE", "reason": "no SQL and no metric_call: nothing to analyse"}
    else:
        static = {"outcome": "PASS" if rep.ok else "FAIL", "source": static_source, "tier": rep.tier,
                  "tables": rep.tables, "violations": rep.violations, "warnings": rep.warnings}
        warnings += rep.warnings
        if not rep.ok:
            flagged = True
            reasons.append(f"static analysis refused the SQL: {'; '.join(rep.violations[:3])}")

    # ---- 3a. reconciliation target (declared, or inferred as a safety net) -----------------
    mentioned = match_governed_metrics(model, text)
    call, call_source, infer_notes = declared, "declared" if declared else None, []
    if declared is None:
        call, infer_notes = infer_metric_call(model, text, rows)
        call_source = "inferred" if call else None
        if call is not None:
            try:
                _validate_call_on_build(ctx, call)
            except ValueError as exc:
                call, call_source = None, None
                infer_notes.append(f"inferred metric_call is not computable on this build: {exc}")
    else:
        # the declared claim must be the question asked, not merely a claim the rows satisfy; and the
        # submitted rows must have the shape the question asks for (one value or one row per month, CNY or fen)
        contradictions = claim_contradictions(model, question, declared)
        contradictions += shape_contradictions(question, declared, derived, model.metrics[declared["metric"]].unit,
                                               months_from_text(question)[0])
        warnings += contradictions

    # ---- 2. honesty: re-execute ---------------------------------------------------------
    governed = None
    shaped, shape_notes, shape_error, ungoverned = None, [], None, []
    if call is not None and derived is not None:
        governed = _derived_check(ctx, model, call, derived, rows)
    elif call is not None:
        try:
            shaped, shape_notes = shape_for_metric(rows, call, call["metric"])
        except _ShapeError as exc:
            shape_error = str(exc)
            ungoverned = ungoverned_keys(rows, call, model.metrics[call["metric"]].dimensions)
            if ungoverned:      # the rows answer a breakdown no governed metric has: unknown, not wrong
                shape_error = (f"the rows are keyed by {ungoverned}, which {call['metric']} has no dimension for "
                               f"(it has {['month', *model.metrics[call['metric']].dimensions]}); no governed metric "
                               f"covers this breakdown")
        if shaped is not None:
            governed = reconcile_answer_to_metric(ctx.con, model, shaped, call["metric"], call["dimensions"],
                                                  call["months"], call["filters"],
                                                  rel_tol=_tolerance(model, call["metric"]))
    if sql is not None:
        if rep is not None and not rep.ok:
            reexec = {"outcome": "INCONCLUSIVE", "reason": "not executed: static analysis refused the SQL"}
        else:
            reexec = _reexecute(ctx, sql, rows)
    elif declared is not None:
        if governed is not None:
            reexec = {**_summ(governed), "source": "governed_metric_call" + ("+derive" if derived else "")}
        elif ungoverned:
            reexec = {"outcome": "INCONCLUSIVE", "reason": shape_error}
        else:
            reexec = {"outcome": "FAIL", "reason": f"answer does not match its declared claim: {shape_error}"}
    else:
        reexec = {"outcome": "FAIL", "reason": "no SQL and no metric_call: the numbers cannot be reproduced"}
    if reexec["outcome"] in ("FAIL", "ERROR"):
        flagged = True
        reasons.append(f"re-execution {reexec['outcome']}: {reexec.get('reason') or 'answer differs from its query'}")
    elif reexec["outcome"] == "INCONCLUSIVE" and sql is not None and rep is not None and rep.ok:
        reasons.append(f"re-execution INCONCLUSIVE: {reexec.get('reason')}")

    # ---- 3b. reconciliation -------------------------------------------------------------
    recon: dict = {"metric_call": call, "metric_call_source": call_source, "mentioned_metrics": mentioned}
    if contradictions:
        recon["contradictions"] = contradictions
    finance = None
    if call is None:
        recon["outcome"] = "INCONCLUSIVE"
        recon["reason"] = "no governed metric or official record covers this answer"
        reasons.append("exploratory: no governed metric or official record covers this answer (needs human review)")
        if mentioned:
            reasons.append(f"the question mentions governed metric(s) {mentioned}: declare metric_call so the answer "
                           f"can be reconciled" + (f" ({'; '.join(infer_notes)})" if infer_notes else ""))
    elif shape_error is not None and ungoverned:
        recon["outcome"] = "INCONCLUSIVE"
        recon["reason"] = shape_error
        reasons.append(f"exploratory: {shape_error} (needs human review)")
    elif shape_error is not None:
        if call_source == "declared":       # the agent's own claim and rows disagree: a verdict, not an error
            recon["outcome"] = "FAIL"
            recon["reason"] = f"answer does not match its declared claim: {shape_error}"
            flagged = True
            reasons.append(recon["reason"])
        else:
            recon["outcome"] = "INCONCLUSIVE"
            recon["reason"] = f"answer rows do not fit the inferred metric_call: {shape_error}"
            reasons.append(f"exploratory: {recon['reason']}; declare metric_call to have it reconciled")
    else:
        recon["governed"] = _summ(governed)
        if derived is not None:
            recon["derive"] = derived
        finance = (finance_close_check(ctx.con, shaped, call, model)
                   if governed["outcome"] == "PASS" and derived is None else None)
        if finance is not None:
            recon["finance_close"] = _summ(finance) if finance["outcome"] != "NOT_APPLICABLE" else finance
        recon["notes"] = shape_notes
        g = governed["outcome"]
        f = finance["outcome"] if finance else None
        if g == "FAIL" or f == "FAIL":
            flagged = True
            recon["outcome"] = "FAIL"
            what = "governed metric" if g == "FAIL" else "Finance close"
            reasons.append(f"answer disagrees with the {what} for {call['metric']} "
                           f"({call_source} metric_call): {_mismatch_text(governed if g == 'FAIL' else finance)}")
        elif g == "ERROR":                  # the harness could not compute its oracle (answer shape is checked above)
            recon["outcome"] = "ERROR" if call_source == "declared" else "INCONCLUSIVE"
            if call_source == "declared":
                flagged = True
            reasons.append(f"reconciliation could not compare the answer: {governed.get('error')}")
        elif g == "INCONCLUSIVE":
            recon["outcome"] = "INCONCLUSIVE"
            reasons.append(governed.get("reason") or "the governed metric returned no rows: nothing to reconcile against")
        elif call_source == "declared" and contradictions:
            recon["outcome"] = "INCONCLUSIVE"
            reasons.append("the numbers reconcile with the declared metric_call, but it does not answer the question "
                           "as asked: " + "; ".join(contradictions) + " (declare the metric_call the question asks "
                           "for, or have a human confirm the reading)")
        elif call_source == "declared":
            recon["outcome"] = "PASS"
            reasons.append(f"reconciles with governed metric {call['metric']}"
                           + (f" after the declared step {_derive_text(derived)}" if derived else "")
                           + (" and the Finance close" if f == "PASS" else ""))
        else:
            recon["outcome"] = "INCONCLUSIVE"
            recon["inferred_outcome"] = "PASS"
            reasons.append(f"numbers match governed metric {call['metric']} under an inferred reading of the "
                           f"question; declare metric_call to have the answer verified")

    if flagged:
        tier = "FLAGGED"
    elif static["outcome"] == "PASS" and reexec["outcome"] == "PASS" and recon["outcome"] == "PASS":
        tier = "VERIFIED"
    else:
        tier = "CONSISTENT"
    label, label_notes = trust_label(tier, finance, {r["month"] for r in shaped or ()})

    # ---- evidence + gate ------------------------------------------------------------------
    subject = {"kind": "answer", "question": question.strip(), "answer_digest": digest(rows),
               "sql_digest": digest(sql) if sql is not None else None, "metric_call": jsonable(declared),
               "derive": jsonable(derived),
               "claims_digest": digest(claims), "build_id": ctx.build_id, "semantic_digest": model.digest}
    store = EvidenceStore(ctx.ws)
    common = {"check_version": CHECK_VERSION, "stage": STAGE, "subject": subject}
    records = [
        store.record(check_id="answer.sql_static", outcome=static["outcome"], metrics=jsonable(static),
                     producer="verifier:sql_static", **common,
                     limitations=["sqlglot-based static analysis; the read-only, locked connection is the "
                                  "enforcement layer"]),
        store.record(check_id="answer.reexecution", outcome=reexec["outcome"], metrics=jsonable(reexec),
                     producer="verifier:reconcile", **common,
                     limitations=[f"re-executed on build {ctx.build_id}"]),
        store.record(check_id="answer.reconcile", outcome=recon["outcome"], metrics=jsonable(recon),
                     producer="verifier:reconcile", **common,
                     limitations=["the governed metric definition is the oracle: a wrong definition would be "
                                  "reproduced, not caught"]
                     + (["metric_call inferred from the question text (cannot verify, only flag)"]
                        if call_source == "inferred" else [])),
    ]
    decision = decide(ctx.ws, STAGE, subject, requested_by=ctx.principal)
    warnings = list(dict.fromkeys(warnings))
    cert = answer_certificate(ctx, model, question=question.strip(), claims=claims, rows=rows, sql=sql, call=call,
                              call_source=call_source, mentioned=mentioned,
                              notes=[*infer_notes, *shape_notes,
                                     *(f"metric_call contradicts the question: {c}" for c in contradictions)],
                              records=records, finance=finance, tier=tier, label=label, label_notes=label_notes,
                              decision=decision, reasons=reasons, warnings=warnings)
    append_jsonl(ctx.ws.registry / CERTIFICATES_LOG, cert)
    return {"tier": tier, "trust_label": label, "allowed_use": ALLOWED_USE[label], "reasons": reasons,
            "warnings": warnings, "evidence_ids": [r.evidence_id for r in records], "gate": decision.to_dict(),
            "checks": {"sql_static": static, "reexecution": _summ(reexec), "reconcile": recon},
            "subject_digest": digest(subject), "certificate": cert}


# =========================================================================== second opinion (T2, H v0.4)
STAGE_INTERNAL = "answer_internal"
CROSS_CHECK_PRODUCER = "verifier:cross_check"
SECOND_OPINION_REL_TOL = 5e-4      # the census grader's tolerance: 0.05% relative, or half a unit in the last place
MIN_SIG_DIGITS = 3
KEY_HINTS = frozenset({"month", "year", "quarter", "week", "date", "day", "rank"})


def _number(v) -> float | None:
    """A reported value as a float; None for anything that is not a finite number (a key, a null)."""
    if isinstance(v, bool) or not isinstance(v, (int, float, Decimal, np.integer, np.floating)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _key_text(v) -> str:
    """A key value as comparable text: dates in ISO form, whole numbers without '.0', case and spaces ignored."""
    if v is None:
        return ""
    if isinstance(v, (date, datetime)):
        v = iso_value(v)
    elif _number(v) is not None and float(v).is_integer():
        v = int(float(v))
    return str(v).strip().casefold()


def _places(v: float) -> tuple[int, int]:
    """(decimal places, significant digits) of a reported number, as written (611761.0 -> 0, 6)."""
    _, digits, exp = Decimal(repr(v)).normalize().as_tuple()
    return max(0, -exp), (len(digits) if any(digits) else 1)


def values_close(a: float, b: float, rel_tol: float = SECOND_OPINION_REL_TOL) -> bool:
    """Two reported values within one tolerance of each other, symmetric: rel_tol of the larger, or half a
    unit in the coarser one's last place; both with at least 3 significant digits unless they are equal.
    Two whole numbers must be equal: counts, sums in minor units and identifiers have no rounding."""
    if a == b:
        return True
    if float(a).is_integer() and float(b).is_integer():
        return False
    (da, sa), (db, sb) = _places(a), _places(b)
    if min(sa, sb) < MIN_SIG_DIGITS:
        return False
    return abs(a - b) <= max(rel_tol * max(abs(a), abs(b)), 0.5 * 10.0 ** -min(da, db))


def compare_answers(rows_a: list[dict], rows_b: list[dict], rel_tol: float = SECOND_OPINION_REL_TOL,
                    key_hints=KEY_HINTS) -> dict:
    """Do two independently produced answers state the same numbers? Column names are compared ignoring
    case; a column is a key when any of its values is not a number or its name is a key hint (month,
    year, a dimension); rows pair up by key and every other value must be `values_close`. The first answer
    is the one delivered, so every one of its columns must be in the second; a column only the second has
    is set aside when it is a key with a single value (a label such as the month asked about) or an extra
    value column (a component, a helper count). Any other difference in columns: not comparable.
    PASS, FAIL (with the differing keys and values) or INCONCLUSIVE (the answers are not comparable)."""
    a = [{str(k).strip().lower(): v for k, v in r.items()} for r in rows_a]
    b = [{str(k).strip().lower(): v for k, v in r.items()} for r in rows_b]
    if not a or not b:
        return {"outcome": "INCONCLUSIVE", "reason": "an answer has no rows"}
    if any(len(n) != len(r) for n, r in zip(a + b, [*rows_a, *rows_b])):
        return {"outcome": "INCONCLUSIVE", "reason": "an answer has columns whose names differ only in case"}
    hints = {h.lower() for h in key_hints}
    cols_a, cols_b = set().union(*(r.keys() for r in a)), set().union(*(r.keys() for r in b))
    if cols_a - cols_b:
        return {"outcome": "INCONCLUSIVE", "reason": f"the answers use different columns: the second does not "
                f"state {sorted(cols_a - cols_b)} ({sorted(cols_a)} vs {sorted(cols_b)})"}
    set_aside = sorted(cols_b - cols_a)
    for c in set_aside:
        if (c in hints or any(_number(r.get(c)) is None for r in b)) and len({_key_text(r.get(c)) for r in b}) > 1:
            return {"outcome": "INCONCLUSIVE", "reason": f"the answers use different columns: the second splits "
                    f"its rows by {c!r} ({sorted(cols_a)} vs {sorted(cols_b)})"}
    b = [{k: v for k, v in r.items() if k in cols_a} for r in b]
    keys = sorted(c for c in cols_a if c in hints or any(_number(r.get(c)) is None for r in a + b))
    vals = sorted(cols_a - set(keys))
    if not vals:
        return {"outcome": "INCONCLUSIVE", "reason": "no numeric column to compare"}

    def index(rows):
        out = {}
        for r in rows:
            k = tuple(_key_text(r.get(c)) for c in keys)
            if k in out:
                return None
            out[k] = r
        return out
    ia, ib = index(a), index(b)
    if ia is None or ib is None:
        return {"outcome": "INCONCLUSIVE", "reason": f"an answer repeats a key over columns {keys}"}
    only_a, only_b = sorted(set(ia) - set(ib)), sorted(set(ib) - set(ia))
    mism = [{"key": dict(zip(keys, k)), "column": c, "first": ia[k][c], "second": ib[k][c]}
            for k in sorted(set(ia) & set(ib)) for c in vals
            if not (_number(ia[k][c]) is not None and _number(ib[k][c]) is not None
                    and values_close(_number(ia[k][c]), _number(ib[k][c]), rel_tol))]
    ok = not (only_a or only_b or mism)
    return {"outcome": "PASS" if ok else "FAIL", "key_columns": keys, "value_columns": vals,
            "second_columns_set_aside": set_aside, "n_rows": len(ia),
            "rel_tol": rel_tol, "n_mismatches": len(mism), "mismatches": mism[:10],
            "only_in_first": [dict(zip(keys, k)) for k in only_a[:10]],
            "only_in_second": [dict(zip(keys, k)) for k in only_b[:10]]}


def second_opinion_problems(cert: dict) -> list[str]:
    """Why an answer cannot be lifted by a second opinion (empty: it can). Its own checks must have passed:
    static analysis and re-execution PASS; an answer the governed metric contradicts (reconcile FAIL) or that
    the harness could not reconcile (ERROR) stays held, however many models agree with it."""
    out = {c["check_id"]: c["outcome"] for c in cert["checks"]}
    problems = [f"{cid} is {out.get(cid)}, not PASS" for cid in ("answer.sql_static", "answer.reexecution")
                if out.get(cid) != "PASS"]
    if out.get("answer.reconcile") in ("FAIL", "ERROR"):
        problems.append(f"answer.reconcile is {out['answer.reconcile']}: the governed check contradicts the answer")
    if cert["tier"] == "FLAGGED":
        problems.append("the answer is FLAGGED")
    return problems


def cross_check_answers(ctx: ToolContext, first: dict, second: dict) -> dict:
    """Host-side second opinion (trust label T2, docs/DESIGN.zh-CN.md §5.2: a non-certified question answered
    by two independent paths that agree). `first` and `second` are {"certificate_id", "answer_rows",
    "model", "provider"}: two answers to the same question, each submitted with submit_answer by its own
    principal on the same build, by models of two different providers (E1b: a second run of the same agent
    repeats its errors). Agreement records answer.cross_check PASS on the FIRST answer's subject (which
    includes its rows digest) and asks the answer_internal gate (internal decision support; answer_publish is
    unchanged). Lifted: answers the gate could not verify (reconcile INCONCLUSIVE), never an answer whose
    own checks failed or that the governed metric contradicts.

    Not an agent tool: it writes the registry, which agents cannot. In this reference implementation the
    principals, models and providers are the host's claims and the host must keep the second agent blind to
    the first answer (production: workload identities, as for every producer). Agreement of two models is
    not truth: a question both misread passes."""
    certs = {c["certificate_id"]: c for c in read_jsonl(ctx.ws.registry / CERTIFICATES_LOG)}
    pair = []
    for side, x in (("first", first), ("second", second)):
        cert = certs.get(x.get("certificate_id"))
        if cert is None:
            raise ValueError(f"{side}: no certificate {x.get('certificate_id')!r} in this workspace")
        if cert["producer"] != "dsharness.tools.submit_answer":
            raise ValueError(f"{side}: {cert['certificate_id']} is not an answer certificate from submit_answer")
        if digest(x.get("answer_rows")) != cert["answer"]["rows_digest"]:
            raise ValueError(f"{side}: answer_rows are not the rows certificate {cert['certificate_id']} was issued for")
        pair.append(cert)
    ca, cb = pair
    if ca["tier"] == "VERIFIED":
        raise ValueError(f"{ca['certificate_id']} is already VERIFIED: publish it through answer_publish")
    ma, mb, pa, pb = (str(x.get(k) or "").strip() for k in ("model", "provider") for x in (first, second))
    problems = [f"first answer: {p}" for p in second_opinion_problems(ca)]
    problems += [f"second answer: {p}" for p in second_opinion_problems(cb)]
    if " ".join(ca["question"].split()).casefold() != " ".join(cb["question"].split()).casefold():
        problems.append("the two answers are to different questions")
    if ca["build"]["build_id"] != cb["build"]["build_id"]:
        problems.append(f"the answers come from different builds ({ca['build']['build_id']} vs {cb['build']['build_id']})")
    if ca["requested_by"].casefold() == cb["requested_by"].casefold():
        problems.append(f"both answers were submitted by {ca['requested_by']}: a second opinion needs a second agent")
    if not (ma and mb and pa and pb) or pa.casefold() == pb.casefold():
        problems.append(f"a second opinion needs models from two named, different providers "
                        f"(got {pa!r}/{ma!r} and {pb!r}/{mb!r})")
    hints = KEY_HINTS | set(semantic_model(ctx).dimensions)
    comparison = (compare_answers(first["answer_rows"], second["answer_rows"], SECOND_OPINION_REL_TOL, hints)
                  if not problems else {"outcome": "INCONCLUSIVE", "reason": "not compared: " + "; ".join(problems)})
    store = EvidenceStore(ctx.ws)
    first_ev = {c["check_id"]: c["evidence_id"] for c in ca["checks"]}
    subject = next(r.subject for r in store.all() if r.evidence_id == first_ev["answer.reconcile"])
    rec = store.record(check_id="answer.cross_check", check_version=CHECK_VERSION, stage=STAGE_INTERNAL,
                       subject=subject, producer=CROSS_CHECK_PRODUCER,
                       # a disagreement is not a verdict on this answer (either may be right): HOLD, not REJECT
                       outcome="PASS" if comparison["outcome"] == "PASS" else "INCONCLUSIVE",
                       metrics=jsonable({**comparison, "second_certificate_id": cb["certificate_id"],
                                         "models": [ma, mb], "problems": problems}),
                       limitations=["two models agreeing is not truth: a question both misread passes",
                                    "the model names are the caller's claim"])
    decision = decide(ctx.ws, STAGE_INTERNAL, subject, requested_by=ctx.principal)
    label = "T2" if decision.verdict == "ALLOW" else ca["trust_label"]
    if comparison["outcome"] == "PASS" and decision.verdict == "ALLOW":
        why = f"cross-checked: {mb} ({pb}) answered the same question independently with the same numbers"
    elif comparison["outcome"] == "PASS":
        why = (f"the second opinion ({mb}) agrees, but the answer_internal gate says {decision.verdict}: "
               + "; ".join(decision.reasons[:3]))
    elif comparison["outcome"] == "FAIL":
        why = (f"the second opinion ({mb}) disagrees: {comparison['n_mismatches']} value(s) differ, "
               f"{len(comparison['only_in_first']) + len(comparison['only_in_second'])} row(s) in one answer only; "
               f"a person should compare the two answers")
    else:
        why = f"no second opinion: {comparison['reason']}"
    body = {k: v for k, v in ca.items() if k not in ("certificate_id", *CALLER_FIELDS)}
    body.update({
        "issued_at": iso_utc(utcnow()), "producer": "dsharness.tools.cross_check_answers",
        "checks": [*ca["checks"], {"check_id": "answer.cross_check", "outcome": rec.outcome,
                                   "evidence_id": rec.evidence_id, "producer": rec.producer}],
        "trust_label": label, "allowed_use": ALLOWED_USE[label],
        "gate": {"stage": STAGE_INTERNAL, "verdict": decision.verdict, "subject_digest": decision.subject_digest,
                 "policy_digest": decision.policy_digest},
        "reasons": [why, *ca["reasons"]],
        "caveats": list(dict.fromkeys([*ca["caveats"], *(["T2: two models agree; not a governed or reconciled "
                                                          "number"] if label == "T2" else [])])),
        "second_opinion": {"certificate_id": cb["certificate_id"], "second_requested_by": cb["requested_by"],
                           "first_model": ma or None, "first_provider": pa or None,
                           "second_model": mb or None, "second_provider": pb or None,
                           "outcome": comparison["outcome"], "evidence_id": rec.evidence_id,
                           "requested_by": ctx.principal},
    })
    body = scrub_pii(jsonable(body))
    cert = {"certificate_id": certificate_id(body), **body, "harness": {"name": None, "version": None},
            "model": {"provider": pa or None, "name": ma or None},
            "cost": {"llm_usd": None, "input_tokens": None, "output_tokens": None, "warehouse_bytes_scanned": None}}
    append_jsonl(ctx.ws.registry / CERTIFICATES_LOG, cert)
    return {"outcome": comparison["outcome"], "trust_label": label, "allowed_use": ALLOWED_USE[label],
            "gate": decision.to_dict(), "comparison": comparison, "problems": problems,
            "evidence_id": rec.evidence_id, "certificate": cert}



# =========================================================================== trust label + certificate
def trust_label(tier: str, finance: dict | None, answer_months: set[str]) -> tuple[str | None, list[str]]:
    """User-facing label for a tier (T0..T3, None for FLAGGED) and why (docs/DESIGN.zh-CN.md §5.2)."""
    if tier == "FLAGGED":
        return None, ["FLAGGED: no trust label; do not use these numbers"]
    if tier != "VERIFIED":
        return "T3", ["exploratory: reproducible, but no governed metric or independent record verifies it"]
    if finance is not None and finance.get("outcome") == "PASS":
        open_months = sorted(answer_months - set(finance.get("months_checked") or ()))
        if not open_months:
            return "T0", [f"reconciled exactly to the Finance close for {sorted(answer_months)}"]
        return "T1", [f"provisional: Finance has not closed {open_months}; re-scored when the books close"]
    why = (finance or {}).get("error") or (finance or {}).get("reason") or "no independent record covers it"
    return "T1", [f"provisional: {why}; the governed definition is the only oracle"]


def certificate_id(cert: dict) -> str:
    """Content id over the harness-issued body (caller-filled harness/model/cost fields excluded)."""
    return "ac-" + digest({k: v for k, v in cert.items() if k not in ("certificate_id", *CALLER_FIELDS)})[:24]


def answer_certificate(ctx: ToolContext, model: SemanticModel, *, question: str, claims: list[str],
                       rows: list[dict], sql: str | None, call: dict | None, call_source: str | None,
                       mentioned: list[str], notes: list[str], records: list, finance: dict | None, tier: str,
                       label: str | None, label_notes: list[str], decision, reasons: list[str],
                       warnings: list[str]) -> dict:
    """What was asked, how it was resolved, what was checked and how far the answer can be trusted
    (docs/answer_certificate.schema.json). The certificate restates evidence; it is not evidence."""
    report = load_report(ctx.ws, ctx.build_id)
    metric = None
    caveats = list(label_notes)
    if call is not None:
        m = model.metrics[call["metric"]]
        metric = {"name": m.name, "type": m.type, "owner": m.owner,
                  "definition_digest": metric_definition_digest(model, m.name),
                  "independent_record": m.independent_record or None}
        caveats += m.caveats
    question_months, _ = months_from_text(question)
    reconcile_rec = next(r for r in records if r.check_id == "answer.reconcile")
    checks = [{"check_id": r.check_id, "outcome": r.outcome, "evidence_id": r.evidence_id, "producer": r.producer}
              for r in records]
    checks.append({"check_id": "answer.finance_close", "outcome": finance["outcome"] if finance else "NOT_RUN",
                   "evidence_id": reconcile_rec.evidence_id, "producer": reconcile_rec.producer,
                   "months_checked": list((finance or {}).get("months_checked") or [])})
    body = {
        "schema": CERTIFICATE_SCHEMA, "issued_at": iso_utc(utcnow()), "ai_generated": True,
        "producer": "dsharness.tools.submit_answer", "requested_by": ctx.principal, "task_id": ctx.task_id,
        "question": question, "claims": list(claims),
        "answer": {"n_rows": len(rows), "columns": sorted(set().union(*(r.keys() for r in rows))) if rows else [],
                   "rows_digest": digest(rows)},
        "term_resolution": {"mentioned_metrics": list(mentioned), "metric_call_source": call_source,
                            "question_months": list(question_months) if question_months else None,
                            "notes": list(notes)},
        "metric_call": jsonable(call), "metric": metric, "semantic_digest": model.digest,
        "sql": {"submitted_digest": digest(sql) if sql is not None else None,
                "compiled_metric_digest": digest(compile_metric(model, **call)) if call is not None else None},
        "build": {"build_id": ctx.build_id, "cutoff": report["cutoff"], "status": report["status"],
                  "pipeline_digest": report["pipeline_digest"], "source_digest": report["source_digest"]},
        "checks": checks, "tier": tier, "trust_label": label, "allowed_use": ALLOWED_USE[label],
        "gate": {"stage": STAGE, "verdict": decision.verdict, "subject_digest": decision.subject_digest,
                 "policy_digest": decision.policy_digest},
        "reasons": list(reasons), "caveats": list(dict.fromkeys([*caveats, *warnings])),
    }
    body = scrub_pii(jsonable(body))
    return {"certificate_id": certificate_id(body), **body,
            "harness": {"name": None, "version": None},
            "model": {"provider": None, "name": None},
            "cost": {"llm_usd": None, "input_tokens": None, "output_tokens": None, "warehouse_bytes_scanned": None}}


def _derived_check(ctx: ToolContext, model: SemanticModel, call: dict, derived: dict, rows: list[dict]) -> dict:
    """Recompute the declared step from the governed rows of `call` and compare the submitted rows."""
    try:
        q = lambda metric: run_with_timeout(  # noqa: E731
            ctx.con, QUERY_TIMEOUT_S, lambda: _query_metric(ctx.con, model, metric, call["dimensions"], call["months"],
                                                            call["filters"]))
        df = q(call["metric"])
        if df.empty:
            return {"outcome": "INCONCLUSIVE", "reason": "the governed metric returned no rows: nothing to derive from"}
        parts = None
        if derived["months"] == "pool":
            raw = model.spec["metrics"][call["metric"]]
            parts = (q(raw["numerator"]), q(raw["denominator"]))
        period = lambda metric, months, filters: run_with_timeout(  # noqa: E731
            ctx.con, QUERY_TIMEOUT_S, lambda: _query_metric_period(ctx.con, model, metric, call["dimensions"], months,
                                                                   filters))
        inputs: dict = {}
        if derived["months"] == "total":
            inputs["period"] = period(call["metric"], call["months"], call["filters"])
        if derived.get("windows"):
            inputs["windows"] = [period(call["metric"], w, call["filters"]) for w in derived["windows"]]
        if derived.get("divide_by"):
            den = derived["divide_by"]
            inputs["den"] = (period(den["metric"], call["months"], den["filters"]) if derived["months"] == "total" else
                             run_with_timeout(ctx.con, QUERY_TIMEOUT_S, lambda: _query_metric(
                                 ctx.con, model, den["metric"], call["dimensions"], call["months"], den["filters"])))
        expected, keys = apply_derive(df, derived, call["dimensions"], parts=parts, months=call["months"], inputs=inputs)
    except DeriveError as exc:
        return {"outcome": "INCONCLUSIVE", "reason": f"the declared step cannot be computed: {exc}"}
    except (duckdb.Error, ValueError) as exc:
        return {"outcome": "ERROR", "error": str(exc).splitlines()[0][:300]}
    if expected.empty:
        return {"outcome": "INCONCLUSIVE", "reason": "the declared step leaves no rows"}
    res = compare_derived(rows, expected, keys)
    return {**res, "source": "governed_metric_call+derive"}


def _derive_text(d: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in d.items() if v is not None)


def _reexecute(ctx: ToolContext, sql: str, rows: list[dict]) -> dict:
    try:
        cols, qrows, truncated = _execute(ctx, sql, MAX_REEXEC_ROWS)
    except duckdb.InterruptException:
        return {"outcome": "FAIL", "reason": f"the SQL did not finish within {QUERY_TIMEOUT_S:.0f}s"}
    except duckdb.Error as exc:
        return {"outcome": "FAIL", "reason": f"the SQL fails to execute: {str(exc).splitlines()[0][:300]}"}
    if truncated:
        return {"outcome": "FAIL", "reason": f"the SQL returns more than {MAX_REEXEC_ROWS} rows: not an answer"}
    return compare_row_multisets(rows, cols, qrows)


def _mismatch_text(res: dict) -> str:
    parts = []
    if res.get("n_mismatches"):
        ex = res["mismatches"][0]
        keys = {k: v for k, v in ex.items() if k not in ("actual", "expected", "abs_diff")}
        parts.append(f"{res['n_mismatches']} value(s) differ, e.g. {keys}: answer {ex['actual']} vs "
                     f"expected {ex['expected']}")
    if res.get("n_missing_keys"):
        parts.append(f"{res['n_missing_keys']} expected row(s) missing, e.g. {res['missing_keys'][0]}")
    if res.get("n_extra_keys"):
        parts.append(f"{res['n_extra_keys']} unexpected row(s), e.g. {res['extra_keys'][0]}")
    return "; ".join(parts) or str(res.get("error") or res.get("reason") or "mismatch")
