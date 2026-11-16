"""Population stability (PSI) drift monitoring for model inputs and scores.

Why this exists: a model validated on last quarter's customers silently degrades when the
population changes (the D9 shift: a new livestream channel and a price rise). Labels arrive
a month late, so input drift is the earliest available alarm. A drift flag is not proof the
model is wrong -- it is the trigger to re-evaluate on matured labels and, if needed, retrain
through the normal gates (never an automatic promotion).

Binning: numeric features with more than ``bins`` distinct reference values use reference
quantile bins with open-ended outer bins; low-cardinality numerics and categoricals are
compared value by value (so a constant reference still detects a shift in either
direction). Missing values form their own bin, because a jump in missingness is drift too.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

EPS = 1e-4            # floor for empty bins so log ratios stay finite
MISSING = "__missing__"
MODERATE = 0.1        # conventional "watch" level; 0.2 is the usual "act" level


def _is_numeric(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)


def _labels(ref: pd.Series, cur: pd.Series, bins: int) -> tuple[pd.Series, pd.Series, str]:
    num_ref, num_cur = _is_numeric(ref), _is_numeric(cur)
    if num_ref != num_cur and not (ref.isna().all() or cur.isna().all()):
        raise ValueError(f"reference is {'numeric' if num_ref else 'categorical'} but current is not")
    if num_ref and ref.dropna().nunique() > bins:
        qs = np.quantile(ref.dropna().to_numpy(dtype=float), np.linspace(0, 1, bins + 1)[1:-1])
        edges = np.unique(qs)

        def cut(s: pd.Series) -> pd.Series:
            out = pd.Series(np.searchsorted(edges, s.to_numpy(dtype=float), side="left").astype(str),
                            index=s.index, dtype=object)
            out[s.isna()] = MISSING
            return out
        return cut(ref), cut(cur), "numeric"

    def cat(s: pd.Series) -> pd.Series:
        # numerics go through float so int 1 and float 1.0 (ints with NaN) are the same bin
        out = (s.astype(float) if _is_numeric(s) else s.astype(object)).map(str)
        out[s.isna()] = MISSING
        return out
    return cat(ref), cat(cur), "categorical"


def _psi(ref: pd.Series, cur: pd.Series, bins: int) -> tuple[float, str]:
    if bins < 2:
        raise ValueError("bins must be >= 2")
    ref, cur = pd.Series(ref), pd.Series(cur)
    if ref.empty or cur.empty:
        raise ValueError("psi needs non-empty reference and current samples")
    lr, lc, kind = _labels(ref, cur, bins)
    keys = sorted(set(lr) | set(lc))
    r = np.maximum(lr.value_counts(normalize=True).reindex(keys, fill_value=0.0).to_numpy(), EPS)
    c = np.maximum(lc.value_counts(normalize=True).reindex(keys, fill_value=0.0).to_numpy(), EPS)
    return float(np.sum((c - r) * np.log(c / r))), kind


def psi(ref: pd.Series, cur: pd.Series, bins: int = 10) -> float:
    """Population stability index sum((c - r) * ln(c / r)) over bins defined on the reference."""
    return _psi(ref, cur, bins)[0]


def drift_report(ref_df: pd.DataFrame, cur_df: pd.DataFrame, features: list[str],
                 threshold: float = 0.2, bins: int = 10) -> dict:
    """Per-feature PSI; ``flagged`` lists features with psi > threshold (outcome FAIL if any)."""
    if not features:
        raise ValueError("no features to monitor")
    missing = [f for f in features if f not in ref_df.columns or f not in cur_df.columns]
    if missing:
        raise ValueError(f"features missing from reference/current frames: {missing}")
    per: dict[str, dict] = {}
    for f in features:
        value, kind = _psi(ref_df[f], cur_df[f], bins)
        per[f] = {"psi": value, "kind": kind,
                  "n_ref": int(len(ref_df)), "n_cur": int(len(cur_df)),
                  "missing_share_ref": float(ref_df[f].isna().mean()),
                  "missing_share_cur": float(cur_df[f].isna().mean())}
    flagged = sorted((f for f, d in per.items() if d["psi"] > threshold), key=lambda f: -per[f]["psi"])
    watch = sorted(f for f, d in per.items() if MODERATE < d["psi"] <= threshold)
    return {"threshold": threshold, "bins": bins, "psi": {f: d["psi"] for f, d in per.items()},
            "features": per, "flagged": flagged, "watch": watch,
            "outcome": "FAIL" if flagged else "PASS"}
