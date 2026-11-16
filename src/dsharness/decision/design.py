"""Pre-registered design of the July policy experiment: power, the A/B split and the assignment.

Why this exists: an experiment that cannot detect the effect it is meant to measure produces an
INCONCLUSIVE readout by construction, and an experiment designed after peeking at outcomes is not
evidence. So the design -- who is eligible, how the month is split between policies, how many
customers each policy targets, how they are randomised, and the power that size buys -- is a pure
function of (design constants, the scoring build, the registered models), fixed in code and
digested before the oracle is asked for a single outcome.

The design compares targeting POLICIES, not models: the eligible July population is split into
one part per policy by a salted hash (two policies: halves); each policy picks its top-k inside its
part, and each target list is randomised 50/50. Customers are disjoint across policies, so the whole
comparison is ONE experiment (one id, one combined assignment): the oracle releases one potential
outcome per customer and refuses any second July experiment on the same customers.

Power is computed for the readout's actual PASS rule (value-per-contact 95% CI above zero, after
the channel's costs). Before the experiment the assumed value and SDs come from the policy's
operating point on held-out randomised campaigns (``power_from_moments``; in-sample model
predictions would carry a winner's curse because the list was selected on them); the evaluator can
recompute it from custody truth (``value_power`` on the true p0/p1) to show what the design could
have detected. ``itt_power`` ("churn went down") is reported beside it only for contrast.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
import math

import numpy as np
import pandas as pd
from scipy.stats import norm

from ..common import digest
from ..verify.stats import z_critical
from .experiment import ValueParams, assign
from .policies import POLICIES

POWER_TARGET = 0.8


def _probs(x, name: str) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    if a.ndim != 1 or a.size == 0 or not np.all(np.isfinite(a)) or np.any((a < 0) | (a > 1)):
        raise ValueError(f"{name} must be a non-empty 1-D array of probabilities")
    return a


def value_moments(p0, p1, price_fen, vp: ValueParams) -> dict:
    """Mean and per-arm variance of the readout's value statistic for customers drawn at random
    from a target list with potential churn probabilities p0 (control) and p1 (treated).

    loss_c = H*m*price*churn,  churn ~ Bern(p0)
    loss_t = H*m*price*churn + offer*price*(1-churn),  churn ~ Bern(p1)
    value per contact = E[loss_c] - E[loss_t] - contact
    """
    p0, p1 = _probs(p0, "p0"), _probs(p1, "p1")
    price = np.asarray(price_fen, dtype=float)
    if price.shape != p0.shape or p1.shape != p0.shape:
        raise ValueError("p0, p1 and price_fen must have the same length")
    if not np.all(np.isfinite(price)) or np.any(price <= 0):
        raise ValueError("price_fen must be positive and finite")
    a = vp.value_multiplier * price                   # loss when the customer churns
    b = vp.offer_cost_rate * price                    # offer cost when a treated customer stays
    m_c, s_c = np.mean(p0 * a), np.mean(p0 * a * a)
    m_t, s_t = np.mean(p1 * a + (1 - p1) * b), np.mean(p1 * a * a + (1 - p1) * b * b)
    return {"value_per_contact_fen": float(m_c - m_t - vp.contact_cost_fen),
            "sd_control": float(math.sqrt(max(s_c - m_c ** 2, 0.0))),
            "sd_treatment": float(math.sqrt(max(s_t - m_t ** 2, 0.0))),
            "itt_effect": float(np.mean(p0) - np.mean(p1)), "n_list": int(p0.size)}


def power_from_moments(value_per_contact_fen: float, sd_control: float, sd_treatment: float, n_per_arm: int, *,
                       alpha: float = 0.05, power_target: float = POWER_TARGET) -> dict:
    """P(readout PASS) = P(value-per-contact 95% LCB > 0) with ``n_per_arm`` customers per arm for an
    assumed true value per contact and per-arm SDs of the readout's loss statistic; the minimum
    detectable value at ``power_target`` and the n per arm that target needs at the assumed value."""
    if not isinstance(n_per_arm, int) or isinstance(n_per_arm, bool) or n_per_arm < 2:
        raise ValueError(f"n_per_arm must be an int >= 2, got {n_per_arm!r}")
    if not 0.0 < power_target < 1.0:
        raise ValueError("power_target must be in (0, 1)")
    for name, v in (("value_per_contact_fen", value_per_contact_fen), ("sd_control", sd_control),
                    ("sd_treatment", sd_treatment)):
        if not math.isfinite(v):
            raise ValueError(f"{name} must be finite")
    if sd_control < 0 or sd_treatment < 0:
        raise ValueError("standard deviations must be >= 0")
    z = z_critical(alpha)
    z_pow = float(norm.ppf(power_target))
    var1 = sd_control ** 2 + sd_treatment ** 2                   # n * Var(estimate)
    se = math.sqrt(var1 / n_per_arm)
    mu = float(value_per_contact_fen)
    power = float(norm.cdf(mu / se - z)) if se > 0 else float(mu > 0)
    need = math.ceil((z + z_pow) ** 2 * var1 / mu ** 2) if mu > 0 else None
    return {"assumed_value_per_contact_fen": mu, "sd_control": float(sd_control),
            "sd_treatment": float(sd_treatment), "n_per_arm": n_per_arm, "alpha": alpha, "se_fen": se,
            "power": power, "power_target": power_target, "mde_fen": (z + z_pow) * se,
            "n_per_arm_for_target": need,
            "note": None if mu > 0 else "assumed value per contact <= 0: no sample size makes a PASS likely"}


def itt_power(p0, p1, n_per_arm: int, *, alpha: float = 0.05) -> float:
    """P(ITT churn-reduction 95% CI above 0) -- the weaker "churn went down" criterion, shown next to
    the value power to make clear how much of the design's power the channel costs consume."""
    p0, p1 = _probs(p0, "p0"), _probs(p1, "p1")
    if not isinstance(n_per_arm, int) or isinstance(n_per_arm, bool) or n_per_arm < 2:
        raise ValueError(f"n_per_arm must be an int >= 2, got {n_per_arm!r}")
    m0, m1 = float(p0.mean()), float(p1.mean())
    se = math.sqrt((m0 * (1 - m0) + m1 * (1 - m1)) / n_per_arm)
    return float(norm.cdf((m0 - m1) / se - z_critical(alpha))) if se > 0 else float(m0 > m1)


