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

from ..common import append_jsonl, digest, utcnow
from ..context.catalog import Catalog, describe_table as _describe_table, load_catalog, search_catalog as _search
from ..context.semantic import (SemanticModel, compile_metric, coverage, describe_metric as _describe_metric,
                                list_metrics as _list_metrics, load_semantic_model, load_verified_queries,
                                metric_definition_digest, query_metric as _query_metric)
from ..evidence import EvidenceStore, decide
from ..evidence.attest import CERTIFICATE_LOG
from ..evidence.records import iso_utc
from ..lake.build import load_report
from ..verify.reconcile import compare_frames, reconcile_answer_to_metric
from ..verify.sql_static import analyze_sql
from .context import ToolContext, ToolError, governed_tool, iso_value, jsonable, run_with_timeout, scrub_pii

CHECK_VERSION = "1"
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
CERTIFICATE_SCHEMA = "https://dsharness.dev/schemas/answer-certificate/v1"
CERTIFICATES_LOG = CERTIFICATE_LOG                 # registry/answer_certificates.jsonl, append-only
INDEPENDENT_RECORDS = {"net_revenue_fen": "silver.finance_closed_revenue"}
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
def semantic_model() -> SemanticModel:
    return load_semantic_model()


@functools.cache
def catalog() -> Catalog:
    return load_catalog()


# =========================================================================== context tools
@governed_tool("list_metrics")
def list_metrics(ctx: ToolContext) -> dict:
    """Governed metrics, the build's month coverage and worked examples (runtime-context split only)."""
    model = semantic_model()
    first, last = coverage(ctx.con, model)
    examples = [{"question": q["question"], "metric_call": q["metric_call"], "sql": q["sql"], "notes": q["notes"]}
                for q in load_verified_queries("context", model=model)]
    return {"metrics": _list_metrics(model), "coverage": {"first_month": first, "last_month": last},
            "build_id": ctx.build_id, "cutoff": ctx.cutoff, "semantic_digest": model.digest,
            "examples": examples,
            "guidance": ["Answer with query_metric whenever a governed metric covers the question.",
                         "Submit with submit_answer and declare metric_call = {metric, dimensions, months, filters}.",
                         "The metric_call must be the question asked: its metric, months containing the months "
                         "it names, the values it names as filters, a dimension for every 'by X'. A call that "
                         "contradicts the question is held for human review, however well it reconciles.",
                         "run_sql results are uncertified; use them only when no governed metric fits."]}


@governed_tool("describe_metric")
def describe_metric(ctx: ToolContext, name: str) -> dict:
    return _describe_metric(semantic_model(), name)


@governed_tool("query_metric")
def query_metric(ctx: ToolContext, metric: str, dimensions: list[str] | None = None,
                 months: list[str] | None = None, filters: dict | None = None) -> dict:
    """Governed metric rows (month, <dimensions...>, value). `months` is an inclusive pair."""
    model = semantic_model()
    call = normalize_metric_call({"metric": metric, "dimensions": dimensions or [], "months": months,
                                  "filters": filters})
    sql = compile_metric(model, **call)
    df = run_with_timeout(ctx.con, QUERY_TIMEOUT_S, lambda: _query_metric(ctx.con, model, **call))
    rows = df.to_dict("records")
    return {"rows": rows[:ctx.max_rows], "n_rows": len(rows), "truncated": len(rows) > ctx.max_rows,
            "columns": list(df.columns), "metric_call": call, "tier": "governed", "sql": sql,
            "semantic_digest": model.digest, "build_id": ctx.build_id,
            "caveats": list(model.metrics[call["metric"]].caveats),
            "submit_hint": "Report these rows unchanged (full precision) and pass this metric_call to submit_answer."}


@governed_tool("search_catalog")
def search_catalog(ctx: ToolContext, query: str) -> dict:
    hits = _search(catalog(), query)
    return {"results": hits[:20], "n_results": len(hits),
            "note": "certified = silver.* (governed); staging/uncertified tables carry known defects."}


@governed_tool("describe_table")
def describe_table(ctx: ToolContext, table: str) -> dict:
    return _describe_table(catalog(), table)


