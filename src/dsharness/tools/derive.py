"""Declared derivations on governed metric rows (H v0.2, docs/experiments/H_v0.2_PLAN.md F2/F4).

Many business questions need one arithmetic step on a governed result: a total over the quarter, a
share of the total, a control-minus-treatment difference, growth between two months, a top-N, a
conversion from fen to CNY. Without a way to declare that step, an agent either submits the raw
monthly rows (which reconcile, so the gate verified an answer of the wrong shape) or submits the
derived numbers (which do not reconcile, so a correct answer was rejected). Here the agent declares
the step, the harness recomputes it from the governed rows, and the submitted rows must equal the
harness's result.

    derive = {"months": "sum" | "mean" | "pool" | "growth" | "cumsum" | "change" | "total" | None,
              "windows": [[first, last], [first, last]] | None,     # growth between two periods (v0.5 F20)
              "divide_by": {"metric": m, "filters": {...}} | None,  # ratio of two governed metrics (v0.5 F18)
              "across": {"op": "share" | "difference" | "ratio" | "top", "dimension": d | "month",
                         "a": v, "b": v, "n": k} | None,      # share: optional a, b = the members reported
              "keep": {dimension: value | [values]} | None,
              "unit": "cny" | "percent" | None}

Steps run in that order: collapse (or accumulate) the month column, combine rows across one dimension, keep the
rows the question names (a share "from the south region" is computed against all regions, then
only south is reported), convert the unit. `across` may combine the months themselves (dimension
"month", H v0.3 F16: the highest month, one month minus another) instead of a `months` roll-up. The fixed list is the whole language: anything else stays HOLD with a reason the agent can
read. Month roll-ups follow the metric's semantics, not the agent's choice:

- `sum` only for metrics whose monthly values add up (`month_rollup: sum` in the semantic model, or
  an additive `sum` / `count` measure); a distinct count summed over months counts customers twice;
- `pool` only for ratio metrics: sum(numerator) / sum(denominator) over the months, the way the
  caveats say a rate over several months must be computed;
- `mean` is the average of the monthly values (for "average monthly ..." questions);
- `growth` is (last month - first month) / first month of the declared months;
- `cumsum` is the running total since the first declared month (H v0.3 F17); unlike the others it
  keeps one row per month, and like `sum` only for metrics whose monthly values add up.
- `change` is each month's change from the month before, (this - previous) / previous (H v0.5 F21,
  "month-over-month change for each month"); it keeps one row per month and drops the first declared
  month, which is only the base.
- `total` is the metric over the declared months taken as one period (H v0.5 F19), computed by the
  harness from the line rows: a distinct count counts each customer or order once (customers who
  ordered in two months of a quarter are one customer of the quarter), a sum adds the months, a ratio
  divides the period's numerator by its denominator. Only for event metrics (`period_defined`).
- `growth` with `windows` compares two periods, each taken as one period like `total` (H v0.5 F20):
  "Q3 against Q2", "January-November against the same months a year earlier". Without `windows` it
  compares the first and last declared month, as before.

`divide_by` (H v0.5 F18) divides the metric by a second governed metric at the same months, dimensions
and filters (or its own filters): sales per active customer, units per order, new customers as a share
of active customers. It runs after the months step (per month, or over the period with `total`) and
before `across`, `keep` and `unit`.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
import numbers

import pandas as pd

from ..context.semantic import period_defined

MONTH_OPS = ("sum", "mean", "pool", "growth", "cumsum", "change", "total")
ACROSS_OPS = ("share", "difference", "ratio", "top")
UNITS = ("cny", "percent")
REL_TOL = 1e-9
MIN_SIG_DIGITS = 3


class DeriveError(ValueError):
    """The declared derivation is not allowed for this metric or call; the message is for the agent."""


def normalize_derive(raw: dict | None, metric: str, spec: dict, call: dict) -> dict | None:
    """Validate a declared derivation against the metric spec and the metric_call. None = no derivation."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - {"months", "across", "keep", "unit", "windows", "divide_by"}:
        raise DeriveError("derive must be an object with keys months, across, keep, unit, windows, divide_by")
    months, across, keep, unit = raw.get("months"), raw.get("across"), raw.get("keep"), raw.get("unit")
    windows, divide_by = raw.get("windows"), raw.get("divide_by")
    if all(x is None for x in (months, across, keep, unit, windows, divide_by)):
        return None
    m = spec["metrics"][metric]
    mtype = m.get("type", "simple")
    if months is not None:
        if months not in MONTH_OPS:
            raise DeriveError(f"derive.months must be one of {list(MONTH_OPS)}")
        if months in ("sum", "cumsum") and month_rollup(spec, metric) != "sum":
            raise DeriveError(f"{metric} does not add up over months (a distinct count or a rate); use 'pool' for "
                              f"a rate, 'mean' for an average of monthly values, or ask a human")
        if months == "pool" and mtype != "ratio":
            raise DeriveError(f"'pool' recomputes a rate from its counts; {metric} is not a ratio metric")
        if months == "growth" and not call.get("months"):
            raise DeriveError("'growth' needs metric_call.months (first and last month)")
        if months == "total" and not period_defined(spec, metric):
            raise DeriveError(f"{metric} is measured at a point in time: it has no value over a whole period; use "
                              f"'mean' for an average of monthly values, or ask a human")
        if months == "total" and not call.get("months"):
            raise DeriveError("'total' needs metric_call.months (the period)")
        if months == "change" and not (call.get("months") and call["months"][0] < call["months"][1]):
            raise DeriveError("'change' needs metric_call.months to start one month before the first month reported "
                              "(that month is the base)")
    if windows is not None:
        windows = _windows(windows, months, metric, spec, call)
    if divide_by is not None:
        divide_by = _divide_by(divide_by, months, spec, call)
    if across is not None:
        if not isinstance(across, dict) or across.get("op") not in ACROSS_OPS:
            raise DeriveError(f"derive.across.op must be one of {list(ACROSS_OPS)}")
        d, op = across.get("dimension"), across["op"]
        if d == "month":                   # E1b: "which month was highest", "February 2026 minus February 2025"
            if months is not None:
                raise DeriveError("derive.across over 'month' combines the monthly rows itself; leave derive.months out")
            if op == "share" and month_rollup(spec, metric) != "sum":
                raise DeriveError(f"a month's share of the period total needs a metric that adds up over months; "
                                  f"{metric} does not")
            first, last = call.get("months") or (None, None)
            for v in (across.get("a"), across.get("b")):
                if v is not None and not (first and first <= str(v) <= last):
                    raise DeriveError(f"derive.across.a/b {v!r} must be a month in metric_call.months "
                                      f"{[first, last]} (YYYY-MM)")
        elif d not in call["dimensions"]:
            raise DeriveError(f"derive.across.dimension {d!r} must be 'month' or one of metric_call.dimensions "
                              f"{call['dimensions']}")
        if op in ("difference", "ratio") and (across.get("a") is None or across.get("b") is None):
            raise DeriveError(f"'{op}' needs a and b: two values of {d} (result = a {'-' if op == 'difference' else '/'} b)")
        if op == "top" and not (isinstance(across.get("n"), int) and across["n"] >= 1):
            raise DeriveError("'top' needs n >= 1")
        if op == "share" and (mtype == "ratio" or m.get("unit") == "score 1-5"):
            raise DeriveError(f"a share of the total needs an additive metric; {metric} is a rate or an average")
        across = {k: across.get(k) for k in ("op", "dimension", "a", "b", "n") if across.get(k) is not None}
    if keep is not None:
        left = [x for x in call["dimensions"] if not (across and across["op"] in ("difference", "ratio")
                                                         and x == across["dimension"])]
        if not isinstance(keep, dict) or not keep or set(keep) - set(left):
            raise DeriveError(f"derive.keep must map dimensions of the derived rows {left} to the values to report")
        keep = {k: sorted({str(x) for x in (v if isinstance(v, list) else [v])}) for k, v in keep.items()}
    if unit is not None:
        if unit not in UNITS:
            raise DeriveError(f"derive.unit must be one of {list(UNITS)}")
        if unit == "cny" and m.get("unit") != "fen":
            raise DeriveError(f"'cny' converts fen to CNY; {metric} is in {m.get('unit')}")
    out = {"months": months, "across": across, "keep": keep, "unit": unit}
    # the v0.5 keys appear only when used, so a v0.4 derivation (and its certificate) is unchanged
    out.update({k: v for k, v in (("windows", windows), ("divide_by", divide_by)) if v is not None})
    return out


