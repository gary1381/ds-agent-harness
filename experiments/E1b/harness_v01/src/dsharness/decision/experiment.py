"""Randomised retention-contact experiment: assignment and readout.

Why this exists: a churn model says who is *likely to leave*, not who will *stay because we
called*. The only evidence the business loop accepts for "contacting these customers is worth
it" is a randomised comparison within the targeted population, read out intention-to-treat
with an interval, after a sample-ratio check proves the assignment was not broken.

Assignment is a pure function of (salt, customer_id), so anyone can re-derive which arm a
customer was in -- an auditable property a random-number call does not have. The readout only
sees assignments, observed outcomes and prices; it never sees the model scores, so it cannot
mistake predicted risk for causal effect.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
import hashlib

import numpy as np
import pandas as pd

from ..verify.stats import srm_test, two_proportion_diff, z_critical

ARMS = ("treatment", "control")


# Contact channels. Both offer a one-month discount on the next bill, which only a customer who stays
# to pay that bill redeems: the offer is charged to treated customers who are retained, never to
# churners or to the control arm. Contact cost is paid for every treated (contacted) customer.
CHANNELS: dict[str, dict] = {
    "call": {"contact_cost_fen": 1500, "offer_cost_rate": 0.2},   # agent phone call + 20% of one month
    "app": {"contact_cost_fen": 50, "offer_cost_rate": 0.1},      # in-app message + 10% of one month
}
DEFAULT_CHANNEL = "app"
COST_MODEL = "contact_cost_fen per treated customer + offer_cost_rate x price for treated customers who are retained"


@dataclass
class ValueParams:
    """Business assumptions that turn retained customers into money. Every readout echoes them.

    Defaults are the ``app`` channel (the July decision's channel); ``ValueParams.for_channel("call")``
    gives the phone-call economics. Cost model (shared by ``readout`` and the oracle's truth):
    ``contact_cost_fen`` for every treated customer plus ``offer_cost_rate * price`` for every
    treated customer who is retained (the discount is redeemed on the next bill)."""
    horizon_months_retained_value: int = 6   # months of revenue a retained customer is credited with
    margin_rate: float = 0.6                 # contribution margin on that revenue
    contact_cost_fen: int = CHANNELS[DEFAULT_CHANNEL]["contact_cost_fen"]
    offer_cost_rate: float = CHANNELS[DEFAULT_CHANNEL]["offer_cost_rate"]   # share of one month's price
    channel: str = DEFAULT_CHANNEL

    def __post_init__(self) -> None:
        if self.horizon_months_retained_value <= 0:
            raise ValueError("horizon_months_retained_value must be positive")
        if not 0.0 <= self.margin_rate <= 1.0:
            raise ValueError("margin_rate must be in [0, 1]")
        if self.contact_cost_fen < 0 or self.offer_cost_rate < 0:
            raise ValueError("costs must be non-negative")
        if not isinstance(self.channel, str) or not self.channel.strip():
            raise ValueError("channel must be a non-empty label")

    @classmethod
    def for_channel(cls, channel: str, **overrides) -> "ValueParams":
        """Preset economics of a known channel; other assumptions (horizon, margin) may be overridden."""
        if channel not in CHANNELS:
            raise ValueError(f"unknown channel {channel!r}; known: {sorted(CHANNELS)}")
        bad = set(overrides) & {"contact_cost_fen", "offer_cost_rate", "channel"}
        if bad:
            raise ValueError(f"{sorted(bad)} are fixed by the channel preset; build ValueParams(...) directly "
                             "for custom economics")
        return cls(**CHANNELS[channel], channel=channel, **overrides)

    @property
    def value_multiplier(self) -> float:
        """Fen of value per fen of monthly price retained."""
        return self.horizon_months_retained_value * self.margin_rate

    def treated_cost(self, price_fen, retained) -> np.ndarray | float:
        """Cost of contacting a customer: contact + offer if retained (indicator or probability)."""
        return self.contact_cost_fen + self.offer_cost_rate * np.asarray(price_fen, dtype=float) * np.asarray(
            retained, dtype=float)

    def expected_value(self, p0, p1, price_fen) -> np.ndarray:
        """Expected incremental value (fen) of contacting a customer with potential churn
        probabilities p0 (not contacted) and p1 (contacted): retained value minus expected cost."""
        p0, p1 = np.asarray(p0, dtype=float), np.asarray(p1, dtype=float)
        price = np.asarray(price_fen, dtype=float)
        return (p0 - p1) * price * self.value_multiplier - self.treated_cost(price, 1.0 - p1)


def assign(customer_ids: Iterable[str], salt: str, p_treat: float = 0.5) -> pd.DataFrame:
    """Deterministic bucketing: first 8 bytes of sha256(salt + id) as a uniform in [0, 1)."""
    if not 0.0 < p_treat < 1.0:
        raise ValueError(f"p_treat must be in (0, 1), got {p_treat}")
    if not salt:
        raise ValueError("an empty salt makes assignments collide across experiments")
    ids = [str(c) for c in customer_ids]
    if not ids:
        raise ValueError("no customers to assign")
    if len(set(ids)) != len(ids):
        raise ValueError("customer_ids contain duplicates")
    u = np.array([int.from_bytes(hashlib.sha256((salt + c).encode()).digest()[:8], "big") / 2.0 ** 64
                  for c in ids])
    return pd.DataFrame({"customer_id": ids, "arm": np.where(u < p_treat, "treatment", "control")})


def _require(df: pd.DataFrame, cols: set[str], name: str) -> None:
    missing = cols - set(df.columns)
    if missing:
        raise ValueError(f"{name} lacks columns {sorted(missing)}")
    if df["customer_id"].duplicated().any():
        raise ValueError(f"{name} has duplicate customer_id rows")


def _interval(point: float, half: float) -> dict:
    return {"point": float(point), "ci": [float(point - half), float(point + half)]}


def readout(assignments: pd.DataFrame, outcomes: pd.DataFrame, prices: pd.DataFrame,
            vp: ValueParams | None = None, *, alpha: float = 0.05, min_n_per_arm: int = 100,
            expected_share_treatment: float = 0.5, basis: str = "value") -> dict:
    """Intention-to-treat readout of a two-arm experiment.

    Effects are "control minus treatment" churn, so positive = the contact reduced churn.
    Value uses the price-weighted ITT difference (unbiased when the effect varies with price)
    and the cost model of ``ValueParams`` (contact for every treated customer, offer only for
    treated customers who are retained), estimated from the treatment arm:
      loss_control_i   = H * margin * churn_i * price_i
      loss_treatment_i = H * margin * churn_i * price_i + offer_rate * price_i * (1 - churn_i)
      value_per_contact_fen = mean_c(loss) - mean_t(loss) - contact_cost      (SE from both arms)
      incremental_value_fen = n_treat * value_per_contact_fen

    outcome: FAIL if the SRM check fails (assignment broken) or the ``basis`` CI is entirely
    below zero (the intervention is proven to lose value / increase churn); INCONCLUSIVE if an
    arm is smaller than ``min_n_per_arm`` or the CI spans zero; PASS only if the CI is entirely
    above zero. ``basis`` is ``"value"`` (default: the business decision) or ``"itt"``.
    """
    vp = vp or ValueParams()
    if basis not in ("value", "itt"):
        raise ValueError(f"basis must be 'value' or 'itt', got {basis!r}")
    z = z_critical(alpha)
    _require(assignments, {"customer_id", "arm"}, "assignments")
    _require(outcomes, {"customer_id", "churned"}, "outcomes")
    _require(prices, {"customer_id", "price_fen"}, "prices")
    bad_arm = set(assignments["arm"]) - set(ARMS)
    if bad_arm:
        raise ValueError(f"unknown arms {sorted(bad_arm)}")

    a = assignments[["customer_id", "arm"]].astype({"customer_id": str})
    o = outcomes[["customer_id", "churned"]].astype({"customer_id": str})
    p = prices[["customer_id", "price_fen"]].astype({"customer_id": str})
    df = a.merge(o, on="customer_id", how="left").merge(p, on="customer_id", how="left")
    for col, src in (("churned", "outcomes"), ("price_fen", "prices")):
        miss = df.loc[df[col].isna(), "customer_id"]
        if len(miss):
            raise ValueError(f"{len(miss)} assigned customers have no row in {src} "
                             f"(e.g. {miss.head(3).tolist()}); an ITT readout must not drop them")
    churned = df["churned"].astype(float).to_numpy()
    if not np.all((churned == 0.0) | (churned == 1.0)):
        raise ValueError("churned must be boolean / 0-1")
    price = df["price_fen"].astype(float).to_numpy()
    if np.any(~np.isfinite(price)) or np.any(price <= 0):
        raise ValueError("price_fen must be positive and finite")
    is_t = (df["arm"] == "treatment").to_numpy()

    n_t, n_c = int(is_t.sum()), int((~is_t).sum())
    srm = srm_test(n_t, n_c, expected_share_treatment)
    base = {"n": {"treatment": n_t, "control": n_c}, "srm": srm, "alpha": alpha, "basis": basis,
            "value_params": asdict(vp), "channel": vp.channel, "cost_model": COST_MODEL,
            "price_mean_fen": float(price.mean()),
            "n_outcome_rows_not_assigned": int((~o["customer_id"].isin(a["customer_id"])).sum()),
            "limitations": [
                "value depends on ValueParams assumptions (horizon, margin, contact and offer cost)",
                "effect applies to this targeted population only; it does not transfer to another targeting rule",
                "normal-approximation intervals; prices must be known-at-assignment (not current mutable price)",
            ]}
    if n_t == 0 or n_c == 0:
        empty = "treatment" if n_t == 0 else "control"
        return {**base, "outcome": "FAIL", "reasons": [f"arm '{empty}' is empty"], "churn_rate": None,
                "itt": None, "incremental_retained": None, "incremental_value_fen": None,
                "value_per_contact_fen": None, "projected_value_fen_all_targeted": None}

    x_t, x_c = int(churned[is_t].sum()), int(churned[~is_t].sum())
    itt = two_proportion_diff(x_c, n_c, x_t, n_t, alpha)   # diff = churn_control - churn_treatment
    retained = {"point": itt["diff"] * n_t, "ci": [itt["ci"][0] * n_t, itt["ci"][1] * n_t]}

    k = vp.value_multiplier
    loss = k * churned * price + np.where(is_t, vp.offer_cost_rate * price * (1.0 - churned), 0.0)
    ddof = 1 if min(n_t, n_c) > 1 else 0
    d_loss = loss[~is_t].mean() - loss[is_t].mean()
    se_loss = float(np.sqrt(loss[~is_t].var(ddof=ddof) / n_c + loss[is_t].var(ddof=ddof) / n_t))
    per_contact = _interval(d_loss - vp.contact_cost_fen, z * se_loss)
    value_t = _interval(n_t * per_contact["point"], n_t * z * se_loss)
    n_all = n_t + n_c
    projected = {"point": per_contact["point"] * n_all, "ci": [c * n_all for c in per_contact["ci"]]}

    lo, hi = (per_contact["ci"] if basis == "value" else itt["ci"])
    reasons: list[str] = []
    if srm["outcome"] != "PASS":
        outcome = "FAIL"
        reasons.append(f"sample ratio mismatch (p={srm['p_value']:.2e} < {srm['threshold']}): "
                       "assignment or logging is broken; effect estimates are not trustworthy")
    elif min(n_t, n_c) < min_n_per_arm:
        outcome = "INCONCLUSIVE"
        reasons.append(f"smallest arm has {min(n_t, n_c)} < {min_n_per_arm} customers")
    elif lo > 0:
        outcome = "PASS"
        reasons.append(f"{basis} CI [{lo:.4g}, {hi:.4g}] is above zero")
    elif hi < 0:
        outcome = "FAIL"
        reasons.append(f"{basis} CI [{lo:.4g}, {hi:.4g}] is below zero: contacting this population "
                       + ("loses money" if basis == "value" else "increases churn"))
    else:
        outcome = "INCONCLUSIVE"
        reasons.append(f"{basis} CI [{lo:.4g}, {hi:.4g}] spans zero")

    return {**base, "outcome": outcome, "reasons": reasons,
            "churn_rate": {"treatment": x_t / n_t, "control": x_c / n_c},
            "itt": itt, "incremental_retained": retained,
            "incremental_value_fen": value_t, "value_per_contact_fen": per_contact,
            "projected_value_fen_all_targeted": projected}
