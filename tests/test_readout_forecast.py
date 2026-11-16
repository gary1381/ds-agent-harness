"""realise() and shift_claim() on hand-built order lines (docs/experiments/E3b/CLAIMS_SPEC.md)."""
from pathlib import Path

import pandas as pd
import pytest

from dsharness.readout.forecast import realise, realise_file, shift_claim


def L(order, sku, at, buyer, amount, qty=1, line_type="product", country="United Kingdom", market="UK"):
    return dict(order_no=order, sku=sku, item_name="x", qty=qty, ordered_at=pd.Timestamp(at), unit_price=amount,
                buyer_id=buyer, country=country, amount=float(amount), market=market, line_type=line_type)


@pytest.fixture
def lines():
    rows = [
        L("1", "10001", "2025-06-10", 1, 100),            # history: buyers 1, 2
        L("2", "10002", "2025-07-15", 2, 50),
        L("3", "10001", "2025-09-01 09:00", 1, 30),       # window Sep: buyer 1 repeat, 3 new, guest
        L("3", "10002", "2025-09-01 09:00", 1, 20),
        L("4", "10001", "2025-09-08", 3, 40, country="France", market="non-UK"),
        L("5", "10003", "2025-09-09", None, 10),
        L("C6", "10001", "2025-09-10", 1, -30, qty=-1, line_type="cancellation"),
        L("7", "POST", "2025-09-10", 1, 5, line_type="fee"),
        L("8", "10001", "2025-10-02", 2, 60),
        L("9", "10001", "2025-12-05", 1, 999),            # after the windows: never used
    ]
    df = pd.DataFrame(rows)
    df["buyer_id"] = df.buyer_id.astype("Int64")
    return df


SEP = ["2025-09-01", "2025-10-01"]


def total(q, by=None, keys=(None,), f=None, window=SEP):
    return {"id": q, "type": "total", "says": "", "quantity": q, "filter": f or {}, "window": window, "by": by,
            "values": [{"key": k, "point": 1} for k in keys]}


@pytest.mark.parametrize("q,want", [("product_sales", 100), ("product_qty", 4), ("product_lines", 4), ("orders", 3),
                                    ("buyers", 2), ("new_buyers", 1), ("repeat_buyers", 1), ("repeat_sales", 50),
                                    ("guest_sales", 10), ("cancellation_value", 30), ("cancellation_lines", 1)])
def test_quantities(lines, tmp_path, q, want):
    assert realise(total(q), lines, tmp_path)["values"] == [{"key": None, "actual": want}]


def test_weeks_months_and_empty_keys(lines, tmp_path):
    r = realise(total("product_sales", "week", ["2025-09-01", "2025-09-08", "2025-09-15"]), lines, tmp_path)
    assert [v["actual"] for v in r["values"]] == [50, 50, 0]
    r = realise(total("orders", "month", ["2025-09", "2025-10"], window=["2025-09-01", "2025-11-01"]), lines, tmp_path)
    assert [v["actual"] for v in r["values"]] == [3, 1]


def test_filters(lines, tmp_path):
    assert realise(total("product_sales", f={"market": "non-UK"}), lines, tmp_path)["values"][0]["actual"] == 40
    assert realise(total("product_sales", f={"buyers": {"ids": ["1"]}}), lines, tmp_path)["values"][0]["actual"] == 50
    act = {"buyers": {"active_in": ["2025-06-01", "2025-09-01"]}}
    assert realise(total("product_sales", f=act), lines, tmp_path)["values"][0]["actual"] == 50   # drops guests
    top = {"buyers": {"top_by": "product_sales", "n": 1, "over": ["2025-06-01", "2025-09-01"]}}
    assert realise(total("product_sales", f=top), lines, tmp_path)["values"][0]["actual"] == 50
    bad = {"buyers": {"active_in": ["2025-06-01", "2025-09-15"]}}    # looks into the window
    assert "error" in realise(total("product_sales", f=bad), lines, tmp_path)


def test_share_and_verdict(lines, tmp_path):
    share = {"id": "s", "type": "share", "says": "", "window": SEP,
             "numerator": {"quantity": "product_sales", "filter": {"market": "UK"}},
             "denominator": {"quantity": "product_sales", "filter": {}}, "values": [{"key": None, "point": 0.5}]}
    verdict = {"id": "v", "type": "verdict", "says": "", "statement": "", "rests_on": "s", "op": ">=", "value": 0.5,
               "answer": "yes"}
    r = realise_file({"claims": [share, verdict]}, lines, tmp_path)
    assert r[0]["values"][0]["actual"] == 0.6 and r[1]["came_true"] is True


def test_list_metrics_missing_buyers_and_ties(lines, tmp_path):
    pd.DataFrame({"buyer_id": [1, 2, 99], "p": [0.9, 0.1, 0.5]}).to_csv(tmp_path / "l.csv", index=False)
    claim = {"id": "l", "type": "list", "says": "", "file": "l.csv", "id_column": "buyer_id", "score_column": "p",
             "higher_is": "event", "k": 1, "population": {"buyers": {"active_in": ["2025-06-01", "2025-09-01"]}},
             "event": {"quantity": "orders", "filter": {}, "window": SEP, "op": ">=", "value": 1},
             "claimed": [{"metric": "precision_at_k", "point": 1.0}]}
    r = realise(claim, lines, tmp_path)
    assert r["metrics"] == {"precision_at_k": 1.0, "recall_at_k": 1.0, "auc": 1.0, "base_rate": 0.5}
    assert (r["n_population"], r["n_file"], r["n_dropped"]) == (2, 3, 1)
    pd.DataFrame({"buyer_id": [1, 2], "p": [0.5, 0.5]}).to_csv(tmp_path / "l.csv", index=False)
    assert realise(claim, lines, tmp_path)["metrics"]["auc"] == 0.5


