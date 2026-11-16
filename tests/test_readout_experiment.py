"""The experiment readout gate (src/dsharness/readout/experiment.py) on a small synthetic randomised test."""
from __future__ import annotations

import math
import random

import duckdb
import pytest

from dsharness.readout import experiment as X


@pytest.fixture(scope="module")
def design(tmp_path_factory):
    """3,000 units in thirds; treatment a lifts visits by 10 points and spend by 2, b by 5 points and 0.5."""
    db = tmp_path_factory.mktemp("exp") / "test.duckdb"
    rng = random.Random(7)
    rows = []
    for i in range(3000):
        g = ("A", "B", "C")[i % 3]
        region = rng.choice(["north", "south"])
        p = {"A": 0.30, "B": 0.25, "C": 0.20}[g]
        visit = int(rng.random() < p)
        buy = int(visit and rng.random() < 0.3)
        spend = round(rng.expovariate(1 / {"A": 30, "B": 22, "C": 20}[g]), 2) if buy else 0.0
        rows.append((g, region, rng.randint(1, 12), visit, buy, spend))
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE t (grp VARCHAR, region VARCHAR, recency INT, visit INT, buy INT, spend DOUBLE)")
    con.executemany("INSERT INTO t VALUES (?, ?, ?, ?, ?, ?)", rows)
    con.close()
    return X.Design(db=db, table="t", assignment="grp", groups={"a": "A", "b": "B", "none": "C"}, control="none",
                    outcomes=("visit", "buy", "spend"), pre_treatment=("region", "recency"), window_days=14,
                    prespecified={"region": {"north": [{"column": "region", "op": "=", "value": "north"}],
                                             "south": [{"column": "region", "op": "=", "value": "south"}]}},
                    facts=(3000, 1000, 1000, 1000, 14, 100, 1000))


def ref(design, **c):
    con = X.connect(design)
    r = X.recompute(con, design, {"role": "result", **c})
    con.close()
    return r


def claim(design, cid="c1", **c):
    r = ref(design, **c)
    return {"id": cid, "role": "result", **c, "estimate": round(r["estimate"], 4),
            **({"low": round(r["low"], 4), "high": round(r["high"], 4)} if r["low"] is not None else {})}


def outcome(res, check):
    return res["checks"][check]["outcome"]


def test_supported_readout_is_allowed(design):
    c = claim(design, outcome="visit", estimand="mean_difference", groups=["a", "none"], direction="higher")
    text = f"Treatment a raised visits by {c['estimate'] * 100:.1f} points [{c['low'] * 100:.1f}, {c['high'] * 100:.1f}]."
    res = X.check_readout(design, text, [c])
    assert res["gate"] == "ALLOW", res["reasons"]


def test_wrong_estimate_is_rejected(design):
    c = claim(design, outcome="spend", estimand="mean_difference", groups=["a", "none"])
    c["estimate"] = round(c["estimate"] * 1.5, 3)
    res = X.check_readout(design, "", [c])
    assert res["gate"] == "REJECT" and outcome(res, "exp.recompute") == "FAIL"


def test_rounded_numbers_pass(design):
    r = ref(design, outcome="spend", estimand="mean_difference", groups=["a", "none"], scale=250_000)
    c = {"id": "p", "role": "projection", "outcome": "spend", "estimand": "mean_difference", "groups": ["a", "none"],
         "scale": 250_000, "estimate": float(f"{r['estimate']:.2g}"), "assumption": "the same customers next season"}
    res = X.check_readout(design, "", [c])
    assert outcome(res, "exp.recompute") == "PASS", res["reasons"]


def test_per_buyer_comparison_is_rejected_unless_caveat(design):
    c = claim(design, outcome="spend", estimand="mean_difference", groups=["a", "none"],
              population=[{"column": "buy", "op": "=", "value": 1}])
    assert X.check_readout(design, "", [c])["checks"]["exp.estimand"]["outcome"] == "FAIL"
    c["role"] = "caveat"
    assert X.check_readout(design, "", [c])["checks"]["exp.estimand"]["outcome"] == "PASS"


def test_direction_against_sign_and_interval(design):
    """Luna's review 11-07: the sign of the estimate decides FAIL; significance alone only holds."""
    r = ref(design, outcome="spend", estimand="mean_difference", groups=["b", "none"])
    c = claim(design, outcome="spend", estimand="mean_difference", groups=["b", "none"],
              direction="higher" if r["low"] <= 0 else "none")
    assert outcome(X.check_readout(design, "", [c]), "exp.contrast") == "INCONCLUSIVE"
    c["direction"] = "unclear"
    assert outcome(X.check_readout(design, "", [c]), "exp.contrast") == "PASS"
    c["direction"] = "lower" if r["estimate"] > 0 else "higher"
    assert outcome(X.check_readout(design, "", [c]), "exp.contrast") == "FAIL"
    v = claim(design, outcome="visit", estimand="mean_difference", groups=["a", "none"], direction="none")
    assert outcome(X.check_readout(design, "", [v]), "exp.contrast") == "INCONCLUSIVE"