def _windows(windows, months, metric: str, spec: dict, call: dict) -> list[tuple[str, str]]:
    """Two periods for growth, each inside metric_call.months: [[base first, base last], [first, last]]."""
    if months != "growth":
        raise DeriveError("derive.windows goes with months = 'growth': growth from the first window to the second")
    if (not isinstance(windows, (list, tuple)) or len(windows) != 2
            or not all(isinstance(w, (list, tuple)) and len(w) == 2 for w in windows)):
        raise DeriveError("derive.windows must be two month ranges: [[base first, base last], [first, last]]")
    first, last = call.get("months") or (None, None)
    out = []
    for a, b in windows:
        a, b = str(a), str(b)
        if not (_is_month(a) and _is_month(b) and a <= b):
            raise DeriveError(f"derive.windows: {[a, b]} is not a month range (YYYY-MM, first <= last)")
        if not (first and first <= a and b <= last):
            raise DeriveError(f"derive.windows: {[a, b]} must lie inside metric_call.months {[first, last]}")
        out.append((a, b))
    (a0, b0), (a1, b1) = out
    length = lambda a, b: (int(b[:4]) - int(a[:4])) * 12 + int(b[5:]) - int(a[5:])   # noqa: E731
    back_to_back = shift_month(b0, 1) == a1 and length(a0, b0) == length(a1, b1)
    year_apart = (shift_month(a0, 12), shift_month(b0, 12)) == (a1, b1)
    if not (back_to_back or year_apart):
        raise DeriveError("derive.windows must be two periods of the same length, back to back (Q2 then Q3) or a "
                          "year apart (Jan-Nov 2024 then Jan-Nov 2025), base first")
    if not period_defined(spec, metric):
        raise DeriveError(f"{metric} is measured at a point in time: it has no value over a whole window")
    return out