@governed_tool("run_sql")
def run_sql(ctx: ToolContext, sql: str) -> dict:
    """Statically analysed, read-only SQL. Violations are refused; results are always uncertified."""
    _check_sql_arg(sql)
    rep = analyze_sql(sql, catalog())
    if not rep.ok:
        raise ToolError("refused", "SQL refused by static analysis", violations=rep.violations,
                        warnings=rep.warnings)
    cols, rows, truncated = _execute(ctx, sql, ctx.max_rows)
    pii = [c for c in cols if c.lower() in catalog().pii_columns]
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
    model = semantic_model()
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
# Phrases that name a governed quantity. Every metric in the semantic model must appear here
# (enforced by a test), so adding a metric without vocabulary fails loudly. Program metrics need the
# retention-program context somewhere in the text (lookahead), so a plain churn question keeps
# meaning customer churn.
_PROGRAM = r"(?=[\s\S]*\b(?:program(?:me)?s?|campaigns?|enrol\w*|treatment|control (?:arm|group)|arms?)\b)"
GOVERNED_TERMS: dict[str, str] = {
    "net_revenue_fen": r"\brevenues?\b|\brefund(?:s|ed)?\b",
    "addon_units": r"\badd[- ]?ons?\b",
    "active_subscribers_eom": r"\bactive (?:subscribers?|customers?|subscriptions?|users?)\b",
    "active_subscribers_bom": r"\bactive (?:subscribers?|customers?|subscriptions?|users?)\b",
    "churned_customers": r"\bchurn(?:ed|s|ing)?\b",
    "churn_rate": r"\bchurn(?:ed)?[- ]rates?\b|\brate of churn\b",
    "arpu_fen": r"\barpu\b|\baverage revenue per (?:user|subscriber|customer|account)\b",
    "avg_csat": r"\bcsat\b|\bcustomer satisfaction\b|\bsatisfaction scores?\b",
    "program_enrolled": r"\benrol\w*",
    "program_churned": _PROGRAM + r"[\s\S]*\bchurn(?:ed|s|ing)?\b",
    "program_churn_rate": _PROGRAM + r"[\s\S]*(?:\bchurn(?:ed)?[- ]rates?\b|\brate of churn\b)",
}
_SUPPRESSES = {"churn_rate": {"churned_customers"}, "arpu_fen": {"net_revenue_fen"},
               "program_churned": {"churned_customers", "program_enrolled"},
               "program_churn_rate": {"churn_rate", "churned_customers", "program_churned", "program_enrolled"}}
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


def match_governed_metrics(model: SemanticModel, text: str) -> list[str]:
    """Governed metrics a question or claim talks about (most specific reading wins)."""
    low = text.lower()
    hits = {m for m, pat in GOVERNED_TERMS.items() if m in model.metrics and re.search(pat, low)}
    for m, drop in _SUPPRESSES.items():
        if m in hits:
            hits -= drop
    if {"active_subscribers_eom", "active_subscribers_bom"} <= hits:
        if re.search(r"\b(start|beginning|begin|opening)\b", low):
            hits.discard("active_subscribers_eom")
        elif re.search(r"\b(end|closing)\b", low):
            hits.discard("active_subscribers_bom")
    return sorted(hits)


def _ym(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}"


def _scan_months(text: str) -> tuple[list[str], str, bool]:
    """(months the text names explicitly, the text with those mentions removed, names a specific day).
    A backwards range contributes the marker '!'; a specific day contributes no month."""
    low = f" {text.lower()} "
    found: list[str] = []

    def take(pattern, fn):
        nonlocal low
        for mt in list(re.finditer(pattern, low)):
            found.extend(fn(mt))
        low = re.sub(pattern, " ", low)

    def span(y, a, b):
        return [_ym(y, k) for k in range(a, b + 1)] if a <= b else ["!"]

    take(rf"\b({_MN})\.?\s*(?:to|through|thru|until|-|–)\s*({_MN})\.?,?\s+(\d{{4}})\b",
         lambda mt: span(int(mt[3]), _MONTH_NAMES[mt[1]], _MONTH_NAMES[mt[2]]))
    take(rf"\b({_MN})\.?,?\s+(\d{{4}})\b", lambda mt: [_ym(int(mt[2]), _MONTH_NAMES[mt[1]])])
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


def _value_pattern(v: str) -> str:
    return _VALUE_ALIASES.get(v) or (re.escape(v).replace("_", "[ _-]") + r"(?:e?s|e?d)?")