def test_missing_file_and_short_data(lines, tmp_path):
    claim = {"id": "pb", "type": "per_buyer", "says": "", "file": "none.csv", "quantity": "product_sales",
             "filter": {}, "window": SEP}
    assert "missing file" in realise(claim, lines, tmp_path)["error"]
    late = total("product_sales", window=["2025-12-01", "2026-03-01"])
    assert "data ends" in realise(late, lines, tmp_path)["error"]


def test_shift_months_and_weeks():
    c = total("product_sales", "month", ["2025-09", "2025-10", "2025-11"], window=["2025-09-01", "2025-12-01"])
    s = shift_claim(c, "2025-09-01", "2024-09-01")
    assert s["window"] == ["2024-09-01", "2024-12-01"] and [v["key"] for v in s["values"]] == ["2024-09", "2024-10",
                                                                                                "2024-11"]
    w = shift_claim(total("orders", "week", ["2025-09-01", "2025-09-08"], window=["2025-09-01", "2025-12-01"]),
                    "2025-09-01", "2024-09-01")
    assert w["window"] == ["2024-09-02", "2024-12-02"] and [v["key"] for v in w["values"]] == ["2024-09-02",
                                                                                               "2024-09-09"]


# --- the gate (forecast_gate.py) -------------------------------------------------------------------------------------

from dsharness.readout import forecast_gate as G  # noqa: E402


def test_numbers_skip_dates_and_read_units():
    got = [(w, v) for w, v, _ in G.numbers_in("From 1 September to 30 November 2025 (week 36, 2025-09-01): £2.1m, "
                                              "range £1.8m–£2.4m, 38% of sales, 4,316 orders, 3 months.")]
    assert [v for _, v in got] == [2.1e6, 1.8e6, 2.4e6, 38, 4316]


def fdoc(point=100, low=None, high=None, window=("2025-09-01", "2025-12-01"), q="product_sales"):
    v = {"key": None, "point": point}
    if low is not None:
        v.update(low=low, high=high, level=0.9)
    return {"asof": "2025-09-01", "claims": [{"id": "c", "type": "total", "says": "x", "quantity": q, "filter": {},
                                              "window": list(window), "by": None, "values": [v]}]}


def run(doc, answer, lines, tmp_path, **kw):
    rep = kw.pop("reproduce", {"claims": doc, "files": {}, "error": None})
    tt = kw.pop("timetravel", ({"claims": doc, "files": {}}, {"claims": doc, "files": {}}))
    return G.check_forecast(answer, doc, tmp_path, lines[lines.ordered_at < "2025-09-01"], "2025-09-01",
                            reproduce=rep, timetravel=tt, backtest=kw.pop("backtest", {}))


def test_gate_allows_a_clean_claim_and_flags_loose_numbers(lines, tmp_path):
    assert run(fdoc(1000, 800, 1200), "Sales £1,000 (£800–£1,200).", lines, tmp_path)["verdict"] == "ALLOW"
    r = run(fdoc(1000, 800, 1200), "Sales £1,000 (£800–£1,200), up from £950.", lines, tmp_path)
    assert r["verdict"] == "HOLD" and "£950" in r["reasons"][0]


def test_gate_checks_past_numbers_and_windows(lines, tmp_path):
    past = fdoc(150, window=("2025-06-10", "2025-09-01"))     # product sales from the data start to August: 150
    assert run(past, "Summer sales were £150.", lines, tmp_path)["verdict"] == "ALLOW"
    assert run(fdoc(120, window=("2025-06-10", "2025-09-01")), "£120", lines, tmp_path)["verdict"] == "REJECT"
    assert run(fdoc(1000, window=("2025-09-01", "2026-03-01")), "£1,000", lines, tmp_path)["verdict"] == "REJECT"


def test_gate_reproduce_timetravel_and_range(lines, tmp_path):
    doc = fdoc(1000, 950, 1050)
    other = fdoc(1001, 950, 1050)
    assert run(doc, "£1,000 (£950–£1,050)", lines, tmp_path, reproduce={"claims": other, "files": {}})["verdict"] \
        == "REJECT"
    assert run(doc, "£1,000 (£950–£1,050)", lines, tmp_path,
               timetravel=({"claims": doc, "files": {}}, {"claims": other, "files": {}}))["verdict"] == "REJECT"
    bt = {o: {"claims": fdoc(100), "realised": [{"id": "c", "values": [{"key": None, "actual": a}]}]}
          for o, a in (("2024-09-01", 80), ("2025-01-01", 125), ("2025-02-01", 110))}   # errors 25%, 20%, 9%
    r = run(doc, "£1,000 (£950–£1,050)", lines, tmp_path, backtest=bt)
    assert r["verdict"] == "REJECT" and "median error was 20.0%" in r["reasons"][0]
    wide = fdoc(1000, 700, 1300)
    assert run(wide, "£1,000 (£700–£1,300)", lines, tmp_path, reproduce={"claims": wide, "files": {}},
               timetravel=({"claims": wide, "files": {}},) * 2, backtest=bt)["verdict"] == "ALLOW"


def test_file_diff_ignores_row_order_and_float_noise():
    a = "buyer_id,p\n1,0.123456\n2,0.5\n"
    assert G.file_diff(a, "buyer_id,p\n2,0.5\n1,0.123457\n") is None                # order and last digit
    assert "largest difference 0.1" in G.file_diff(a, "buyer_id,p\n1,0.223456\n2,0.5\n")
    assert "rows" in G.file_diff(a, "buyer_id,p\n1,0.1\n")
    assert G.file_diff(a, None) == "present in one run only"
