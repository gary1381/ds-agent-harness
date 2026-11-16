"""Targeting policies: who gets the retention contact.

Kept deliberately dumb and deterministic. A policy only *selects*; whether the selection
creates value is a separate question that only a randomised readout (decision.experiment)
can answer -- ranking by churn risk is not ranking by treatment effect.
"""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd


def _check_k(k: int, available: int) -> None:
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    if k > available:
        raise ValueError(f"k={k} exceeds the {available} eligible customers")


def top_k(scores: pd.DataFrame, k: int, col: str = "score") -> list[str]:
    """The k highest-scoring customers; ties broken by customer_id so the list is reproducible."""
    missing = {"customer_id", col} - set(scores.columns)
    if missing:
        raise ValueError(f"scores frame lacks columns {sorted(missing)}")
    if scores["customer_id"].duplicated().any():
        raise ValueError("scores contain duplicate customer_id rows")
    vals = pd.to_numeric(scores[col], errors="raise")
    if vals.isna().any():
        raise ValueError(f"{int(vals.isna().sum())} customers have no {col}; score them or exclude them explicitly")
    _check_k(k, len(scores))
    ranked = (pd.DataFrame({"customer_id": scores["customer_id"].astype(str), "v": vals.to_numpy()})
              .sort_values(["v", "customer_id"], ascending=[False, True], kind="stable"))
    return ranked["customer_id"].head(k).tolist()


def random_k(ids: Iterable[str], k: int, seed: int) -> list[str]:
    """k customers drawn uniformly without replacement; independent of input order."""
    pool = sorted(str(i) for i in ids)
    if len(set(pool)) != len(pool):
        raise ValueError("ids contain duplicates")
    _check_k(k, len(pool))
    pick = np.random.default_rng(seed).choice(len(pool), size=k, replace=False)
    return [pool[i] for i in sorted(pick)]


# ---------------------------------------------------------------------------- named targeting policies
def risk_top_k(scores: pd.DataFrame, k: int) -> list[str]:
    """Status quo: the k customers with the highest predicted churn risk (column ``score``)."""
    return top_k(scores, k, col="score")


def expected_values(pred: pd.DataFrame, vp) -> pd.DataFrame:
    """Per customer: predicted uplift (p0 - p1) and expected value per contact (fen) under ``vp``
    (``decision.experiment.ValueParams``): uplift x retained value - contact - offer x P(retained | treated)."""
    missing = {"customer_id", "p0", "p1", "price_fen"} - set(pred.columns)
    if missing:
        raise ValueError(f"predictions lack columns {sorted(missing)}")
    if pred["customer_id"].duplicated().any():
        raise ValueError("predictions contain duplicate customer_id rows")
    vals = pred[["p0", "p1", "price_fen"]].apply(pd.to_numeric, errors="raise")
    if vals.isna().any().any():
        raise ValueError("predictions contain missing p0 / p1 / price_fen; score or exclude those customers")
    if ((vals[["p0", "p1"]] < 0) | (vals[["p0", "p1"]] > 1)).any().any() or (vals["price_fen"] <= 0).any():
        raise ValueError("p0 / p1 must be probabilities and price_fen positive")
    return pd.DataFrame({"customer_id": pred["customer_id"].astype(str).to_numpy(),
                         "uplift": (vals["p0"] - vals["p1"]).to_numpy(),
                         "ev_fen": vp.expected_value(vals["p0"], vals["p1"], vals["price_fen"])})


def uplift_value_top_k(pred: pd.DataFrame, k: int, vp) -> list[str]:
    """Up to k customers with the highest POSITIVE expected value per contact; fewer when fewer
    customers are worth contacting (a contact with negative expected value is never made to fill k)."""
    ev = expected_values(pred, vp)
    if not isinstance(k, int) or k <= 0:
        raise ValueError(f"k must be a positive int, got {k!r}")
    positive = ev[ev["ev_fen"] > 0]
    if positive.empty:
        return []
    return top_k(positive.rename(columns={"ev_fen": "score"})[["customer_id", "score"]], min(k, len(positive)))


POLICIES = {
    "risk_top_k": "top-k churn risk (churn candidate score)",
    "uplift_value_top_k": "top-k positive expected value per contact (uplift model x retained value - cost)",
}
