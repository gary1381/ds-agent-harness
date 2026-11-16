"""verify/stats.py and monitor/drift.py: statistics must be right *and* refuse degenerate input."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import brier_score_loss, roc_auc_score

from dsharness.monitor.drift import drift_report, psi
from dsharness.verify.stats import (auc, bootstrap_ci, brier, ece, srm_test, two_proportion_diff,
                                    z_critical)


# ---------------------------------------------------------------------------- SRM
def test_srm_detects_45_55_at_10k():
    r = srm_test(4500, 5500)
    assert r["outcome"] == "FAIL"
    assert r["chi2"] == pytest.approx(100.0)
    assert r["p_value"] < 1e-20


@pytest.mark.parametrize("n_a,n_b", [(5000, 5000), (5050, 4950), (4930, 5070)])
def test_srm_passes_plausible_splits(n_a, n_b):
    assert srm_test(n_a, n_b)["outcome"] == "PASS"


def test_srm_respects_designed_share():
    assert srm_test(3000, 7000, expected_share_a=0.3)["outcome"] == "PASS"
    assert srm_test(3000, 7000, expected_share_a=0.5)["outcome"] == "FAIL"


@pytest.mark.parametrize("args", [(0, 0), (-1, 5), (5, 5, 0.0), (5, 5, 1.0)])
def test_srm_rejects_invalid_input(args):
    with pytest.raises(ValueError):
        srm_test(*args)


# ---------------------------------------------------------------------------- proportions
def test_two_proportion_known_values():
    r = two_proportion_diff(30, 100, 20, 100)
    assert r["diff"] == pytest.approx(0.10)
    se = math.sqrt(0.3 * 0.7 / 100 + 0.2 * 0.8 / 100)
    assert r["se"] == pytest.approx(se)
    assert r["ci"] == pytest.approx([0.10 - 1.959964 * se, 0.10 + 1.959964 * se], rel=1e-5)
    pooled = 0.25
    assert r["z"] == pytest.approx(0.10 / math.sqrt(pooled * 0.75 * 0.02))
    assert 0.09 < r["p_value"] < 0.11
    flipped = two_proportion_diff(20, 100, 30, 100)
    assert flipped["diff"] == pytest.approx(-0.10) and flipped["p_value"] == pytest.approx(r["p_value"])


def test_two_proportion_ci_coverage_in_simulation():
    rng = np.random.default_rng(7)
    p_a, p_b, n_a, n_b = 0.12, 0.08, 800, 700
    covered = 0
    for _ in range(200):
        r = two_proportion_diff(int(rng.binomial(n_a, p_a)), n_a, int(rng.binomial(n_b, p_b)), n_b)
        covered += r["ci"][0] <= p_a - p_b <= r["ci"][1]
    assert covered / 200 >= 0.90


def test_two_proportion_degenerate_and_invalid():
    r = two_proportion_diff(0, 50, 0, 60)
    assert r["diff"] == 0 and r["p_value"] == 1.0 and r["z"] == 0.0
    for bad in [(1, 0, 1, 10), (11, 10, 1, 10), (-1, 10, 1, 10)]:
        with pytest.raises(ValueError):
            two_proportion_diff(*bad)
    with pytest.raises(ValueError):
        two_proportion_diff(1, 10, 1, 10, alpha=0)
    assert z_critical(0.05) == pytest.approx(1.959964, rel=1e-6)


# ---------------------------------------------------------------------------- bootstrap
def test_bootstrap_mean_ci_is_seeded_and_covers_mean():
    x = np.random.default_rng(1).normal(10.0, 2.0, 400)
    lo, hi = bootstrap_ci(x, n=1000, seed=3)
    assert lo < 10.0 < hi and hi - lo < 0.6
    assert bootstrap_ci(x, n=1000, seed=3) == (lo, hi)
    assert bootstrap_ci(x, n=1000, seed=4) != (lo, hi)


def test_bootstrap_callable_statistic():
    rng = np.random.default_rng(2)
    y = rng.integers(0, 2, 500)
    s = y + rng.normal(0, 1.0, 500)
    point = auc(y, s)
    lo, hi = bootstrap_ci(lambda idx: auc(y[idx], s[idx]), n=300, seed=0, n_obs=len(y))
    assert lo < point < hi
    with pytest.raises(ValueError, match="n_obs"):
        bootstrap_ci(lambda idx: 0.0, n=10)


def test_cluster_bootstrap_is_wider_for_clustered_data():
    rng = np.random.default_rng(5)
    clusters = np.repeat(np.arange(40), 25)              # 40 customers x 25 rows each
    x = rng.normal(0, 1, 40)[clusters] + rng.normal(0, 0.2, clusters.size)
    lo_iid, hi_iid = bootstrap_ci(x, n=500, seed=0)
    lo_cl, hi_cl = bootstrap_ci(x, n=500, seed=0, cluster=clusters)
    assert (hi_cl - lo_cl) > 3 * (hi_iid - lo_iid)
    with pytest.raises(ValueError, match="one label per observation"):
        bootstrap_ci(x, n=10, cluster=clusters[:-1])


def test_bootstrap_refuses_nonfinite_statistic():
    y = np.array([0] * 3 + [1])                          # many resamples have a single class
    s = np.arange(4.0)
    with pytest.raises(ValueError):
        bootstrap_ci(lambda idx: auc(y[idx], s[idx]), n=200, seed=0, n_obs=4)
    with pytest.raises(ValueError, match="non-finite"):
        bootstrap_ci(lambda idx: float("nan"), n=20, n_obs=10)


# ---------------------------------------------------------------------------- classification
def test_auc_brier_match_sklearn():
    rng = np.random.default_rng(11)
    y = rng.integers(0, 2, 2000)
    p = np.clip(0.3 * y + rng.uniform(0, 0.7, 2000), 0, 1)
    p_rounded = np.round(p, 1)                           # heavy ties
    assert auc(y, p) == pytest.approx(roc_auc_score(y, p))
    assert auc(y, p_rounded) == pytest.approx(roc_auc_score(y, p_rounded))
    assert brier(y, p) == pytest.approx(brier_score_loss(y, p))


def test_ece_values():
    rng = np.random.default_rng(3)
    p = rng.uniform(0, 1, 200_000)
    y = (rng.uniform(0, 1, p.size) < p).astype(int)
    assert ece(y, p) < 0.01
    y2 = np.array([0, 1] * 500)
    assert ece(y2, np.full(1000, 0.9)) == pytest.approx(0.4)


def test_classification_metrics_refuse_bad_input():
    with pytest.raises(ValueError, match="single class"):
        auc([1, 1, 1], [0.1, 0.2, 0.3])
    with pytest.raises(ValueError, match="binary"):
        brier([0, 2], [0.1, 0.2])
    with pytest.raises(ValueError, match="probabilities"):
        ece([0, 1], [0.1, 1.2])
    with pytest.raises(ValueError, match="non-finite"):
        auc([0, 1], [0.1, float("nan")])
    with pytest.raises(ValueError, match="length"):
        brier([0, 1, 1], [0.1, 0.2])


# ---------------------------------------------------------------------------- drift
def test_psi_zero_for_identical_and_small_for_same_distribution():
    rng = np.random.default_rng(0)
    ref = pd.Series(rng.normal(0, 1, 5000))
    assert psi(ref, ref.copy()) == pytest.approx(0.0, abs=1e-12)
    assert psi(ref, pd.Series(rng.normal(0, 1, 5000))) < 0.02


def test_psi_flags_shifted_distribution():
    rng = np.random.default_rng(0)
    ref = pd.Series(rng.normal(0, 1, 5000))
    assert psi(ref, pd.Series(rng.normal(1.0, 1, 5000))) > 0.2
    assert psi(ref, pd.Series(rng.normal(0, 2.0, 5000))) > 0.2


def test_psi_categorical_and_low_cardinality():
    ref = pd.Series(["organic"] * 40 + ["paid_search"] * 35 + ["referral"] * 25)
    same = ref.sample(frac=1.0, random_state=0)
    assert psi(ref, same) == pytest.approx(0.0, abs=1e-12)
    new_channel = pd.Series(["organic"] * 30 + ["paid_search"] * 25 + ["referral"] * 15 + ["livestream"] * 30)
    assert psi(ref, new_channel) > 0.2
    const = pd.Series([0] * 100)                         # constant reference: shift either way is drift
    assert psi(const, pd.Series([5] * 100)) > 0.2
    assert psi(const, pd.Series([-1] * 100)) > 0.2
    ints = pd.Series([0, 1, 2, 3] * 50)
    floats = pd.Series([0.0, 1.0, 2.0, 3.0] * 50)        # e.g. the same counts read back with NaNs elsewhere
    assert psi(ints, floats) == pytest.approx(0.0, abs=1e-12)


def test_psi_counts_missingness_as_drift():
    rng = np.random.default_rng(1)
    ref = pd.Series(rng.normal(0, 1, 2000))
    cur = ref.copy()
    cur[:600] = np.nan
    assert psi(ref, cur) > 0.2


def test_psi_refuses_bad_input():
    with pytest.raises(ValueError):
        psi(pd.Series([], dtype=float), pd.Series([1.0]))
    with pytest.raises(ValueError, match="numeric"):
        psi(pd.Series(np.arange(50.0)), pd.Series(["a"] * 50))
    with pytest.raises(ValueError):
        psi(pd.Series([1.0, 2.0]), pd.Series([1.0]), bins=1)


def test_drift_report_flags_shifted_features():
    rng = np.random.default_rng(4)
    n = 3000
    ref = pd.DataFrame({"minutes": rng.lognormal(4.2, 0.5, n), "tickets": rng.poisson(0.3, n),
                        "channel": rng.choice(["organic", "paid_search", "referral"], n)})
    cur = pd.DataFrame({"minutes": rng.lognormal(3.7, 0.5, n), "tickets": rng.poisson(0.3, n),
                        "channel": rng.choice(["organic", "paid_search", "referral", "livestream"], n,
                                              p=[.3, .25, .2, .25])})
    rep = drift_report(ref, cur, ["minutes", "tickets", "channel"])
    assert rep["outcome"] == "FAIL"
    assert set(rep["flagged"]) == {"minutes", "channel"}
    assert rep["psi"]["tickets"] < 0.1
    assert rep["features"]["tickets"]["kind"] == "categorical"     # low-cardinality count
    assert rep["features"]["minutes"]["kind"] == "numeric"
    assert drift_report(ref, ref, ["minutes", "channel"])["outcome"] == "PASS"
    with pytest.raises(ValueError, match="missing"):
        drift_report(ref, cur, ["minutes", "nope"])
    with pytest.raises(ValueError):
        drift_report(ref, cur, [])


def test_drift_detects_planted_d9_channel_shift(small_ws):
    """D9: from 2026-08 a new livestream channel appears. Compare signup cohorts in the drift export."""
    from dsharness.workspace import CUTOFFS
    c = pd.read_csv(small_ws.source_dir(CUTOFFS["drift"]) / "crm_customers.csv", parse_dates=["created_at"])
    cohort = c["created_at"].dt.strftime("%Y-%m")
    ref, cur = c[cohort.between("2026-05", "2026-07")], c[cohort == "2026-08"]
    rep = drift_report(ref, cur, ["channel", "region"])
    assert rep["flagged"] == ["channel"] and rep["outcome"] == "FAIL"
    assert rep["psi"]["region"] < 0.1


def test_power_from_moments_textbook_values_and_guards():
    from dsharness.decision.design import power_from_moments
    # z_0.975 + z_0.8 = 2.8016: with sd 1 per arm and n = 2 * 2.8016^2 / 0.5^2 per arm, power is 0.8 at 0.5
    n = math.ceil(2 * 2.8016 ** 2 / 0.25)
    pw = power_from_moments(0.5, 1.0, 1.0, n)
    assert pw["power"] == pytest.approx(0.8, abs=0.01) and pw["n_per_arm_for_target"] == n
    assert pw["mde_fen"] == pytest.approx(2.8016 * math.sqrt(2 / n), rel=1e-3)
    assert power_from_moments(0.5, 1.0, 1.0, 4 * n)["power"] > 0.99
    assert power_from_moments(0.0, 1.0, 1.0, n)["power"] == pytest.approx(0.025, abs=1e-3)   # alpha / 2
    neg = power_from_moments(-0.5, 1.0, 1.0, n)
    assert neg["power"] < 0.001 and neg["n_per_arm_for_target"] is None and "no sample size" in neg["note"]
    for bad in (dict(n_per_arm=1), dict(n_per_arm=2.5), dict(sd_control=-1.0),
                dict(value_per_contact_fen=float("nan")), dict(power_target=1.0)):
        with pytest.raises(ValueError):
            power_from_moments(**{"value_per_contact_fen": 0.5, "sd_control": 1.0, "sd_treatment": 1.0,
                                  "n_per_arm": n, **bad})


def test_itt_power_is_the_two_proportion_power():
    from dsharness.decision.design import itt_power
    # 0.20 vs 0.10 with 200 per arm: z = 0.1 / sqrt((0.16 + 0.09) / 200) = 2.828 -> power ~ 0.81
    assert itt_power([0.2] * 10, [0.1] * 10, 200) == pytest.approx(0.807, abs=0.01)
    assert itt_power([0.2] * 10, [0.2] * 10, 200) == pytest.approx(0.025, abs=1e-3)
    with pytest.raises(ValueError):
        itt_power([0.2], [1.2], 200)
