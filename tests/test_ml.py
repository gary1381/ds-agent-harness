"""ML factory: PIT features and labels, the trusted training template, and the sealed evaluator.

Sealed-evaluation tests run in private workspaces that share the session lake but have their
own registry/runs/custody ledger, so holdout budget spent here never leaks into other tests."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil

import pandas as pd
import pytest

from dsharness.common import read_jsonl
from dsharness.lake.build import warehouse_path
from dsharness.ml import custody as custody_mod
from dsharness.ml import features as features_mod
from dsharness.ml import train as train_mod
from dsharness.ml.custody import SealedEvaluator, expected_calibration_error, rank_auc
from dsharness.ml.features import (NAIVE_FEATURES, TRUSTED_FEATURES, FeatureDef, FeatureError, build_features,
                                   build_labels, build_training_frame, population_sql, readonly_connection)
from dsharness.ml.train import (CandidateIntegrityError, CandidateSpec, load_candidate, score, train_candidate)
from dsharness.verify import leakage as leakage_mod
from dsharness.workspace import CUTOFFS, Workspace

TRAIN_MONTHS = ("2025-12-01", "2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01")
TRUSTED = tuple(TRUSTED_FEATURES)
SPEC_NAMES = {"tenure_months", "plan", "region", "channel", "last_charge_fen", "minutes_last_month",
              "minutes_3m_avg", "minutes_trend", "sessions_last_month", "tickets_90d", "billing_tickets_90d",
              "cancel_tickets_90d", "avg_csat_180d", "refunds_180d", "addons_90d"}
_CUSTOMER_ID = re.compile(r"C\d{6}")


def _private_ws(base: Workspace, root: Path, *, share_runs: Workspace | None = None) -> Workspace:
    """Workspace sharing `base`'s lake and custody truth files, with its own ledger.
    With `share_runs`, registry/ and runs/ point at that workspace (read-only use)."""
    ws = Workspace(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "lake").symlink_to(base.lake, target_is_directory=True)
    (root / "custody").mkdir()
    for f in base.custody.glob("*.parquet"):
        (root / "custody" / f.name).symlink_to(f)
    if share_runs is not None:
        (root / "registry").symlink_to(share_runs.registry, target_is_directory=True)
        (root / "runs").symlink_to(share_runs.runs, target_is_directory=True)
    return ws.ensure()


@pytest.fixture(scope="module")
def ml_ws(small_ws, tmp_path_factory) -> Workspace:
    return _private_ws(small_ws, tmp_path_factory.mktemp("ml") / "ws")


@pytest.fixture(scope="module")
def candidates(ml_ws, train_build) -> dict:
    hgb = {"max_iter": 60, "max_leaf_nodes": 8, "learning_rate": 0.05}
    naive = (*TRUSTED, "known_end_flag", "has_cancel_reason")
    specs = {
        "trusted": CandidateSpec(TRUSTED, "logreg", {"C": 0.5}, TRAIN_MONTHS),
        "naive": CandidateSpec(naive, "logreg", {"C": 0.5}, TRAIN_MONTHS, extra_features=NAIVE_FEATURES),
        "hgb": CandidateSpec(TRUSTED, "hgb", hgb, TRAIN_MONTHS),
        "small": CandidateSpec(("tenure_months", "minutes_trend"), "logreg", {}, TRAIN_MONTHS[1:]),
    }
    return {k: train_candidate(ml_ws, train_build, s, requested_by="ds:test") for k, s in specs.items()}


@pytest.fixture
def evaluator_ws(small_ws, ml_ws, candidates, tmp_path) -> Workspace:
    """Fresh holdout budget, same candidates."""
    return _private_ws(small_ws, tmp_path / "ws", share_runs=ml_ws)


def _truth(ws: Workspace, month: str) -> pd.DataFrame:
    t = pd.read_parquet(ws.custody / "truth_customer_month.parquet")
    return t[(t["month"] == month) & t["at_risk"] & ~t["is_test"]]


# ------------------------------------------------------------------ features & labels
def test_feature_library():
    assert len(TRUSTED_FEATURES) >= 10 and SPEC_NAMES <= set(TRUSTED_FEATURES)
    assert all(f.origin == "trusted" and f.name == k for k, f in TRUSTED_FEATURES.items())
    assert set(NAIVE_FEATURES) == {"minutes_last_month_naive", "current_price_fen", "has_cancel_reason",
                                   "known_end_flag"}
    assert all(f.origin == "proposed" for f in NAIVE_FEATURES.values())
    assert "monthly_price_fen" not in TRUSTED_FEATURES["last_charge_fen"].sql
    with pytest.raises(FeatureError):
        FeatureDef("Bad Name", "SELECT 1", "", "trusted")
    with pytest.raises(FeatureError):
        FeatureDef("x", "SELECT 1", "", "vetted")


def test_population_and_labels_match_custody(small_ws, train_build):
    with readonly_connection(warehouse_path(small_ws, train_build)) as con:
        for as_of in ("2026-01-01", "2026-04-01"):
            truth = _truth(small_ws, as_of[:7]).set_index("customer_id")["churned"].astype(int)
            y = build_labels(con, as_of, CUTOFFS["train"]).set_index("customer_id")
            assert set(y.index) == set(truth.index)
            assert y["label_mature"].all()
            assert (y["churned_next_month"].reindex(truth.index).astype(int) == truth).all()
            assert 0 < truth.mean() < 0.2


def test_immature_labels_are_unknown_not_negative(small_ws, train_build):
    with readonly_connection(warehouse_path(small_ws, train_build)) as con:
        y = build_labels(con, "2026-05-01", CUTOFFS["train"])     # May churn needs data to 2026-06-08
        assert not y["label_mature"].any() and y["churned_next_month"].isna().all()
        frame = build_training_frame(con, ["2026-04-01", "2026-05-01"], ["tenure_months"], CUTOFFS["train"])
        assert set(frame["as_of"]) == {"2026-04-01"} and frame.attrs["immature_as_of"] == ["2026-05-01"]
        assert frame["churned_next_month"].isin([0, 1]).all()
        # a cutoff earlier than the build's data is detected (it would not be the build's cutoff)
        with pytest.raises(ValueError, match="after the stated cutoff"):
            build_labels(con, "2026-03-01", "2026-05-10")


def test_build_features_shape_and_contract(small_ws, train_build):
    with readonly_connection(warehouse_path(small_ws, train_build)) as con:
        X = build_features(con, "2026-04-01", list(TRUSTED))
        n_pop = con.execute(f"SELECT COUNT(*) FROM ({population_sql('2026-04-01')})").fetchone()[0]
        assert len(X) == n_pop and X["customer_id"].is_unique and list(X.columns) == ["customer_id", *TRUSTED]
        assert X["tickets_90d"].notna().all() and X["region"].notna().all()
        assert X["last_charge_fen"].dropna().isin([2900, 5900, 9900]).all()   # unit-corrected, price paid
        dup = FeatureDef("dup", "SELECT customer_id, amount_fen AS value FROM silver.invoices", "", "proposed")
        with pytest.raises(FeatureError, match="more than one row"):
            build_features(con, "2026-04-01", ["dup"], extra={"dup": dup})
        cols = FeatureDef("cols", "SELECT customer_id, 1 AS score FROM silver.customers", "", "proposed")
        with pytest.raises(FeatureError, match="columns"):
            build_features(con, "2026-04-01", ["cols"], extra={"cols": cols})
        shadow = FeatureDef("plan", "SELECT customer_id, 'pro' AS value FROM silver.customers", "", "proposed")
        with pytest.raises(FeatureError, match="redefine a trusted"):
            build_features(con, "2026-04-01", ["plan"], extra={"plan": shadow})
        with pytest.raises(KeyError):
            build_features(con, "2026-04-01", ["known_end_flag"])     # naive features are never implicit
        with pytest.raises(ValueError, match="first day"):
            build_features(con, "2026-04-02", ["plan"])


def test_ml_modules_never_touch_custody():
    for mod in (features_mod, train_mod, leakage_mod):
        src = Path(mod.__file__).read_text()
        assert "ws.custody" not in src and "truth_" not in src, mod.__name__
    assert "truth_customer_month" in Path(custody_mod.__file__).read_text()


# ------------------------------------------------------------------ training
def test_train_candidate_records(ml_ws, train_build, candidates):
    r = candidates["trusted"]
    assert re.fullmatch(r"[0-9a-f]{16}", r["candidate_id"])
    assert len(r["cv"]) == len(TRAIN_MONTHS) - 1 >= 3
    for i, fold in enumerate(r["cv"], start=1):
        assert fold["valid_as_of"] == TRAIN_MONTHS[i] and fold["train_as_of"] == list(TRAIN_MONTHS[:i])
        assert {"auc", "brier", "logloss", "base_rate", "n", "n_train"} <= set(fold)
    assert set(r["cv_mean"]) == {"auc", "brier", "logloss", "base_rate"} and r["cv_mean"]["auc"] > 0.55
    run = ml_ws.runs / r["candidate_id"]
    assert {p.name for p in run.iterdir()} == {"model.joblib", "spec.json", "cv.json"}
    rec = [x for x in read_jsonl(ml_ws.registry / "candidates.jsonl") if x["candidate_id"] == r["candidate_id"]][-1]
    assert rec["artifact_digest"] == r["artifact_digest"] and rec["requested_by"] == "ds:test"
    assert rec["build_id"] == train_build and rec["build_cutoff"] == CUTOFFS["train"]
    assert candidates["naive"]["feature_set_digest"] != r["feature_set_digest"]
    pipe, spec = load_candidate(ml_ws, r["candidate_id"])
    assert spec.features == TRUSTED and spec.model == "logreg"


def test_candidate_identity_is_deterministic(ml_ws, train_build, candidates):
    spec = CandidateSpec(("tenure_months", "minutes_trend"), "logreg", {}, TRAIN_MONTHS[1:])
    again = train_candidate(ml_ws, train_build, spec, requested_by="ds:other")
    assert again["candidate_id"] == candidates["small"]["candidate_id"]
    assert again["cv"] == candidates["small"]["cv"]
    # unused extra definitions are not part of the identity
    noisy = CandidateSpec(spec.features, "logreg", {}, spec.train_as_of, extra_features=NAIVE_FEATURES)
    assert train_mod.candidate_id_for(noisy, train_build) == again["candidate_id"]
    assert train_mod.candidate_id_for(spec, "2026-06-30_" + train_build[-8:]) != again["candidate_id"]


def test_train_refuses_bad_requests(ml_ws, train_build, build_reports):
    ok = dict(features=("tenure_months",), model="logreg", params={}, train_as_of=TRAIN_MONTHS)
    bad = [
        (dict(ok, train_as_of=("2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01")), "not mature"),
        (dict(ok, train_as_of=TRAIN_MONTHS[:3]), ">= 4 as_of"),
        (dict(ok, train_as_of=TRAIN_MONTHS[::-1]), "strictly increasing"),
        (dict(ok, model="xgboost"), "model must be"),
        (dict(ok, params={"random_state": 1}), "seed"),
        (dict(ok, features=("known_end_flag",)), "unknown feature"),
    ]
    for kwargs, msg in bad:
        with pytest.raises((ValueError, KeyError), match=msg):
            train_candidate(ml_ws, train_build, CandidateSpec(**kwargs), requested_by="ds:test")
    with pytest.raises(ValueError, match="requested_by"):
        train_candidate(ml_ws, train_build, CandidateSpec(**ok), requested_by=" ")
    with pytest.raises(RuntimeError, match="FAILED"):
        train_candidate(ml_ws, build_reports[CUTOFFS["drift"]].build_id, CandidateSpec(**ok), requested_by="ds:t")
    with pytest.raises(ValueError, match="malformed build id"):
        train_candidate(ml_ws, "../custody", CandidateSpec(**ok), requested_by="ds:t")


def test_score(ml_ws, train_build, score_build, candidates):
    cid = candidates["trusted"]["candidate_id"]
    s = score(ml_ws, cid, train_build, "2026-06-01")
    assert list(s.columns) == ["customer_id", "score"] and s["customer_id"].is_unique
    assert s["score"].between(0, 1).all() and s["score"].std() > 0
    assert len(score(ml_ws, cid, score_build, "2026-07-01")) > 0           # production scoring
    with pytest.raises(ValueError, match="after build"):
        score(ml_ws, cid, train_build, "2026-07-01")
    with pytest.raises(KeyError):
        score(ml_ws, "0" * 16, train_build, "2026-06-01")
    with pytest.raises(KeyError):
        score(ml_ws, "../../custody", train_build, "2026-06-01")


# ------------------------------------------------------------------ sealed evaluation
def test_sealed_trusted_candidate(evaluator_ws, train_build, candidates):
    ev = SealedEvaluator(evaluator_ws)
    r = ev.evaluate(candidates["trusted"]["candidate_id"], train_build)
    m = r["metrics"]
    assert r["outcome"] == "PASS" and m["auc"] > 0.6 and m["auc_ci95"][0] > 0.5
    assert m["auc_ci95"][0] <= m["auc"] <= m["auc_ci95"][1]
    assert m["brier_ci95"][0] <= m["brier"] <= m["brier_ci95"][1]
    truth = _truth(evaluator_ws, "2026-06")
    assert m["n"] == len(truth) and m["n_positive"] == int(truth["churned"].sum())
    assert abs(m["base_rate"] - truth["churned"].mean()) < 1e-12
    assert 0 <= m["ece"] < 0.2 and m["logloss"] > 0
    assert set(m["per_region_auc"]) == {"north", "south", "east", "west"}
    assert sum(v["n"] for v in m["per_region_auc"].values()) == m["n"]
    assert r["coverage"]["n_scored_outside_holdout"] >= 0 and r["bootstrap"]["reps"] == 500
    assert r["budget"] == {"used": 1, "limit": 3, "remaining": 2}


def test_naive_candidate_dev_cv_inflates(evaluator_ws, train_build, candidates):
    """known_end_flag / has_cancel_reason look brilliant in dev CV on the train build (they read
    churns that happened after each as_of) and collapse on the sealed month."""
    ev = SealedEvaluator(evaluator_ws)
    naive, trusted = candidates["naive"], candidates["trusted"]
    sealed_naive = ev.evaluate(naive["candidate_id"], train_build)["metrics"]["auc"]
    sealed_trusted = ev.evaluate(trusted["candidate_id"], train_build)["metrics"]["auc"]
    naive_gap = naive["cv_mean"]["auc"] - sealed_naive
    assert naive["cv_mean"]["auc"] > 0.9 and naive_gap > 0.2
    assert naive_gap > abs(trusted["cv_mean"]["auc"] - sealed_trusted) + 0.15


def test_holdout_budget_enforced(evaluator_ws, train_build, candidates):
    ev = SealedEvaluator(evaluator_ws)
    ids = [candidates[k]["candidate_id"] for k in ("trusted", "naive", "hgb", "small")]
    first = [ev.evaluate(cid, train_build) for cid in ids[:3]]
    assert [r["budget"]["remaining"] for r in first] == [2, 1, 0]
    assert all(r["outcome"] in ("PASS", "INCONCLUSIVE", "FAIL") for r in first)
    over = ev.evaluate(ids[3], train_build)
    assert over["outcome"] == "ERROR" and over["reason"] == "holdout budget exhausted"
    assert "metrics" not in over
    # the identical artifact again reveals nothing new: recorded result, no budget spent
    again = ev.evaluate(ids[0], train_build)
    assert again["cached"] and again["metrics"] == first[0]["metrics"]
    assert ev.budget_used() == 3 and ev.budget_remaining() == 0
    statuses = [e["status"] for e in read_jsonl(evaluator_ws.custody / "ledger.jsonl")]
    assert statuses == ["consumed", "consumed", "consumed", "refused"]
    # a different task id is not a fresh budget for the same holdout
    with pytest.raises(ValueError, match="unknown sealed task"):
        SealedEvaluator(evaluator_ws, task_id="churn-2026-06-retry")


def test_sealed_result_is_aggregate_only(evaluator_ws, train_build, candidates):
    r = SealedEvaluator(evaluator_ws).evaluate(candidates["hgb"]["candidate_id"], train_build)
    text = json.dumps(r)
    assert not _CUSTOMER_ID.search(text)

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            assert len(v) <= 2, v          # only (lo, hi) intervals
            for x in v:
                walk(x)
    walk(r)
    assert set(r) >= {"outcome", "metrics", "bootstrap", "coverage", "budget", "limitations"}


def test_sealed_preconditions_do_not_spend_budget(evaluator_ws, train_build, score_build, candidates):
    ev = SealedEvaluator(evaluator_ws)
    cid = candidates["trusted"]["candidate_id"]
    # a later build would put June into the features (e.g. known_end_flag sees June churns)
    r = ev.evaluate(cid, score_build)
    assert r["outcome"] == "ERROR" and "cutoff" in r["reason"]
    assert ev.evaluate("f" * 16, train_build)["outcome"] == "ERROR"
    assert ev.evaluate("../../custody", train_build)["outcome"] == "ERROR"
    # a candidate trained on a build that contains the holdout month
    peek = train_candidate(evaluator_ws, score_build,
                           CandidateSpec(("tenure_months",), "logreg", {}, ("2026-02-01", "2026-03-01",
                                                                            "2026-04-01", "2026-05-01")),
                           requested_by="ds:test")
    r = ev.evaluate(peek["candidate_id"], train_build)
    assert r["outcome"] == "ERROR" and "overlaps the holdout" in r["reason"]
    assert ev.budget_used() == 0


def test_tampered_artifact_is_refused(small_ws, ml_ws, train_build, candidates, tmp_path):
    ws = _private_ws(small_ws, tmp_path / "ws")
    shutil.copytree(ml_ws.registry, ws.registry, dirs_exist_ok=True)
    cid = candidates["small"]["candidate_id"]
    shutil.copytree(ml_ws.runs / cid, ws.runs / cid)
    swapped = ws.runs / cid / "model.joblib"
    shutil.copy(ml_ws.runs / candidates["trusted"]["candidate_id"] / "model.joblib", swapped)
    with pytest.raises(CandidateIntegrityError, match="digest"):
        load_candidate(ws, cid)
    r = SealedEvaluator(ws).evaluate(cid, train_build)
    assert r["outcome"] == "ERROR" and "integrity" in r["reason"]
    assert SealedEvaluator(ws).budget_used() == 0


def _ws_with_truth(small_ws: Workspace, ml_ws: Workspace, root: Path, edit) -> Workspace:
    """Evaluator workspace (fresh ledger, shared candidates) whose custody truth is an edited COPY;
    the shared truth file behind the symlink is never written."""
    ws = _private_ws(small_ws, root, share_runs=ml_ws)
    target = ws.custody / "truth_customer_month.parquet"
    truth = pd.read_parquet(target)
    target.unlink()                                   # drop the symlink, then write a private file
    edit(truth).to_parquet(target, index=False)
    return ws


_BASE_KEYS = {"task_id", "candidate_id", "build_id", "as_of", "artifact_digest", "feature_origins"}


def _assert_charged_without_holdout_numbers(ev: SealedEvaluator, r: dict, reason: str) -> dict:
    """ERROR after the custody read: one unit of budget spent, a fixed reason, nothing holdout-derived."""
    assert r["outcome"] == "ERROR" and r["reason"] == reason
    assert not re.search(r"\d", r["reason"])
    assert set(r) == _BASE_KEYS | {"outcome", "reason", "budget"}            # no metrics, no coverage counts
    assert r["budget"] == {"used": 1, "limit": 3, "remaining": 2} and ev.budget_used() == 1
    ledger = read_jsonl(ev.ws.custody / "ledger.jsonl")
    assert [e["status"] for e in ledger] == ["consumed"] and ledger[0]["result"]["reason"] == reason
    return ledger[0]


def test_unscored_holdout_customers_are_charged_and_release_no_counts(small_ws, ml_ws, train_build, candidates,
                                                                      tmp_path):
    """ML-1: the number of holdout customers without a score is custody-derived. Asking must spend
    budget, and the answer must not contain the count."""
    def add_unscored(t: pd.DataFrame) -> pd.DataFrame:
        june = t[(t["month"] == "2026-06") & t["at_risk"] & ~t["is_test"]].head(7).copy()
        june["customer_id"] = [f"C99{i:04d}" for i in range(len(june))]      # in no build's population
        return pd.concat([t, june], ignore_index=True)
    ws = _ws_with_truth(small_ws, ml_ws, tmp_path / "ws", add_unscored)
    ev = SealedEvaluator(ws)
    cid = candidates["trusted"]["candidate_id"]
    r = ev.evaluate(cid, train_build)
    entry = _assert_charged_without_holdout_numbers(ev, r, custody_mod.HOLDOUT_UNSCORED)
    assert entry["internal_reason"] == "7 holdout customers have no score"   # operators' detail stays in custody
    # asking again for the same artifact reveals nothing new and spends nothing
    again = ev.evaluate(cid, train_build)
    assert again["cached"] and again["reason"] == r["reason"] and ev.budget_used() == 1


def test_single_class_holdout_is_charged(small_ws, ml_ws, train_build, candidates, tmp_path):
    def no_churn(t: pd.DataFrame) -> pd.DataFrame:
        t = t.copy()
        t["churned"] = t["churned"].astype(int)
        t.loc[t["month"] == "2026-06", "churned"] = 0
        return t
    ws = _ws_with_truth(small_ws, ml_ws, tmp_path / "ws", no_churn)
    ev = SealedEvaluator(ws)
    r = ev.evaluate(candidates["trusted"]["candidate_id"], train_build)
    entry = _assert_charged_without_holdout_numbers(ev, r, custody_mod.HOLDOUT_SINGLE_CLASS)
    assert entry["internal_reason"] == "holdout has a single label class"


def test_failure_after_the_custody_read_is_charged_and_generic(evaluator_ws, train_build, candidates,
                                                               monkeypatch):
    def broken(self, y, p):
        raise RuntimeError("only 17 of 500 bootstrap resamples had both classes")
    monkeypatch.setattr(SealedEvaluator, "_bootstrap", broken)
    ev = SealedEvaluator(evaluator_ws)
    r = ev.evaluate(candidates["trusted"]["candidate_id"], train_build)
    entry = _assert_charged_without_holdout_numbers(ev, r, custody_mod.HOLDOUT_FAILED)
    assert entry["internal_reason"].startswith("RuntimeError: only 17 of 500")
    assert "17 of 500" not in json.dumps(r)


def test_unreadable_custody_is_not_charged(small_ws, ml_ws, train_build, candidates, tmp_path):
    ws = _private_ws(small_ws, tmp_path / "ws", share_runs=ml_ws)
    (ws.custody / "truth_customer_month.parquet").unlink()
    ev = SealedEvaluator(ws)
    with pytest.raises(FileNotFoundError):
        ev.evaluate(candidates["trusted"]["candidate_id"], train_build)
    assert ev.budget_used() == 0 and read_jsonl(ws.custody / "ledger.jsonl") == []


def test_score_coverage_is_checked_against_the_build_before_custody(evaluator_ws, train_build, candidates,
                                                                    monkeypatch):
    """Scores that do not cover the scoring build's own population are refused from the build
    alone: custody is never opened and no budget is spent."""
    real_score = custody_mod.score

    def custody_read(self):
        pytest.fail("custody was read for a candidate whose scores are refused from the build alone")
    monkeypatch.setattr(SealedEvaluator, "_holdout_labels", custody_read)
    tamper = {
        "population member unscored": (lambda s: s.iloc[5:], "population members unscored"),
        "score outside the population": (
            lambda s: pd.concat([s, pd.DataFrame({"customer_id": ["C990000"], "score": [0.5]})]), "outside it"),
        "repeated customer": (lambda s: pd.concat([s, s.iloc[:2]]), "repeat 2 customer ids"),
        "missing probability": (lambda s: s.assign(score=s["score"].where(s.index != 3)), "finite probabilities"),
    }
    ev = SealedEvaluator(evaluator_ws)
    cid = candidates["trusted"]["candidate_id"]
    for why, (edit, needle) in tamper.items():
        monkeypatch.setattr(custody_mod, "score", lambda *a, edit=edit: edit(real_score(*a)))
        r = ev.evaluate(cid, train_build)
        assert r["outcome"] == "ERROR" and needle in r["reason"], (why, r)
        assert "metrics" not in r and r["budget"]["used"] == 0, why
    assert ev.budget_used() == 0
    assert {e["status"] for e in read_jsonl(evaluator_ws.custody / "ledger.jsonl")} == {"rejected"}
    # the honest scores are still evaluated (and charged) once the tampering stops
    monkeypatch.undo()
    r = ev.evaluate(cid, train_build)
    assert r["outcome"] == "PASS" and r["budget"]["used"] == 1


def test_rank_auc_matches_sklearn():
    import numpy as np
    from sklearn.metrics import roc_auc_score
    rng = np.random.default_rng(0)
    for _ in range(20):
        y = rng.integers(0, 2, 200)
        p = np.round(rng.random(200), 1)          # plenty of ties
        assert rank_auc(y, p) == pytest.approx(roc_auc_score(y, p), abs=1e-12)
    with pytest.raises(ValueError):
        rank_auc([1, 1], [0.2, 0.3])


def test_expected_calibration_error():
    assert expected_calibration_error([0, 1, 0, 1], [0.0, 1.0, 0.0, 1.0]) == 0.0
    assert expected_calibration_error([0, 0, 0, 0], [0.9, 0.9, 0.9, 0.9]) == pytest.approx(0.9)
    assert expected_calibration_error([0, 1], [0.25, 0.75], bins=2) == pytest.approx(0.25)
