"""Trusted training template for churn candidates.

Trust boundary: a candidate is a *spec* (feature names, model family, params, training
months), not code. This module owns everything that touches data: features come from
`dsharness.ml.features` on one READY build, preprocessing is fit inside each CV fold only,
and the fitted artifact is registered with its sha256 in `registry/candidates.jsonl`.
`runs/` is candidate-writable scratch, so anything that later loads an artifact (scoring,
the sealed evaluator) re-checks it against the registry and refuses a swapped file before
unpickling it.

Development metrics come from expanding-window time CV on the build itself (train on
months < k, validate on month k). This module never reads `custody/`; the only honest
estimate of future performance is `dsharness.ml.custody.SealedEvaluator`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
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
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler
from threadpoolctl import threadpool_limits   # hard dependency of scikit-learn

from ..common import append_jsonl, digest, file_digest, read_jsonl, utcnow
from ..lake.build import load_report, warehouse_path
from ..workspace import Workspace
from . import features as F
from .features import FeatureDef, build_features, build_training_frame, readonly_connection, resolve_features

MODELS = ("logreg", "hgb")
MIN_FOLDS = 3
LABEL = "churned_next_month"
MISSING_CATEGORY = "__missing__"
CANDIDATES_LEDGER = "candidates.jsonl"      # under registry/
_CANDIDATE_ID = re.compile(r"^[0-9a-f]{16}$")
_BUILD_ID = re.compile(r"^\d{4}-\d{2}-\d{2}_[0-9a-f]{8}$")

# Candidate identity includes the code that turns a spec into a model.
CODE_VERSION = digest({p.name: file_digest(p) for p in (Path(F.__file__), Path(__file__))})[:16]


def _single_threaded():
    """Fit/predict on one thread: the data are small, results stay reproducible, and OpenMP
    thread pools (HistGradientBoosting) can stall badly on an oversubscribed host."""
    return threadpool_limits(limits=1)


class CandidateIntegrityError(RuntimeError):
    """A registered candidate's files no longer match the digests recorded at training time."""


@dataclass(frozen=True)
class CandidateSpec:
    features: tuple[str, ...]
    model: str  # 'logreg' | 'hgb'
    params: dict
    train_as_of: tuple[str, ...]
    seed: int = 0
    extra_features: dict | None = None   # name -> FeatureDef (or its dict form)

    def __post_init__(self):
        object.__setattr__(self, "features", tuple(self.features))
        object.__setattr__(self, "train_as_of", tuple(self.train_as_of))
        object.__setattr__(self, "params", dict(self.params or {}))
        if self.extra_features is not None:
            extra = {k: v if isinstance(v, FeatureDef) else FeatureDef(**v)
                     for k, v in dict(self.extra_features).items()}
            object.__setattr__(self, "extra_features", extra)

    def validate(self) -> None:
        if self.model not in MODELS:
            raise ValueError(f"model must be one of {MODELS}, got {self.model!r}")
        if not self.features:
            raise ValueError("a candidate needs at least one feature")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise ValueError(f"seed must be an int, got {self.seed!r}")
        if "random_state" in self.params:
            raise ValueError("set the seed through CandidateSpec.seed, not params['random_state']")
        days = [F.validate_as_of(a) for a in self.train_as_of]
        if any(b <= a for a, b in zip(days, days[1:])):
            raise ValueError(f"train_as_of must be strictly increasing, got {self.train_as_of}")
        if len(days) < MIN_FOLDS + 1:
            raise ValueError(f"expanding-window CV needs >= {MIN_FOLDS + 1} as_of months "
                             f"(>= {MIN_FOLDS} folds), got {len(days)}")
        resolve_features(self.features, self.extra_features)

    def to_dict(self) -> dict:
        """Canonical form (identity). Extra definitions not used by `features` are not part of it."""
        extra = None
        if self.extra_features:
            used = {n: asdict(f) for n, f in self.extra_features.items() if n in self.features}
            extra = used or None
        return {"features": list(self.features), "model": self.model, "params": self.params,
                "train_as_of": list(self.train_as_of), "seed": self.seed, "extra_features": extra}

    @classmethod
    def from_dict(cls, d: dict) -> "CandidateSpec":
        return cls(features=tuple(d["features"]), model=d["model"], params=dict(d.get("params") or {}),
                   train_as_of=tuple(d["train_as_of"]), seed=int(d.get("seed", 0)),
                   extra_features=d.get("extra_features"))


# ---------------------------------------------------------------------------- builds
def ready_build_cutoff(ws: Workspace, build_id: str) -> str:
    """Cutoff of a READY build; raises for malformed ids, missing or FAILED builds."""
    if not isinstance(build_id, str) or not _BUILD_ID.match(build_id):
        raise ValueError(f"malformed build id {build_id!r}")
    rep = load_report(ws, build_id)
    if rep["status"] != "READY":
        raise RuntimeError(f"build {build_id} is {rep['status']}: {rep['reasons'][:5]}")
    return rep["cutoff"]


