"""Decision loop (policies, assignment, ITT readout, custody oracle) and the ROI model.

The experiment tests target July 2026 with a deliberately simple, transparent risk proxy
computed from certified silver tables (low recent usage = high risk) so they do not depend on
the ML factory. What is under test is the experiment machinery: deterministic assignment, SRM,
an ITT readout with intervals, value with intervals -- and that those intervals cover the
oracle's *true* effect for the targeted set.

Oracle discipline: ``outcomes`` is one-shot per experiment id and one experiment per customer and
month (recorded in the custody experiment ledger), so every test runs the oracle over a private copy
of the session world's custody files and never writes a ledger into the shared session workspace.
Simulations that deliberately re-run the month (interval coverage) give each replicate its own
ledger: parallel universes, never a second look inside one.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import json
import math
import shutil
from pathlib import Path
import threading

import numpy as np
import pandas as pd
import pytest

from dsharness.common import open_readonly, read_jsonl
from dsharness.decision.design import PolicyExperimentDesign, value_power
from dsharness.decision.experiment import CHANNELS, ValueParams, assign, readout
from dsharness.decision.oracle import (LEDGER, PRE_CHURNED, CustomerAlreadyAssignedError, ExperimentAlreadyRunError,
                                       ExperimentLedgerError, ExperimentOracle)
from dsharness.decision.policies import expected_values, random_k, risk_top_k, top_k, uplift_value_top_k
from dsharness.economics.roi import (MEASURED_KEYS, PROFILES, Assumptions, assumptions_table, decision_value,
                                     portfolio_estimate)
from dsharness.lake.build import warehouse_path
from dsharness.workspace import Workspace

AS_OF = "2026-07-01"
K = 400            # small_ws has ~820 eligible customers in July
SALT = "exp:retention-contact:2026-07"
EXP = "test_decision:retention-contact:2026-07"      # controller-issued experiment id
_CUSTODY_FILES = ("world_meta.json", "truth_experiment.parquet", "truth_customer_month.parquet")

_POPULATION_SQL = f"""
SELECT s.customer_id
FROM silver.subscriptions s JOIN silver.customers c USING (customer_id)
WHERE NOT c.is_test_account AND s.started_at < TIMESTAMP '{AS_OF}'
  AND (s.ended_at IS NULL OR s.ended_at >= TIMESTAMP '{AS_OF}')
"""
# Risk proxy: negative mean minutes over the last three months *available* at as_of
# (no usage visible yet = treated as zero usage = highest risk).
_SCORES_SQL = f"""
WITH pop AS ({_POPULATION_SQL}),
mins AS (
  SELECT customer_id, AVG(minutes) AS m FROM silver.usage_monthly
  WHERE usage_month BETWEEN '2026-04' AND '2026-06' AND available_at <= TIMESTAMP '{AS_OF}'
  GROUP BY 1)
SELECT p.customer_id, -COALESCE(m.m, 0) AS score FROM pop p LEFT JOIN mins m USING (customer_id)
"""
# Price known at as_of from the price book (not subscriptions.monthly_price_fen, which is the
# mutable *current* price, and not "last charge", which new joiners may not have yet).
_PRICES_SQL = f"""
SELECT s.customer_id, r.price_fen FROM silver.subscriptions s JOIN silver.ref_plan_prices r
  ON r.plan = s.plan AND DATE '{AS_OF}' BETWEEN r.valid_from AND r.valid_to