def _divide_by(raw, months, spec: dict, call: dict) -> dict:
    """The second governed metric of a ratio (numerator = metric_call). Same months and dimensions;
    filters default to metric_call.filters."""
    if not isinstance(raw, dict) or set(raw) - {"metric", "filters"} or not isinstance(raw.get("metric"), str):
        raise DeriveError("derive.divide_by must be {metric, filters}: the governed metric to divide by")
    name = raw["metric"]
    if name not in spec["metrics"]:
        raise DeriveError(f"derive.divide_by.metric {name!r} is not a governed metric (see list_metrics)")
    if months not in (None, "total"):
        raise DeriveError("derive.divide_by divides month by month (no months step) or over the period "
                          "(months = 'total'); other month steps cannot be combined with it")
    if months == "total" and not period_defined(spec, name):
        raise DeriveError(f"{name} is measured at a point in time: it has no value over a whole period")
    allowed = spec["metrics"][name].get("dimensions") or []
    bad = [d for d in call["dimensions"] if d not in allowed]
    if bad:
        raise DeriveError(f"{name} has no dimension {bad[0]!r}: the two metrics must share the breakdown")
    filters = raw.get("filters")
    if filters is not None and not isinstance(filters, dict):
        raise DeriveError("derive.divide_by.filters must be an object dimension -> value(s)")
    return {"metric": name, "filters": call.get("filters") if filters is None else (filters or None)}