def value_power(p0, p1, price_fen, vp: ValueParams, n_per_arm: int, *, alpha: float = 0.05,
                power_target: float = POWER_TARGET) -> dict:
    """``power_from_moments`` with the moments implied by per-customer potential-outcome
    probabilities of a target list (model predictions, or custody truth on the evaluator side)."""
    m = value_moments(p0, p1, price_fen, vp)
    return {**m, **power_from_moments(m["value_per_contact_fen"], m["sd_control"], m["sd_treatment"], n_per_arm,
                                      alpha=alpha, power_target=power_target)}


# ---------------------------------------------------------------------------- the policy experiment design
@dataclass(frozen=True)
class PolicyExperimentDesign:
    """Targeting policies compared in ONE randomised experiment on disjoint parts of the month.

    The eligible population is split into ``len(policies)`` equal parts by a salted hash (one
    policy: everyone is eligible for it). ``k_per_policy`` is the most customers a policy may
    target in its part (``uplift_value_top_k`` targets fewer when fewer customers have a positive
    expected value). Each target list is randomised ``p_treat`` : 1 - ``p_treat``. All salts derive
    from the experiment id, so the split and the randomisations are re-derivable by anyone."""
    experiment_id: str
    as_of: str
    policies: tuple[str, ...] = ("risk_top_k", "uplift_value_top_k")
    k_per_policy: int = 500
    channel: str = "app"
    p_treat: float = 0.5
    min_n_per_arm: int = 100

    def __post_init__(self) -> None:
        object.__setattr__(self, "policies", tuple(self.policies))
        if not isinstance(self.experiment_id, str) or not self.experiment_id:
            raise ValueError("experiment_id is required")
        if not isinstance(self.k_per_policy, int) or isinstance(self.k_per_policy, bool) or self.k_per_policy < 2:
            raise ValueError(f"k_per_policy must be an int >= 2, got {self.k_per_policy!r}")
        if not 1 <= len(self.policies) <= 2:
            raise ValueError("a policy experiment compares one or two policies")
        for p in self.policies:
            if p not in POLICIES:
                raise ValueError(f"unknown policy {p!r}; known: {sorted(POLICIES)}")
        if len(set(self.policies)) != len(self.policies):
            raise ValueError("each policy appears once in a design")
        ValueParams.for_channel(self.channel)          # validates the channel
        if not 0.0 < self.p_treat < 1.0:
            raise ValueError("p_treat must be in (0, 1)")

    @property
    def split_salt(self) -> str:
        return f"{self.experiment_id}:split"

    def treat_salt(self, policy: str) -> str:
        return f"{self.experiment_id}:{policy}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["policies"] = list(self.policies)
        return d

    def digest(self) -> str:
        return digest(self.to_dict())

    def split(self, population: Iterable[str]) -> dict[str, list[str]]:
        """policy -> its part of the eligible population (salted-hash equal split, order-independent)."""
        ids = sorted({str(c) for c in population})
        if not ids:
            raise ValueError("empty eligible population")
        if len(self.policies) == 1:
            return {self.policies[0]: ids}
        halves = assign(ids, self.split_salt, 0.5)
        first = set(halves.loc[halves["arm"] == "treatment", "customer_id"])
        return {self.policies[0]: [c for c in ids if c in first],
                self.policies[1]: [c for c in ids if c not in first]}

    def assignment(self, targets: dict[str, list[str]]) -> pd.DataFrame:
        """Combined assignment (customer_id, policy, arm) for the policies' target lists."""
        parts = []
        for policy in self.policies:
            ids = targets.get(policy)
            if not ids:
                raise ValueError(f"policy {policy} targets nobody; the experiment cannot read it out")
            a = assign(ids, self.treat_salt(policy), self.p_treat)
            parts.append(a.assign(policy=policy)[["customer_id", "policy", "arm"]])
        out = pd.concat(parts, ignore_index=True)
        if out["customer_id"].duplicated().any():
            raise ValueError("a customer is targeted by two policies; the parts must be disjoint")
        return out