def _dim_pattern(d: str) -> str:
    return _DIM_PHRASES.get(d) or (re.escape(d).replace("_", "[ _-]") + r"s?")


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
    low = question.lower()
    filters = declared["filters"] or {}
    for d in model.metrics[declared["metric"]].dimensions:
        named = {v for v in model.dimensions[d].values or () if re.search(rf"\b{_value_pattern(v)}\b", low)}
        f = filters.get(d)
        fv = set(f) if isinstance(f, list) else ({f} if f is not None else set())
        if d in declared["dimensions"]:
            # a breakdown carries every named value as its own row unless a filter drops it; a filter
            # the question does not ask for narrows the answer
            ok = not fv or (bool(named) and named <= fv)
        else:
            ok = fv == named
        if ok:
            continue
        if not named:
            out.append(f"metric_call filters {d} to {sorted(fv)} but the question names no {d}")
        elif fv:
            out.append(f"the question names {d} {sorted(named)} but metric_call filters {d} to {sorted(fv)}")
        else:
            out.append(f"the question names {d} {sorted(named)} but metric_call neither filters nor breaks down "
                       f"by {d}")
    for d in model.dimensions:
        if d != "month" and d not in declared["dimensions"] and re.search(_BREAKDOWN + _dim_pattern(d) + r"\b", low):
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


def compare_row_multisets(answer_rows: list[dict], query_cols: list[str], query_rows: list[tuple]) -> dict:
    """Order-insensitive row comparison: same columns, same rows, numbers within 1e-9 (relative)."""
    a_cols = sorted(set().union(*(r.keys() for r in answer_rows))) if answer_rows else []
    q_cols = sorted(query_cols)
    base = {"n_answer_rows": len(answer_rows), "n_query_rows": len(query_rows), "query_columns": query_cols}
    if answer_rows and a_cols != q_cols:
        return {**base, "outcome": "FAIL", "answer_columns": a_cols,
                "reason": f"answer columns {a_cols} differ from the columns the SQL returns {q_cols}"}
    a = sorted((tuple(_canon(r.get(c)) for c in q_cols) for r in answer_rows), key=_sort_key)
    idx = [query_cols.index(c) for c in q_cols]
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
def finance_close_check(con: duckdb.DuckDBPyConnection, rows: list[dict], call: dict) -> dict:
    """Revenue totals vs the independent Finance close (closed months only)."""
    dims = call["dimensions"]
    if call["metric"] != "net_revenue_fen" or call["filters"] or not set(dims) <= {"region"}:
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
                  metric_call: dict | None = None, claims: list[str] | tuple = ()) -> dict:
    """Verify an answer (static -> re-execution -> reconciliation), record evidence, return tier, trust
    label, gate decision and the answer certificate."""
    model = semantic_model()
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
    text = " ".join([question, *claims])
    reasons: list[str] = []
    warnings: list[str] = []
    flagged = False

    # ---- 1. static analysis -------------------------------------------------------------
    if sql is not None:
        rep = analyze_sql(sql, catalog())
        static_source = "submitted_sql"
    elif declared is not None:
        rep = analyze_sql(compile_metric(model, **declared), catalog())
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
        # the declared claim must be the question asked, not merely a claim the rows satisfy
        contradictions = claim_contradictions(model, question, declared)
        warnings += contradictions

    # ---- 2. honesty: re-execute ---------------------------------------------------------
    governed = None
    shaped, shape_notes, shape_error = None, [], None
    if call is not None:
        try:
            shaped, shape_notes = shape_for_metric(rows, call, call["metric"])
        except _ShapeError as exc:
            shape_error = str(exc)
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
            reexec = {**_summ(governed), "source": "governed_metric_call"}
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
        finance = finance_close_check(ctx.con, shaped, call) if governed["outcome"] == "PASS" else None
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
            reasons.append("the governed metric returned no rows: nothing to reconcile against")
        elif call_source == "declared" and contradictions:
            recon["outcome"] = "INCONCLUSIVE"
            reasons.append("the numbers reconcile with the declared metric_call, but it does not answer the question "
                           "as asked: " + "; ".join(contradictions) + " (declare the metric_call the question asks "
                           "for, or have a human confirm the reading)")
        elif call_source == "declared":
            recon["outcome"] = "PASS"
            reasons.append(f"reconciles with governed metric {call['metric']}"
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
                  "independent_record": INDEPENDENT_RECORDS.get(m.name)}
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
