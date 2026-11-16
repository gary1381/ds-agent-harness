"""Declared derivations (H v0.2 F2/F4): the harness recomputes the step from governed rows."""
from __future__ import annotations

import pandas as pd
import pytest

from dsharness.tools.derive import DeriveError, apply_derive, compare_derived, month_rollup, normalize_derive

SPEC = {
    "datasets": {"inv": {"measures": {"amount": {"agg": "sum"}}},
                 "subs": {"measures": {"n": {"agg": "count_distinct"}}},
                 "prog": {"measures": {"c": {"agg": "count_distinct"}}}},
    "metrics": {
        "rev": {"type": "simple", "unit": "fen", "dataset": "inv", "measure": "amount"},
        "active": {"type": "simple", "unit": "customers", "dataset": "subs", "measure": "n"},
        "enrolled": {"type": "simple", "unit": "customers", "dataset": "prog", "measure": "c", "month_rollup": "sum"},
        "rate": {"type": "ratio", "unit": "ratio", "numerator": "churned", "denominator": "enrolled"},
    },
}
REV = pd.DataFrame({"month": ["2026-01"] * 2 + ["2026-02"] * 2 + ["2026-03"] * 2,
                    "region": ["north", "south"] * 3,
                    "value": [100, 300, 200, 300, 300, 400]})


def call(metric="rev", dims=("region",), months=("2026-01", "2026-03")):
    return {"metric": metric, "dimensions": list(dims), "months": months, "filters": None}


def derive(raw, metric="rev", dims=("region",), months=("2026-01", "2026-03"), rows=REV, parts=None):
    d = normalize_derive(raw, metric, SPEC, call(metric, dims, months))
    return apply_derive(rows, d, list(dims), parts=parts, months=months)


def test_quarter_total_in_cny_single_value():
    """E1 B-H16 shape: one total over Q1 in CNY, from monthly fen rows."""
    df, keys = derive({"months": "sum", "unit": "cny"}, dims=(), rows=REV.groupby("month", as_index=False)["value"].sum())
    assert keys == [] and df["value"].tolist() == [16.0]
    assert compare_derived([{"value": 16.0}], df, keys)["outcome"] == "PASS"
    # the E1 failure: monthly rows submitted for a single value
    monthly = [{"month": m, "value": v} for m, v in (("2026-01", 400), ("2026-02", 500), ("2026-03", 700))]
    assert compare_derived(monthly, df, keys)["outcome"] == "FAIL"


def test_share_of_total_by_region_over_the_period():
    """E1 B-H08 shape: share of the year's revenue from one region."""
    df, keys = derive({"months": "sum", "across": {"op": "share", "dimension": "region"}})
    got = dict(zip(df["region"], df["value"]))
    assert got == pytest.approx({"north": 600 / 1600, "south": 1000 / 1600})
    rows = [{"region": "north", "value": 0.375}, {"region": "south", "value": 0.625}]
    assert compare_derived(rows, df, keys)["outcome"] == "PASS"
    assert compare_derived([{"region": "north", "value": 0.38}, {"region": "south", "value": 0.625}],
                           df, keys)["outcome"] == "FAIL"


def test_reported_precision_rule():
    df = pd.DataFrame({"value": [0.2246725]})
    assert compare_derived([{"value": 0.22467}], df, [])["outcome"] == "PASS"
    assert compare_derived([{"value": 0.2247}], df, [])["outcome"] == "PASS"
    assert compare_derived([{"value": 0.22}], df, [])["outcome"] == "FAIL"        # 2 significant digits
    assert compare_derived([{"value": 0.225}], df, [])["outcome"] == "PASS"         # 3 digits, within half a unit
    assert compare_derived([{"value": 0.226}], df, [])["outcome"] == "FAIL"


def test_mean_over_months_per_region_in_cny():
    """E1 B-H19 shape: average monthly revenue per region, in CNY."""
    df, keys = derive({"months": "mean", "unit": "cny"})
    assert keys == ["region"] and dict(zip(df["region"], df["value"])) == pytest.approx({"north": 2.0, "south": 10 / 3})


def test_difference_and_growth():
    df, keys = derive({"across": {"op": "difference", "dimension": "region", "a": "south", "b": "north"}})
    assert keys == ["month"] and df["value"].tolist() == [200, 100, 100]
    df, keys = derive({"months": "growth"})
    assert dict(zip(df["region"], df["value"])) == pytest.approx({"north": 2.0, "south": 1 / 3})


