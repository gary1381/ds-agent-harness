"""H v0.5 regression tests built from E3a arm-H false holds and harness-induced errors
(docs/experiments/H_v0.5_PLAN.md). The synthetic world stands in for the retail one: each test keeps
the shape of the E3a question it cites. As in test_answer_v02, expected values are computed here from
the governed rows with plain pandas, independently of `tools/derive.py`."""
from __future__ import annotations

import pytest

from dsharness import tools as T
from dsharness.tools.analytics import months_from_text

from test_answer_v02 import private_ws, rows, verdict


@pytest.fixture(scope="module")
def analyst(small_ws, tmp_path_factory):
    ctx = T.make_context(private_ws(small_ws, tmp_path_factory.mktemp("v05") / "ws"), "score", "analyst",
                         "agent:analyst-v05")
    yield ctx
    ctx.close()


def test_year_through_a_month_is_january_to_that_month():
    """F26, E3a R-H07: 'each month of 2025 through November' was read as all of 2025, so every right call
    (2025-01..2025-11, or 2024-12..2025-11 with the base month) was contradicted."""
    q = ("Show me the month-over-month percentage change in gross product sales for each month of 2025 through "
         "November, as a percent (for example, 12.5 means up 12.5%).")
    assert months_from_text(q) == (("2025-01", "2025-11"), None)
    assert months_from_text("for each month of 2026 up to March") == (("2026-01", "2026-03"), None)
    # an explicit range is unchanged
    assert months_from_text("from January through November 2025") == (("2025-01", "2025-11"), None)


def test_month_over_month_change_for_each_month(analyst):
    """F21, E3a R-H07: month-over-month change for each month had no derive step (growth collapses the
    months), so five right answers were held."""
    q = ("Show me the month-over-month percentage change in net revenue for each month of 2026 through March, "
         "as a percent.")
    df, call = rows(analyst, "net_revenue_fen", [], ["2025-12", "2026-03"])
    v = df.sort_values("month").set_index("month")["value"].astype(float)
    pct = (v / v.shift(1) - 1) * 100
    answer = [{"month": m, "value": float(pct[m])} for m in ("2026-01", "2026-02", "2026-03")]
    d = {"months": "change", "unit": "percent"}
    r = T.submit_answer(analyst, q, answer, metric_call=call, derive=d)
    assert verdict(r) == ("VERIFIED", "ALLOW"), (r["reasons"], r["warnings"])
    # the base month is not part of the answer, and a wrong month is caught
    r = T.submit_answer(analyst, q, [{"month": "2025-12", "value": 0.0}, *answer], metric_call=call, derive=d)
    assert r["tier"] == "FLAGGED"
    bad = [dict(answer[0]), *answer[1:]]
    bad[0]["value"] += 1.0
    r = T.submit_answer(analyst, q, bad, metric_call=call, derive=d)
    assert verdict(r) == ("FLAGGED", "REJECT")


def test_change_needs_a_base_month(analyst):
    _, call = rows(analyst, "net_revenue_fen", [], ["2026-01", "2026-01"])
    r = T.submit_answer(analyst, "Month-over-month change in net revenue in January 2026?", [{"value": 1.0}],
                        metric_call=call, derive={"months": "change"})
    assert r["kind"] == "invalid_argument" and "base" in r["error"], r


def test_share_reports_both_named_members(analyst):
    """F22, E3a R-L40: 'UK and non-UK shares of the quarter' with across.share a = UK, b = non-UK kept only the
    UK row, FAILED the right pair, and one agent resubmitted the UK share alone (harness-induced)."""
    q = "What shares of January 2026 net revenue came from the north and the south regions? Give shares between 0 and 1."
    df, call = rows(analyst, "net_revenue_fen", ["region"], ["2026-01", "2026-01"])
    by = df.set_index("region")["value"].astype(float)
    share = by / by.sum()
    pair = [{"region": "north", "value": float(share["north"])}, {"region": "south", "value": float(share["south"])}]
    d = {"across": {"op": "share", "dimension": "region", "a": "north", "b": "south"}}
    r = T.submit_answer(analyst, q, pair, metric_call=call, derive=d)
    assert verdict(r) == ("VERIFIED", "ALLOW"), (r["reasons"], r["warnings"])
    # a alone still means "only this member" (E1b C-H15), and the FAIL now says how to keep the other row
    r = T.submit_answer(analyst, q, pair, metric_call=call,
                        derive={"across": {"op": "share", "dimension": "region", "a": "north"}})
    assert r["tier"] == "FLAGGED"
    assert "name them all" in r["checks"]["reconcile"]["governed"]["reason"]


