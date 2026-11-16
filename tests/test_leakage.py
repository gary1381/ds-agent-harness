"""Leakage checks: time travel on the PIT builds and the static temporal-tag lint.

The temporal tags for silver tables are spelled out here so these tests do not depend on
the context catalog; one optional test checks the real catalog agrees."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from dsharness.ml.features import NAIVE_FEATURES, TRUSTED_FEATURES, FeatureDef
from dsharness.verify.leakage import (check_feature, pit_pairs, static_temporal_check, temporal_tags,
                                      time_travel_check)
from dsharness.workspace import CUTOFFS, PIT_CUTOFFS


def _tags(table: str, **cols: str) -> dict[str, str]:
    return {f"silver.{table}.{c}": t for c, t in cols.items()}


SILVER_TAGS = {
    **_tags("customers", customer_id="static", created_at="event_time", region="static", channel="static",
            is_test_account="static"),
    **_tags("subscriptions", subscription_id="static", customer_id="static", plan="static",
            monthly_price_fen="mutable_current", started_at="event_time", ended_at="mutable_current",
            cancel_reason="post_outcome", updated_at="availability_time"),
    **_tags("invoices", invoice_id="static", customer_id="static", invoice_type="static", amount_fen="static",
            unit_corrected="static", issued_at="event_time", available_at="availability_time",
            revenue_month="event_time", original_invoice_id="static", region="static", is_test_account="static"),
    **_tags("usage_monthly", customer_id="static", usage_month="event_time", minutes="static", sessions="static",
            pricing_page_views="static",
            available_at="availability_time"),
    **_tags("tickets", ticket_id="static", customer_id="static", created_at="event_time", category="static",
            csat="static", available_at="availability_time"),
}
LEAKY_DYNAMIC = ("minutes_last_month_naive", "has_cancel_reason", "known_end_flag")


@pytest.fixture(scope="module")
def builds(build_reports) -> dict:
    """as_of -> (early build, late build) for the two PIT pairs."""
    late = build_reports[CUTOFFS["train"]].build_id
    return {as_of: (build_reports[cut].build_id, late) for as_of, cut in pit_pairs()}


def test_pit_pairs_follow_workspace_cutoffs():
    assert pit_pairs() == [("2026-03-01", PIT_CUTOFFS[0]), ("2026-04-01", PIT_CUTOFFS[1])]


@pytest.mark.parametrize("name", sorted(TRUSTED_FEATURES))
def test_trusted_features_survive_time_travel(small_ws, builds, name):
    for as_of, (early, late) in builds.items():
        r = time_travel_check(small_ws, TRUSTED_FEATURES[name], as_of, early, late)
        assert r["outcome"] == "PASS", (as_of, r["n_diff"], r["examples"], r.get("reason"))
        assert r["n_compared"] > 500 and r["n_diff"] == 0


@pytest.mark.parametrize("name", LEAKY_DYNAMIC)
def test_naive_features_fail_time_travel(small_ws, builds, name):
    for as_of, (early, late) in builds.items():
        r = time_travel_check(small_ws, NAIVE_FEATURES[name], as_of, early, late)
        assert r["outcome"] == "FAIL", (as_of, r)
        assert r["n_diff"] > 0 and 0 < len(r["examples"]) <= 5
        ex = r["examples"][0]
        assert ex["early"] != ex["late"]


def test_membership_difference_is_reported_not_counted_as_leak(small_ws, builds):
    early, late = builds["2026-03-01"]
    r = time_travel_check(small_ws, TRUSTED_FEATURES["tenure_months"], "2026-03-01", early, late)
    m = r["membership"]
    # a churn just before as_of that was reported after it: in the early population only
    assert m["only_in_early"] > 0 and m["only_in_late"] == 0
    assert r["n_compared"] == m["n_early"] - m["only_in_early"]
    assert r["outcome"] == "PASS"


def test_mutable_price_asymmetry(small_ws, builds):
    """current_price_fen reads the CURRENT price. No price changed between the PIT builds and the
    train build (the rise is 2026-08-01), so time travel cannot see the leak -- only the static
    check can. Both checks are required for exactly this reason."""
    f = NAIVE_FEATURES["current_price_fen"]
    for as_of, (early, late) in builds.items():
        assert time_travel_check(small_ws, f, as_of, early, late)["outcome"] == "PASS"
    st = static_temporal_check(f.sql, SILVER_TAGS)
    assert st["outcome"] == "FAIL"
    assert [(x["rule"], x["column"]) for x in st["findings"]] == [("mutable_current_unguarded", "monthly_price_fen")]
    combined = check_feature(small_ws, f, SILVER_TAGS)
    assert combined["outcome"] == "FAIL"
    assert all(t["outcome"] == "PASS" for t in combined["time_travel"])
    assert any("monthly_price_fen" in r for r in combined["reasons"])


def test_time_travel_refuses_misconfigured_builds(small_ws, build_reports, builds):
    f = TRUSTED_FEATURES["tickets_90d"]
    early_feb, late = builds["2026-03-01"]
    # early build is not the day before as_of: a PIT-correct feature could differ -> not a valid test
    r = time_travel_check(small_ws, f, "2026-04-01", early_feb, late)
    assert r["outcome"] == "ERROR" and "day before" in r["reason"]
    # late build earlier than early
    assert time_travel_check(small_ws, f, "2026-03-01", early_feb, early_feb)["outcome"] == "ERROR"
    # FAILED build (billing schema v2) and unknown build
    drift = build_reports[CUTOFFS["drift"]].build_id
    r = time_travel_check(small_ws, f, "2026-03-01", early_feb, drift)
    assert r["outcome"] == "ERROR" and "FAILED" in r["reason"]
    assert time_travel_check(small_ws, f, "2026-03-01", "2026-02-28_00000000", late)["outcome"] == "ERROR"
    assert time_travel_check(small_ws, f, "2026-03-15", early_feb, late)["outcome"] == "ERROR"


def test_broken_feature_is_error_not_pass(small_ws, builds):
    early, late = builds["2026-03-01"]
    bad_cols = FeatureDef("bad_cols", "SELECT customer_id, 1 AS v, 2 AS w FROM silver.customers", "x", "proposed")
    dup = FeatureDef("dup_rows", "SELECT customer_id, 1 AS value FROM silver.invoices", "x", "proposed")
    broken = FeatureDef("broken", "SELECT customer_id, nope AS value FROM silver.customers", "x", "proposed")
    for f in (bad_cols, dup, broken):
        r = time_travel_check(small_ws, f, "2026-03-01", early, late)
        assert r["outcome"] == "ERROR", r
        assert check_feature(small_ws, f, SILVER_TAGS)["outcome"] in ("ERROR", "FAIL")


# ------------------------------------------------------------------ static
@pytest.mark.parametrize("name", sorted(TRUSTED_FEATURES))
def test_static_trusted_pass(name):
    r = static_temporal_check(TRUSTED_FEATURES[name].sql, SILVER_TAGS)
    assert r["outcome"] == "PASS", r["findings"]


def test_static_naive_flagged():
    expect = {"current_price_fen": ("FAIL", "mutable_current_unguarded"),
              "has_cancel_reason": ("FAIL", "post_outcome_unguarded"),
              "known_end_flag": ("FAIL", "mutable_current_unguarded"),
              "minutes_last_month_naive": ("INCONCLUSIVE", "event_rows_unbounded")}
    for name, (outcome, rule) in expect.items():
        r = static_temporal_check(NAIVE_FEATURES[name].sql, SILVER_TAGS)
        assert r["outcome"] == outcome and r["findings"][0]["rule"] == rule, (name, r)


@pytest.mark.parametrize("sql,outcome,rule", [
    # ended_at "as known by as_of": guarded by the row's availability column
    ("SELECT customer_id, CAST(CASE WHEN updated_at < {as_of} THEN ended_at END IS NOT NULL AS INTEGER) AS value "
     "FROM silver.subscriptions", "PASS", None),
    ("SELECT customer_id, COUNT(*) AS value FROM silver.tickets GROUP BY customer_id",
     "INCONCLUSIVE", "event_rows_unbounded"),
    ("SELECT customer_id, COUNT(*) AS value FROM silver.tickets "
     "WHERE created_at >= {as_of} AND available_at < {as_of} GROUP BY 1", "FAIL", "looks_forward"),
    ("SELECT customer_id, COUNT(*) AS value FROM silver.tickets "
     "WHERE available_at < {as_of} + INTERVAL 30 DAY GROUP BY 1", "FAIL", "looks_forward"),
    ("SELECT customer_id, COUNT(*) AS value FROM silver.tickets "
     "WHERE available_at BETWEEN {as_of} - INTERVAL 90 DAY AND {as_of} GROUP BY 1", "PASS", None),
    ("SELECT customer_id, COUNT(*) AS value FROM silver.tickets WHERE {as_of} > available_at GROUP BY 1",
     "PASS", None),
    # guard applied on a derived table's column still bounds the base table
    ("SELECT u.customer_id, SUM(u.minutes) AS value FROM (SELECT customer_id, minutes, available_at "
     "FROM silver.usage_monthly) u WHERE u.available_at < {as_of} GROUP BY 1", "PASS", None),
    # SELECT * through a CTE does not hide a mutable column
    ("WITH s AS (SELECT * FROM silver.subscriptions) SELECT customer_id, monthly_price_fen AS value FROM s",
     "FAIL", "mutable_current_unguarded"),
    # unqualified column through a join resolves to its table
    ("SELECT p.customer_id, max(monthly_price_fen) AS value FROM population p "
     "JOIN silver.subscriptions USING (customer_id) GROUP BY 1", "FAIL", "mutable_current_unguarded"),
    # correlated scalar subquery
    ("SELECT p.customer_id, (SELECT max(ended_at) FROM silver.subscriptions s "
     "WHERE s.customer_id = p.customer_id) AS value FROM population p", "FAIL", "mutable_current_unguarded"),
    # UNION branches are each analysed
    ("SELECT customer_id, value FROM (SELECT customer_id, cancel_reason AS value FROM silver.subscriptions "
     "UNION ALL SELECT customer_id, category FROM silver.tickets WHERE available_at < {as_of}) x",
     "FAIL", "post_outcome_unguarded"),
    # a guard on one instance of a self-joined table does not cover the other
    ("SELECT a.customer_id, COUNT(b.ticket_id) AS value FROM silver.tickets a JOIN silver.tickets b "
     "USING (customer_id) WHERE a.available_at < {as_of} GROUP BY 1", "INCONCLUSIVE", "event_rows_unbounded"),
    ("SELECT customer_id, 1 AS value FROM bronze_safe.billing_invoices", "INCONCLUSIVE", "untagged_table"),
    ("SELECT customer_id, foo AS value FROM silver.customers", "INCONCLUSIVE", "unresolved_column"),
    ("SELEC customer_id FROM", "ERROR", None),
    ("SELECT 1 AS customer_id, 2 AS value; SELECT 3", "ERROR", None),
    ("INSERT INTO silver.customers SELECT * FROM silver.customers", "ERROR", None),
])
def test_static_rules(sql, outcome, rule):
    r = static_temporal_check(sql, SILVER_TAGS)
    assert r["outcome"] == outcome, r
    if rule:
        assert rule in {f["rule"] for f in r["findings"]}, r["findings"]


def test_temporal_tags_from_catalog_objects():
    col = lambda tag: SimpleNamespace(temporal=tag, description="", pii=False)
    catalog = SimpleNamespace(tables={
        "silver.subscriptions": SimpleNamespace(schema="silver", columns={
            "customer_id": col("static"), "monthly_price_fen": col("mutable_current"),
            "updated_at": col("availability_time")}),
        "tickets": {"schema": "silver", "columns": [
            {"name": "customer_id", "temporal": "static"}, {"name": "available_at", "temporal": "availability_time"},
            {"name": "note", "temporal": None}]},
    })
    tags = temporal_tags(catalog)
    assert tags == {"silver.subscriptions.customer_id": "static",
                    "silver.subscriptions.monthly_price_fen": "mutable_current",
                    "silver.subscriptions.updated_at": "availability_time",
                    "silver.tickets.customer_id": "static", "silver.tickets.available_at": "availability_time"}
    r = static_temporal_check(NAIVE_FEATURES["current_price_fen"].sql, catalog)
    assert r["outcome"] == "FAIL"
    bad = SimpleNamespace(tables={"silver.x": SimpleNamespace(schema="silver", columns={"a": col("sometimes")})})
    with pytest.raises(ValueError, match="unknown temporal tag"):
        temporal_tags(bad)
    with pytest.raises(ValueError, match="unknown temporal tag"):
        static_temporal_check("SELECT 1", {"silver.x.a": "fresh"})


# ------------------------------------------------------------------ combined
def test_check_feature_trusted_pass_and_naive_fail(small_ws):
    for name in ("minutes_trend", "last_charge_fen", "avg_csat_180d", "plan"):
        r = check_feature(small_ws, TRUSTED_FEATURES[name], SILVER_TAGS)
        assert r["outcome"] == "PASS", r["reasons"]
        assert len(r["time_travel"]) == 2 and r["static"]["outcome"] == "PASS"
    for name in NAIVE_FEATURES:
        r = check_feature(small_ws, NAIVE_FEATURES[name], SILVER_TAGS)
        assert r["outcome"] == "FAIL", (name, r["reasons"])
        assert r["reasons"]


def test_check_feature_on_new_proposals(small_ws):
    good = FeatureDef("sessions_3m_max", """
        SELECT customer_id, CAST(MAX(sessions) AS DOUBLE) AS value FROM silver.usage_monthly
        WHERE usage_month >= strftime({as_of} - INTERVAL 3 MONTH, '%Y-%m') AND available_at < {as_of}
        GROUP BY customer_id""", "max monthly sessions, last 3 months", "proposed")
    assert check_feature(small_ws, good, SILVER_TAGS)["outcome"] == "PASS"
    # counts tickets that arrive up to 30 days after as_of: caught both ways
    late = FeatureDef("tickets_incl_next_30d", """
        SELECT customer_id, COUNT(*) AS value FROM silver.tickets
        WHERE created_at < {as_of} + INTERVAL 30 DAY AND available_at < {as_of} + INTERVAL 30 DAY
        GROUP BY customer_id""", "leaky", "proposed")
    r = check_feature(small_ws, late, SILVER_TAGS)
    assert r["outcome"] == "FAIL"
    assert all(t["outcome"] == "FAIL" for t in r["time_travel"]) and r["static"]["outcome"] == "FAIL"


def test_real_catalog_tags_agree():
    """Integration with the context catalog, when present: same verdicts as the explicit tags."""
    catalog_mod = pytest.importorskip("dsharness.context.catalog")
    catalog = catalog_mod.load_catalog()
    tags = temporal_tags(catalog)
    assert {k: v for k, v in tags.items() if k in SILVER_TAGS} == SILVER_TAGS
    for name, f in {**TRUSTED_FEATURES, **NAIVE_FEATURES}.items():
        assert (static_temporal_check(f.sql, catalog)["outcome"]
                == static_temporal_check(f.sql, SILVER_TAGS)["outcome"]), name
