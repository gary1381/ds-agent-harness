"""H v0.2 regression tests, each built from an E1 arm-H failure (docs/experiments/H_v0.2_PLAN.md).

The expected values are computed here from the governed rows with plain pandas, independently of
`tools/derive.py`, so a bug in the derivation cannot confirm itself."""
from __future__ import annotations

import pandas as pd
import pytest

from dsharness import tools as T
from dsharness.tools import analytics as A
from dsharness.workspace import Workspace


def private_ws(base: Workspace, root) -> Workspace:
    """Shares base's lake, sources and custody truth files; own registry, runs and ledgers (as in test_tools)."""
    root.mkdir(parents=True)
    (root / "lake").symlink_to(base.lake, target_is_directory=True)
    (root / "sources").symlink_to(base.sources, target_is_directory=True)
    (root / "custody").mkdir()
    for f in base.custody.iterdir():
        if f.suffix in (".parquet", ".json"):
            (root / "custody" / f.name).symlink_to(f)
    return Workspace(root).ensure()


@pytest.fixture(scope="module")
def analyst(small_ws, tmp_path_factory):
    ws: Workspace = private_ws(small_ws, tmp_path_factory.mktemp("v02") / "ws")
    ctx = T.make_context(ws, "score", "analyst", "agent:analyst-v02")
    yield ctx
    ctx.close()


def rows(ctx, metric, dims, months, filters=None) -> tuple[pd.DataFrame, dict]:
    q = T.query_metric(ctx, metric, dims, months, filters)
    return pd.DataFrame(q["rows"]), q["metric_call"]


def verdict(r) -> tuple[str, str]:
    return r["tier"], r["gate"]["verdict"]


# --------------------------------------------------------------------------- F1 + F2: quarter total in CNY
Q_ZH = "2026年第一季度（1月至3月）北区的净收入是多少元人民币？"          # E1 B-H06


def test_monthly_fen_rows_for_a_quarter_total_in_cny_are_no_longer_verified(analyst):
    """E1: the gate VERIFIED these rows and the answer was delivered wrong."""
    df, call = rows(analyst, "net_revenue_fen", ["region"], ["2026-01", "2026-03"], {"region": "north"})
    r = T.submit_answer(analyst, Q_ZH, df.to_dict("records"), metric_call=call)
    assert verdict(r) == ("CONSISTENT", "HOLD"), r["reasons"]
    assert any("one value over 2026-01..2026-03" in w for w in r["warnings"])
    assert any("CNY" in w for w in r["warnings"])


def test_declared_quarter_total_in_cny_verifies(analyst):
    df, call = rows(analyst, "net_revenue_fen", ["region"], ["2026-01", "2026-03"], {"region": "north"})
    total_cny = df["value"].sum() / 100
    r = T.submit_answer(analyst, Q_ZH, [{"value": total_cny}], metric_call=call,
                        derive={"months": "sum", "unit": "cny"})
    assert verdict(r) == ("VERIFIED", "ALLOW"), r["reasons"]
    assert r["checks"]["reconcile"]["derive"] == {"months": "sum", "across": None, "keep": None, "unit": "cny"}
    assert r["trust_label"] == "T1"                      # a derived value has no independent record


def test_declared_step_with_wrong_arithmetic_is_flagged(analyst):
    df, call = rows(analyst, "net_revenue_fen", ["region"], ["2026-01", "2026-03"], {"region": "north"})
    r = T.submit_answer(analyst, Q_ZH, [{"value": df["value"].sum() / 100 * 1.01}], metric_call=call,
                        derive={"months": "sum", "unit": "cny"})
    assert verdict(r) == ("FLAGGED", "REJECT"), r["reasons"]


def test_wrong_region_in_chinese_is_a_contradiction(analyst):
    df, call = rows(analyst, "net_revenue_fen", ["region"], ["2026-01", "2026-03"], {"region": "south"})
    r = T.submit_answer(analyst, Q_ZH, [{"value": df["value"].sum() / 100}], metric_call=call,
                        derive={"months": "sum", "unit": "cny"})
    assert verdict(r) == ("CONSISTENT", "HOLD")
    assert any("region ['north']" in w for w in r["warnings"]), r["warnings"]


# --------------------------------------------------------------------------- F2: share, mean, pooled effect
def test_share_of_annual_revenue_from_one_region(analyst):
    """E1 B-H08: the correct share was FLAGGED (it is not the metric's rows)."""
    q = "What share of our total net revenue in 2025 came from the south region? Give a share between 0 and 1."
    df, call = rows(analyst, "net_revenue_fen", ["region"], ["2025-01", "2025-12"])
    by = df.groupby("region")["value"].sum()
    share = by["south"] / by.sum()
    d = {"months": "sum", "across": {"op": "share", "dimension": "region"}, "keep": {"region": "south"}}
    r = T.submit_answer(analyst, q, [{"value": round(share, 6)}], metric_call=call, derive=d)
    assert verdict(r) == ("VERIFIED", "ALLOW"), r["reasons"]
    r = T.submit_answer(analyst, q, [{"value": round(by["north"] / by.sum(), 6)}], metric_call=call, derive=d)
    assert verdict(r) == ("FLAGGED", "REJECT")