# ---- F18-F20 need an event metric that is a distinct count: paying customers per revenue month ----------
def _paying_model(path):
    import yaml
    from dsharness.context.semantic import DEFAULT_SEMANTIC_MODEL
    spec = yaml.safe_load(DEFAULT_SEMANTIC_MODEL.read_text(encoding="utf-8"))
    spec["datasets"]["invoices"]["measures"]["customers"] = {"agg": "count_distinct", "expr": "b.customer_id"}
    spec["metrics"]["paying_customers"] = {
        "type": "simple", "terms": [r"\bpaying customers?\b"], "label": "Paying customers", "unit": "customers",
        "owner": "test", "description": "Distinct customers with an invoice line in the month.", "dataset": "invoices",
        "measure": "customers", "dimensions": ["region", "channel", "plan"], "caveats": ["test metric"]}
    path.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")


@pytest.fixture(scope="module")
def own(small_ws, tmp_path_factory):
    ws = private_ws(small_ws, tmp_path_factory.mktemp("v05own") / "ws")
    ws.context.mkdir()
    _paying_model(ws.context / "semantic_model.yaml")
    ctx = T.make_context(ws, "score", "analyst", "agent:analyst-v05-own")
    yield ctx
    ctx.close()


def _sql(ctx, sql):
    return ctx.con.execute(sql).fetchall()


def test_period_distinct_count_counts_each_customer_once(own):
    """F19, E3a R-L31: 'per active customer across October to December' counts each customer once in the quarter
    (1,153.26), not month by month; distinct counts had no period value, so the question could not be declared."""
    q = "How many paying customers did we have in Q1 2026, counting each customer once?"
    df, call = rows(own, "paying_customers", [], ["2026-01", "2026-03"])
    (truth,), = _sql(own, "SELECT COUNT(DISTINCT customer_id) FROM silver.invoices WHERE NOT is_test_account "
                          "AND revenue_month BETWEEN '2026-01' AND '2026-03'")
    assert truth < df["value"].sum()              # customers who paid in two months: the monthly sum double-counts
    r = T.submit_answer(own, q, [{"value": int(truth)}], metric_call=call, derive={"months": "total"})
    assert verdict(r) == ("VERIFIED", "ALLOW"), (r["reasons"], r["warnings"])
    r = T.submit_answer(own, q, [{"value": int(df["value"].sum())}], metric_call=call, derive={"months": "total"})
    assert verdict(r) == ("FLAGGED", "REJECT")


def test_total_is_refused_for_a_point_in_time_metric(own):
    _, call = rows(own, "active_subscribers_eom", [], ["2026-01", "2026-03"])
    r = T.submit_answer(own, "How many active subscribers did we have in Q1 2026?", [{"value": 1}],
                        metric_call=call, derive={"months": "total"})
    assert r["kind"] == "invalid_argument" and "point in time" in r["error"], r