# ---------------------------------------------------------------------------- model template
def _coerce_frame(X: pd.DataFrame, categorical: list[str], numeric: list[str]) -> pd.DataFrame:
    """First pipeline step: fixed column order and types, so scoring cannot drift from training."""
    missing = [c for c in [*categorical, *numeric] if c not in X.columns]
    if missing:
        raise ValueError(f"feature frame lacks columns {missing}")
    out = {}
    for c in categorical:
        out[c] = np.array([MISSING_CATEGORY if pd.isna(v) else str(v) for v in X[c].tolist()], dtype=object)
    for c in numeric:
        out[c] = pd.to_numeric(X[c], errors="raise").to_numpy(dtype="float64", na_value=np.nan)
    return pd.DataFrame(out, index=X.index)


def _feature_kinds(frame: pd.DataFrame, names) -> tuple[list[str], list[str]]:
    categorical, numeric = [], []
    for n in names:
        s = frame[n]
        (numeric if pd.api.types.is_bool_dtype(s) or pd.api.types.is_numeric_dtype(s) else categorical).append(n)
    return categorical, numeric


def make_pipeline(spec: CandidateSpec, categorical: list[str], numeric: list[str]) -> Pipeline:
    transformers = []
    if categorical:
        transformers.append(("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), categorical))
    if numeric:
        transformers.append(("num", Pipeline([
            ("impute", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)),
            ("scale", StandardScaler())]), numeric))
    if spec.model == "logreg":
        model = LogisticRegression(**{"max_iter": 2000, **spec.params}, random_state=spec.seed)
    elif spec.model == "hgb":
        model = HistGradientBoostingClassifier(**spec.params, random_state=spec.seed)
    else:
        raise ValueError(f"unknown model {spec.model!r}")
    return Pipeline([
        ("coerce", FunctionTransformer(_coerce_frame, kw_args={"categorical": categorical, "numeric": numeric})),
        ("prep", ColumnTransformer(transformers, remainder="drop")),
        ("model", model),
    ])


def classification_metrics(y, p) -> dict:
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    if len(np.unique(y)) < 2:
        raise ValueError("metrics need both classes in y")
    return {"auc": float(roc_auc_score(y, p)), "brier": float(brier_score_loss(y, p)),
            "logloss": float(log_loss(y, p, labels=[0, 1])), "base_rate": float(y.mean()), "n": int(len(y))}


def _require_both_classes(y: pd.Series, what: str) -> None:
    if y.nunique() < 2:
        raise ValueError(f"{what} has a single label class ({y.unique().tolist()}); cannot fit or score")


# ---------------------------------------------------------------------------- registry
def _registry_path(ws: Workspace) -> Path:
    return ws.registry / CANDIDATES_LEDGER


def candidate_record(ws: Workspace, candidate_id: str) -> dict:
    """Latest registry record for a candidate (KeyError if unknown)."""
    if not isinstance(candidate_id, str) or not _CANDIDATE_ID.match(candidate_id):
        raise KeyError(f"malformed candidate id {candidate_id!r}")
    recs = [r for r in read_jsonl(_registry_path(ws)) if r.get("candidate_id") == candidate_id]
    if not recs:
        raise KeyError(f"unknown candidate {candidate_id}")
    return recs[-1]


def candidate_id_for(spec: CandidateSpec, build_id: str) -> str:
    return digest({"spec": spec.to_dict(), "build_id": build_id, "code_version": CODE_VERSION})[:16]


# ---------------------------------------------------------------------------- API
def train_candidate(ws: Workspace, build_id: str, spec: CandidateSpec, *, requested_by: str) -> dict:
    """Fit a candidate with time-ordered CV and register the artifact. Raises on any invalid input."""
    if not isinstance(requested_by, str) or not requested_by.strip():
        raise ValueError("requested_by is required (who asked for this candidate)")
    spec.validate()
    cutoff = ready_build_cutoff(ws, build_id)
    immature = [a for a in spec.train_as_of if not F.label_mature(a, cutoff)]
    if immature:
        raise ValueError(f"labels for {immature} are not mature at build cutoff {cutoff} "
                         f"(need as_of + 1 month + {F.LABEL_MATURITY_DAYS} days <= cutoff)")
    defs = resolve_features(spec.features, spec.extra_features)
    names = list(spec.features)
    with readonly_connection(warehouse_path(ws, build_id)) as con:
        frame = build_training_frame(con, spec.train_as_of, names, cutoff, spec.extra_features)
    if frame.attrs.get("immature_as_of"):
        raise ValueError(f"immature labels at {frame.attrs['immature_as_of']}")
    categorical, numeric = _feature_kinds(frame, names)
    template = make_pipeline(spec, categorical, numeric)

    folds, caught_all = [], []
    as_ofs = list(spec.train_as_of)
    for k in range(1, len(as_ofs)):
        tr = frame[frame["as_of"].isin(as_ofs[:k])]
        va = frame[frame["as_of"] == as_ofs[k]]
        _require_both_classes(tr[LABEL], f"training months {as_ofs[:k]}")
        _require_both_classes(va[LABEL], f"validation month {as_ofs[k]}")
        with warnings.catch_warnings(record=True) as caught, _single_threaded():
            warnings.simplefilter("always")
            fitted = clone(template).fit(tr[names], tr[LABEL])
            p = fitted.predict_proba(va[names])[:, 1]
        caught_all += caught
        folds.append({"fold": k, "train_as_of": as_ofs[:k], "valid_as_of": as_ofs[k],
                      "n_train": int(len(tr)), **classification_metrics(va[LABEL], p)})
    cv_mean = {m: float(np.mean([f[m] for f in folds])) for m in ("auc", "brier", "logloss", "base_rate")}

    with warnings.catch_warnings(record=True) as caught, _single_threaded():
        warnings.simplefilter("always")
        final = clone(template).fit(frame[names], frame[LABEL])
    caught_all += caught
    fit_warnings = sorted({f"{w.category.__name__}: {str(w.message)[:200]}" for w in caught_all})

    candidate_id = candidate_id_for(spec, build_id)
    feature_set = {n: {"sql": d.sql, "origin": d.origin, "description": d.description} for n, d in defs.items()}
    feature_set_digest = digest(feature_set)
    spec_doc = {"candidate_id": candidate_id, "build_id": build_id, "build_cutoff": cutoff,
                "code_version": CODE_VERSION, "spec": spec.to_dict(),
                "feature_kinds": {"categorical": categorical, "numeric": numeric},
                "feature_set": feature_set, "feature_set_digest": feature_set_digest}
    cv_doc = {"folds": folds, "cv_mean": cv_mean, "warnings": fit_warnings, "n_rows": int(len(frame))}

    ws.runs.mkdir(parents=True, exist_ok=True)
    run_dir, tmp = ws.runs / candidate_id, ws.runs / f".{candidate_id}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    joblib.dump(final, tmp / "model.joblib")
    (tmp / "spec.json").write_text(json.dumps(spec_doc, indent=2, sort_keys=True))
    (tmp / "cv.json").write_text(json.dumps(cv_doc, indent=2, sort_keys=True))
    artifact_digest = file_digest(tmp / "model.joblib")
    shutil.rmtree(run_dir, ignore_errors=True)
    tmp.rename(run_dir)

    append_jsonl(_registry_path(ws), {
        "kind": "candidate", "candidate_id": candidate_id, "build_id": build_id, "build_cutoff": cutoff,
        "code_version": CODE_VERSION, "spec": spec.to_dict(), "spec_doc_digest": digest(spec_doc),
        "artifact_digest": artifact_digest, "feature_set_digest": feature_set_digest,
        "feature_origins": {n: d.origin for n, d in defs.items()}, "cv_mean": cv_mean,
        "requested_by": requested_by, "created_at": utcnow()})
    return {"candidate_id": candidate_id, "build_id": build_id, "cv": folds, "cv_mean": cv_mean,
            "artifact_digest": artifact_digest, "feature_set_digest": feature_set_digest,
            "n_rows": int(len(frame)), "warnings": fit_warnings, "run_dir": str(run_dir)}


def load_candidate(ws: Workspace, candidate_id: str) -> tuple[Pipeline, CandidateSpec]:
    """Load a registered candidate after verifying its files against the registry digests."""
    rec = candidate_record(ws, candidate_id)
    run = ws.runs / candidate_id
    spec_path, model_path = run / "spec.json", run / "model.joblib"
    if not spec_path.exists() or not model_path.exists():
        raise CandidateIntegrityError(f"candidate {candidate_id}: run files are missing in {run}")
    spec_doc = json.loads(spec_path.read_text())
    if digest(spec_doc) != rec["spec_doc_digest"]:
        raise CandidateIntegrityError(f"candidate {candidate_id}: spec.json differs from the registered spec")
    if file_digest(model_path) != rec["artifact_digest"]:
        raise CandidateIntegrityError(f"candidate {candidate_id}: model.joblib digest differs from the "
                                      f"registered artifact; refusing to unpickle it")
    return joblib.load(model_path), CandidateSpec.from_dict(spec_doc["spec"])


def score(ws: Workspace, candidate_id: str, build_id: str, as_of: str) -> pd.DataFrame:
    """customer_id, score (P(churn in the month starting at as_of)) for the population at as_of."""
    pipe, spec = load_candidate(ws, candidate_id)
    cutoff = ready_build_cutoff(ws, build_id)
    if F.validate_as_of(as_of) > F.parse_day(cutoff) + timedelta(days=1):
        raise ValueError(f"as_of {as_of} is after build {build_id} (cutoff {cutoff}): the build cannot "
                         f"know the population or features at that time")
    with readonly_connection(warehouse_path(ws, build_id)) as con:
        X = build_features(con, as_of, list(spec.features), spec.extra_features)
    with _single_threaded():
        p = pipe.predict_proba(X[list(spec.features)])[:, 1]
    if not np.all(np.isfinite(p)) or np.any((p < 0) | (p > 1)):
        raise RuntimeError(f"candidate {candidate_id} produced invalid probabilities")
    return pd.DataFrame({"customer_id": X["customer_id"].values, "score": p})
