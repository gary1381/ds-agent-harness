"""Small, dependency-light statistics used by checks: bootstrap CIs, proportion tests, SRM,
and the classification metrics the sealed evaluator and monitors report.

Why this exists: a number without an interval invites over-reading, and a randomised
experiment whose arms are not the size the design promised (sample-ratio mismatch) is
broken no matter how good its effect estimate looks. Every function here validates its
inputs and raises ``ValueError`` on degenerate data (one-class AUC, empty arms, NaNs)
instead of returning a plausible-looking number -- a silently wrong statistic is exactly
the failure mode the harness exists to catch.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
import math

import numpy as np
from scipy import stats as _st

SRM_ALPHA = 0.001   # conventional SRM threshold: alarms are rare and almost always real bugs


def z_critical(alpha: float) -> float:
    """Two-sided normal critical value, e.g. 1.96 for alpha=0.05."""
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    return float(_st.norm.ppf(1.0 - alpha / 2.0))


def _as_float_array(x, name: str) -> np.ndarray:
    arr = np.asarray(x, dtype=float)
    if arr.ndim != 1:
        raise ValueError(f"{name} must be 1-D, got shape {arr.shape}")
    if arr.size == 0:
        raise ValueError(f"{name} is empty")
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} contains {int((~np.isfinite(arr)).sum())} non-finite values")
    return arr


def _as_binary(y, name: str = "y") -> np.ndarray:
    arr = _as_float_array(y, name)
    if not np.all((arr == 0.0) | (arr == 1.0)):
        raise ValueError(f"{name} must be binary 0/1")
    return arr


def _as_prob(p, name: str = "p") -> np.ndarray:
    arr = _as_float_array(p, name)
    if np.any((arr < 0.0) | (arr > 1.0)):
        raise ValueError(f"{name} must be probabilities in [0, 1]")
    return arr


# ---------------------------------------------------------------------------- bootstrap
def bootstrap_ci(values_or_fn: Sequence[float] | np.ndarray | Callable[[np.ndarray], float],
                 n: int = 1000, alpha: float = 0.05, seed: int = 0, cluster=None, *,
                 n_obs: int | None = None) -> tuple[float, float]:
    """Percentile bootstrap interval.

    ``values_or_fn`` is either a 1-D array of observations (statistic = mean) or a callable
    ``fn(idx) -> float`` that computes the statistic on the resampled row positions ``idx``
    (so any statistic over aligned arrays, e.g. AUC, can be bootstrapped). A callable needs
    the number of rows: from ``cluster`` (one label per row) or ``n_obs``.

    ``cluster`` switches to a cluster bootstrap: whole clusters are resampled with
    replacement, which is what keeps intervals honest when rows are not independent
    (several months of the same customer, several tickets of one account).
    """
    if n < 2:
        raise ValueError("need at least 2 bootstrap replicates")
    z_critical(alpha)  # validates alpha
    if callable(values_or_fn):
        fn = values_or_fn
        if cluster is not None:
            size = len(cluster)
        elif n_obs is not None:
            size = int(n_obs)
        else:
            raise ValueError("bootstrap_ci with a callable needs cluster= or n_obs= to know the row count")
    else:
        vals = _as_float_array(values_or_fn, "values")
        size = vals.size

        def fn(idx: np.ndarray) -> float:
            return float(vals[idx].mean())
    if size < 2:
        raise ValueError(f"need at least 2 observations to bootstrap, got {size}")

    rng = np.random.default_rng(seed)
    if cluster is not None:
        labels = np.asarray(cluster)
        if labels.shape != (size,):
            raise ValueError(f"cluster must have one label per observation ({size}), got {labels.shape}")
        _, inverse = np.unique(labels, return_inverse=True)
        order = np.argsort(inverse, kind="stable")
        bounds = np.cumsum(np.bincount(inverse))
        groups = np.split(order, bounds[:-1])
        n_groups = len(groups)
        if n_groups < 2:
            raise ValueError("cluster bootstrap needs at least 2 clusters")

        def draw() -> np.ndarray:
            pick = rng.integers(0, n_groups, size=n_groups)
            return np.concatenate([groups[g] for g in pick])
    else:
        def draw() -> np.ndarray:
            return rng.integers(0, size, size=size)

    reps = np.empty(n)
    for i in range(n):
        reps[i] = fn(draw())
    bad = ~np.isfinite(reps)
    if bad.any():
        raise ValueError(f"statistic was non-finite in {int(bad.sum())}/{n} bootstrap replicates "
                         "(e.g. a resample with a single class); the interval would be biased")
    lo, hi = np.quantile(reps, [alpha / 2.0, 1.0 - alpha / 2.0])
    return float(lo), float(hi)


# ---------------------------------------------------------------------------- proportions
def two_proportion_diff(x_a: int, n_a: int, x_b: int, n_b: int, alpha: float = 0.05) -> dict:
    """Difference of two independent proportions, ``diff = p_a - p_b`` (normal approximation).

    ``se`` is the unpooled standard error used for the Wald CI; ``z``/``p_value`` use the pooled
    standard error (the usual test of H0: p_a == p_b). Both are reported so readers can see
    which one drives which number.
    """
    for name, x, nn in (("a", x_a, n_a), ("b", x_b, n_b)):
        if nn <= 0:
            raise ValueError(f"arm {name}: n must be positive, got {nn}")
        if not 0 <= x <= nn:
            raise ValueError(f"arm {name}: successes {x} outside [0, {nn}]")
    z_crit = z_critical(alpha)
    p_a, p_b = x_a / n_a, x_b / n_b
    diff = p_a - p_b
    se = math.sqrt(p_a * (1 - p_a) / n_a + p_b * (1 - p_b) / n_b)
    pooled = (x_a + x_b) / (n_a + n_b)
    se_pooled = math.sqrt(pooled * (1 - pooled) * (1 / n_a + 1 / n_b))
    if se_pooled > 0:
        z = diff / se_pooled
        p_value = float(2.0 * _st.norm.sf(abs(z)))
    else:   # both arms all-0 or all-1: no evidence of a difference
        z, p_value = 0.0, 1.0
    return {"p_a": p_a, "p_b": p_b, "n_a": int(n_a), "n_b": int(n_b), "diff": diff, "se": se,
            "ci": [diff - z_crit * se, diff + z_crit * se], "z": float(z), "p_value": p_value,
            "se_pooled": se_pooled, "alpha": alpha, "method": "wald_unpooled_ci+pooled_z"}


def srm_test(n_a: int, n_b: int, expected_share_a: float = 0.5) -> dict:
    """Sample-ratio-mismatch chi-square test (1 dof). ``outcome`` is PASS if p >= 0.001."""
    if n_a < 0 or n_b < 0:
        raise ValueError("arm sizes must be non-negative")
    n = n_a + n_b
    if n == 0:
        raise ValueError("SRM test on an empty experiment")
    if not 0.0 < expected_share_a < 1.0:
        raise ValueError(f"expected_share_a must be in (0, 1), got {expected_share_a}")
    exp_a, exp_b = n * expected_share_a, n * (1.0 - expected_share_a)
    chi2 = (n_a - exp_a) ** 2 / exp_a + (n_b - exp_b) ** 2 / exp_b
    p_value = float(_st.chi2.sf(chi2, df=1))
    return {"n_a": int(n_a), "n_b": int(n_b), "observed_share_a": n_a / n,
            "expected_share_a": expected_share_a, "chi2": float(chi2), "p_value": p_value,
            "threshold": SRM_ALPHA, "outcome": "PASS" if p_value >= SRM_ALPHA else "FAIL"}


# ---------------------------------------------------------------------------- classification
def auc(y, s) -> float:
    """ROC AUC via the Mann-Whitney rank statistic (ties get average ranks)."""
    y = _as_binary(y)
    s = _as_float_array(s, "s")
    if y.size != s.size:
        raise ValueError(f"y and s differ in length ({y.size} vs {s.size})")
    n_pos = int(y.sum())
    n_neg = y.size - n_pos
    if n_pos == 0 or n_neg == 0:
        raise ValueError("AUC is undefined with a single class")
    ranks = _st.rankdata(s)
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def brier(y, p) -> float:
    y = _as_binary(y)
    p = _as_prob(p)
    if y.size != p.size:
        raise ValueError(f"y and p differ in length ({y.size} vs {p.size})")
    return float(np.mean((p - y) ** 2))


def ece(y, p, bins: int = 10) -> float:
    """Expected calibration error with equal-width probability bins (weighted by bin size)."""
    if bins < 1:
        raise ValueError("bins must be >= 1")
    y = _as_binary(y)
    p = _as_prob(p)
    if y.size != p.size:
        raise ValueError(f"y and p differ in length ({y.size} vs {p.size})")
    idx = np.minimum((p * bins).astype(int), bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.sum() / y.size * abs(y[m].mean() - p[m].mean())
    return float(total)
