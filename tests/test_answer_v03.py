"""H v0.3 regression tests built from E1b arm-H failures (docs/experiments/H_v0.3_PLAN.md, F15 / F16).

As in test_answer_v02, the expected values are computed here from the governed rows with plain pandas,
independently of `tools/derive.py`."""
from __future__ import annotations

import pytest

from dsharness import tools as T

from test_answer_v02 import private_ws, rows, verdict


@pytest.fixture(scope="module")
def analyst(small_ws, tmp_path_factory):
    ctx = T.make_context(private_ws(small_ws, tmp_path_factory.mktemp("v03") / "ws"), "score", "analyst",
                         "agent:analyst-v03")
    yield ctx
    ctx.close()


def test_highest_month_is_one_row(analyst):
    """E1b C-H20: 'which month had the highest churn rate' was answered with all twelve months
    (VERIFIED, delivered, wrong) because across.dimension = 'month' was refused."""
    q = ("Which month from January to March 2026 had the highest monthly churn rate, and what was that rate? "
         "Give it as a share between 0 and 1.")
    df, call = rows(analyst, "churn_rate", [], ["2026-01", "2026-03"])
    top = df.sort_values("value", ascending=False).iloc[0]
    d = {"across": {"op": "top", "dimension": "month", "n": 1}}
    r = T.submit_answer(analyst, q, [{"month": top["month"], "value": float(top["value"])}], metric_call=call, derive=d)
    assert verdict(r) == ("VERIFIED", "ALLOW"), (r["reasons"], r["warnings"])
    low = df.sort_values("value").iloc[0]
    r = T.submit_answer(analyst, q, [{"month": low["month"], "value": float(low["value"])}], metric_call=call, derive=d)
    assert verdict(r) == ("FLAGGED", "REJECT")


def test_one_month_minus_another(analyst):
    """E1b C-H31: a difference between two months was held: the rows had no month column and the
    claim spanned several months."""
    q = "How much higher was net revenue in March 2026 than in January 2026, in fen?"
    df, call = rows(analyst, "net_revenue_fen", [], ["2026-01", "2026-03"])
    by = df.set_index("month")["value"]
    d = {"across": {"op": "difference", "dimension": "month", "a": "2026-03", "b": "2026-01"}}
    r = T.submit_answer(analyst, q, [{"value": float(by["2026-03"] - by["2026-01"])}], metric_call=call, derive=d)
    assert verdict(r) == ("VERIFIED", "ALLOW"), (r["reasons"], r["warnings"])


def test_share_of_one_region_names_keep(analyst):
    """E1b C-H15 / C-L28: 'the west region's share' answered with the west row was told the derived
    rows are every region; the agents then submitted all four (delivered, wrong). Now the reason names
    derive.keep, and with it the single value verifies."""
    q = "What share of total net revenue in January 2026 came from the west region? Give a share between 0 and 1."
    df, call = rows(analyst, "net_revenue_fen", ["region"], ["2026-01", "2026-01"])
    share = float(df.set_index("region")["value"]["west"] / df["value"].sum())
    d = {"across": {"op": "share", "dimension": "region"}}
    r = T.submit_answer(analyst, q, [{"region": "west", "value": share}], metric_call=call, derive=d)
    assert r["tier"] == "FLAGGED"
    assert "derive.keep = {'region': ['west']}" in r["checks"]["reconcile"]["governed"]["reason"]
    r = T.submit_answer(analyst, q, [{"value": share}], metric_call=call, derive={**d, "keep": {"region": "west"}})
    assert verdict(r) == ("VERIFIED", "ALLOW"), (r["reasons"], r["warnings"])
    # the way the C-H15 agent wrote it: share with a = west
    r = T.submit_answer(analyst, q, [{"value": share}], metric_call=call,
                        derive={"across": {"op": "share", "dimension": "region", "a": "west"}})
    assert verdict(r) == ("VERIFIED", "ALLOW"), (r["reasons"], r["warnings"])


def test_running_total_by_month(analyst):
    """E1b C-H32: 'cumulative net revenue at the end of each month' was held in all six attempts: derive had
    no running total (one agent tried months = 'cumsum' and was refused), so the right rows were FLAGGED."""
    q = ("What was the cumulative net revenue, in CNY, at the end of each month from January through March 2026? "
         "Give one row per month with the running total since January.")
    df, call = rows(analyst, "net_revenue_fen", [], ["2026-01", "2026-03"])
    df = df.sort_values("month")
    d = {"months": "cumsum", "unit": "cny"}
    run = [{"month": m, "value": float(v) / 100} for m, v in zip(df["month"], df["value"].astype(float).cumsum())]
    r = T.submit_answer(analyst, q, run, metric_call=call, derive=d)
    assert verdict(r) == ("VERIFIED", "ALLOW"), (r["reasons"], r["warnings"])
    monthly = [{"month": m, "value": float(v) / 100} for m, v in zip(df["month"], df["value"])]
    r = T.submit_answer(analyst, q, monthly, metric_call=call, derive=d)
    assert verdict(r) == ("FLAGGED", "REJECT")