def test_ratio_of_two_metrics_per_month_and_over_the_period(own):
    """F18, E3a R-L11 / R-L13 / R-L39 / R-L31: per customer, per order and share-of-customers questions had no
    governed ratio, so agents declared the numerator and reconcile compared a ratio with a count (FAIL)."""
    q1 = "What was net revenue per paying customer in February 2026, in CNY?"
    rev, call = rows(own, "net_revenue_fen", [], ["2026-02", "2026-02"])
    cust, _ = rows(own, "paying_customers", [], ["2026-02", "2026-02"])
    per = float(rev["value"].iloc[0]) / float(cust["value"].iloc[0]) / 100
    d = {"divide_by": {"metric": "paying_customers"}, "unit": "cny"}
    r = T.submit_answer(own, q1, [{"value": per}], metric_call=call, derive=d)
    assert verdict(r) == ("VERIFIED", "ALLOW"), (r["reasons"], r["warnings"])
    # the E3a failure: the ratio submitted with the numerator alone declared
    r = T.submit_answer(own, q1, [{"value": per}], metric_call=call, derive={"unit": "cny"})
    assert r["tier"] != "VERIFIED"
    # over a quarter: the quarter's revenue over customers counted once in the quarter
    q2 = "What was net revenue per paying customer across Q1 2026, in CNY, counting each customer once?"
    rev, call = rows(own, "net_revenue_fen", ["region"], ["2026-01", "2026-03"])
    tot = rev.groupby("region")["value"].sum().astype(float)
    den = dict(_sql(own, "SELECT region, COUNT(DISTINCT customer_id) FROM silver.invoices WHERE NOT is_test_account "
                         "AND revenue_month BETWEEN '2026-01' AND '2026-03' GROUP BY region"))
    answer = [{"region": g, "value": float(tot[g]) / den[g] / 100} for g in tot.index]
    d = {"months": "total", "divide_by": {"metric": "paying_customers"}, "unit": "cny"}
    r = T.submit_answer(own, q2, answer, metric_call=call, derive=d)
    assert verdict(r) == ("VERIFIED", "ALLOW"), (r["reasons"], r["warnings"])
    # the mean of monthly ratios is a different number (E3a REVIEW: 781.00 vs 1,153.26) and is caught
    mon, _ = rows(own, "paying_customers", ["region"], ["2026-01", "2026-03"])
    m = rev.merge(mon, on=["month", "region"])
    mean = (m["value_x"].astype(float) / m["value_y"].astype(float)).groupby(m["region"]).mean() / 100
    r = T.submit_answer(own, q2, [{"region": g, "value": float(mean[g])} for g in mean.index], metric_call=call,
                        derive=d)
    assert r["tier"] == "FLAGGED"


def test_divide_by_is_validated(own):
    _, call = rows(own, "net_revenue_fen", ["invoice_type"], ["2026-01", "2026-03"])
    r = T.submit_answer(own, "Net revenue per paying customer by invoice type in Q1 2026?", [{"value": 1.0}],
                        metric_call=call, derive={"months": "total", "divide_by": {"metric": "paying_customers"}})
    assert r["kind"] == "invalid_argument" and "share the breakdown" in r["error"], r
    _, call = rows(own, "net_revenue_fen", [], ["2026-01", "2026-03"])
    r = T.submit_answer(own, "Net revenue per paying customer in Q1 2026?", [{"value": 1.0}], metric_call=call,
                        derive={"months": "sum", "divide_by": {"metric": "paying_customers"}})
    assert r["kind"] == "invalid_argument" and "divide_by" in r["error"], r


def test_growth_between_two_quarters(own):
    """F20, E3a R-L18 / R-H14: 'Q2 to Q3' growth was recomputed from the first and last month of the range
    (99.5% instead of 22.47%), FAILED in all ten attempts, and one agent fell back to monthly rows."""
    q = "By what percentage did net revenue change from Q4 2025 to Q1 2026? Use Q4 2025 as the base."
    df, call = rows(own, "net_revenue_fen", [], ["2025-10", "2026-03"])
    v = df.set_index("month")["value"].astype(float)
    q4, q1 = v["2025-10":"2025-12"].sum(), v["2026-01":"2026-03"].sum()
    right = (q1 / q4 - 1) * 100
    d = {"months": "growth", "windows": [["2025-10", "2025-12"], ["2026-01", "2026-03"]], "unit": "percent"}
    r = T.submit_answer(own, q, [{"value": right}], metric_call=call, derive=d)
    assert verdict(r) == ("VERIFIED", "ALLOW"), (r["reasons"], r["warnings"])
    first_last = (v["2026-03"] / v["2025-10"] - 1) * 100       # what v0.4 recomputed
    r = T.submit_answer(own, q, [{"value": first_last}], metric_call=call, derive=d)
    assert verdict(r) == ("FLAGGED", "REJECT")
    r = T.submit_answer(own, q, [{"value": right}], metric_call=call,
                        derive={**d, "windows": [["2025-07", "2025-09"], ["2026-01", "2026-03"]]})
    assert r["kind"] == "invalid_argument" and "inside metric_call.months" in r["error"], r


