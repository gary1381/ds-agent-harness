"""Uplift learning from the randomised program, offline validation, targeting policies and the one
pre-registered July experiment (W7).

The library tests use synthetic data where the true effect is known. The workflow test runs on a
private copy of the session world (shared lake / custody truth, own registry, runs and ledgers):
policies are registered and validated, gated per policy, and the July experiment is run ONCE.
Custody is read here only to *grade* (evaluator side), never by the code under test."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dsharness import tools as T
from dsharness.bench import tasks as BT
from dsharness.bench.run import private_workspace
from dsharness.common import digest, read_jsonl
from dsharness.decision.design import PolicyExperimentDesign
from dsharness.decision.experiment import ValueParams
from dsharness.decision.oracle import LEDGER, CustomerAlreadyAssignedError
from dsharness.evidence import ApprovalStore, EvidenceStore
from dsharness.lake.build import warehouse_path
from dsharness.ml import uplift as U
from dsharness.ml.features import readonly_connection
from dsharness.ml.train import CandidateIntegrityError
from dsharness.workspace import CUTOFFS, Workspace

MODELER = "agent:uplift-modeler"
OWNER = "user:uplift-owner"


@pytest.fixture(scope="module")
def uws(small_ws, tmp_path_factory) -> Workspace:
    return private_workspace(small_ws, tmp_path_factory.mktemp("uplift") / "ws")


@pytest.fixture(scope="module")
def frame(small_ws, score_build) -> pd.DataFrame:
    with readonly_connection(warehouse_path(small_ws, score_build)) as con:
        return U.program_frame(con, CUTOFFS["score"], U.UpliftSpec().features)


# =========================================================================== data
def test_program_frame_uses_mature_campaigns_and_the_randomised_arm(frame, small_ws, score_build, train_build):
    assert frame.attrs["immature_months"] == ["2026-06"]            # June labels mature on 2026-07-08
    assert sorted(frame["campaign_month"].unique()) == ["2026-03", "2026-04", "2026-05"]
    assert "exposed" not in frame.columns                            # ITT: compliance is never a feature or a group
    assert (frame["treated"] == (frame["arm"] == "treatment").astype(int)).all()
    assert (frame["as_of"] == frame["campaign_month"] + "-01").all()
    truth = pd.read_parquet(small_ws.custody / "truth_program.parquet")   # evaluator-side check of the labels
    j = frame.merge(truth, on=["campaign_month", "customer_id"], suffixes=("", "_t"))
    assert len(j) == len(frame) == int(truth["campaign_month"].isin(["2026-03", "2026-04", "2026-05"]).sum())
    assert (j["churned"] == j["churned_t"].astype(int)).all() and (j["arm"] == j["arm_t"]).all()
    with readonly_connection(warehouse_path(small_ws, score_build)) as con:
        with pytest.raises(ValueError, match="not mature"):
            U.program_frame(con, CUTOFFS["score"], ["tenure_months"], months=["2026-06"])
        with pytest.raises(ValueError, match="not in silver.retention_contacts"):
            U.program_frame(con, CUTOFFS["score"], ["tenure_months"], months=["2025-12"])
    with readonly_connection(warehouse_path(small_ws, train_build)) as con:
        early = U.program_frame(con, CUTOFFS["train"], ["tenure_months"])
    assert sorted(early["campaign_month"].unique()) == ["2026-03", "2026-04"]
    assert early.attrs["immature_months"] == ["2026-05"]             # immature is unknown, never "no churn"


def test_spec_validation():
    U.UpliftSpec().validate()
    for bad in (dict(method="forest"), dict(features=("current_price_fen",)), dict(C=0), dict(seed=True),
                dict(method="interaction", features=("tenure_months",)), dict(train_months=("2026-3",)),
                dict(features=())):
        with pytest.raises(ValueError):
            U.UpliftSpec(**bad).validate()
    spec = U.UpliftSpec(method="interaction", C=0.5)
    assert U.UpliftSpec.from_dict(spec.to_dict()) == spec


# =========================================================================== offline validation (synthetic truth)
def _synthetic(n=3000, months=("2026-03", "2026-04", "2026-05"), seed=0, effect=0.25):
    rng = np.random.default_rng(seed)
    x = rng.uniform(0, 1, n)
    tau = effect * x                                   # true churn reduction grows with x
    p0 = 0.08 + 0.3 * x
    t = (rng.random(n) < 0.5).astype(int)
    churn = (rng.random(n) < p0 - t * tau).astype(int)
    return pd.DataFrame({"campaign_month": np.array(months)[rng.integers(0, len(months), n)],
                         "customer_id": [f"S{i % (n // 2):05d}" for i in range(n)],     # repeat customers
                         "treated": t, "churned": churn, "x": x, "tau": tau,
                         "price_fen": rng.choice([2900.0, 5900.0], n)})


def test_qini_ranks_true_effect_above_random_and_reversed():
    d = _synthetic()
    t, y, tau = d["treated"].to_numpy(), d["churned"].to_numpy(), d["tau"].to_numpy()
    good, bad = U.qini(tau, t, y), U.qini(-tau, t, y)
    rand = U.qini(np.random.default_rng(1).random(len(d)), t, y)
    assert good["qini_coefficient"] > rand["qini_coefficient"] > bad["qini_coefficient"]
    assert good["qini_coefficient"] > 0 > bad["qini_coefficient"]
    assert good["incremental_retained"][-1] == pytest.approx(bad["incremental_retained"][-1])   # same total effect
    assert len(good["grid"]) == 10 and good["n"] == len(d)
    one_arm = U.qini(np.arange(10.0), np.array([1] * 5 + [0] * 5), np.zeros(10))
    assert np.isnan(one_arm["incremental_retained"][0])          # top 10% has no control: no invented value


def test_offline_validation_outcomes():
    d = _synthetic()
    cheap = ValueParams(contact_cost_fen=0, offer_cost_rate=0.0, channel="free")
    good = U.offline_validation(d, d["tau"].to_numpy(), price=d["price_fen"], vp=cheap, top_fraction=0.3,
                                n_boot=200)
    assert good["outcome"] == "PASS" and good["better_than_random"] == "yes"
    assert good["qini_coefficient"]["ci"][0] > 0 and good["value_top_fraction"]["ci"][0] > 0
    assert good["bootstrap"]["cluster"] == "customer_id" and good["estimand"].startswith("ITT")
    assert {"sd_control", "sd_treatment", "n_treatment", "n_control"} <= set(good["value_top_fraction"])
    worse = U.offline_validation(d, -d["tau"].to_numpy(), n_boot=200)
    assert worse["outcome"] == "FAIL" and "worse than random" in worse["reasons"][0]
    # a good ranking at a loss-making operating point (the call channel) fails on value
    small = _synthetic(effect=0.04, seed=3)
    costly = U.offline_validation(small, small["tau"].to_numpy(), price=small["price_fen"],
                                  vp=ValueParams.for_channel("call"), top_fraction=0.5, n_boot=200)
    assert costly["outcome"] == "FAIL" and "lost money" in " ".join(costly["reasons"])
    one_month = d.assign(campaign_month="2026-03")
    assert U.offline_validation(one_month, d["tau"].to_numpy(), n_boot=50)["outcome"] == "INCONCLUSIVE"
    with pytest.raises(ValueError, match="one finite value per frame row"):
        U.offline_validation(d, d["tau"].to_numpy()[:-1])
    with pytest.raises(ValueError, match="needs price and vp"):
        U.offline_validation(d, d["tau"].to_numpy(), top_fraction=0.3)


def test_cross_fit_predicts_each_month_with_a_model_that_never_saw_it(frame):
    spec = U.UpliftSpec(C=0.1)
    cf = U.cross_fit(frame, spec)
    assert list(cf.index) == list(frame.index) and (cf["uplift"] == cf["p0"] - cf["p1"]).all()
    held = frame["campaign_month"] == "2026-04"
    manual = U.fit_uplift(frame[~held], spec).predict(frame[held])
    assert np.allclose(cf.loc[held, "uplift"].to_numpy(), manual["uplift"].to_numpy())
    in_sample = U.fit_uplift(frame, spec).predict(frame[held])
    assert not np.allclose(in_sample["uplift"].to_numpy(), manual["uplift"].to_numpy())
    with pytest.raises(ValueError, match="campaign months"):
        U.cross_fit(frame[held], spec)


# =========================================================================== registered models
def test_train_register_load_and_predict(uws, score_build):
    res = U.train_uplift_model(uws, score_build, U.UpliftSpec(), requested_by=MODELER)
    mid = res["model_id"]
    assert res["train_months"] == ["2026-03", "2026-04", "2026-05"] and res["immature_months_excluded"] == ["2026-06"]
    assert res["offline_validation"]["outcome"] in ("PASS", "FAIL", "INCONCLUSIVE")
    rec = U.uplift_record(uws, mid)
    assert rec["artifact_digest"] == res["artifact_digest"] and rec["requested_by"] == MODELER
    assert U.train_uplift_model(uws, score_build, U.UpliftSpec(), requested_by=MODELER)["model_id"] == mid  # seeded
    pred = U.predict_uplift(uws, mid, score_build, "2026-07-01")
    assert pred["customer_id"].is_unique and ((pred[["p0", "p1"]] >= 0) & (pred[["p0", "p1"]] <= 1)).all().all()
    assert np.allclose(pred["uplift"], pred["p0"] - pred["p1"])
    with pytest.raises(ValueError, match="after build"):
        U.predict_uplift(uws, mid, score_build, "2026-08-01")
    inter = U.train_uplift_model(uws, score_build, U.UpliftSpec(method="interaction"), requested_by=MODELER,
                                 validate=False)
    assert set(inter["describe"]["effect_coefficients"]) == {"treated", *U.EFFECT_MODIFIERS}
    assert inter["offline_validation"] is None
    with pytest.raises(KeyError):
        U.uplift_record(uws, "0" * 16)
    # a swapped artifact is refused before it is unpickled
    path = Path(res["run_dir"]) / "model.joblib"
    original = path.read_bytes()
    path.write_bytes(original + b"\0")
    try:
        with pytest.raises(CandidateIntegrityError, match="refusing to unpickle"):
            U.load_uplift_model(uws, mid)
    finally:
        path.write_bytes(original)
    U.load_uplift_model(uws, mid)


def test_uplift_module_never_touches_custody():
    src = Path(U.__file__).read_text()
    assert "ws.custody" not in src and "truth_" not in src


def test_evaluator_grades_uplift_ranking_against_true_effect(uws, small_ws, score_build):
    truth = pd.read_parquet(small_ws.custody / "truth_experiment.parquet")
    oracle = pd.DataFrame({"customer_id": truth["customer_id"], "uplift": truth["p0"] - truth["p1"]})
    perfect = BT.grade_uplift_ranking(uws, oracle, "experiment")
    assert perfect["spearman"] == pytest.approx(1.0) and perfect["share_of_oracle_uplift_captured"]["30%"] == 1.0
    assert perfect["persuadable_auc"] > 0.9
    mid = U.train_uplift_model(uws, score_build, U.UpliftSpec(), requested_by=MODELER, validate=False)["model_id"]
    learned = BT.grade_uplift_ranking(uws, U.predict_uplift(uws, mid, score_build, "2026-07-01"), "experiment")
    assert -1 <= learned["spearman"] <= 1 and learned["n"] > 500
    prog = pd.read_parquet(small_ws.custody / "truth_program.parquet")
    by_enrolment = prog[["campaign_month", "customer_id"]].assign(uplift=prog["p0"] - prog["p1"])
    assert BT.grade_uplift_ranking(uws, by_enrolment, "program")["spearman"] == pytest.approx(1.0)
    with pytest.raises(ValueError, match="lack columns"):
        BT.grade_uplift_ranking(uws, oracle.drop(columns="uplift"))
    with pytest.raises(ValueError, match="source"):
        BT.grade_uplift_ranking(uws, oracle, "july")


# =========================================================================== policies -> gates -> ONE experiment
def _approve_experiment(ws, subject):
    ApprovalStore(ws).record(stage="model_experiment", approver=OWNER, role="business_owner", requested_by=MODELER,
                             subject=subject)


def test_policies_offline_gates_and_one_pre_registered_experiment(uws, score_build):
    ctx = T.make_context(uws, "train", "data_scientist", MODELER)
    try:
        names = [f["name"] for f in T.list_features(ctx)["trusted"]]
        cid = T.train_candidate(ctx, names, "logreg", {"C": 0.5}, list(BT.TRAIN_AS_OF))["candidate_id"]
        unreleased = T.train_candidate(ctx, names[:6], "logreg", {"C": 0.5}, list(BT.TRAIN_AS_OF))["candidate_id"]
        assert T.request_final_evaluation(ctx, cid)["outcome"] == "PASS"
    finally:
        ctx.close()
    mid = U.train_uplift_model(uws, score_build, U.UpliftSpec(), requested_by=MODELER, validate=False)["model_id"]
    k = 120
    common = dict(k=k, channel="app", as_of=BT.EXPERIMENT_AS_OF, score_build=score_build, requested_by=MODELER)
    risk = T.ds.register_policy(uws, "risk_top_k", churn_candidate_id=cid, **common)
    upl = T.ds.register_policy(uws, "uplift_value_top_k", uplift_model_id=mid, **common)
    assert risk["models_check"]["outcome"] == upl["models_check"]["outcome"] == "PASS"
    assert risk["policy_id"] == digest(risk["subject"])[:16] and risk["subject"]["kind"] == "policy"
    assert T.ds.register_policy(uws, "risk_top_k", churn_candidate_id=cid, **common)["policy_id"] == risk["policy_id"]
    # a policy reading a model that never passed its sealed evaluation is not released
    blocked = T.ds.register_policy(uws, "risk_top_k", churn_candidate_id=unreleased, **common)
    assert blocked["models_check"]["outcome"] == "INCONCLUSIVE"
    with pytest.raises(ValueError, match="exactly one churn candidate"):
        T.ds.register_policy(uws, "risk_top_k", uplift_model_id=mid, **common)
    with pytest.raises(ValueError, match="unknown channel"):
        T.ds.register_policy(uws, "risk_top_k", churn_candidate_id=cid, **{**common, "channel": "sms"})

    offline = {}
    for pol in (risk, upl):
        off = T.ds.validate_policy_offline(uws, pol["policy_id"], n_boot=200)
        offline[pol["subject"]["policy"]] = off
        assert off["outcome"] in ("PASS", "FAIL", "INCONCLUSIVE") and off["operating_point"]["k"] == k
        (rec,) = [r for r in EvidenceStore(uws).all() if r.evidence_id in off["evidence_ids"]]
        assert rec.check_id == "policy.offline_qini" and rec.producer == "verifier:uplift"
        assert rec.subject == pol["subject"] and rec.outcome == off["outcome"]
        assert re_ids(json.dumps(off)) == set()                       # aggregates only
    ds = T.make_context(uws, "score", "data_scientist", MODELER)
    try:
        assert T.request_release(ds, policy_id=risk["policy_id"], stage="model_experiment")["verdict"] in (
            "HOLD", "REJECT")                                          # no business-owner approval yet
        verdicts = {}
        for pol in (risk, upl, blocked):
            _approve_experiment(uws, pol["subject"])
            verdicts[pol["policy_id"]] = T.request_release(ds, policy_id=pol["policy_id"],
                                                           stage="model_experiment")["verdict"]
        assert verdicts[blocked["policy_id"]] == "HOLD"
        for name, pol in (("risk_top_k", risk), ("uplift_value_top_k", upl)):
            expected = {"PASS": "ALLOW", "FAIL": "REJECT", "INCONCLUSIVE": "HOLD"}[offline[name]["outcome"]]
            assert verdicts[pol["policy_id"]] == expected
        bad = T.request_release(ds, candidate_id=cid, policy_id=risk["policy_id"], stage="model_scale")
        assert bad["kind"] == "invalid_argument"
        assert T.request_release(ds, stage="model_scale")["kind"] == "invalid_argument"
        assert T.request_release(ds, policy_id="f" * 16, stage="model_scale")["kind"] == "invalid_argument"
    finally:
        ds.close()

    ids = {"risk_top_k": risk["policy_id"], "uplift_value_top_k": upl["policy_id"]}
    cleared = tuple(n for n in ("risk_top_k", "uplift_value_top_k") if verdicts[ids[n]] == "ALLOW")
    blocked_names = [n for n in ids if n not in cleared]
    for n in blocked_names:     # the controller refuses to experiment on a policy the gate did not clear
        with pytest.raises(PermissionError, match="model_experiment ALLOW"):
            T.ds.plan_policy_experiment(uws, PolicyExperimentDesign("w7-blocked", BT.EXPERIMENT_AS_OF, (n,), k),
                                        {n: ids[n]})
    dry = T.ds.plan_policy_experiment(uws, PolicyExperimentDesign("w7-dry", BT.EXPERIMENT_AS_OF, tuple(ids), k),
                                      ids, require_gate=False)
    assert not set(dry["targets"]["risk_top_k"]) & set(dry["targets"]["uplift_value_top_k"])
    assert len(dry["targets"]["risk_top_k"]) == k and len(dry["targets"]["uplift_value_top_k"]) <= k
    if not cleared:
        pytest.skip("no policy cleared offline on this world; the experiment path is covered by the bench")

    design = PolicyExperimentDesign("w7-exp-2026-07", BT.EXPERIMENT_AS_OF, cleared, k)
    sub = {n: ids[n] for n in cleared}
    res = T.ds.run_policy_experiment(uws, design, sub, requested_by="controller:test")
    (prereg,) = read_jsonl(uws.registry / "experiments.jsonl")
    assert prereg["design_digest"] == design.digest() and prereg["assignment_digest"] == res["assignment_digest"]
    releases = [e for e in read_jsonl(uws.custody / LEDGER) if e["status"] == "released"]
    assert len(releases) == 1 and releases[0]["experiment_id"] == design.experiment_id    # ONE oracle call
    for n in cleared:
        ro = res["readouts"][n]
        assert ro["srm"]["outcome"] in ("PASS", "FAIL") and ro["targeting"]["policy_id"] == ids[n]
        assert ro["n"]["treatment"] + ro["n"]["control"] == len(res["targets"][n])
        (rec,) = [r for r in EvidenceStore(uws).all() if r.evidence_id in ro["evidence_ids"]]
        assert rec.check_id == "experiment.readout" and rec.producer == "experiment:readout"
        assert rec.subject == T.ds.policy_record(uws, ids[n])["subject"]           # on ITS policy, not the model
        truth = BT.decision_truth(uws, res["targets"][n], "app")
        assert set(truth) >= {"value_per_contact_fen", "channel"} and truth["channel"] == "app"
    # re-running the identical pre-registered design replays the release; anything else is refused
    again = T.ds.run_policy_experiment(uws, design, sub, requested_by="controller:test")
    def strip(r):
        return {n: {k_: v for k_, v in ro.items() if k_ != "evidence_ids"} for n, ro in r["readouts"].items()}
    assert strip(again) == strip(res) and again["assignment_digest"] == res["assignment_digest"]
    assert len([e for e in read_jsonl(uws.custody / LEDGER) if e["status"] == "released"]) == 1
    with pytest.raises(ValueError, match="pre-registered with another design"):
        T.ds.run_policy_experiment(uws, PolicyExperimentDesign(design.experiment_id, BT.EXPERIMENT_AS_OF, cleared,
                                                               k, p_treat=0.4), sub, requested_by="controller:test")
    with pytest.raises(CustomerAlreadyAssignedError):        # a fresh id cannot re-randomise the same customers
        T.ds.run_policy_experiment(uws, PolicyExperimentDesign("w7-exp-again", BT.EXPERIMENT_AS_OF, cleared, k),
                                   sub, requested_by="controller:test")


def re_ids(text: str) -> set[str]:
    import re
    return set(re.findall(r"C\d{6}", text))