"""


def _oracle_workspace(root, small_ws) -> Workspace:
    """Private copy of the custody truth the oracle reads; its experiment ledger starts empty."""
    ws = Workspace(root).ensure()
    for name in _CUSTODY_FILES:
        shutil.copy2(small_ws.custody / name, ws.custody / name)
    return ws


@pytest.fixture(scope="module")
def july(small_ws, score_build, tmp_path_factory):
    con = open_readonly(warehouse_path(small_ws, score_build))
    try:
        scores = con.execute(_SCORES_SQL).df()
        prices = con.execute(_PRICES_SQL).df()
    finally:
        con.close()
    oracle_ws = _oracle_workspace(tmp_path_factory.mktemp("oracle_ws"), small_ws)
    return {"scores": scores, "prices": prices, "targeted": top_k(scores, K),
            "oracle": ExperimentOracle(oracle_ws)}


@pytest.fixture
def fresh(small_ws, tmp_path):
    """(workspace, oracle) with an empty experiment ledger, for tests that inspect or break it."""
    ws = _oracle_workspace(tmp_path, small_ws)
    return ws, ExperimentOracle(ws)


# ---------------------------------------------------------------------------- experiment on small_ws
def test_readout_ci_covers_oracle_true_effect(july):
    tgt, orc = july["targeted"], july["oracle"]
    a = assign(tgt, SALT)
    out = orc.outcomes(a, EXP)
    for vp in (ValueParams(), ValueParams.for_channel("call")):
        ro = readout(a, out, july["prices"], vp)
        truth = orc.true_effect(tgt, vp)
        assert ro["srm"]["outcome"] == "PASS" and ro["channel"] == vp.channel
        assert ro["n"]["treatment"] + ro["n"]["control"] == K
        lo, hi = ro["itt"]["ci"]
        assert lo <= truth["mean_effect"] <= hi, (ro["itt"], truth["mean_effect"])
        vlo, vhi = ro["value_per_contact_fen"]["ci"]
        assert vlo <= truth["value_per_contact_fen"] <= vhi, (vp.channel, ro["value_per_contact_fen"], truth)
        rlo, rhi = ro["incremental_retained"]["ci"]
        assert rlo <= truth["mean_effect"] * ro["n"]["treatment"] <= rhi

    # The business point: high churn *risk* is not high treatment *value*, and channel costs decide
    # the sign. With a phone call (1500 fen + 20% offer) contacting the riskiest customers is proven
    # to lose money, and the readout says so instead of reporting "fewer churns" as a success.
    call = ValueParams.for_channel("call")
    truth = orc.true_effect(tgt, call)
    ro = readout(a, out, july["prices"], call)
    assert truth["mean_effect"] > 0 and truth["expected_value_fen"] < 0
    assert ro["outcome"] == "FAIL" and "loses money" in ro["reasons"][0]
    assert ro["incremental_value_fen"]["ci"][1] < 0
    # the app channel is cheaper per contact than the call for the same list and outcomes
    assert orc.true_effect(tgt, ValueParams())["value_per_contact_fen"] > truth["value_per_contact_fen"] + 1000


def _parallel(orc: ExperimentOracle, tmp_path, i: int) -> ExperimentOracle:
    """The same oracle with its own ledger: one replicate of a coverage simulation (a parallel
    universe), never a second look at the real month."""
    clone = copy.copy(orc)
    clone._ledger = tmp_path / f"universe-{i}" / LEDGER
    return clone


def test_readout_interval_coverage_across_assignments(july, tmp_path):
    tgt, orc, prices = july["targeted"], july["oracle"], july["prices"]
    truth = orc.true_effect(tgt)
    reps, cov_itt, cov_val = 120, 0, 0
    for i in range(reps):
        a = assign(tgt, f"{SALT}:{i}")
        ro = readout(a, _parallel(orc, tmp_path, i).outcomes(a, f"{EXP}:rep{i}"), prices)
        cov_itt += ro["itt"]["ci"][0] <= truth["mean_effect"] <= ro["itt"]["ci"][1]
        v = ro["value_per_contact_fen"]["ci"]
        cov_val += v[0] <= truth["value_per_contact_fen"] <= v[1]
    assert cov_itt / reps >= 0.88 and cov_val / reps >= 0.88


def test_oracle_control_arm_reproduces_custody_churn(july, small_ws):
    orc = july["oracle"]
    a = assign(july["targeted"], SALT)
    out = orc.outcomes(a, EXP)
    assert list(out.columns) == ["customer_id", "churned"]         # observable outcome only
    cm = pd.read_parquet(small_ws.custody / "truth_customer_month.parquet")
    jul = cm[cm["month"] == "2026-07"].set_index("customer_id")["churned"]
    ctrl = out[(a["arm"] == "control").to_numpy()]
    ctrl = ctrl[ctrl["customer_id"].isin(jul.index)]
    assert len(ctrl) > 100
    assert (ctrl["churned"].to_numpy() == jul.loc[ctrl["customer_id"]].to_numpy()).all()
    again = orc.outcomes(a, EXP)
    assert again.equals(out)                                        # idempotent replay


def test_oracle_pre_churned_and_refusals(july, small_ws, fresh, tmp_path):
    _, orc = fresh
    exp = pd.read_parquet(small_ws.custody / "truth_experiment.parquet")
    pop = july["scores"]["customer_id"]
    # churned late in June but not yet visible at the 2026-06-30 cutoff: in the targeted
    # population, outside the at-risk set. A contact cannot bring them back.
    pre = sorted(set(pop) - set(exp["customer_id"]))
    assert pre
    assert (orc.segment_truth(pre) == PRE_CHURNED).all()
    for arm in ("treatment", "control"):           # both potential outcomes: two parallel universes
        out = _parallel(orc, tmp_path, arm).outcomes(pd.DataFrame({"customer_id": pre, "arm": arm}),
                                                     f"{EXP}:pre-churned:{arm}")
        assert out["churned"].all()
    t = orc.true_effect(pre)
    assert t["mean_effect"] == 0 and t["n_pre_churned"] == len(pre)
    assert t["expected_value_fen"] == pytest.approx(-len(pre) * ValueParams().contact_cost_fen)  # no offer redeemed

    cm = pd.read_parquet(small_ws.custody / "truth_customer_month.parquet")
    test_acct = cm.loc[cm["is_test"], "customer_id"].iloc[0]
    with pytest.raises(ValueError, match="not in the 2026-07 experiment population"):
        orc.outcomes(pd.DataFrame({"customer_id": [test_acct], "arm": ["control"]}), f"{EXP}:refusal")
    with pytest.raises(ValueError, match="duplicates"):
        orc.true_effect([pre[0], pre[0]])
    with pytest.raises(ValueError, match="unknown arms"):
        orc.outcomes(pd.DataFrame({"customer_id": pre[:1], "arm": ["both"]}), f"{EXP}:refusal")
    seg = orc.segment_truth(exp["customer_id"])
    assert set(seg) <= {"persuadable", "sleeping_dog", "unmoved"}


def test_true_effect_value_formula(july):
    orc, tgt = july["oracle"], july["targeted"][:50]
    vp = ValueParams(horizon_months_retained_value=3, margin_rate=0.5, contact_cost_fen=100, offer_cost_rate=0.1)
    t = orc.true_effect(tgt, vp)
    rows = orc._truth.loc[tgt]
    eff = rows["p0"] - rows["p1"]
    # the offer is charged only to treated customers who stay (probability 1 - p1)
    expected = float((eff * rows["price_fen"]).sum() * 1.5
                     - (100 + 0.1 * rows["price_fen"] * (1 - rows["p1"])).sum())
    assert t["expected_value_fen"] == pytest.approx(expected)
    assert t["expected_retained"] == pytest.approx(float(eff.sum()))
    assert t["value_per_contact_fen"] == pytest.approx(expected / 50)


# ---------------------------------------------------------------------------- oracle one-shot discipline
def _flip(a: pd.DataFrame) -> pd.DataFrame:
    return a.assign(arm=np.where(a["arm"] == "treatment", "control", "treatment"))


def test_outcomes_are_one_shot_per_experiment_id(july, fresh):
    ws, orc = fresh
    ledger_path = ws.custody / LEDGER
    a = assign(july["targeted"], SALT)
    first = orc.outcomes(a, "exp-1")

    # the first call is recorded: id, assignment digest, timestamp, the observed outcomes
    (rec,) = read_jsonl(ledger_path)
    assert rec["status"] == "released" and rec["experiment_id"] == "exp-1" and rec["month"] == "2026-07"
    assert rec["assignment_digest"] == orc.assignment_digest(a) and rec["ts"]
    assert rec["truth_digest"] == orc.truth_digest
    assert rec["n"] == {arm: int((a["arm"] == arm).sum()) for arm in ("treatment", "control")}
    assert rec["churned"] == sorted(first.loc[first["churned"], "customer_id"])
    assert not {"p0", "p1", "u", "tau_logit", "segment_truth"} & set(rec)       # no potential outcomes

    # idempotent: the identical assignment (any row order, any oracle instance) gets the recorded outcomes
    shuffled = a.sample(frac=1.0, random_state=7).reset_index(drop=True)
    assert orc.assignment_digest(shuffled) == rec["assignment_digest"]
    again = ExperimentOracle(ws).outcomes(shuffled, "exp-1")
    assert list(again["customer_id"]) == list(shuffled["customer_id"])
    expected = first.set_index("customer_id")["churned"].loc[shuffled["customer_id"]].to_numpy()
    assert (again["churned"].to_numpy() == expected).all()
    assert orc.outcomes(a, "exp-1").equals(first)
    assert len(read_jsonl(ledger_path)) == 1                                       # replays add nothing

    # a different assignment for a used id would reveal the other potential outcome: refused + logged
    others = [_flip(a), assign(july["targeted"], SALT + ":other"), a.iloc[:-1]]
    for other in others:
        assert orc.assignment_digest(other) != rec["assignment_digest"]
        with pytest.raises(ExperimentAlreadyRunError, match="already released outcomes"):
            orc.outcomes(other, "exp-1")
    entries = read_jsonl(ledger_path)
    assert [e["status"] for e in entries] == ["released", "refused", "refused", "refused"]
    for e, other in zip(entries[1:], others):
        assert e["experiment_id"] == "exp-1" and e["ts"]
        assert e["assignment_digest"] == orc.assignment_digest(other)
        assert e["released_assignment_digest"] == rec["assignment_digest"]
        assert "churned" not in e and "assignment" not in e

    # refusals do not disturb the original release; a fresh id is NOT a licence to re-randomise the
    # same customers (one experiment per customer and month), but disjoint customers are a new experiment
    assert orc.outcomes(a, "exp-1").equals(first)
    with pytest.raises(CustomerAlreadyAssignedError, match="already assigned in released experiment"):
        orc.outcomes(_flip(a), "exp-2")
    with pytest.raises(CustomerAlreadyAssignedError):
        orc.outcomes(a.iloc[:3], "exp-3")                                  # any overlap is refused
    refused = read_jsonl(ledger_path)[-1]
    assert refused["status"] == "refused" and refused["overlapping_experiments"] == ["exp-1"]
    assert refused["n_overlapping_customers"] == 3 and "churned" not in refused
    others = sorted(set(july["scores"]["customer_id"]) - set(a["customer_id"]))[:50]
    fresh_exp = orc.outcomes(assign(others, SALT), "exp-2")              # exp-2 was never released
    assert [e["status"] for e in read_jsonl(ledger_path)][-1] == "released" and len(fresh_exp) == 50
    assert orc.outcomes(a, "exp-1").equals(first)                        # replays still work


def test_outcomes_requires_a_valid_experiment_id_and_bad_input_spends_nothing(july, fresh):
    ws, orc = fresh
    a = assign(july["targeted"], SALT)
    with pytest.raises(TypeError):
        orc.outcomes(a)                                                    # experiment_id is required
    for bad in (None, "", " exp", "exp 1", "-exp", "exp\n", "x" * 201, 7):
        with pytest.raises(ValueError, match="experiment_id"):
            orc.outcomes(a, bad)
    with pytest.raises(ValueError, match="unknown arms"):
        orc.outcomes(a.assign(arm="both"), "exp-1")
    with pytest.raises(ValueError, match="not in the 2026-07 experiment population"):
        orc.outcomes(pd.concat([a, pd.DataFrame({"customer_id": ["C999999"], "arm": ["control"]})]), "exp-1")
    with pytest.raises(ValueError, match="duplicates"):
        orc.outcomes(pd.concat([a, a.iloc[:1]]), "exp-1")
    with pytest.raises(ValueError, match="customer_id and arm"):
        orc.outcomes(a.rename(columns={"arm": "group"}), "exp-1")
    assert not (ws.custody / LEDGER).exists()                              # validation precedes the ledger
    orc.outcomes(_flip(a), "exp-1")                                        # so the id is still unused
    assert [e["status"] for e in read_jsonl(ws.custody / LEDGER)] == ["released"]


def test_experiment_ledger_integrity_failures_are_loud(july, fresh):
    ws, orc = fresh
    path = ws.custody / LEDGER
    a = assign(july["targeted"], SALT)
    orc.outcomes(a, "exp-1")
    good = path.read_text()

    rec = json.loads(good)
    rec["churned"] = rec["churned"][1:]                                    # someone "fixes" an outcome
    path.write_text(json.dumps(rec) + "\n")
    with pytest.raises(ExperimentLedgerError, match="edited ledger"):
        orc.outcomes(a, "exp-1")
    path.write_text(good + "{not json\n")
    with pytest.raises(ExperimentLedgerError, match="not valid JSON"):
        orc.outcomes(a, "exp-2")                                           # a corrupt ledger blocks every id
    path.write_text(good + "[]\n")
    with pytest.raises(ExperimentLedgerError, match="not an experiment ledger entry"):
        orc.outcomes(a, "exp-2")
    path.write_text(good + good)
    with pytest.raises(ExperimentLedgerError, match="released 2 times"):
        orc.outcomes(a, "exp-1")

    # the world is rebuilt under a recorded experiment: the old outcomes must not be served
    path.write_text(good)
    exp = pd.read_parquet(ws.custody / "truth_experiment.parquet")
    exp["u"] = 1.0 - exp["u"]
    exp.to_parquet(ws.custody / "truth_experiment.parquet", index=False)
    rebuilt = ExperimentOracle(ws)
    assert rebuilt.truth_digest != orc.truth_digest
    with pytest.raises(ExperimentLedgerError, match="custody truth changed"):
        rebuilt.outcomes(a, "exp-1")


def test_concurrent_first_calls_release_exactly_once(july, fresh):
    """Racing first calls with different assignments: the ledger lock lets exactly one through."""
    ws, orc = fresh
    n_threads, rounds = 8, 8
    chunk = len(july["targeted"]) // rounds
    for r in range(rounds):              # each round on its own customers: one experiment per customer
        ids = july["targeted"][r * chunk:(r + 1) * chunk]
        designs = [assign(ids, f"{SALT}:race{i}") for i in range(n_threads)]
        start = threading.Barrier(n_threads)

        def run(a, eid=f"exp-race-{r}", start=start):
            start.wait()
            try:
                return orc.outcomes(a, eid)
            except ExperimentAlreadyRunError:
                return None

        with ThreadPoolExecutor(n_threads) as pool:
            results = list(pool.map(run, designs))
        winners = [i for i, res in enumerate(results) if res is not None]
        assert len(winners) == 1, (r, winners)
        entries = [e for e in read_jsonl(ws.custody / LEDGER) if e["experiment_id"] == f"exp-race-{r}"]
        assert sorted(e["status"] for e in entries) == ["refused"] * (n_threads - 1) + ["released"]
        released = next(e for e in entries if e["status"] == "released")
        assert released["assignment_digest"] == orc.assignment_digest(designs[winners[0]])


# ---------------------------------------------------------------------------- readout on synthetic data
def _synthetic(n_t, n_c, churn_t, churn_c, price=5000, seed=0):
    rng = np.random.default_rng(seed)
    ids = [f"X{i:05d}" for i in range(n_t + n_c)]
    arm = ["treatment"] * n_t + ["control"] * n_c
    churned = np.concatenate([rng.random(n_t) < churn_t, rng.random(n_c) < churn_c])
    return (pd.DataFrame({"customer_id": ids, "arm": arm}),
            pd.DataFrame({"customer_id": ids, "churned": churned}),
            pd.DataFrame({"customer_id": ids, "price_fen": price}))


def test_readout_outcomes():
    cheap = ValueParams(contact_cost_fen=0, offer_cost_rate=0.0)
    a, o, p = _synthetic(2000, 2000, 0.05, 0.20)
    ro = readout(a, o, p, cheap)
    assert ro["outcome"] == "PASS" and ro["itt"]["ci"][0] > 0
    # spec formula when prices are equal: retained * horizon * margin * price - costs
    expected = ro["incremental_retained"]["point"] * 6 * 0.6 * 5000
    assert ro["incremental_value_fen"]["point"] == pytest.approx(expected)
    # costs: contact for every treated customer, the offer only for treated customers who stayed
    treated_retained = int((~o["churned"].to_numpy()[:2000]).sum())
    for channel in CHANNELS:
        vp = ValueParams.for_channel(channel)
        with_cost = readout(a, o, p, vp)
        costs = 2000 * vp.contact_cost_fen + vp.offer_cost_rate * 5000 * treated_retained
        assert with_cost["incremental_value_fen"]["point"] == pytest.approx(expected - costs)
        assert with_cost["value_per_contact_fen"]["point"] == pytest.approx((expected - costs) / 2000)
        assert with_cost["cost_model"].startswith("contact_cost_fen per treated customer")

    a, o, p = _synthetic(150, 150, 0.10, 0.11)
    ro = readout(a, o, p, cheap)
    assert ro["outcome"] == "INCONCLUSIVE" and "spans zero" in ro["reasons"][0]

    a, o, p = _synthetic(40, 40, 0.0, 0.5)
    assert readout(a, o, p, cheap)["outcome"] == "INCONCLUSIVE"            # n per arm < 100

    a, o, p = _synthetic(2000, 2000, 0.30, 0.10)                            # contact backfires
    ro = readout(a, o, p, cheap, basis="itt")
    assert ro["outcome"] == "FAIL" and "increases churn" in ro["reasons"][0]


def test_readout_srm_failure_overrides_a_great_effect():
    a, o, p = _synthetic(4500, 5500, 0.05, 0.30)
    ro = readout(a, o, p, ValueParams(contact_cost_fen=0, offer_cost_rate=0.0))
    assert ro["srm"]["outcome"] == "FAIL"
    assert ro["outcome"] == "FAIL" and "sample ratio mismatch" in ro["reasons"][0]
    assert readout(a, o, p, expected_share_treatment=0.45)["srm"]["outcome"] == "PASS"


def test_readout_refuses_to_drop_rows():
    a, o, p = _synthetic(200, 200, 0.1, 0.1)
    with pytest.raises(ValueError, match="no row in outcomes"):
        readout(a, o.iloc[1:], p)
    with pytest.raises(ValueError, match="no row in prices"):
        readout(a, o, p.iloc[:-1])
    with pytest.raises(ValueError, match="duplicate"):
        readout(pd.concat([a, a.iloc[:1]]), o, p)
    with pytest.raises(ValueError, match="unknown arms"):
        readout(a.assign(arm="holdout"), o, p)
    with pytest.raises(ValueError, match="positive"):
        readout(a, o, p.assign(price_fen=0))
    with pytest.raises(ValueError, match="basis"):
        readout(a, o, p, basis="vibes")
    empty = readout(a.assign(arm="control"), o, p)
    assert empty["outcome"] == "FAIL" and empty["itt"] is None


# ---------------------------------------------------------------------------- assignment & policies
def test_assign_is_deterministic_and_balanced():
    ids = [f"C{i:06d}" for i in range(10_000)]
    a1 = assign(ids, "s1")
    assert list(a1.columns) == ["customer_id", "arm"]
    assert a1.equals(assign(ids, "s1"))
    rev = assign(ids[::-1], "s1").set_index("customer_id").loc[ids].reset_index()
    assert rev.equals(a1)                                                  # order-independent
    assert (a1["arm"] != assign(ids, "s2")["arm"]).mean() > 0.4            # salt re-randomises
    assert 0.48 < (a1["arm"] == "treatment").mean() < 0.52
    assert 0.18 < (assign(ids, "s1", p_treat=0.2)["arm"] == "treatment").mean() < 0.22
    for bad in [dict(customer_ids=["a", "a"], salt="s"), dict(customer_ids=[], salt="s"),
                dict(customer_ids=["a"], salt=""), dict(customer_ids=["a"], salt="s", p_treat=1.0)]:
        with pytest.raises(ValueError):
            assign(**bad)


def test_top_k_and_random_k():
    s = pd.DataFrame({"customer_id": ["c", "a", "b", "d"], "score": [0.5, 0.9, 0.9, 0.1]})
    assert top_k(s, 3) == ["a", "b", "c"]                                   # tie broken by id
    assert top_k(s.rename(columns={"score": "risk"}), 1, col="risk") == ["a"]
    with pytest.raises(ValueError, match="exceeds"):
        top_k(s, 5)
    with pytest.raises(ValueError, match="no score"):
        top_k(s.assign(score=[0.1, None, 0.2, 0.3]), 2)
    with pytest.raises(ValueError, match="duplicate"):
        top_k(pd.concat([s, s.iloc[:1]]), 2)
    with pytest.raises(ValueError):
        top_k(s, 0)
    ids = [f"C{i}" for i in range(100)]
    r = random_k(ids, 10, seed=1)
    assert len(set(r)) == 10 and set(r) <= set(ids)
    assert random_k(ids[::-1], 10, seed=1) == r and random_k(ids, 10, seed=2) != r
    with pytest.raises(ValueError):
        random_k(ids, 101, seed=0)


# ---------------------------------------------------------------------------- economics
def test_profiles_use_cited_labour_costs():
    cn, us = Assumptions(), Assumptions.for_profile("us")
    assert Assumptions.for_profile("cn") == cn and set(PROFILES) == {"cn", "us"}
    assert cn.currency == "CNY" and us.currency == "USD"
    assert cn.loaded_annual_cost == pytest.approx(248_752 * 1.4)
    assert cn.loaded_cost_per_hour == pytest.approx(248_752 * 1.4 / 2000)
    assert us.loaded_annual_cost == pytest.approx(120_230 / 0.70)                 # ~172k
    assert 171_000 < us.loaded_annual_cost < 173_000
    digest_file = Path(__file__).resolve().parents[1] / "docs" / "research" / "findings_2026-10-19.txt"
    text = digest_file.read_text(encoding="utf-8")                  # the cited source exists and says so
    assert "248,752" in text and "120,230" in text and "70.0%" in text
    for a in (cn, us):
        assert "docs/research/findings_2026-10-19.txt" in a.sources["annual_wage"]
        assert "_research_digest" not in json.dumps(a.sources)
        assert "ASSUMPTION" in a.sources["use_cases"]
        tab = assumptions_table(a)
        assert {"annual_wage", "loaded_cost_per_hour", "analytics_qa.silent_failure_rate"} <= set(tab["name"])
    assert "ASSUMPTION" in cn.sources["loaded_multiplier"]
    with pytest.raises(ValueError, match="unknown profile"):
        Assumptions.for_profile("eu")


@pytest.mark.parametrize("profile", PROFILES)
def test_portfolio_accounting_identities(profile):
    a = Assumptions.for_profile(profile)
    df = portfolio_estimate(a)
    body, total = df[df["use_case"] != "TOTAL"], df[df["use_case"] == "TOTAL"].iloc[0]
    assert list(body["use_case"]) == [u.name for u in a.use_cases]
    cost_cols = ["llm_cost", "platform_cost", "review_cost", "error_cost"]
    assert np.allclose(body["total_cost"], body[cost_cols].sum(axis=1))
    assert np.allclose(df["net_value"], df["gross_value"] - df["total_cost"])
    assert body["platform_cost"].sum() == pytest.approx(a.platform_cost_per_month)
    for c in ("gross_value", "llm_cost", "review_cost", "error_cost", "net_value", "setup_cost"):
        assert total[c] == pytest.approx(body[c].sum())
    for _, r in df.iterrows():
        if r["net_value"] > 0:
            assert r["pays_back"] and r["payback_months"] == pytest.approx(r["setup_cost"] / r["net_value"])
        else:
            assert not r["pays_back"] and math.isnan(r["payback_months"])
    uc = a.use_cases[0]
    row = body.iloc[0]
    handled = uc.tasks_per_month * uc.adoption
    assert row["gross_labor_value"] == pytest.approx(
        handled * (1 - uc.flag_rate) * uc.minutes_saved_per_task / 60 * a.loaded_cost_per_hour)
    assert row["review_cost"] == pytest.approx(handled * uc.review_minutes_per_task / 60 * a.loaded_cost_per_hour)
    assert row["llm_cost"] == pytest.approx(handled * uc.llm_cost_per_task)
    assert row["error_cost"] == pytest.approx(handled * uc.silent_failure_rate * uc.error_cost_per_silent_failure)
    assert (df["experiment_value"] == 0).all()        # revenue is never assumed, only measured
    assert set(df["currency"]) == {a.currency}


def test_portfolio_measured_overrides():
    a = Assumptions()
    base = portfolio_estimate(a).set_index("use_case")
    m = portfolio_estimate(a, {
        "silent_failure_rate": 0.0,                                   # global: harness measured
        "analytics_qa": {"silent_failure_rate": 0.01, "cost_per_correct_task_usd": 0.30, "correct_rate": 0.9},
        "experiment_readout": {"experiment_value_per_month": 50_000.0},
    }).set_index("use_case")
    qa = a.use_cases[0]
    handled = qa.tasks_per_month * qa.adoption
    assert m.loc["analytics_qa", "silent_failure_rate"] == 0.01               # specific beats global
    assert m.loc["analytics_qa", "llm_cost"] == pytest.approx(handled * 0.30 * 7.1 * 0.9)
    assert m.loc["analytics_qa", "error_cost"] == pytest.approx(handled * 0.01 * qa.error_cost_per_silent_failure)
    assert m.loc["pipeline_breakfix", "error_cost"] == 0
    assert m.loc["experiment_readout", "gross_value"] == pytest.approx(
        base.loc["experiment_readout", "gross_value"] + 50_000)
    assert m.loc["analytics_qa", "measured_fields"] == \
        "correct_rate,cost_per_correct_task_usd,silent_failure_rate"
    assert m.loc["churn_model", "measured_fields"] == "silent_failure_rate"
    assert base.loc["churn_model", "measured_fields"] == ""
    assert m.loc["TOTAL", "net_value"] > base.loc["TOTAL", "net_value"]
    # without correct_rate the per-correct cost is charged on every task (conservative)
    c = portfolio_estimate(a, {"analytics_qa": {"cost_per_correct_task": 3.0}}).set_index("use_case")
    assert c.loc["analytics_qa", "llm_cost"] == pytest.approx(handled * 3.0)
    us = portfolio_estimate(Assumptions.for_profile("us"), {"llm_cost_per_task_usd": 1.0}).set_index("use_case")
    assert (us.drop(index="TOTAL")["llm_cost_per_task"] == 1.0).all()
    # a use-case cost measurement replaces a global one even when expressed differently
    mixed = portfolio_estimate(a, {"llm_cost_per_task_usd": 1.0,
                                   "analytics_qa": {"cost_per_correct_task": 3.0}}).set_index("use_case")
    assert mixed.loc["analytics_qa", "llm_cost_per_task"] == 3.0
    assert mixed.loc["pipeline_breakfix", "llm_cost_per_task"] == pytest.approx(7.1)


@pytest.mark.parametrize("measured,match", [
    ({"silent_failure_rte": 0.1}, "unknown measured keys"),
    ({"analytics_qa": {"silentfailure": 0.1}}, "unknown measured keys"),
    ({"silent_failure_rate": 1.5}, r"\[0, 1\]"),
    ({"analytics_qa": {"silent_failure_rate": 0.6, "flag_rate": 0.6}}, "exceed 1"),
    ({"llm_cost_per_task": float("nan")}, "finite"),
    ({"analytics_qa": {"cost_per_correct_task": 1.0, "llm_cost_per_task": 1.0}}, "not both"),
    ({"analytics_qa": {"correct_rate": 0.9}}, "only used with"),
    ({"analytics_qa": 0.5}, "must be a dict"),
])
def test_portfolio_rejects_bad_measurements(measured, match):
    with pytest.raises(ValueError, match=match):
        portfolio_estimate(Assumptions(), measured)
    assert "silent_failure_rate" in MEASURED_KEYS


def test_portfolio_decision_value_comes_only_from_a_passing_readout_lcb():
    a = Assumptions()
    passing = {"outcome": "PASS", "value_per_contact_fen": {"point": 900.0, "ci": [300.0, 1500.0]}}
    dv = decision_value(passing, 1000, a)
    assert dv["counted"] and dv["value_per_month"] == pytest.approx(300.0 / 100 * 1000)   # LCB, not the point
    us = decision_value(passing, 1000, Assumptions.for_profile("us"))
    assert us["currency"] == "USD" and us["value_per_month"] == pytest.approx(3000.0 / 7.1)
    for outcome in ("INCONCLUSIVE", "FAIL"):
        held = decision_value({**passing, "outcome": outcome}, 1000, a)
        assert not held["counted"] and held["value_per_month"] == 0.0 and outcome in held["reason"]
    with pytest.raises(ValueError, match="positive lower bound"):
        decision_value({"outcome": "PASS", "value_per_contact_fen": {"point": 1.0, "ci": [-5.0, 7.0]}}, 10, a)
    with pytest.raises(ValueError, match="contacts_per_month"):
        decision_value(passing, -1, a)
    base = portfolio_estimate(a).set_index("use_case")
    m = portfolio_estimate(a, readout=passing, contacts_per_month=1000).set_index("use_case")
    assert m.loc["experiment_readout", "experiment_value"] == pytest.approx(3000.0)
    assert m.loc["experiment_readout", "gross_value"] == pytest.approx(
        base.loc["experiment_readout", "gross_value"] + 3000.0)
    assert "experiment_value_per_month" in m.loc["experiment_readout", "measured_fields"]
    held = portfolio_estimate(a, readout={**passing, "outcome": "INCONCLUSIVE"}, contacts_per_month=1000)
    assert held.set_index("use_case").loc["experiment_readout", "experiment_value"] == 0.0
    with pytest.raises(ValueError, match="not both"):
        portfolio_estimate(a, {"experiment_readout": {"experiment_value_per_month": 5.0}}, readout=passing,
                           contacts_per_month=10)
    with pytest.raises(ValueError, match="contacts_per_month"):
        portfolio_estimate(a, readout=passing)
    with pytest.raises(ValueError, match="only used with readout"):
        portfolio_estimate(a, contacts_per_month=10)


# ---------------------------------------------------------------------------- channels and the shared cost model
def test_channel_presets_and_expected_value():
    app, call = ValueParams(), ValueParams.for_channel("call")
    assert ValueParams.for_channel("app") == app and app.channel == "app"
    assert (app.contact_cost_fen, app.offer_cost_rate) == (50, 0.1)
    assert (call.contact_cost_fen, call.offer_cost_rate, call.channel) == (1500, 0.2, "call")
    assert ValueParams.for_channel("call", horizon_months_retained_value=3).value_multiplier == pytest.approx(1.8)
    with pytest.raises(ValueError, match="unknown channel"):
        ValueParams.for_channel("sms")
    with pytest.raises(ValueError, match="fixed by the channel preset"):
        ValueParams.for_channel("app", contact_cost_fen=0)
    with pytest.raises(ValueError, match="channel"):
        ValueParams(channel=" ")
    ev = app.expected_value([0.20, 0.10, 1.0], [0.10, 0.10, 1.0], [5000, 5000, 5000])
    # 0.1 * 3.6 * 5000 - 50 - 0.1*5000*0.9 ; no effect: -50 - 450 ; already churned: contact only
    assert ev.tolist() == pytest.approx([1800 - 50 - 450, -50 - 450, -50])


def test_readout_estimates_the_same_value_the_oracle_defines():
    """Monte Carlo: averaged over randomisations and outcome draws, the readout's value per contact
    equals the expected value of ValueParams (the oracle's truth formula) -- one cost model."""
    rng = np.random.default_rng(7)
    n = 600
    p0 = rng.uniform(0.02, 0.4, n)
    p1 = np.clip(p0 - rng.uniform(-0.05, 0.15, n), 0.0, 1.0)
    price = rng.choice([2900.0, 5900.0, 9900.0], n)
    ids = [f"S{i:04d}" for i in range(n)]
    prices = pd.DataFrame({"customer_id": ids, "price_fen": price})
    for vp in (ValueParams(), ValueParams.for_channel("call")):
        truth = float(vp.expected_value(p0, p1, price).mean())
        est = []
        for r in range(300):
            a = assign(ids, f"mc:{r}")
            t = (a["arm"] == "treatment").to_numpy()
            churn = rng.random(n) < np.where(t, p1, p0)
            ro = readout(a, pd.DataFrame({"customer_id": ids, "churned": churn}), prices, vp)
            est.append(ro["value_per_contact_fen"]["point"])
        se = np.std(est) / np.sqrt(len(est))
        assert abs(np.mean(est) - truth) < 4 * se, (vp.channel, np.mean(est), truth, se)


def test_value_power_matches_simulated_pass_rate():
    rng = np.random.default_rng(11)
    n_list, n_arm = 400, 200
    p0 = rng.uniform(0.1, 0.35, n_list)
    p1 = p0 * 0.45
    price = rng.choice([2900.0, 5900.0], n_list)
    vp = ValueParams()
    pw = value_power(p0, p1, price, vp, n_arm)
    assert 0.2 < pw["power"] < 0.98 and pw["value_per_contact_fen"] > 0
    ids = [f"P{i:04d}" for i in range(n_list)]
    prices = pd.DataFrame({"customer_id": ids, "price_fen": price})
    passes, reps = 0, 300
    for r in range(reps):
        arm = np.array(["treatment"] * n_arm + ["control"] * (n_list - n_arm))[rng.permutation(n_list)]
        churn = rng.random(n_list) < np.where(arm == "treatment", p1, p0)
        ro = readout(pd.DataFrame({"customer_id": ids, "arm": arm}),
                     pd.DataFrame({"customer_id": ids, "churned": churn}), prices, vp)
        passes += ro["outcome"] == "PASS"
    assert abs(passes / reps - pw["power"]) < 0.1, (passes / reps, pw["power"])
    worse = value_power(p0, p0, price, vp, n_arm)          # no effect: costs only
    assert worse["power"] < 0.01 and worse["n_per_arm_for_target"] is None and worse["note"]
    with pytest.raises(ValueError):
        value_power(p0, p1[:-1], price, vp, n_arm)


# ---------------------------------------------------------------------------- policies and the experiment design
def test_named_policies():
    s = pd.DataFrame({"customer_id": ["c", "a", "b"], "score": [0.5, 0.9, 0.1]})
    assert risk_top_k(s, 2) == ["a", "c"]
    vp = ValueParams()
    pred = pd.DataFrame({"customer_id": ["x", "y", "z", "w"], "p0": [0.30, 0.10, 0.25, 0.40],
                         "p1": [0.10, 0.10, 0.20, 0.42], "price_fen": [5900, 5900, 2900, 9900]})
    ev = expected_values(pred, vp).set_index("customer_id")["ev_fen"]
    assert ev["x"] > ev["z"] > 0 > ev["y"] and ev["w"] < 0           # sleeping dog w: contact backfires
    assert uplift_value_top_k(pred, 5, vp) == ["x", "z"]              # only positive value, never filled up to k
    assert uplift_value_top_k(pred, 1, vp) == ["x"]
    assert uplift_value_top_k(pred.assign(p1=pred["p0"]), 3, vp) == []
    with pytest.raises(ValueError, match="probabilities"):
        uplift_value_top_k(pred.assign(p0=1.5), 2, vp)
    with pytest.raises(ValueError, match="duplicate"):
        expected_values(pd.concat([pred, pred.iloc[:1]]), vp)


def test_policy_experiment_design_split_and_assignment():
    ids = [f"C{i:05d}" for i in range(4000)]
    d = PolicyExperimentDesign("exp-design-1", "2026-07-01")
    parts = d.split(ids)
    a_ids, b_ids = parts["risk_top_k"], parts["uplift_value_top_k"]
    assert not set(a_ids) & set(b_ids) and sorted(a_ids + b_ids) == ids
    assert 0.47 < len(a_ids) / len(ids) < 0.53
    assert d.split(ids[::-1]) == parts                                    # order-independent, re-derivable
    assert PolicyExperimentDesign("exp-design-2", "2026-07-01").split(ids) != parts
    targets = {"risk_top_k": a_ids[:500], "uplift_value_top_k": b_ids[:300]}
    asg = d.assignment(targets)
    assert list(asg.columns) == ["customer_id", "policy", "arm"] and len(asg) == 800
    for pol, n in (("risk_top_k", 500), ("uplift_value_top_k", 300)):
        share = (asg.loc[asg["policy"] == pol, "arm"] == "treatment").mean()
        assert 0.4 < share < 0.6
    assert d.digest() == PolicyExperimentDesign("exp-design-1", "2026-07-01").digest()
    assert d.digest() != PolicyExperimentDesign("exp-design-1", "2026-07-01", k_per_policy=400).digest()
    with pytest.raises(ValueError, match="targets nobody"):
        d.assignment({"risk_top_k": a_ids[:10], "uplift_value_top_k": []})
    with pytest.raises(ValueError, match="two policies"):
        d.assignment({"risk_top_k": a_ids[:10], "uplift_value_top_k": a_ids[5:15]})
    single = PolicyExperimentDesign("exp-design-3", "2026-07-01", policies=("risk_top_k",), channel="call")
    assert single.split(ids) == {"risk_top_k": ids}
    for bad in (dict(policies=("risk_top_k", "risk_top_k")), dict(policies=("nope",)), dict(channel="sms"),
                dict(k_per_policy=1), dict(policies=()), dict(experiment_id="")):
        with pytest.raises(ValueError):
            PolicyExperimentDesign(**{"experiment_id": "x", "as_of": "2026-07-01", **bad})


def test_oracle_benchmark_only_power_and_targets(july, fresh):
    _, orc = fresh
    tgt = july["targeted"][:200]
    vp = ValueParams()
    pw = orc.true_power(tgt, 100, vp)
    assert pw["basis"].startswith("oracle") and 0.0 <= pw["power"] <= 1.0 and 0.0 <= pw["itt_power"] <= 1.0
    assert pw["value_per_contact_fen"] == pytest.approx(orc.true_effect(tgt, vp)["value_per_contact_fen"])
    best = orc.oracle_targets(july["scores"]["customer_id"], 50, vp)
    assert 0 < len(best) <= 50 and len(set(best)) == len(best)
    rows = orc._truth.loc[best]
    assert (vp.expected_value(rows["p0"], rows["p1"], rows["price_fen"]) > 0).all()
    assert orc.true_effect(best, vp)["value_per_contact_fen"] > orc.true_effect(tgt, vp)["value_per_contact_fen"]
    assert not (orc._ledger.exists())                                     # truth accessors never touch the ledger
