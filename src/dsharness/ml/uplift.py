"""Uplift (heterogeneous treatment effect) of the retention contact, learned from the randomised
2026-03..06 program in ``silver.retention_contacts``.

Trust boundary: the same as ``ml.train``. An uplift model is a *spec* (trusted feature names, a
method, a regularisation strength), fitted by this module on ONE READY build; features come from
``ml.features`` at ``as_of`` = the first day of each campaign month (point-in-time: the contact
row itself becomes available a day later and is never a feature). Labels (churn during the
campaign month) are used only when mature at the build cutoff -- on the 2026-06-30 score build
that is March..May; June is excluded, never read as "no churn". The artifact is registered with
its sha256 in ``registry/uplift_models.jsonl`` and re-verified before it is unpickled. This
module never reads ``custody/``; grading an uplift ranking against true potential outcomes is
evaluator code (``bench.tasks``).

Identification: the treatment is the randomised ``arm`` (intention to treat). ``exposed`` is
post-assignment compliance; conditioning on it (per-protocol) would compare self-selected
customers and is refused here.

Methods (small, transparent, seeded):
  * ``t_learner``   two L2-regularised logistic regressions, P(churn | x, control) and
                    P(churn | x, treatment); uplift = p0 - p1.
  * ``interaction`` one L2-regularised logistic regression with a shared baseline over all
                    features plus treatment x (1, pre-specified effect modifiers): the effect part
                    is a handful of reviewable coefficients.

Offline validation (``offline_validation``): leave-one-campaign-month-out cross-fitting, then a
Qini curve on the pooled held-out rows (ITT within the top fraction of the ranking) and the
realised value per contact of the top fraction, with percentile intervals from a bootstrap that
resamples whole customers (a customer can be enrolled in several months). The intervals condition
on the fitted fold models. The result is evidence-ready (outcome + metrics + limitations); only
``dsharness.tools`` records it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import json
from pathlib import Path
import re
import shutil
import warnings

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ..common import append_jsonl, digest, file_digest, read_jsonl, utcnow
from ..lake.build import warehouse_path
from ..workspace import Workspace
from . import features as F
from . import train as T
from .features import TRUSTED_FEATURES, build_features, build_labels, readonly_connection

PROGRAM_TABLE = "silver.retention_contacts"
METHODS = ("t_learner", "interaction")
UPLIFT_LEDGER = "uplift_models.jsonl"          # under registry/
# Pre-specified effect modifiers of the interaction method: the discount should matter more for
# price-sensitive customers who still use the product. Pricing-page views were instrumented in
# 2026-01 precisely to measure price sensitivity, so they lead; billing tickets are a weaker trace.
# Fixed before any outcome is seen; changing them is a new spec.
EFFECT_MODIFIERS = ("pricing_views_3m", "minutes_3m_avg", "billing_tickets_90d")
QINI_GRID = tuple(round(0.1 * i, 1) for i in range(1, 11))
MIN_HELDOUT_MONTHS = 2
MIN_EVENTS_PER_ARM = 5
BOOTSTRAP_REPS = 500
BOOTSTRAP_SEED = 20260701
_MODEL_ID = re.compile(r"^[0-9a-f]{16}$")

CODE_VERSION = digest({p.name: file_digest(p) for p in (Path(F.__file__), Path(T.__file__), Path(__file__))})[:16]


@dataclass(frozen=True)
class UpliftSpec:
    features: tuple[str, ...] = tuple(TRUSTED_FEATURES)
    method: str = "interaction"       # effect on the logit scale x modifiers: uplift scales with risk
    C: float = 0.1
    seed: int = 0
    effect_modifiers: tuple[str, ...] = EFFECT_MODIFIERS     # used by method='interaction' only
    train_months: tuple[str, ...] | None = None             # None: every program month mature at the cutoff

    def __post_init__(self):
        object.__setattr__(self, "features", tuple(self.features))
        object.__setattr__(self, "effect_modifiers", tuple(self.effect_modifiers))
        if self.train_months is not None:
            object.__setattr__(self, "train_months", tuple(self.train_months))

    def validate(self) -> None:
        if self.method not in METHODS:
            raise ValueError(f"method must be one of {METHODS}, got {self.method!r}")
        if not self.features or len(set(self.features)) != len(self.features):
            raise ValueError("features must be a non-empty list of distinct names")
        untrusted = [f for f in self.features if f not in TRUSTED_FEATURES]
        if untrusted:
            raise ValueError(f"uplift models use trusted point-in-time features only; not trusted: {untrusted}")
        if not isinstance(self.C, (int, float)) or isinstance(self.C, bool) or not self.C > 0:
            raise ValueError(f"C must be a positive number, got {self.C!r}")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise ValueError(f"seed must be an int, got {self.seed!r}")
        if self.method == "interaction":
            bad = [m for m in self.effect_modifiers if m not in self.features]
            if bad:
                raise ValueError(f"effect modifiers {bad} are not among the model's features")
        if self.train_months is not None:
            for m in self.train_months:
                if not re.fullmatch(r"\d{4}-\d{2}", str(m)):
                    raise ValueError(f"train_months entries must be 'YYYY-MM', got {m!r}")

    def to_dict(self) -> dict:
        return {"features": list(self.features), "method": self.method, "C": float(self.C), "seed": self.seed,
                "effect_modifiers": list(self.effect_modifiers) if self.method == "interaction" else [],
                "train_months": list(self.train_months) if self.train_months is not None else None}

    @classmethod
    def from_dict(cls, d: dict) -> "UpliftSpec":
        return cls(features=tuple(d["features"]), method=d["method"], C=float(d["C"]), seed=int(d["seed"]),
                   effect_modifiers=tuple(d.get("effect_modifiers") or EFFECT_MODIFIERS),
                   train_months=tuple(d["train_months"]) if d.get("train_months") is not None else None)


# ---------------------------------------------------------------------------- data
def program_months(con) -> list[str]:
    rows = con.execute(f"SELECT DISTINCT campaign_month FROM {PROGRAM_TABLE} ORDER BY 1").fetchall()
    return [r[0] for r in rows]


def program_frame(con, cutoff: str, features, months=None) -> pd.DataFrame:
    """One row per program enrolment: campaign_month, as_of, contact_id, customer_id, arm, treated
    (ITT: arm == 'treatment'), churned (in the campaign month), and the features at as_of.

    ``months=None`` uses every campaign month whose labels are mature at ``cutoff`` and lists the
    others in ``frame.attrs['immature_months']``; naming an immature month explicitly raises."""
    names = list(features)
    available = program_months(con)
    if not available:
        raise ValueError(f"{PROGRAM_TABLE} is empty in this build: no randomised program to learn from")
    if months is None:
        use = [m for m in available if F.label_mature(f"{m}-01", cutoff)]
        immature = [m for m in available if m not in use]
    else:
        use, immature = list(months), []
        unknown = [m for m in use if m not in available]
        if unknown:
            raise ValueError(f"campaign months {unknown} are not in {PROGRAM_TABLE} (has {available})")
        late = [m for m in use if not F.label_mature(f"{m}-01", cutoff)]
        if late:
            raise ValueError(f"labels of campaign months {late} are not mature at cutoff {cutoff} "
                             f"(need month start + 1 month + {F.LABEL_MATURITY_DAYS} days)")
    if not use:
        raise ValueError(f"no campaign month of {available} has mature labels at cutoff {cutoff}")
    parts = []
    for m in use:
        as_of = f"{m}-01"
        c = con.execute(f"SELECT contact_id, customer_id, arm FROM {PROGRAM_TABLE} WHERE campaign_month = ? "
                        f"ORDER BY customer_id", [m]).df()
        bad = set(c["arm"]) - {"treatment", "control"}
        if bad:
            raise ValueError(f"{PROGRAM_TABLE} {m}: unknown arms {sorted(bad, key=str)}")
        if c["customer_id"].duplicated().any():
            raise ValueError(f"{PROGRAM_TABLE} {m}: a customer is enrolled twice in one campaign month")
        X = build_features(con, as_of, names)
        y = build_labels(con, as_of, cutoff)
        d = c.merge(X, on="customer_id", how="left", indicator=True)
        missing = d.loc[d["_merge"] != "both", "customer_id"]
        if len(missing):
            raise ValueError(f"{len(missing)} customers enrolled in {m} are not in the population active at "
                             f"{as_of} (e.g. {missing.head(3).tolist()}): program and build disagree")
        d = d.drop(columns="_merge").merge(y[["customer_id", "churned_next_month"]], on="customer_id", how="left")
        if d["churned_next_month"].isna().any():
            raise ValueError(f"immature or missing labels for {m}")
        d.insert(0, "as_of", as_of)
        d.insert(0, "campaign_month", m)
        d["treated"] = (d["arm"] == "treatment").astype(int)
        d["churned"] = d["churned_next_month"].astype(int)
        parts.append(d.drop(columns="churned_next_month"))
    frame = pd.concat(parts, ignore_index=True)
    frame.attrs["immature_months"] = immature
    return frame


# ---------------------------------------------------------------------------- models
class _InteractionLogit:
    """Shared baseline over all features + treated x (1, standardised effect modifiers)."""

    def __init__(self, prep: Pipeline, modifiers: list[str], C: float, seed: int):
        self.prep, self.modifiers, self.C, self.seed = prep, modifiers, C, seed

    def _design(self, X: pd.DataFrame, treated: np.ndarray) -> np.ndarray:
        z = self.prep.transform(X)
        t = np.asarray(treated, dtype=float)[:, None]
        mods = self.mod_prep.transform(X[self.modifiers].astype(float)) if self.modifiers else np.empty((len(X), 0))
        return np.hstack([z, t, mods * t])

    def fit(self, X: pd.DataFrame, treated: np.ndarray, y: np.ndarray) -> "_InteractionLogit":
        self.prep = clone(self.prep).fit(X)
        if self.modifiers:
            self.mod_prep = Pipeline([("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
                                      ("scale", StandardScaler())]).fit(X[self.modifiers].astype(float))
        self.model = LogisticRegression(C=self.C, max_iter=5000, random_state=self.seed).fit(
            self._design(X, treated), y)
        return self

    def potential_outcomes(self, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        n = len(X)
        return (self.model.predict_proba(self._design(X, np.zeros(n)))[:, 1],
                self.model.predict_proba(self._design(X, np.ones(n)))[:, 1])

    def effect_coefficients(self) -> dict:
        w = self.model.coef_[0]
        k = len(self.modifiers)
        return {"treated": float(w[-k - 1]), **{m: float(c) for m, c in zip(self.modifiers, w[len(w) - k:])}}


class UpliftModel:
    """Fitted uplift model: ``predict(X)`` -> customer_id, p0 (churn if not contacted), p1 (if
    contacted, intention to treat), uplift = p0 - p1."""

    def __init__(self, spec: UpliftSpec, categorical: list[str], numeric: list[str]):
        self.spec, self.categorical, self.numeric = spec, categorical, numeric

    def _template(self) -> Pipeline:
        cand = T.CandidateSpec(self.spec.features, "logreg", {"C": float(self.spec.C)}, (), seed=self.spec.seed)
        return T.make_pipeline(cand, self.categorical, self.numeric)

    def fit(self, frame: pd.DataFrame) -> "UpliftModel":
        names = list(self.spec.features)
        for arm in (0, 1):
            sub = frame[frame["treated"] == arm]
            if sub["churned"].nunique() < 2:
                raise ValueError(f"the {'treatment' if arm else 'control'} arm has a single outcome class; "
                                 "cannot fit an uplift model")
        with warnings.catch_warnings(record=True) as caught, T._single_threaded():
            warnings.simplefilter("always")
            if self.spec.method == "t_learner":
                self.models = [clone(self._template()).fit(frame.loc[frame["treated"] == a, names],
                                                           frame.loc[frame["treated"] == a, "churned"])
                               for a in (0, 1)]
            else:
                prep = Pipeline(self._template().steps[:-1])
                self.models = [_InteractionLogit(prep, list(self.spec.effect_modifiers), float(self.spec.C),
                                                 self.spec.seed).fit(frame[names], frame["treated"].to_numpy(),
                                                                     frame["churned"].to_numpy())]
        self.fit_warnings = sorted({f"{w.category.__name__}: {str(w.message)[:200]}" for w in caught})
        return self

    def predict(self, X: pd.DataFrame) -> pd.DataFrame:
        names = list(self.spec.features)
        with T._single_threaded():
            if self.spec.method == "t_learner":
                p0 = self.models[0].predict_proba(X[names])[:, 1]
                p1 = self.models[1].predict_proba(X[names])[:, 1]
            else:
                p0, p1 = self.models[0].potential_outcomes(X[names])
        if not (np.all(np.isfinite(p0)) and np.all(np.isfinite(p1))):
            raise RuntimeError("uplift model produced non-finite probabilities")
        return pd.DataFrame({"customer_id": X["customer_id"].astype(str).to_numpy(), "p0": p0, "p1": p1,
                             "uplift": p0 - p1})

    def describe(self) -> dict:
        out = {"method": self.spec.method}
        if self.spec.method == "interaction":
            out["effect_coefficients"] = self.models[0].effect_coefficients()
        return out


def fit_uplift(frame: pd.DataFrame, spec: UpliftSpec) -> UpliftModel:
    spec.validate()
    categorical, numeric = T._feature_kinds(frame, spec.features)
    return UpliftModel(spec, categorical, numeric).fit(frame)


def cross_fit(frame: pd.DataFrame, spec: UpliftSpec) -> pd.DataFrame:
    """Leave-one-campaign-month-out predictions: each month is predicted by a model fitted on the
    others. Columns: campaign_month, customer_id, p0, p1, uplift (aligned with ``frame`` rows)."""
    months = sorted(frame["campaign_month"].unique())
    if len(months) < MIN_HELDOUT_MONTHS:
        raise ValueError(f"cross-fitting needs >= {MIN_HELDOUT_MONTHS} campaign months, got {months}")
    out = []
    for m in months:
        held = frame["campaign_month"] == m
        model = fit_uplift(frame[~held], spec)
        p = model.predict(frame[held])
        p.index = frame.index[held]
        out.append(p.assign(campaign_month=m))
    return pd.concat(out).loc[frame.index]


# ---------------------------------------------------------------------------- offline validation
def qini(score, treated, churned, grid=QINI_GRID) -> dict:
    """Qini curve of a ranking on randomised data (ITT). For the top fraction f of the ranking:
    incremental retained = n_f * (churn_control(f) - churn_treatment(f)); the random-targeting
    line is f * incremental(1). ``qini_coefficient`` = mean over f of (curve - random) / N, in
    retained customers per enrolment (0 = no better than random). NaN when a top fraction lacks
    one arm (the bootstrap then reports it as degenerate instead of inventing a value)."""
    s = np.asarray(score, dtype=float)
    t = np.asarray(treated, dtype=int)
    y = np.asarray(churned, dtype=float)
    n = len(s)
    order = np.argsort(-s, kind="stable")
    t, y = t[order], y[order]
    ct, cc = np.cumsum(t), np.cumsum(1 - t)
    yt, yc = np.cumsum(y * t), np.cumsum(y * (1 - t))
    curve = []
    for f in grid:
        k = max(int(round(f * n)), 1) - 1
        if ct[k] == 0 or cc[k] == 0:
            curve.append(np.nan)
        else:
            curve.append((yc[k] / cc[k] - yt[k] / ct[k]) * (k + 1))
    curve = np.array(curve)
    random = np.array(grid) * curve[-1]
    return {"grid": list(grid), "incremental_retained": curve.tolist(), "random": random.tolist(),
            "qini_coefficient": float(np.mean(curve - random) / n), "auuc": float(np.mean(curve) / n), "n": n}


def _value_top(score, treated, churned, price, fraction: float, vp, *, moments: bool = False):
    """Realised value per contact (fen) of contacting the top ``fraction`` of the ranking, by the
    readout's estimator (decision.experiment.readout) restricted to those rows. With
    ``moments=True`` also the per-arm SDs of the loss statistic and the arm sizes (for power)."""
    s = np.asarray(score, dtype=float)
    n_top = max(int(round(fraction * len(s))), 2)
    top = np.argsort(-s, kind="stable")[:n_top]
    t = np.asarray(treated, dtype=int)[top].astype(bool)
    y = np.asarray(churned, dtype=float)[top]
    p = np.asarray(price, dtype=float)[top]
    if t.sum() < 2 or (~t).sum() < 2:
        return {"point": float("nan")} if moments else float("nan")
    loss = vp.value_multiplier * y * p + np.where(t, vp.offer_cost_rate * p * (1.0 - y), 0.0)
    point = float(loss[~t].mean() - loss[t].mean() - vp.contact_cost_fen)
    if not moments:
        return point
    return {"point": point, "sd_control": float(loss[~t].std(ddof=1)), "sd_treatment": float(loss[t].std(ddof=1)),
            "n_treatment": int(t.sum()), "n_control": int((~t).sum())}


def offline_validation(frame: pd.DataFrame, score, *, price=None, vp=None, top_fraction: float | None = None,
                       n_boot: int = BOOTSTRAP_REPS, seed: int = BOOTSTRAP_SEED, alpha: float = 0.05,
                       label: str = "policy") -> dict:
    """Evidence-ready offline validation of a ranking ``score`` (one per ``frame`` row; for a learned
    uplift model use ``cross_fit`` predictions, never in-sample ones) on held-out randomised campaigns.

    outcome: INCONCLUSIVE if fewer than ``MIN_HELDOUT_MONTHS`` campaign months or fewer than
    ``MIN_EVENTS_PER_ARM`` churns per arm; FAIL if the Qini coefficient's upper bound is below 0
    (the ranking is worse than random targeting on held-out campaigns) or, when ``top_fraction`` is
    given, the realised value per contact of that operating point has an upper bound below 0 (the
    policy already lost money on held-out randomised campaigns under ``vp``'s channel costs); PASS
    otherwise. PASS means "validated under the ITT protocol and not shown to be worse than random
    or loss-making" -- with the program's size the intervals usually span 0, so better-than-random
    is reported separately and is NOT implied."""
    from ..verify.stats import bootstrap_ci
    s = np.asarray(score, dtype=float)
    if len(s) != len(frame) or not np.all(np.isfinite(s)):
        raise ValueError("score must be one finite value per frame row")
    t = frame["treated"].to_numpy(dtype=int)
    y = frame["churned"].to_numpy(dtype=float)
    cust = frame["customer_id"].astype(str).to_numpy()
    months = sorted(frame["campaign_month"].unique())
    events = {"treatment": int(y[t == 1].sum()), "control": int(y[t == 0].sum())}
    base = {"label": label, "n_enrolments": int(len(frame)), "n_customers": int(len(set(cust))),
            "campaign_months": months, "events": events, "estimand": "ITT on the randomised arm (never `exposed`)",
            "bootstrap": {"reps": n_boot, "seed": seed, "cluster": "customer_id", "alpha": alpha},
            "limitations": [
                "intervals resample customers but condition on the fitted fold models",
                "held-out months are earlier campaigns of the same program (same channel and offer)",
                "PASS = not worse than random targeting offline; only a prospective experiment shows value",
            ]}
    if len(months) < MIN_HELDOUT_MONTHS or min(events.values()) < MIN_EVENTS_PER_ARM:
        return {**base, "outcome": "INCONCLUSIVE", "qini": None, "qini_coefficient": None,
                "reasons": [f"too little held-out data: {len(months)} campaign month(s), churn events {events} "
                            f"(need >= {MIN_HELDOUT_MONTHS} months and >= {MIN_EVENTS_PER_ARM} per arm)"]}
    q = qini(s, t, y)

    def stat(idx: np.ndarray) -> float:
        return qini(s[idx], t[idx], y[idx])["qini_coefficient"]
    lo, hi = bootstrap_ci(stat, n=n_boot, alpha=alpha, seed=seed, cluster=cust)
    out = {**base, "qini": q, "qini_coefficient": {"point": q["qini_coefficient"], "ci": [lo, hi]},
           "better_than_random": "yes" if lo > 0 else "no" if hi < 0 else "undetermined"}
    if top_fraction is not None:
        if price is None or vp is None:
            raise ValueError("top_fraction needs price and vp")
        if not 0 < top_fraction <= 1:
            raise ValueError("top_fraction must be in (0, 1]")
        pr = np.asarray(price, dtype=float)
        if len(pr) != len(s) or not np.all(np.isfinite(pr)) or np.any(pr <= 0):
            raise ValueError("price must be one positive value per frame row")
        mom = _value_top(s, t, y, pr, top_fraction, vp, moments=True)
        if not np.isfinite(mom["point"]):
            raise ValueError(f"the top {top_fraction:.0%} of held-out enrolments lacks one arm; cannot value it")
        vlo, vhi = bootstrap_ci(lambda idx: _value_top(s[idx], t[idx], y[idx], pr[idx], top_fraction, vp),
                                n=n_boot, alpha=alpha, seed=seed + 1, cluster=cust)
        out["value_top_fraction"] = {"fraction": top_fraction, "channel": vp.channel, **mom,
                                     "ci": [vlo, vhi], "unit": "fen per contact (realised, held-out)"}
    fails = []
    if hi < 0:
        fails.append(f"held-out Qini coefficient CI [{lo:.4f}, {hi:.4f}] is below 0: the ranking is worse than "
                     f"random targeting on earlier randomised campaigns")
    vt = out.get("value_top_fraction")
    if vt is not None and vt["ci"][1] < 0:
        fails.append(f"contacting the top {vt['fraction']:.0%} of held-out enrolments ({vt['channel']} channel) "
                     f"realised {vt['point']:.0f} fen per contact, CI [{vt['ci'][0]:.0f}, {vt['ci'][1]:.0f}] below 0: "
                     f"the operating point already lost money on earlier randomised campaigns")
    if fails:
        out["outcome"], out["reasons"] = "FAIL", fails
    else:
        value_note = "" if vt is None else (f"; top {vt['fraction']:.0%} realised {vt['point']:.0f} fen/contact "
                                            f"CI [{vt['ci'][0]:.0f}, {vt['ci'][1]:.0f}]")
        out["outcome"] = "PASS"
        out["reasons"] = [f"held-out Qini coefficient {q['qini_coefficient']:+.4f} CI [{lo:.4f}, {hi:.4f}] "
                          f"({'better than random' if lo > 0 else 'not distinguishable from random'}){value_note}; "
                          f"validated by ITT on {len(months)} held-out campaign months"]
    return out


# ---------------------------------------------------------------------------- registry + API
def _registry_path(ws: Workspace) -> Path:
    return ws.registry / UPLIFT_LEDGER


def uplift_record(ws: Workspace, model_id: str) -> dict:
    if not isinstance(model_id, str) or not _MODEL_ID.match(model_id):
        raise KeyError(f"malformed uplift model id {model_id!r}")
    recs = [r for r in read_jsonl(_registry_path(ws)) if r.get("model_id") == model_id]
    if not recs:
        raise KeyError(f"unknown uplift model {model_id}")
    return recs[-1]


def train_uplift_model(ws: Workspace, build_id: str, spec: UpliftSpec, *, requested_by: str,
                       validate: bool = True) -> dict:
    """Fit on the build's mature campaign months, register the artifact, and (by default) run the
    leave-one-month-out offline validation of the same spec. Raises on invalid input."""
    if not isinstance(requested_by, str) or not requested_by.strip():
        raise ValueError("requested_by is required (who asked for this model)")
    spec.validate()
    cutoff = T.ready_build_cutoff(ws, build_id)
    with readonly_connection(warehouse_path(ws, build_id)) as con:
        frame = program_frame(con, cutoff, spec.features, spec.train_months)
    model = fit_uplift(frame, spec)
    validation = None
    if validate:
        cf = cross_fit(frame, spec)
        validation = offline_validation(frame, cf["uplift"].to_numpy(), label=f"uplift:{spec.method}")
    model_id = digest({"spec": spec.to_dict(), "build_id": build_id, "code_version": CODE_VERSION})[:16]
    months = sorted(frame["campaign_month"].unique())
    spec_doc = {"model_id": model_id, "build_id": build_id, "build_cutoff": cutoff, "code_version": CODE_VERSION,
                "spec": spec.to_dict(), "train_months": months,
                "immature_months_excluded": frame.attrs.get("immature_months", []),
                "feature_kinds": {"categorical": model.categorical, "numeric": model.numeric}}
    ws.runs.mkdir(parents=True, exist_ok=True)
    run_dir, tmp = ws.runs / model_id, ws.runs / f".{model_id}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    joblib.dump(model, tmp / "model.joblib")
    (tmp / "spec.json").write_text(json.dumps(spec_doc, indent=2, sort_keys=True))
    if validation is not None:
        (tmp / "offline_validation.json").write_text(json.dumps(validation, indent=2, sort_keys=True, default=str))
    artifact_digest = file_digest(tmp / "model.joblib")
    shutil.rmtree(run_dir, ignore_errors=True)
    tmp.rename(run_dir)
    n_arm = {a: int((frame["treated"] == (a == "treatment")).sum()) for a in ("treatment", "control")}
    append_jsonl(_registry_path(ws), {
        "kind": "uplift_model", "model_id": model_id, "build_id": build_id, "build_cutoff": cutoff,
        "code_version": CODE_VERSION, "spec": spec.to_dict(), "spec_doc_digest": digest(spec_doc),
        "artifact_digest": artifact_digest, "train_months": months, "n_rows": int(len(frame)), "n_by_arm": n_arm,
        "requested_by": requested_by, "created_at": utcnow()})
    return {"model_id": model_id, "build_id": build_id, "artifact_digest": artifact_digest, "train_months": months,
            "immature_months_excluded": frame.attrs.get("immature_months", []), "n_rows": int(len(frame)),
            "n_by_arm": n_arm, "describe": model.describe(), "warnings": model.fit_warnings,
            "offline_validation": validation, "run_dir": str(run_dir)}


def load_uplift_model(ws: Workspace, model_id: str) -> tuple[UpliftModel, UpliftSpec]:
    rec = uplift_record(ws, model_id)
    run = ws.runs / model_id
    spec_path, model_path = run / "spec.json", run / "model.joblib"
    if not spec_path.exists() or not model_path.exists():
        raise T.CandidateIntegrityError(f"uplift model {model_id}: run files are missing in {run}")
    spec_doc = json.loads(spec_path.read_text())
    if digest(spec_doc) != rec["spec_doc_digest"]:
        raise T.CandidateIntegrityError(f"uplift model {model_id}: spec.json differs from the registered spec")
    if file_digest(model_path) != rec["artifact_digest"]:
        raise T.CandidateIntegrityError(f"uplift model {model_id}: model.joblib digest differs from the registered "
                                        f"artifact; refusing to unpickle it")
    return joblib.load(model_path), UpliftSpec.from_dict(spec_doc["spec"])


def predict_uplift(ws: Workspace, model_id: str, build_id: str, as_of: str) -> pd.DataFrame:
    """customer_id, p0, p1, uplift for the population active at ``as_of`` in ``build_id``."""
    model, spec = load_uplift_model(ws, model_id)
    cutoff = T.ready_build_cutoff(ws, build_id)
    if F.validate_as_of(as_of) > F.parse_day(cutoff) + timedelta(days=1):
        raise ValueError(f"as_of {as_of} is after build {build_id} (cutoff {cutoff})")
    with readonly_connection(warehouse_path(ws, build_id)) as con:
        X = build_features(con, as_of, list(spec.features))
    return model.predict(X)


def predict_frame(ws: Workspace, model_id: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Predictions of a registered model for rows that already carry its features (e.g. a program frame)."""
    model, _ = load_uplift_model(ws, model_id)
    return model.predict(frame)


__all__ = ["UpliftSpec", "UpliftModel", "program_frame", "fit_uplift", "cross_fit", "qini", "offline_validation",
           "train_uplift_model", "load_uplift_model", "predict_uplift", "uplift_record", "METHODS",
           "EFFECT_MODIFIERS"]