def test_rows_keyed_by_an_ungoverned_column_are_unknown_not_wrong(analyst):
    """F24, E3a R-H29 Claude r2, r3: a right top-5 by item code declared as the metric with no dimension was
    FAILED 'rows lack a month column'; no governed metric has item code, so the check cannot know."""
    q = "Which two products earned the most net revenue in Q1 2026?"
    _, call = rows(analyst, "net_revenue_fen", [], ["2026-01", "2026-03"])
    r = T.submit_answer(analyst, q, [{"product": "A-100", "value": 5.0}, {"product": "B-200", "value": 4.0}],
                        metric_call=call)
    assert r["tier"] != "VERIFIED" and r["gate"]["verdict"] != "ALLOW"
    assert any("no governed metric covers this breakdown" in x for x in r["reasons"]), r["reasons"]
    assert not any("rows lack a 'month' column" in x for x in r["reasons"]), r["reasons"]
    # a governed dimension left undeclared is still the agent's claim disagreeing with its rows
    df, call = rows(analyst, "net_revenue_fen", [], ["2026-01", "2026-03"])
    reg, _ = rows(analyst, "net_revenue_fen", ["region"], ["2026-01", "2026-03"])
    per = reg.groupby("region")["value"].sum()
    r = T.submit_answer(analyst, q, [{"region": g, "value": float(per[g])} for g in per.index], metric_call=call)
    assert verdict(r) == ("FLAGGED", "REJECT")
    assert any("rows lack a 'month' column" in x for x in r["reasons"]), r["reasons"]


def test_reexecution_pairs_a_renamed_value_column():
    """F27, E3a: 23 arm-H submissions FAILED re-execution only because the SQL said `AS v` (or kept helper
    counts) while the answer, as asked, called the column 'value'."""
    from dsharness.tools.analytics import compare_row_multisets as cmp
    q_rows = [("A", 3.0), ("B", 2.0)]
    ok = cmp([{"sku": "A", "value": 3.0}, {"sku": "B", "value": 2.0}], ["sku", "v"], q_rows)
    assert ok["outcome"] == "PASS" and ok["column_notes"] == ["answer column 'value' compared with SQL column 'v'"]
    assert cmp([{"sku": "A", "value": 3.5}, {"sku": "B", "value": 2.0}], ["sku", "v"], q_rows)["outcome"] == "FAIL"
    # the SQL's own one-column expression
    assert cmp([{"value": 7.25}], ["(sum(amount) / count(DISTINCT buyer_id))"], [(7.25,)])["outcome"] == "PASS"
    # numeric helper columns may be left out; a text column may not (it is a key)
    helper = [("2026-01", 10, 4, 0.4), ("2026-02", 8, 2, 0.25)]
    cols = ["month", "active_customers", "repeat_customers", "value"]
    assert cmp([{"month": "2026-01", "value": 0.4}, {"month": "2026-02", "value": 0.25}], cols, helper)["outcome"] \
        == "PASS"
    assert cmp([{"value": 0.4}, {"value": 0.25}], cols, helper)["outcome"] == "FAIL"
    # two names left on each side stay a mismatch
    assert cmp([{"x": "A", "value": 3.0}], ["sku", "v"], [("A", 3.0)])["outcome"] == "FAIL"


