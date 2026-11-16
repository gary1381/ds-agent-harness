"""Leakage checks for features: a dynamic time-travel test and a static temporal-tag test.

Time travel (the empirical test). Compute the feature at `as_of` on a build whose cutoff is
the day before `as_of` -- a snapshot that cannot contain anything from `as_of` onwards -- and
again on a later build. A point-in-time-correct feature gives identical values for every
customer present in both populations; any difference means the feature reads information
that was not available at `as_of` (late-arriving rows, current-state columns, post-outcome
fields). Population membership may differ slightly (a churn reported after `as_of`); that is
reported separately and is not a feature leak.

Static (the declarative test). Uses the catalog's temporal tags (`static`, `event_time`,
`availability_time`, `mutable_current`, `post_outcome`) to flag reads of mutable or
post-outcome columns without an availability guard, comparisons that look past `as_of`, and
event tables read without bounding their availability column by `as_of`.

The two are complementary and BOTH must pass: a mutable price column is invisible to time
travel until the price actually changes inside the window (it only shows statically), and a
guard that leaks through missingness can satisfy the static rules but not time travel.

These functions return check results; recording evidence is the tools layer's job.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import timedelta
import math

import duckdb
import numpy as np
import pandas as pd
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope

from ..common import digest
from ..lake.build import latest_ready_build, load_report, warehouse_path
from ..ml.features import (AS_OF_PLACEHOLDER, FeatureDef, FeatureError, compute_feature, parse_day,
                           readonly_connection, validate_as_of)
from ..ml.train import ready_build_cutoff
from ..workspace import CUTOFFS, PIT_CUTOFFS, Workspace

CHECK_VERSION = "1"
TEMPORAL_TAGS = ("static", "event_time", "availability_time", "mutable_current", "post_outcome")
MAX_EXAMPLES = 5
# Relations the feature harness provides (not catalog tables).
HARNESS_RELATIONS = {"population": {"customer_id": "static"}}
_SENTINEL = "1111-11-11"     # stands in for {as_of} during static analysis


# ============================================================================ time travel
def _cutoff(ws: Workspace, build_id: str) -> tuple[str | None, str | None]:
    """(cutoff, problem) for a READY build."""
    try:
        return ready_build_cutoff(ws, build_id), None
    except FileNotFoundError:
        return None, f"build {build_id} not found"
    except (ValueError, RuntimeError) as exc:
        return None, str(exc)


def _jsonable(v):
    if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA:
        return None
    if isinstance(v, (np.integer, np.floating, np.bool_)):
        return v.item()
    return v if isinstance(v, (int, float, str, bool)) else str(v)


def _differs(a: pd.Series, b: pd.Series, tol: float) -> np.ndarray:
    numeric = all(pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s) or s.isna().all()
                  for s in (a, b))
    if numeric:
        x = pd.to_numeric(a, errors="raise").to_numpy(dtype="float64", na_value=np.nan)
        y = pd.to_numeric(b, errors="raise").to_numpy(dtype="float64", na_value=np.nan)
        both_nan = np.isnan(x) & np.isnan(y)
        with np.errstate(invalid="ignore"):
            close = np.abs(x - y) <= tol * np.maximum(1.0, np.maximum(np.abs(x), np.abs(y)))
        return ~(both_nan | close)
    x = a.astype(object).where(a.notna(), None).tolist()
    y = b.astype(object).where(b.notna(), None).tolist()
    return np.array([(u is None) != (v is None) or (u is not None and str(u) != str(v)) for u, v in zip(x, y)],
                    dtype=bool)


def time_travel_check(ws: Workspace, feature: FeatureDef, as_of: str, early_build: str, late_build: str,
                      tol: float = 1e-9) -> dict:
    """Compare `feature` at `as_of` on an early build (cutoff = as_of - 1 day) and a later build.

    PASS: identical on every customer in both populations. FAIL: any difference.
    INCONCLUSIVE: nothing to compare. ERROR: misconfigured builds or a broken feature."""
    out = {"check": "time_travel", "check_version": CHECK_VERSION, "feature": feature.name, "as_of": as_of,
           "early_build": early_build, "late_build": late_build, "tol": tol}

    def error(reason):
        return {**out, "outcome": "ERROR", "reason": reason, "n_compared": 0, "n_diff": 0, "examples": []}

    try:
        day = validate_as_of(as_of)
    except ValueError as exc:
        return error(str(exc))
    early_cut, problem = _cutoff(ws, early_build)
    if problem:
        return error(problem)
    late_cut, problem = _cutoff(ws, late_build)
    if problem:
        return error(problem)
    out.update(early_cutoff=early_cut, late_cutoff=late_cut)
    if parse_day(early_cut) != day - timedelta(days=1):
        return error(f"early build cutoff {early_cut} must be the day before as_of {as_of}")
    if parse_day(late_cut) <= parse_day(early_cut):
        return error(f"late build cutoff {late_cut} is not after the early cutoff {early_cut}")
    if load_report(ws, early_build)["pipeline_digest"] != load_report(ws, late_build)["pipeline_digest"]:
        return error("early and late builds come from different pipelines; differences would not isolate leakage")

    values = {}
    for side, bid in (("early", early_build), ("late", late_build)):
        try:
            with readonly_connection(warehouse_path(ws, bid)) as con:
                values[side] = compute_feature(con, as_of, feature).set_index("customer_id")["value"]
        except (FeatureError, duckdb.Error) as exc:
            return error(f"feature failed on the {side} build: {type(exc).__name__}: {str(exc).splitlines()[0][:300]}")
    early, late = values["early"], values["late"]
    common = early.index.intersection(late.index).sort_values()
    a, b = early.loc[common], late.loc[common]
    diff = _differs(a, b, tol)
    n_diff = int(diff.sum())
    examples = [{"customer_id": str(cid), "early": _jsonable(u), "late": _jsonable(v)}
                for cid, u, v in zip(common[diff][:MAX_EXAMPLES], a[diff][:MAX_EXAMPLES], b[diff][:MAX_EXAMPLES])]
    outcome = "INCONCLUSIVE" if len(common) == 0 else ("FAIL" if n_diff else "PASS")
    return {**out, "outcome": outcome, "n_compared": int(len(common)), "n_diff": n_diff, "examples": examples,
            "membership": {"n_early": int(len(early)), "n_late": int(len(late)),
                           "only_in_early": int(len(early.index.difference(late.index))),
                           "only_in_late": int(len(late.index.difference(early.index))),
                           "note": "population membership differences are informational, not a feature leak"}}


# ============================================================================ static
def temporal_tags(catalog) -> dict[str, str]:
    """{"schema.table.column": tag} from a catalog object or mapping exposing
    `.tables[name].columns[col]["temporal"]` (attribute or key access; `columns` may be a dict
    or a list of dicts with "name"). Unknown tag values raise; missing tags are left out."""
    tables = catalog.tables if hasattr(catalog, "tables") else catalog["tables"]

    def get(obj, key):
        return obj.get(key) if isinstance(obj, Mapping) else getattr(obj, key, None)

    out: dict[str, str] = {}
    for tname, tdoc in tables.items():
        schema = get(tdoc, "schema")
        fqn = tname if ("." in tname or not schema) else f"{schema}.{tname}"
        cols = get(tdoc, "columns") or {}
        items = cols.items() if isinstance(cols, Mapping) else ((get(c, "name"), c) for c in cols)
        for cname, cdoc in items:
            tag = get(cdoc, "temporal") if cdoc is not None else None
            if tag is None:
                continue
            if tag not in TEMPORAL_TAGS:
                raise ValueError(f"{fqn}.{cname}: unknown temporal tag {tag!r}")
            out[f"{fqn}.{cname}"] = tag
    return out


class _TagIndex:
    def __init__(self, tags: Mapping[str, str]):
        self.tables: dict[str, dict[str, str]] = {}
        for key, tag in tags.items():
            if tag not in TEMPORAL_TAGS:
                raise ValueError(f"{key}: unknown temporal tag {tag!r}")
            table, _, col = key.rpartition(".")
            if not table:
                raise ValueError(f"temporal tag key {key!r} must be 'table.column'")
            self.tables.setdefault(table.lower(), {})[col.lower()] = tag
        by_name: dict[str, list[str]] = {}
        for fqn in self.tables:
            by_name.setdefault(fqn.rpartition(".")[2], []).append(fqn)
        self._bare = {n: fqns[0] for n, fqns in by_name.items() if len(fqns) == 1}

    def lookup(self, table: exp.Table) -> tuple[str, dict[str, str] | None]:
        """(qualified name, {column: tag}) for a table reference; tags None when untagged."""
        name, db = table.name.lower(), (table.db or "").lower()
        if not db and name in HARNESS_RELATIONS:
            return name, HARNESS_RELATIONS[name]
        fqn = f"{db}.{name}" if db else name
        if fqn in self.tables:
            return fqn, self.tables[fqn]
        if not db and name in self._bare:          # SQL without schema, tags with one
            return self._bare[name], self.tables[self._bare[name]]
        if db and name in self.tables:             # tags keyed by bare table name
            return fqn, self.tables[name]
        return fqn, None


def _strip(node: exp.Expression) -> exp.Expression:
    while isinstance(node, (exp.Paren, exp.Cast, exp.TryCast)):
        node = node.this
    return node


def _asof_relation(node: exp.Expression) -> str | None:
    """'eq' (as_of), 'before' (as_of - x), 'after' (as_of + x) or None (not derived from as_of)."""
    node = _strip(node)
    if isinstance(node, exp.Literal) and node.is_string and node.this == _SENTINEL:
        return "eq"
    if isinstance(node, (exp.Sub, exp.Add)) and _asof_relation(node.this) == "eq" \
            and not list(node.expression.find_all(exp.Column)):
        return "before" if isinstance(node, exp.Sub) else "after"
    return None


def _bounds(col: exp.Column) -> list[tuple[str, str]]:
    """[(kind, relation)] where kind is 'upper'/'lower' bound placed on `col` by an as_of expression."""
    node, parent = col, col.parent
    while isinstance(parent, (exp.Paren, exp.Cast, exp.TryCast)):
        node, parent = parent, parent.parent
    out = []
    if isinstance(parent, (exp.LT, exp.LTE, exp.GT, exp.GTE)):
        col_left = parent.this is node
        other = parent.expression if col_left else parent.this
        rel = _asof_relation(other)
        if rel:
            upper = isinstance(parent, (exp.LT, exp.LTE)) == col_left
            out.append(("upper" if upper else "lower", rel))
    elif isinstance(parent, exp.Between) and parent.this is node:
        for kind, side in (("lower", parent.args.get("low")), ("upper", parent.args.get("high"))):
            rel = _asof_relation(side) if side is not None else None
            if rel:
                out.append((kind, rel))
    return out


def _source_of(scope: Scope, alias: str):
    s = scope
    while s is not None:
        if alias in s.sources:
            return s.sources[alias]
        s = s.parent
    return None


def _is_within(node: exp.Expression, ancestor: exp.Expression) -> bool:
    p = node
    while p is not None:
        if p is ancestor:
            return True
        p = p.parent
    return False


def _projection_columns(scope: Scope, name: str, idx: _TagIndex, depth: int):
    """Base (Table, column) pairs feeding projection `name` of a derived scope (CTE/subquery)."""
    branches = getattr(scope, "set_operation_scopes", None)   # UNION / INTERSECT / EXCEPT branches
    if branches:
        res = []
        for u in branches:
            res += _projection_columns(u, name, idx, depth + 1) or []
        return res
    selects = getattr(scope.expression, "selects", [])
    for sel in selects:
        if not isinstance(sel, exp.Star) and sel.alias_or_name.lower() == name:
            res = []
            for c in scope.columns:
                if _is_within(c, sel):
                    res += _resolve(c, scope, idx, depth + 1) or []
            return res
    if any(isinstance(s, exp.Star) or (isinstance(s, exp.Column) and isinstance(s.this, exp.Star))
           for s in selects):
        return _resolve(exp.column(name), scope, idx, depth + 1)
    return None


def _resolve(col: exp.Column, scope: Scope, idx: _TagIndex, depth: int = 0):
    """Base (exp.Table, column) pairs that a column reference reads, following CTEs and derived
    tables; None if it is not a table column (e.g. a projection alias in ORDER BY)."""
    if depth > 20:
        return None
    name = col.name.lower()
    if col.table:
        src = _source_of(scope, col.table)
        if isinstance(src, exp.Table):
            return [(src, name)]
        if isinstance(src, Scope):
            return _projection_columns(src, name, idx, depth + 1)
        return None
    hits, untagged = [], []
    for _, src in scope.selected_sources.values():
        if isinstance(src, exp.Table):
            cols = idx.lookup(src)[1]
            if cols is None:
                untagged.append(src)
            elif name in cols:
                hits.append((src, name))
        elif isinstance(src, Scope):
            hits += _projection_columns(src, name, idx, depth + 1) or []
    if hits:
        return hits
    return [(t, name) for t in untagged] or None


def static_temporal_check(sql: str, catalog) -> dict:
    """Temporal lint of feature SQL against catalog tags.

    FAIL         mutable_current / post_outcome column read without an upper-bound guard on an
                 availability_time column of the same table instance; or an event/availability
                 column compared to a point at or after as_of (looks forward).
    INCONCLUSIVE an event table (has availability_time, no mutable columns) whose availability
                 column is never bounded by as_of; or tables/columns without temporal tags.
    ERROR        the SQL does not parse as a single query.
    `catalog` is a {"table.column": tag} mapping or a catalog object (see `temporal_tags`)."""
    if isinstance(catalog, Mapping) and all(isinstance(v, str) for v in catalog.values()):
        tags = dict(catalog)
    else:
        tags = temporal_tags(catalog)
    idx = _TagIndex(tags)
    base = {"check": "static_temporal", "check_version": CHECK_VERSION, "tags_digest": digest(tags)}
    rendered = sql.strip().rstrip(";").replace(AS_OF_PLACEHOLDER, f"DATE '{_SENTINEL}'")
    try:
        statements = sqlglot.parse(rendered, dialect="duckdb")
    except SqlglotError as exc:
        return {**base, "outcome": "ERROR", "reason": f"parse error: {str(exc).splitlines()[0][:300]}",
                "findings": [], "tables": [], "columns": {}}
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        return {**base, "outcome": "ERROR", "reason": "feature SQL must be exactly one SELECT query",
                "findings": [], "tables": [], "columns": {}}
    tree = statements[0]
    try:
        scopes = traverse_scope(tree)
    except SqlglotError as exc:
        return {**base, "outcome": "ERROR", "reason": f"cannot resolve query scopes: {exc}",
                "findings": [], "tables": [], "columns": {}}

    refs = []         # (table node, fqn, column, tag | None)
    guards = []       # (table node, column, kind, relation)
    instances = {}    # id(table node) -> (table node, fqn, cols | None)
    unresolved = set()
    for scope in scopes:
        for src in scope.sources.values():
            if isinstance(src, exp.Table):
                fqn, cols = idx.lookup(src)
                instances[id(src)] = (src, fqn, cols)
        for col in scope.columns:
            if isinstance(col.this, exp.Star):
                continue
            resolved = _resolve(col, scope, idx)
            if resolved is None:
                aliases = {sel.alias_or_name.lower() for sel in getattr(scope.expression, "selects", [])}
                if col.name.lower() not in aliases:
                    unresolved.add(col.sql(dialect="duckdb"))
                continue
            bounds = _bounds(col)
            for tnode, cname in resolved:
                fqn, cols = idx.lookup(tnode)
                instances.setdefault(id(tnode), (tnode, fqn, cols))
                refs.append((tnode, fqn, cname, None if cols is None else cols.get(cname)))
                for kind, rel in bounds:
                    guards.append((tnode, cname, kind, rel))

    findings: dict[tuple, dict] = {}

    def add(rule, severity, table, column, message):
        findings.setdefault((rule, table, column), {"rule": rule, "severity": severity, "table": table,
                                                    "column": column, "message": message})

    def guarded(tnode, cols) -> bool:
        avail = {c for c, t in cols.items() if t == "availability_time"}
        return any(g[0] is tnode and g[1] in avail and g[2] == "upper" and g[3] in ("eq", "before")
                   for g in guards)

    for tnode, fqn, cols in instances.values():
        if cols is None:
            add("untagged_table", "INCONCLUSIVE", fqn, None,
                f"{fqn} has no temporal tags in the catalog; cannot judge point-in-time safety")
            continue
        is_event = (any(t == "availability_time" for t in cols.values())
                    and not any(t in ("mutable_current", "post_outcome") for t in cols.values()))
        if is_event and not guarded(tnode, cols):
            avail = sorted(c for c, t in cols.items() if t == "availability_time")
            add("event_rows_unbounded", "INCONCLUSIVE", fqn, None,
                f"{fqn} is an event table but {'/'.join(avail)} is never bounded by as_of "
                f"(late-arriving rows would leak in)")

    for name in sorted(unresolved):
        add("unresolved_column", "INCONCLUSIVE", "?", name,
            f"column {name} does not resolve to a tagged table column")
    for tnode, fqn, cname, tag in refs:
        cols = instances[id(tnode)][2]
        if cols is None:
            continue
        if tag is None:
            add("untagged_column", "INCONCLUSIVE", fqn, cname, f"{fqn}.{cname} has no temporal tag")
        elif tag in ("mutable_current", "post_outcome") and not guarded(tnode, cols):
            what = "current-state (mutable)" if tag == "mutable_current" else "post-outcome"
            add(f"{tag}_unguarded", "FAIL", fqn, cname,
                f"{fqn}.{cname} is {what}; reading it without bounding an availability_time column of "
                f"{fqn} by as_of uses information from after as_of")
    for tnode, cname, kind, rel in guards:
        fqn, cols = instances[id(tnode)][1], instances[id(tnode)][2]
        tag = (cols or {}).get(cname)
        if tag in ("event_time", "availability_time") and (
                (kind == "lower" and rel in ("eq", "after")) or (kind == "upper" and rel == "after")):
            add("looks_forward", "FAIL", fqn, cname,
                f"{fqn}.{cname} is compared to a point at or after as_of: the feature reads the future")

    found = sorted(findings.values(), key=lambda f: (f["severity"] != "FAIL", f["rule"], f["table"], f["column"] or ""))
    if any(f["severity"] == "FAIL" for f in found):
        outcome = "FAIL"
    elif found:
        outcome = "INCONCLUSIVE"
    else:
        outcome = "PASS"
    columns = {}
    for _, fqn, cname, tag in refs:
        columns[f"{fqn}.{cname}"] = tag
    return {**base, "outcome": outcome, "findings": found,
            "tables": sorted({fqn for _, fqn, _ in instances.values()}),
            "columns": dict(sorted(columns.items()))}


# ============================================================================ combined
def pit_pairs() -> list[tuple[str, str]]:
    """(as_of, early_cutoff) pairs available in every workspace: as_of = PIT cutoff + 1 day."""
    return [((parse_day(c) + timedelta(days=1)).isoformat(), c) for c in PIT_CUTOFFS]


def _combine(outcomes: list[str]) -> str:
    for o in ("FAIL", "ERROR", "INCONCLUSIVE"):
        if o in outcomes:
            return o
    return "PASS"


def check_feature(ws: Workspace, feature: FeatureDef, catalog) -> dict:
    """Time travel on the PIT builds (late build = train cutoff) plus the static check.
    PASS only if every part passes; FAIL dominates, then ERROR, then INCONCLUSIVE."""
    travels = []
    try:
        late = latest_ready_build(ws, CUTOFFS["train"])
    except (FileNotFoundError, RuntimeError) as exc:
        late, late_problem = None, f"late (train) build unavailable: {exc}"
    for as_of, early_cutoff in pit_pairs():
        if late is None:
            travels.append({"check": "time_travel", "feature": feature.name, "as_of": as_of, "outcome": "ERROR",
                            "reason": late_problem, "n_compared": 0, "n_diff": 0, "examples": []})
            continue
        try:
            early = latest_ready_build(ws, early_cutoff)
        except (FileNotFoundError, RuntimeError) as exc:
            travels.append({"check": "time_travel", "feature": feature.name, "as_of": as_of, "outcome": "ERROR",
                            "reason": f"PIT build for {early_cutoff} unavailable: {exc}",
                            "n_compared": 0, "n_diff": 0, "examples": []})
            continue
        travels.append(time_travel_check(ws, feature, as_of, early, late))
    static = static_temporal_check(feature.sql, catalog)
    reasons = []
    for t in travels:
        if t["outcome"] == "FAIL":
            reasons.append(f"time_travel@{t['as_of']}: {t['n_diff']} of {t['n_compared']} customers differ "
                           f"between the early and late build")
        elif t["outcome"] != "PASS":
            reasons.append(f"time_travel@{t['as_of']}: {t['outcome']}: {t.get('reason', 'nothing to compare')}")
    if static["outcome"] == "ERROR":
        reasons.append(f"static: ERROR: {static['reason']}")
    reasons += [f"static: {f['severity']}: {f['message']}" for f in static["findings"]]
    outcome = _combine([t["outcome"] for t in travels] + [static["outcome"]])
    return {"check": "feature_leakage", "check_version": CHECK_VERSION, "feature": feature.name,
            "origin": feature.origin, "feature_digest": digest(asdict(feature)), "outcome": outcome,
            "reasons": reasons, "time_travel": travels, "static": static}