def test_ratio_interval_by_either_delta_method(design):
    r = ref(design, outcome="spend", estimand="relative_lift", groups=["a", "none"])
    base = {"id": "r", "role": "result", "outcome": "spend", "estimand": "relative_lift", "groups": ["a", "none"],
            "estimate": round(r["estimate"], 4)}
    lin = {**base, "low": round(r["low_linear"], 4), "high": round(r["high_linear"], 4)}
    assert outcome(X.check_readout(design, "", [lin]), "exp.recompute") == "PASS"
    w = r["high"] - r["low"]
    off = {**base, "low": round(r["low"] - 0.4 * w, 4), "high": round(r["high"], 4)}
    assert outcome(X.check_readout(design, "", [off]), "exp.recompute") == "INCONCLUSIVE"
    mean = claim(design, outcome="spend", estimand="mean_difference", groups=["a", "none"])
    mean["low"] = round(mean["low"] - 0.4 * (mean["high"] - mean["low"]), 4)
    assert outcome(X.check_readout(design, "", [mean]), "exp.recompute") == "FAIL"


def test_group_means_among_buyers_side_by_side(design):
    buyers = [{"column": "buy", "op": "=", "value": 1}]
    a = claim(design, "a", outcome="spend", estimand="group_mean", groups=["a"], population=buyers)
    b = claim(design, "b", outcome="spend", estimand="group_mean", groups=["b"], population=buyers)
    assert outcome(X.check_readout(design, "", [a, b]), "exp.estimand") == "FAIL"
    assert outcome(X.check_readout(design, "", [a]), "exp.estimand") == "PASS"
    a["role"] = b["role"] = "context"
    assert outcome(X.check_readout(design, "", [a, b]), "exp.estimand") == "PASS"


def test_unplanned_subgroup_holds_until_exploratory(design):
    pop = [{"column": "recency", "op": "<=", "value": 3}]
    c = claim(design, outcome="visit", estimand="mean_difference", groups=["a", "none"], population=pop)
    assert outcome(X.check_readout(design, "", [c]), "exp.prespecified") == "INCONCLUSIVE"
    c.update(role="exploratory", searched=4)
    assert outcome(X.check_readout(design, "", [c]), "exp.prespecified") == "PASS"
    planned = claim(design, outcome="visit", estimand="mean_difference", groups=["a", "none"],
                    population=[{"column": "region", "op": "==", "value": "north"}])
    assert outcome(X.check_readout(design, "", [planned]), "exp.prespecified") == "PASS"


def test_horizon_and_projection(design):
    c = claim(design, outcome="spend", estimand="mean_difference", groups=["a", "none"])
    c["horizon_days"] = 365
    assert X.check_readout(design, "", [c])["gate"] == "REJECT"
    c.update(role="projection")
    assert outcome(X.check_readout(design, "", [c]), "exp.horizon") == "INCONCLUSIVE"
    c["assumption"] = "the two-week effect repeats every two weeks"
    assert outcome(X.check_readout(design, "", [c]), "exp.horizon") == "PASS"


def test_pooled_result_holds_when_treatments_differ(design):
    c = claim(design, outcome="visit", estimand="mean_difference", groups=["any", "none"])
    assert outcome(X.check_readout(design, "", [c]), "exp.pooled") == "INCONCLUSIVE"
    per = claim(design, "c2", outcome="visit", estimand="mean_difference", groups=["a", "none"])
    assert outcome(X.check_readout(design, "", [c, per]), "exp.pooled") == "PASS"
    small = claim(design, outcome="visit", estimand="mean_difference", groups=["any", "none"],
                  population=[{"column": "recency", "op": "=", "value": 1}])
    res = X.check_readout(design, "", [small])
    assert outcome(res, "exp.pooled") == "INCONCLUSIVE", res["checks"]["exp.pooled"]


def test_uncovered_number_holds(design):
    c = claim(design, outcome="visit", estimand="group_mean", groups=["a"])
    res = X.check_readout(design, f"Visit rate {c['estimate'] * 100:.1f}%, worth $4,321 a year.", [c])
    assert outcome(res, "exp.numbers_covered") == "INCONCLUSIVE" and "4,321" in res["checks"]["exp.numbers_covered"]["reason"]
    res = X.check_readout(design, "Visit rate is up; at $4,321 per send it pays.", [c], request="Costs $4,321 per send.")
    assert outcome(res, "exp.numbers_covered") == "PASS"