def test_a_qualifier_refines_a_generic_term(tmp_path):
    """F23, E3a R-H08 / R-L40 / R-H14: 毛产品销售额 reached net sales through 销售额, so declaring gross sales was
    'contradicted' and Codex R-H14 r1 switched to net. A generic word is only the default reading: a qualifier of
    a metric that refines it makes that metric the reading, and a bare generic word still means the default."""
    import copy
    import yaml
    from dsharness.context.semantic import DEFAULT_SEMANTIC_MODEL, load_semantic_model
    from dsharness.tools.analytics import claim_contradictions, match_governed_metrics
    spec = yaml.safe_load(DEFAULT_SEMANTIC_MODEL.read_text(encoding="utf-8"))
    net = spec["metrics"]["net_revenue_fen"]
    gross = copy.deepcopy(net)
    net["terms"], net["generic_terms"] = [r"\bnet revenues?\b|\brefund(?:s|ed)?\b"], [r"\brevenues?\b|收入"]
    gross.update(terms=[r"\bgross revenues?\b"], qualifiers=[r"\bgross\b|毛"], suppresses=["net_revenue_fen"],
                 label="Gross revenue")
    spec["metrics"]["gross_revenue_fen"] = gross
    path = tmp_path / "semantic_model.yaml"
    path.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
    model = load_semantic_model(path)
    assert match_governed_metrics(model, "2026年1月的毛产品收入是多少？") == ["gross_revenue_fen"]
    assert match_governed_metrics(model, "What was gross product revenue in January 2026?") == ["gross_revenue_fen"]
    assert match_governed_metrics(model, "What was revenue in January 2026?") == ["net_revenue_fen"]
    assert match_governed_metrics(model, "What was net revenue in January 2026?") == ["net_revenue_fen"]

    def call(metric):
        return {"metric": metric, "dimensions": [], "months": ["2026-01", "2026-01"], "filters": None}
    q = "2026年1月的毛产品收入是多少？"
    assert not [c for c in claim_contradictions(model, q, call("gross_revenue_fen")) if "asks about" in c]
    assert [c for c in claim_contradictions(model, q, call("net_revenue_fen")) if "asks about" in c]
    # the default stands without a qualifier: gross for plain 'revenue' is the definitional error
    assert [c for c in claim_contradictions(model, "What was revenue in January 2026?", call("gross_revenue_fen"))
            if "asks about" in c]
    # only just before the generic word: elsewhere the default reading stands
    assert match_governed_metrics(model, "What was revenue in January 2026, not counting gross adjustments?") \
        == ["net_revenue_fen"]
    # a qualifier with no generic word to refine names nothing
    assert match_governed_metrics(model, "What was the gross churn in January 2026?") == ["churned_customers"]
    bad = copy.deepcopy(spec)
    bad["metrics"]["addon_units"]["qualifiers"] = [r"\bextra\b"]
    path.write_text(yaml.safe_dump(bad, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="qualifiers"):
        load_semantic_model(path)


# ---- from the blind review of the v0.5 patch (GPT-6-Luna, 2026-11-07) ------------------------------------
def test_a_step_over_months_must_cover_the_asked_months(analyst):
    """A Q1 total declared over December to March VERIFIED (a gap older than v0.5: the claim check allows a wider
    call because a per-month answer still holds the asked rows, but a total over a wider call is another number)."""
    q = "What was total net revenue from January to March 2026, in CNY?"
    df, call = rows(analyst, "net_revenue_fen", [], ["2025-12", "2026-03"])
    r = T.submit_answer(analyst, q, [{"value": float(df["value"].sum()) / 100}], metric_call=call,
                        derive={"months": "sum", "unit": "cny"})
    assert r["tier"] != "VERIFIED" and r["gate"]["verdict"] != "ALLOW"
    assert any("declare exactly those months" in w for w in r["warnings"]), r["warnings"]
    # one month year on year is still one call from the base month to the asked month
    q = "By what percentage did net revenue grow in March 2026 compared with March 2025?"
    df, call = rows(analyst, "net_revenue_fen", [], ["2025-03", "2026-03"])
    v = df.set_index("month")["value"].astype(float)
    r = T.submit_answer(analyst, q, [{"value": (v["2026-03"] / v["2025-03"] - 1) * 100}], metric_call=call,
                        derive={"months": "growth", "unit": "percent"})
    assert verdict(r) == ("VERIFIED", "ALLOW"), (r["reasons"], r["warnings"])
    # month over month for one month; and "cumulative ... at the end of" a month is a sum from an earlier start
    df, call = rows(analyst, "net_revenue_fen", [], ["2026-01", "2026-02"])
    v = df.set_index("month")["value"].astype(float)
    r = T.submit_answer(analyst, "How much did net revenue grow in February 2026 over the month before, in percent?",
                        [{"value": (v["2026-02"] / v["2026-01"] - 1) * 100}], metric_call=call,
                        derive={"months": "growth", "unit": "percent"})
    assert verdict(r) == ("VERIFIED", "ALLOW"), (r["reasons"], r["warnings"])
    r = T.submit_answer(analyst, "What was the cumulative net revenue at the end of February 2026, in fen?",
                        [{"value": float(v.sum())}], metric_call=call, derive={"months": "sum"})
    assert verdict(r) == ("VERIFIED", "ALLOW"), (r["reasons"], r["warnings"])
    # a running total starts where the question starts
    q = "Show the running total of net revenue for each month from January to March 2026."
    df, call = rows(analyst, "net_revenue_fen", [], ["2025-12", "2026-03"])
    run = df.sort_values("month")["value"].cumsum()
    r = T.submit_answer(analyst, q, [{"month": m, "value": float(x)} for m, x in zip(df.sort_values("month")["month"], run)],
                        metric_call=call, derive={"months": "cumsum"})
    assert any("running total starts" in w for w in r["warnings"]), r["warnings"]


def test_growth_windows_are_the_asked_periods(analyst):
    q = "By what percentage did net revenue change from Q4 2025 to Q1 2026?"
    df, call = rows(analyst, "net_revenue_fen", [], ["2025-10", "2026-03"])
    v = df.set_index("month")["value"].astype(float)
    # two months of each quarter: not periods of a growth question
    r = T.submit_answer(analyst, q, [{"value": (v["2026-02":"2026-03"].sum() / v["2025-10":"2025-11"].sum() - 1) * 100}],
                        metric_call=call, derive={"months": "growth", "unit": "percent",
                                                  "windows": [["2025-10", "2025-11"], ["2026-02", "2026-03"]]})
    assert r["kind"] == "invalid_argument" and "back to back" in r["error"], r
    # back to back but not the asked quarters
    r = T.submit_answer(analyst, q, [{"value": (v["2026-01":"2026-03"].sum() / v["2025-10":"2025-12"].sum() - 1) * 100}],
                        metric_call=call, derive={"months": "growth", "unit": "percent",
                                                  "windows": [["2025-10", "2025-12"], ["2026-01", "2026-03"]]})
    assert verdict(r) == ("VERIFIED", "ALLOW"), (r["reasons"], r["warnings"])
    r = T.submit_answer(analyst, q, [{"value": (v["2026-02":"2026-03"].sum() / v["2025-12":"2026-01"].sum() - 1) * 100}],
                        metric_call=call, derive={"months": "growth", "unit": "percent",
                                                  "windows": [["2025-12", "2026-01"], ["2026-02", "2026-03"]]})
    assert any("derive.windows compare" in w for w in r["warnings"]), r["warnings"]
    # the same months a year apart
    df, call = rows(analyst, "net_revenue_fen", [], ["2025-01", "2026-03"])
    v = df.set_index("month")["value"].astype(float)
    q = "How much did net revenue grow in January to March 2026 compared with the same months of 2025?"
    r = T.submit_answer(analyst, q, [{"value": (v["2026-01":"2026-03"].sum() / v["2025-01":"2025-03"].sum() - 1) * 100}],
                        metric_call=call, derive={"months": "growth", "unit": "percent",
                                                  "windows": [["2025-01", "2025-03"], ["2026-01", "2026-03"]]})
    assert verdict(r) == ("VERIFIED", "ALLOW"), (r["reasons"], r["warnings"])


def test_change_is_against_the_calendar_month_before():
    """A rate or an average omits a month with no data; the change for the month after it has no base (it was
    computed against the row before, two months back)."""
    import pandas as pd
    from dsharness.tools.derive import apply_derive
    gov = pd.DataFrame({"month": ["2026-01", "2026-03", "2026-04", "2026-01", "2026-02"],
                        "region": ["north", "north", "north", "south", "south"],
                        "value": [0.10, 0.20, 0.25, 0.30, 0.15]})
    d = {"months": "change", "across": None, "keep": None, "unit": None}
    out, keys = apply_derive(gov, d, ["region"], months=("2026-01", "2026-04"))
    got = {(r.month, r.region): round(r.value, 6) for r in out.itertuples()}
    assert got == {("2026-04", "north"): 0.25, ("2026-02", "south"): -0.5}, got
    assert keys == ["month", "region"]