def test_average_monthly_revenue_per_region_in_cny(analyst):
    """E1 B-H19."""
    q = "What was average monthly net revenue per region in 2025, in CNY?"
    df, call = rows(analyst, "net_revenue_fen", ["region"], ["2025-01", "2025-12"])
    mean = (df.groupby("region")["value"].mean() / 100).round(2)
    answer = [{"region": k, "value": float(v)} for k, v in mean.items()]
    r = T.submit_answer(analyst, q, answer, metric_call=call, derive={"months": "mean", "unit": "cny"})
    assert verdict(r) == ("VERIFIED", "ALLOW"), r["reasons"]
    # summing instead of averaging is a different number
    total = (df.groupby("region")["value"].sum() / 100)
    r = T.submit_answer(analyst, q, [{"region": k, "value": float(v)} for k, v in total.items()], metric_call=call,
                        derive={"months": "sum", "unit": "cny"})
    assert verdict(r) == ("CONSISTENT", "HOLD") and any("average per month" in w for w in r["warnings"])


def test_pooled_itt_effect_over_campaign_months(analyst):
    """E1 B-L20: control minus treatment churn, pooled over March-May from the counts."""
    q = ("Across the March to May 2026 retention program campaigns combined, what was the intent-to-treat effect on "
         "churn as a share: control rate minus treatment rate?")
    months = ["2026-03", "2026-05"]
    churned, _ = rows(analyst, "program_churned", ["arm"], months)
    enrolled, _ = rows(analyst, "program_enrolled", ["arm"], months)
    rate = churned.groupby("arm")["value"].sum() / enrolled.groupby("arm")["value"].sum()
    effect = rate["control"] - rate["treatment"]
    _, call = rows(analyst, "program_churn_rate", ["arm"], months)
    d = {"months": "pool", "across": {"op": "difference", "dimension": "arm", "a": "control", "b": "treatment"}}
    r = T.submit_answer(analyst, q, [{"value": effect}], metric_call=call, derive=d)
    assert verdict(r) == ("VERIFIED", "ALLOW"), r["reasons"]
    # the average of the monthly differences is not the pooled effect (the caveat says recompute from counts)
    monthly, _ = rows(analyst, "program_churn_rate", ["arm"], months)
    p = monthly.pivot(index="month", columns="arm", values="value")
    avg = float((p["control"] - p["treatment"]).mean())
    if abs(avg - effect) > 1e-6:
        r = T.submit_answer(analyst, q, [{"value": avg}], metric_call=call, derive=d)
        assert verdict(r) == ("FLAGGED", "REJECT")


# --------------------------------------------------------------------------- F3 + addendum: question parsing
def test_first_quarter_of_2026_is_three_months_not_the_year(analyst):
    """E1: 'first quarter of 2026' was read as 2026-01..2026-12 and the correct call was held."""
    q = "What was the average monthly net revenue in CNY across the first quarter of 2026?"     # E1 B-L16
    assert A.months_from_text(q) == (("2026-01", "2026-03"), None)
    df, call = rows(analyst, "net_revenue_fen", [], ["2026-01", "2026-03"])
    r = T.submit_answer(analyst, q, [{"value": round(df["value"].mean() / 100, 2)}], metric_call=call,
                        derive={"months": "mean", "unit": "cny"})
    assert verdict(r) == ("VERIFIED", "ALLOW"), r["reasons"]


def test_including_refunds_is_a_definition_not_a_breakdown(analyst):
    """E1: 'including add-ons and refunds' was read as a request to break down by invoice type."""
    q = "What was net revenue in February 2026, including add-ons and refunds, in CNY?"         # E1 B-L01
    df, call = rows(analyst, "net_revenue_fen", [], ["2026-02", "2026-02"])
    r = T.submit_answer(analyst, q, [{"value": df["value"].sum() / 100}], metric_call=call, derive={"unit": "cny"})
    assert verdict(r) == ("VERIFIED", "ALLOW"), r["reasons"]
    # still a contradiction when the question asks for the values as a filter
    q2 = "What were refunds in February 2026, in CNY?"
    assert A.claim_contradictions(A.semantic_model(), q2, call)


# --------------------------------------------------------------------------- arguments
def test_derive_needs_a_metric_call_and_a_valid_step(analyst):
    r = T.submit_answer(analyst, Q_ZH, [{"value": 1.0}], derive={"months": "sum"})
    assert r["kind"] == "invalid_argument" and "derive needs a metric_call" in r["error"], r
    _, call = rows(analyst, "active_subscribers_eom", [], ["2026-01", "2026-03"])
    r = T.submit_answer(analyst, "How many active subscribers did we have in Q1 2026?", [{"value": 1.0}],
                        metric_call=call, derive={"months": "sum"})
    assert r["kind"] == "invalid_argument" and "does not add up over months" in r["error"], r


def test_direct_answers_still_verify_unchanged(analyst):
    """The v0.1 behaviour on DIRECT questions (0 silent errors in E1) must not move."""
    q = "What was net revenue by region in each month of Q1 2026?"
    df, call = rows(analyst, "net_revenue_fen", ["region"], ["2026-01", "2026-03"])
    r = T.submit_answer(analyst, q, df.to_dict("records"), metric_call=call)
    assert verdict(r) == ("VERIFIED", "ALLOW"), r["reasons"]


def test_hints_no_longer_say_report_unchanged(analyst):
    q = T.query_metric(analyst, "net_revenue_fen", [], ["2026-01", "2026-01"])
    assert "Report these rows unchanged" not in q["submit_hint"] and "derive" in q["submit_hint"]
    assert any("derive" in g for g in T.list_metrics(analyst)["guidance"])
