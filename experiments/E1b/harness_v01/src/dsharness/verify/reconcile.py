"""Reconcile an answer against an independent oracle (a governed metric or an official record).

Trust boundary: the expected frame comes from reviewed SQL (semantic model, finance close).
The actual frame comes from an agent. Agreement is checked key by key. A missing row, an extra
row and a value outside tolerance each count as FAIL. Input that cannot be compared is ERROR,
never a quiet PASS: duplicate keys, missing columns, non-numeric values.
An empty oracle proves nothing and gives INCONCLUSIVE.
"""
from __future__ import annotations

import math
import re

import duckdb
import numpy as np
import pandas as pd

from ..context.semantic import SemanticModel, compile_metric, query_metric

MAX_LISTED = 20
_NULL_KEY = "<NULL>"
_MONTH_START = re.compile(r"^(\d{4}-\d{2})(-01([ T]00:00(:00(\.0+)?)?)?)?$")


def _error(reason: str, **extra) -> dict:
    return {"outcome": "ERROR", "error": reason, "n_expected": None, "n_actual": None, "mismatches": [],
            "missing_keys": [], "extra_keys": [], "max_abs_diff": None, **extra}


def _key_str(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA or v is pd.NaT:
        return _NULL_KEY
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return str(v).strip()


def _prepare(df: pd.DataFrame, keys: list[str], value_col: str, side: str) -> pd.DataFrame | str:
    if not isinstance(df, pd.DataFrame):
        return f"{side} is not a DataFrame"
    missing = [c for c in [*keys, value_col] if c not in df.columns]
    if missing:
        return f"{side} frame lacks columns {missing} (has {list(df.columns)})"
    out = pd.DataFrame({k: [_key_str(v) for v in df[k].tolist()] for k in keys}, dtype=object)
    raw = df[value_col].reset_index(drop=True)
    num = pd.to_numeric(raw, errors="coerce")
    bad = raw.notna() & num.isna()
    if bad.any():
        return f"{side} column {value_col!r} has non-numeric values, e.g. {raw[bad].head(3).tolist()}"
    out["__v"] = num.astype("float64").to_numpy()
    dup = out.duplicated(keys, keep=False)
    if dup.any():
        return f"{side} frame has duplicate keys, e.g. {out.loc[dup, keys].head(3).to_dict('records')}"
    return out


def compare_frames(actual: pd.DataFrame, expected: pd.DataFrame, keys: list[str], value_col: str,
                   rel_tol: float = 1e-9, abs_tol: float = 0.0) -> dict:
    """Key-by-key comparison. A value matches if |actual - expected| <= max(rel_tol * |expected|, abs_tol).

    The tolerance is relative to the *expected* value, the oracle, not to the larger of the two.
    Key values are compared as strings ('2026-03' == '2026-03'). Both values NaN counts as a match.
    """
    if isinstance(keys, str) or not keys:
        return _error("keys must be a non-empty list of column names")
    if rel_tol < 0 or abs_tol < 0:
        return _error("tolerances must be non-negative")
    exp_f = _prepare(expected, list(keys), value_col, "expected")
    if isinstance(exp_f, str):
        return _error(exp_f)
    act_f = _prepare(actual, list(keys), value_col, "actual")
    if isinstance(act_f, str):
        return _error(act_f, n_expected=len(exp_f))
    merged = exp_f.merge(act_f, on=list(keys), how="outer", suffixes=("_e", "_a"), indicator=True)
    both = merged[merged["_merge"] == "both"]
    e, a = both["__v_e"].to_numpy(), both["__v_a"].to_numpy()
    with np.errstate(invalid="ignore"):
        diff = np.abs(a - e)
        close = (a == e) | (diff <= np.maximum(rel_tol * np.abs(e), abs_tol)) | (np.isnan(a) & np.isnan(e))
    bad = both[~close]
    mismatches = [{**{k: r[k] for k in keys}, "actual": _num(r["__v_a"]), "expected": _num(r["__v_e"]),
                   "abs_diff": _num(abs(r["__v_a"] - r["__v_e"]))} for r in bad.head(MAX_LISTED).to_dict("records")]
    missing = merged[merged["_merge"] == "left_only"][list(keys)]
    extra = merged[merged["_merge"] == "right_only"][list(keys)]
    finite = diff[np.isfinite(diff)]
    result = {
        "n_expected": int(len(exp_f)), "n_actual": int(len(act_f)), "n_compared": int(len(both)),
        "n_mismatches": int(len(bad)), "mismatches": mismatches,
        "n_missing_keys": int(len(missing)), "missing_keys": missing.head(MAX_LISTED).to_dict("records"),
        "n_extra_keys": int(len(extra)), "extra_keys": extra.head(MAX_LISTED).to_dict("records"),
        "max_abs_diff": float(finite.max()) if len(finite) else None,
        "rel_tol": rel_tol, "abs_tol": abs_tol,
    }
    if len(exp_f) == 0 and len(act_f) == 0:
        result["outcome"] = "INCONCLUSIVE"
        result["reason"] = "the oracle returned no rows: nothing to reconcile against"
    else:
        result["outcome"] = "PASS" if not (len(bad) or len(missing) or len(extra)) else "FAIL"
    return result


def _num(v):
    """JSON-safe number: NaN -> None, +/-inf -> 'inf'/'-inf' (evidence records forbid NaN/inf)."""
    v = float(v)
    if math.isnan(v):
        return None
    return v if math.isfinite(v) else str(v)


def _normalise_month(v):
    """'2026-03', '2026-03-01', Timestamp('2026-03-01') -> '2026-03'. Anything else is kept as given."""
    if isinstance(v, pd.Timestamp) and v == v.normalize() and v.day == 1:
        return v.strftime("%Y-%m")
    if isinstance(v, str):
        m = _MONTH_START.match(v.strip())
        if m:
            return m.group(1)
    return v


def reconcile_answer_to_metric(con: duckdb.DuckDBPyConnection, model: SemanticModel, answer_rows: list[dict],
                               metric: str, dimensions: list[str], months, filters=None,
                               rel_tol: float = 1e-9, abs_tol: float = 0.0) -> dict:
    """Recompute `metric` with the governed definition and compare the agent's rows to it.

    `answer_rows` must carry month, every requested dimension and the value under `value` (or under
    the metric's name). An answer that covers only some of the governed rows FAILs (missing keys).
    The metric call defines the question's scope.
    """
    call = {"metric": metric, "dimensions": list(dimensions or []), "months": months, "filters": filters}
    ctx = {"metric_call": {**call, "months": list(months) if months else None},
           "semantic_digest": model.digest}
    try:
        sql = compile_metric(model, metric, call["dimensions"], months, filters)
        expected = query_metric(con, model, metric, call["dimensions"], months, filters)
    except (ValueError, duckdb.Error) as exc:
        return _error(f"governed metric could not be computed: {exc}", **ctx)
    ctx["sql"] = sql
    if not isinstance(answer_rows, list) or not all(isinstance(r, dict) for r in answer_rows):
        return _error("answer_rows must be a list of dicts", **ctx)
    keys = ["month", *[d for d in call["dimensions"] if d != "month"]]
    actual = pd.DataFrame(answer_rows) if answer_rows else pd.DataFrame(columns=[*keys, "value"])
    value_col = "value"
    if "value" not in actual.columns and metric in actual.columns:
        actual = actual.rename(columns={metric: "value"})
        ctx["value_column_used"] = metric
    if "month" in actual.columns:
        actual["month"] = actual["month"].map(_normalise_month)
    return {**compare_frames(actual, expected, keys, value_col, rel_tol=rel_tol, abs_tol=abs_tol), **ctx}