def _is_month(v: str) -> bool:
    return len(v) == 7 and v[4] == "-" and v[:4].isdigit() and v[5:].isdigit() and 1 <= int(v[5:]) <= 12


def shift_month(m: str, k: int) -> str:
    """'YYYY-MM' moved by k months."""
    y, mo = int(m[:4]), int(m[5:7]) - 1 + k
    return f"{y + mo // 12:04d}-{mo % 12 + 1:02d}"


def month_rollup(spec: dict, metric: str) -> str | None:
    """'sum' if the metric's monthly values add up to a period total, else None."""
    m = spec["metrics"][metric]
    if "month_rollup" in m:
        return m["month_rollup"]
    if m.get("type", "simple") != "simple":
        return None
    agg = spec["datasets"][m["dataset"]]["measures"][m["measure"]]["agg"]
    return "sum" if agg in ("sum", "count") else None


def apply_derive(rows: pd.DataFrame, d: dict, dims: list[str], parts: tuple[pd.DataFrame, pd.DataFrame] | None = None,
                 months: tuple[str, str] | None = None, inputs: dict | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Governed rows (month, *dims, value) -> (derived rows, their key columns).

    `parts` = (numerator rows, denominator rows) with the same keys, needed for months='pool'. `inputs` holds the
    other governed frames a step needs, fetched by the caller: "period" (dims, value) for months='total',
    "windows" (two such frames) for growth between windows, "den" (the divide_by metric, monthly or per period)."""
    df = rows.copy()
    keys = ["month", *dims]
    op = d["months"]
    inputs = inputs or {}
    if op is not None:
        rest = [k for k in keys if k != "month"]
        if op == "total":
            df = inputs["period"][[*rest, "value"]].copy()
        elif op == "growth" and d.get("windows"):
            a = inputs["windows"][0][[*rest, "value"]].rename(columns={"value": "v0"})
            b = inputs["windows"][1][[*rest, "value"]].rename(columns={"value": "v1"})
            df = a.merge(b, on=rest) if rest else pd.concat([a.reset_index(drop=True), b.reset_index(drop=True)], axis=1)
            df = df[df["v0"] != 0]
            df["value"] = (df["v1"].astype(float) - df["v0"].astype(float)) / df["v0"].astype(float)
            df = df[[*rest, "value"]]
        elif op == "pool":
            num, den = parts
            n = _total(num, rest).rename(columns={"value": "num"})
            dn = _total(den, rest).rename(columns={"value": "den"})
            df = dn.merge(n, on=rest, how="left") if rest else pd.concat([dn, n], axis=1)
            df = df[df["den"] != 0]
            df["value"] = df["num"].fillna(0).astype(float) / df["den"].astype(float)
            df = df[[*rest, "value"]]
        elif op == "growth":
            first, last = months
            a = df[df["month"] == first].drop(columns="month").rename(columns={"value": "v0"})
            b = df[df["month"] == last].drop(columns="month").rename(columns={"value": "v1"})
            df = a.merge(b, on=rest) if rest else pd.concat([a.reset_index(drop=True), b.reset_index(drop=True)], axis=1)
            df = df[df["v0"] != 0]
            df["value"] = (df["v1"].astype(float) - df["v0"].astype(float)) / df["v0"].astype(float)
            df = df[[*rest, "value"]]
        elif op == "cumsum":       # running total; the month column stays
            df = df.sort_values("month")
            df["value"] = df.groupby(rest, dropna=False)["value"].cumsum() if rest else df["value"].cumsum()
            df = df[[*keys, "value"]]
        elif op == "change":       # each month against the calendar month before; the first month is only the base
            # (a rate or average omits months with no data, so the row before is not always the month before)
            base = df.assign(month=df["month"].map(lambda m: shift_month(m, 1))).rename(columns={"value": "prev"})
            df = df.merge(base[[*keys, "prev"]], on=keys)
            df = df[df["prev"].notna() & (df["prev"].astype(float) != 0)]
            df = df.assign(value=(df["value"].astype(float) - df["prev"].astype(float)) / df["prev"].astype(float))
            df = df.sort_values(keys)[[*keys, "value"]]
        else:
            df = (_total(df, rest) if op == "sum" else
                  (df.groupby(rest, as_index=False, dropna=False)["value"].mean() if rest
                   else pd.DataFrame({"value": [df["value"].astype(float).mean()]})))
        if op not in ("cumsum", "change"):
            keys = rest
    if d.get("divide_by"):
        den = inputs["den"][[*keys, "value"]].rename(columns={"value": "den"})
        df = df.merge(den, on=keys, how="inner") if keys else pd.concat(
            [df.reset_index(drop=True), den.reset_index(drop=True)], axis=1)
        df = df[df["den"].astype(float) != 0]
        df = df.assign(value=df["value"].astype(float) / df["den"].astype(float))[[*keys, "value"]]
    ac = d["across"]
    if ac is not None:
        dim = ac["dimension"]
        other = [k for k in keys if k != dim]
        if ac["op"] == "share":
            tot = df.groupby(other, dropna=False)["value"].transform("sum") if other else df["value"].sum()
            df = df.assign(value=df["value"].astype(float) / tot)
            named = [str(x) for x in (ac.get("a"), ac.get("b")) if x is not None]
            if named:                      # "the west region's share": a (and b) name the members reported (as keep)
                df = df[df[dim].astype(str).isin(named)]
                missing = sorted(set(named) - set(df[dim].astype(str)))
                if missing:
                    raise DeriveError(f"no governed rows for {dim} = {missing[0]!r}")
        elif ac["op"] == "top":
            df = (df.sort_values([*other, "value"], ascending=[True] * len(other) + [False])
                  .groupby(other, dropna=False, sort=False).head(ac["n"]) if other
                  else df.sort_values("value", ascending=False).head(ac["n"]))
        else:
            a = df[df[dim].astype(str) == str(ac["a"])].drop(columns=dim).rename(columns={"value": "va"})
            b = df[df[dim].astype(str) == str(ac["b"])].drop(columns=dim).rename(columns={"value": "vb"})
            if a.empty or b.empty:
                raise DeriveError(f"no governed rows for {dim} = {ac['a'] if a.empty else ac['b']!r}")
            if other:                      # a group with only one side would silently drop out of the merge
                ka, kb = (set(map(tuple, x[other].astype(str).values)) for x in (a, b))
                if ka != kb:
                    raise DeriveError(f"{dim} = {ac['a']!r} and {ac['b']!r} do not cover the same {other}: "
                                      f"{sorted(ka ^ kb)[:4]} have only one of them; narrow metric_call.filters")
            df = a.merge(b, on=other) if other else pd.concat([a.reset_index(drop=True), b.reset_index(drop=True)], axis=1)
            va, vb = df["va"].astype(float), df["vb"].astype(float)
            df = df.assign(value=va - vb if ac["op"] == "difference" else va / vb)[[*other, "value"]]
            keys = other
    for dim, vals in (d.get("keep") or {}).items():
        df = df[df[dim].astype(str).isin(vals)]
    if d["unit"] == "cny":
        df = df.assign(value=df["value"].astype(float) / 100.0)
    elif d["unit"] == "percent":
        df = df.assign(value=df["value"].astype(float) * 100.0)
    return df[[*keys, "value"]].reset_index(drop=True), keys


def _total(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    if keys:
        return df.groupby(keys, as_index=False, dropna=False)["value"].sum()
    return pd.DataFrame({"value": [df["value"].sum()]})


def _decimals_and_sig(v) -> tuple[int, int]:
    try:
        x = Decimal(repr(v) if isinstance(v, float) else str(v)).normalize()
    except InvalidOperation:
        return 0, 0
    _, digits, exp = x.as_tuple()
    return max(0, -exp), (len(digits) if any(digits) else 1)


def value_matches(got, expected: float) -> bool:
    """Equal at the precision the agent reported: within half a unit of its last decimal, with at
    least 3 significant digits (an exact value always matches)."""
    if not isinstance(got, numbers.Real) or isinstance(got, bool) or not math.isfinite(got):
        return False
    got = float(got)                       # numpy scalars repr as "np.float64(...)"; read the plain number
    if got == expected or math.isclose(got, expected, rel_tol=REL_TOL, abs_tol=1e-12):
        return True
    dec, sig = _decimals_and_sig(got)
    return sig >= MIN_SIG_DIGITS and abs(got - expected) <= 0.5 * 10.0 ** (-dec) + 1e-12


def compare_derived(submitted: list[dict], expected: pd.DataFrame, keys: list[str],
                    ignore: tuple[str, ...] = ("month", "period")) -> dict:
    """PASS if the submitted rows are exactly the derived rows (same keys, values at reported precision)."""
    def key(r) -> tuple:
        return tuple(str(r.get(k)).strip().lower() for k in keys)

    sub_cols = set().union(*(r.keys() for r in submitted)) if submitted else set()
    # a key with one value in the derived rows ("south" after keep) may be left out of the answer
    keys = [k for k in keys if k in sub_cols or expected[k].nunique(dropna=False) > 1]
    missing = [k for k in keys if k not in sub_cols]
    if missing:
        hint = "" if "month" in missing else (f" (or, if the question names one {missing[0]}, declare derive.keep = "
                                              f"{{{missing[0]!r}: [the value it names]}} so only that row is derived)")
        return {"outcome": "FAIL", "reason": f"the derived answer needs key columns {missing}{hint}"}
    extra = sorted(sub_cols - {*keys, *expected.columns, *ignore})
    if extra:
        return {"outcome": "FAIL", "reason": f"unexpected columns {extra}: the derived rows are {[*keys, 'value']}"}
    exp = {key(r): r["value"] for r in expected.to_dict("records")}
    got: dict[tuple, object] = {}
    for r in submitted:
        k = key(r)
        if k in got:
            return {"outcome": "FAIL", "reason": f"several rows for {dict(zip(keys, k)) or 'the single value'}"}
        got[k] = r.get("value")
    if set(got) != set(exp):
        reason = f"rows {sorted(got)[:4]} != derived rows {sorted(exp)[:4]}"
        if exp and set(got) > set(exp):    # E3a R-L40: both markets' shares answered, derive kept only a = UK
            reason += ("; the answer has rows the declared step leaves out: if the question asks for them, name them "
                       "all (derive.across.a and b, or derive.keep) or leave the member out of the step so every row "
                       "is derived")
        if got and set(got) < set(exp):    # E1b: "the west region's share" answered with the west row only
            named = [r for r in expected.to_dict("records") if key(r) in got]
            pick = {k: sorted({str(r[k]) for r in named}) for k in keys
                    if k != "month" and expected[k].nunique(dropna=False) > 1}
            if pick:
                reason += (f"; the answer reports only some of the derived rows: if the question names only these, "
                           f"declare derive.keep = {pick} and submit again")
        return {"outcome": "FAIL", "reason": reason, "n_expected": len(exp), "n_answer": len(got)}
    bad = [{"key": dict(zip(keys, k)), "answer": got[k], "derived": exp[k]} for k in exp
           if not value_matches(got[k], float(exp[k]))]
    if bad:
        return {"outcome": "FAIL", "reason": f"{len(bad)} rows differ from the derived values", "mismatches": bad[:5]}
    return {"outcome": "PASS", "n_rows": len(exp)}