def test_pooled_rate_and_itt_difference():
    """E1 B-L20 shape: pooled control-minus-treatment churn over three campaign months, from counts."""
    num = pd.DataFrame({"month": ["2026-03", "2026-03", "2026-04", "2026-04"], "arm": ["control", "treatment"] * 2,
                        "value": [10, 5, 30, 20]})
    den = pd.DataFrame({"month": ["2026-03", "2026-03", "2026-04", "2026-04"], "arm": ["control", "treatment"] * 2,
                        "value": [100, 100, 100, 200]})
    rates = num.assign(value=num["value"] / den["value"])
    df, keys = derive({"months": "pool", "across": {"op": "difference", "dimension": "arm", "a": "control",
                                                     "b": "treatment"}},
                      metric="rate", dims=("arm",), months=("2026-03", "2026-04"), rows=rates, parts=(num, den))
    assert keys == [] and df["value"].tolist() == pytest.approx([40 / 200 - 25 / 300])
    # averaging the monthly rates is a different number; it must not pass as the pooled effect
    avg = (0.10 - 0.05 + 0.30 - 0.10) / 2
    assert compare_derived([{"value": avg}], df, keys)["outcome"] == "FAIL"


def test_top_n():
    df, keys = derive({"months": "sum", "across": {"op": "top", "dimension": "region", "n": 1}})
    assert df.to_dict("records") == [{"region": "south", "value": 1000}]


@pytest.mark.parametrize("raw, metric, msg", [
    ({"months": "sum"}, "active", "does not add up"),
    ({"months": "pool"}, "rev", "not a ratio"),
    ({"across": {"op": "share", "dimension": "region"}}, "rate", "additive"),
    ({"unit": "cny"}, "active", "fen to CNY"),
    ({"across": {"op": "difference", "dimension": "region", "a": "north"}}, "rev", "needs a and b"),
    ({"across": {"op": "share", "dimension": "plan"}}, "rev", "must be 'month' or one of metric_call.dimensions"),
    ({"months": "sum", "across": {"op": "top", "dimension": "month", "n": 1}}, "rev", "leave derive.months out"),
    ({"across": {"op": "share", "dimension": "month"}}, "active", "adds up over months"),
    ({"across": {"op": "difference", "dimension": "month", "a": "2026-03", "b": "2025-12"}}, "rev",
     "must be a month in metric_call.months"),
    ({"months": "cumsum"}, "active", "does not add up"),
    ({"months": "median"}, "rev", "must be one of"),
    ({"sql": "select 1"}, "rev", "keys months, across, keep, unit"),
])
def test_refusals_are_readable(raw, metric, msg):
    with pytest.raises(DeriveError, match=msg):
        normalize_derive(raw, metric, SPEC, call(metric))


def test_month_rollup_follows_the_semantic_model():
    assert month_rollup(SPEC, "rev") == "sum"
    assert month_rollup(SPEC, "active") is None              # distinct customers: summing double-counts
    assert month_rollup(SPEC, "enrolled") == "sum"           # owner says enrolments add up
    assert month_rollup(SPEC, "rate") is None


def test_no_derivation_is_none():
    assert normalize_derive(None, "rev", SPEC, call()) is None
    assert normalize_derive({"months": None, "across": None, "unit": None}, "rev", SPEC, call()) is None


def test_keep_reports_only_the_named_group():
    """E1 B-H08: share of annual revenue from the south region, a single number."""
    df, keys = derive({"months": "sum", "across": {"op": "share", "dimension": "region"}, "keep": {"region": "south"}})
    assert df.to_dict("records") == [{"region": "south", "value": pytest.approx(1000 / 1600)}]
    assert compare_derived([{"value": 0.625}], df, keys)["outcome"] == "PASS"                  # key may be left out
    assert compare_derived([{"region": "south", "value": 0.625}], df, keys)["outcome"] == "PASS"
    assert compare_derived([{"region": "north", "value": 0.625}], df, keys)["outcome"] == "FAIL"
    with pytest.raises(DeriveError, match="derive.keep"):
        normalize_derive({"keep": {"plan": "pro"}}, "rev", SPEC, call())