def test_unknown_column_and_estimand_hold(design):
    bad = [{"id": "x", "role": "result", "outcome": "visit", "estimand": "mean_difference", "groups": ["a", "none"],
            "population": [{"column": "secret", "op": "=", "value": 1}], "estimate": 0.1},
           {"id": "y", "role": "result", "outcome": "visit", "estimand": "magic", "groups": ["a", "none"],
            "estimate": 0.1}]
    res = X.check_readout(design, "", bad)
    assert res["gate"] == "HOLD" and outcome(res, "exp.claims") == "INCONCLUSIVE"


def test_ratio_estimands(design):
    r = ref(design, outcome="visit", estimand="relative_lift", groups=["a", "none"])
    m = ref(design, outcome="visit", estimand="group_mean", groups=["a"])["estimate"]
    m0 = ref(design, outcome="visit", estimand="group_mean", groups=["none"])["estimate"]
    assert math.isclose(r["estimate"], m / m0 - 1)
    s = ref(design, outcome="visit", estimand="anyway_share", groups=["a", "none"])
    assert math.isclose(s["estimate"], m0 / m) and s["low"] < s["estimate"] < s["high"]


def test_numbers_in_parses_units():
    got = {w: v for w, v, _ in X.numbers_in("Up +7.7 pts, 18.3%, $1.42 vs $0.65, $192k, 2 weeks, 2008, 64,000 people")}
    assert got["+7.7 pts"] == 7.7 and got["$192k"] == 192_000 and got["64,000"] == 64_000
    assert "2 weeks" not in " ".join(got) and all("2008" not in w for w in got)


def test_direction_on_a_level_is_not_a_contrast(design):
    """Smoke test 11-07: agents put 'lower' on the women's group mean (lower than the men's); not a claim about 0."""
    c = claim(design, outcome="spend", estimand="group_mean", groups=["b"], direction="lower")
    assert outcome(X.check_readout(design, "", [c]), "exp.contrast") == "PASS"


def test_projection_multiplier_is_covered(design):
    """Calibration 11-07 (codex, annual): '26 two-week periods' was held as an uncovered number."""
    r = ref(design, outcome="spend", estimand="mean_difference", groups=["a", "none"], scale=26)
    c = {"id": "p", "role": "projection", "outcome": "spend", "estimand": "mean_difference", "groups": ["a", "none"],
         "scale": 26, "estimate": round(r["estimate"], 2), "assumption": "the effect repeats every two weeks"}
    res = X.check_readout(design, f"Times 26 periods: about ${c['estimate']:.2f} a year (a projection).", [c])
    assert outcome(res, "exp.numbers_covered") == "PASS", res["reasons"]


def test_known_limit_prose_meaning_is_not_read(design):
    """Luna's review 11-07, blocker 1: the gate certifies numbers and declared structure, not what the prose says they
    mean. Pre-registered as a limit (E-scale, E-nodata and E-read are gate-blind codes); these readouts pass."""
    c = claim(design, outcome="spend", estimand="mean_difference", groups=["a", "none"], direction="higher")
    typical = f"A typical customer sent e-mail a spent ${c['estimate']:.2f} more over the two weeks."
    assert X.check_readout(design, typical, [c])["gate"] == "ALLOW"
    profit = f"E-mail a is profitable: it raised spend by ${c['estimate']:.2f} per customer."
    assert X.check_readout(design, profit, [c])["gate"] == "ALLOW"


def test_known_limit_mislabelled_role(design):
    """A comparison among buyers declared as a caveat passes whatever the prose makes of it."""
    c = claim(design, outcome="spend", estimand="mean_difference", groups=["a", "none"],
              population=[{"column": "buy", "op": "=", "value": 1}])
    c["role"] = "caveat"
    text = f"Among buyers, e-mail a raised spend by ${c['estimate']:.2f}."
    assert X.check_readout(design, text, [c])["gate"] == "ALLOW"


def test_signed_number_needs_a_claim_of_that_sign(design):
    """Luna's second review 11-07: '−$0.77' in the reply must not be covered by a +0.77 claim."""
    c = claim(design, outcome="spend", estimand="mean_difference", groups=["a", "none"], direction="higher")
    e = f"{c['estimate']:.2f}"
    assert outcome(X.check_readout(design, f"E-mail a changed spend by −${e}.", [c]), "exp.numbers_covered") == \
        "INCONCLUSIVE"
    assert outcome(X.check_readout(design, f"E-mail a changed spend by +${e}.", [c]), "exp.numbers_covered") == "PASS"
    assert outcome(X.check_readout(design, f"Without e-mail a, spend falls by ${e}.", [c]),
                   "exp.numbers_covered") == "PASS"
    rev = claim(design, outcome="spend", estimand="mean_difference", groups=["none", "a"], direction="lower")
    assert outcome(X.check_readout(design, f"No e-mail minus e-mail a: −${e}.", [rev]), "exp.numbers_covered") == "PASS"
