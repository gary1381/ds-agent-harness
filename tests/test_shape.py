"""Asked answer shape (H v0.2 F1/F3): one value vs one row per month, CNY vs fen, Chinese month scopes."""
from __future__ import annotations

import pytest

from dsharness.tools.shape import asked_unit, asks_per_month, shape_contradictions, zh_months


@pytest.mark.parametrize("text, first, last", [
    ("2026年第一季度（1月至3月）北区的净收入是多少元人民币？", "2026-01", "2026-03"),     # E1 B-H06
    ("2026年2月底，各销售区域有多少名活跃订阅用户？", "2026-02", "2026-02"),
    ("2026年1月至3月的净收入", "2026-01", "2026-03"),
    ("2026年1-3月的净收入", "2026-01", "2026-03"),
    ("2025年12月至2026年2月的流失人数", "2025-12", "2026-02"),
    ("2026年一季度", "2026-01", "2026-03"),
    ("2026年Q2", "2026-04", "2026-06"),
    ("2025年上半年", "2025-01", "2025-06"),
    ("2025年下半年", "2025-07", "2025-12"),
    ("2025年全年的净收入", "2025-01", "2025-12"),
    ("2026年三月的流失率", "2026-03", "2026-03"),
    ("2026年十二月", "2026-12", "2026-12"),
])
def test_zh_months(text, first, last):
    got = zh_months(text)
    assert got and min(got) == first and max(got) == last and "!" not in got


def test_zh_months_backwards_and_none():
    assert "!" in zh_months("2026年3月至1月")
    assert zh_months("What was net revenue in March 2026?") == []


@pytest.mark.parametrize("q, per_month", [
    ("What was net revenue in each month of Q1 2026?", True),
    ("Net revenue by month for January to March 2026", True),
    ("Give me the month-by-month churn rate for 2025 H2", True),
    ("What was the monthly churn rate in 2025?", True),                               # reviewed example
    ("2026年1月至3月每月的净收入是多少？", True),
    ("2026年第一季度各月的流失率", True),
    ("What was average monthly net revenue per region in 2025, in CNY?", False),     # E1 B-H19
    ("What was the month-over-month growth in net revenue from February to March 2026?", False),
    ("What was Q1 2026 net revenue for the north region in CNY?", False),
    ("2025年月均净收入是多少？", False),
    ("2026年3月净收入环比增长多少？", False),
])
def test_asks_per_month(q, per_month):
    assert asks_per_month(q) is per_month


@pytest.mark.parametrize("q, unit", [
    ("2026年第一季度北区的净收入是多少元人民币？", "cny"),
    ("What was net revenue in March 2026, in CNY?", "cny"),
    ("What was net revenue in March 2026 in fen?", "fen"),
    ("How many minutes were watched in April 2026?", None),
    ("2026年4月的观看分钟数是多少？", None),
    ("Report in CNY (1 CNY = 100 fen)", None),                 # names both: no constraint
])
def test_asked_unit(q, unit):
    assert asked_unit(q) == unit


CALL = {"metric": "net_revenue_fen", "dimensions": ["region"], "months": ("2026-01", "2026-03"), "filters": None}


def test_monthly_fen_rows_for_a_quarter_total_in_cny_are_contradicted():
    """The E1 failure: the gate verified monthly fen rows for a one-number CNY question."""
    q = "2026年第一季度（1月至3月）北区的净收入是多少元人民币？"
    out = shape_contradictions(q, CALL, None, "fen", ("2026-01", "2026-03"))
    assert len(out) == 2 and "one value over" in out[0] and "CNY" in out[1]
    ok = shape_contradictions(q, CALL, {"months": "sum", "across": None, "unit": "cny"}, "fen", ("2026-01", "2026-03"))
    assert ok == []


def test_per_month_question_keeps_monthly_rows():
    q = "What was net revenue by region in each month of Q1 2026?"
    assert shape_contradictions(q, CALL, None, "fen", ("2026-01", "2026-03")) == []
    out = shape_contradictions(q, CALL, {"months": "sum", "across": None, "unit": None}, "fen", ("2026-01", "2026-03"))
    assert out and "one row per month" in out[0]


def test_single_month_has_no_month_constraint():
    q = "What was net revenue by region for March 2026?"
    assert shape_contradictions(q, CALL, None, "fen", ("2026-03", "2026-03")) == []


def test_unknown_months_add_no_constraint():
    assert shape_contradictions("What was net revenue last quarter?", CALL, None, "fen", None) == []


def test_fen_asked_but_converted():
    out = shape_contradictions("Net revenue in March 2026 in fen?", CALL, {"months": None, "across": None, "unit": "cny"},
                               "fen", ("2026-03", "2026-03"))
    assert out and "asks for fen" in out[0]


def test_average_vs_total_over_the_period():
    m = {"months": "sum", "across": None, "unit": None}
    q = "What was average monthly net revenue per region in 2025?"
    assert any("average per month" in c for c in shape_contradictions(q, CALL, m, "fen", ("2025-01", "2025-12")))
    q = "What was total net revenue across January through March 2026?"
    assert shape_contradictions(q, CALL, m, "fen", ("2026-01", "2026-03")) == []
    assert any("total over the period" in c for c in shape_contradictions(q, CALL, {**m, "months": "mean"}, "fen",
                                                                            ("2026-01", "2026-03")))