def test_keep_hint_when_the_answer_is_one_of_the_derived_rows():
    """E1b C-H15 / C-L28: 'the west region's share' answered with the west row; v0.2 replied
    "rows [west] != derived rows [east, north, ...]" and the agents then submitted every region (wrong)."""
    df, keys = derive({"months": "sum", "across": {"op": "share", "dimension": "region"}})
    res = compare_derived([{"region": "south", "value": 0.625}], df, keys)
    assert res["outcome"] == "FAIL" and "derive.keep = {'region': ['south']}" in res["reason"]
    res = compare_derived([{"value": 0.625}], df, keys)                         # no region column at all
    assert res["outcome"] == "FAIL" and "derive.keep" in res["reason"]
    full = [{"region": "north", "value": 0.375}, {"region": "south", "value": 0.625}]
    assert "derive.keep" not in compare_derived(full[:1] + [{"region": "west", "value": 0.1}], df, keys)["reason"]


def test_across_months_highest_month_and_month_difference():
    """E1b C-H20 ('which month in 2025 had the highest churn rate') and C-H31 (February 2026 minus
    February 2025): agents declared across.dimension = 'month', which v0.2 refused."""
    total = REV.groupby("month", as_index=False)["value"].sum()
    df, keys = derive({"across": {"op": "top", "dimension": "month", "n": 1}}, dims=(), rows=total)
    assert df.to_dict("records") == [{"month": "2026-03", "value": 700}]
    assert compare_derived([{"value": 700}], df, keys)["outcome"] == "PASS"             # month may be left out
    assert compare_derived([{"month": "2026-03", "value": 700}], df, keys)["outcome"] == "PASS"
    assert compare_derived([{"month": "2026-02", "value": 700}], df, keys)["outcome"] == "FAIL"
    df, keys = derive({"across": {"op": "difference", "dimension": "month", "a": "2026-03", "b": "2026-01"}})
    assert keys == ["region"] and df.to_dict("records") == [{"region": "north", "value": 200},
                                                           {"region": "south", "value": 100}]


def test_share_with_a_reports_that_member():
    """E1b C-H15: the agent declared across {op: share, dimension: region, a: west}; v0.2 ignored a."""
    df, keys = derive({"months": "sum", "across": {"op": "share", "dimension": "region", "a": "south"}})
    assert df.to_dict("records") == [{"region": "south", "value": pytest.approx(1000 / 1600)}]
    assert compare_derived([{"value": 0.625}], df, keys)["outcome"] == "PASS"
    total = REV.groupby("month", as_index=False)["value"].sum()
    df, keys = derive({"across": {"op": "share", "dimension": "month", "a": "2026-03"}}, dims=(), rows=total)
    assert df.to_dict("records") == [{"month": "2026-03", "value": pytest.approx(700 / 1600)}]


def test_difference_refuses_groups_with_one_side():
    rows = REV[~((REV["region"] == "south") & (REV["month"] == "2026-01"))]
    with pytest.raises(DeriveError, match="do not cover the same"):
        derive({"across": {"op": "difference", "dimension": "month", "a": "2026-03", "b": "2026-01"}}, rows=rows)


def test_running_total_keeps_one_row_per_month():
    """E1b C-H32 (H v0.3 F17): cumulative revenue at the end of each month, per region and in total."""
    df, keys = derive({"months": "cumsum", "unit": "cny"})
    assert keys == ["month", "region"]
    got = {(m, r): v for m, r, v in zip(df["month"], df["region"], df["value"])}
    assert got == pytest.approx({("2026-01", "north"): 1.0, ("2026-02", "north"): 3.0, ("2026-03", "north"): 6.0,
                                 ("2026-01", "south"): 3.0, ("2026-02", "south"): 6.0, ("2026-03", "south"): 10.0})
    total = REV.groupby("month", as_index=False)["value"].sum()
    df, keys = derive({"months": "cumsum"}, dims=(), rows=total)
    assert keys == ["month"] and df["value"].tolist() == [400, 900, 1600]
    assert compare_derived([{"month": m, "value": v} for m, v in (("2026-01", 400), ("2026-02", 900), ("2026-03", 1600))],
                           df, keys)["outcome"] == "PASS"
    # the monthly values themselves are not the running total
    assert compare_derived([{"month": m, "value": v} for m, v in (("2026-01", 400), ("2026-02", 500), ("2026-03", 700))],
                           df, keys)["outcome"] == "FAIL"
