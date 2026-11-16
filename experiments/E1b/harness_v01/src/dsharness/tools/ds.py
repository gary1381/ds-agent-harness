"""Data-science tools: proposed features with leakage evidence, trusted training, sealed final
evaluation and release requests.

Trust boundary: an agent proposes *definitions* (feature SQL, a candidate spec), never code that
runs outside the trusted template. Feature SQL is statically checked (certified silver.* only, no
PII, no file access) before the leakage checks execute it on read-only builds. Candidates are
trained by `ml.train` and evaluated by `ml.custody.SealedEvaluator`, the only code that reads
holdout labels. This module turns those results into evidence:

  feature.leakage  (verifier:leakage)  per proposed feature definition (subject = its digest)
  model.leakage    (verifier:leakage)  per candidate: every feature is TRUSTED, or a proposed
                                       definition whose feature_accept gate is ALLOW
  model.sealed_eval (evaluator:custody) per candidate artifact
  experiment.readout (experiment:readout) via `run_experiment_readout` / `run_policy_experiment`
                                       (controller-side only)

Targeting policies (controller-side functions, not agent tools: the data-scientist role is at its
10-tool limit, and an agent reaches a policy's gates through `request_release(policy_id=...)`):

  policy.models        (verifier:policy)  every model the policy reads is released: the churn
                                          candidate's model_shadow gate is ALLOW; the uplift model
                                          is registered, digest-verified and uses trusted features
  policy.offline_qini  (verifier:uplift)  leave-one-campaign-month-out Qini of the policy's ranking
                                          on the randomised program (ITT), cluster-bootstrap CI
  experiment.readout   (experiment:readout) per policy, from ONE pre-registered July experiment

A model is never experimented on or scaled by itself: model_experiment / model_scale gate a
policy subject (rule + models + channel + k), and each policy's scale depends on its own readout.

`request_release` only asks the gate. A gate ALLOW is permission; nothing here deploys anything.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta

from ..common import append_jsonl, digest, read_jsonl, utcnow
from ..evidence import EvidenceStore, decide, evaluate_gate, load_policy
from ..lake.build import latest_ready_build, warehouse_path
from ..ml import train as ml_train
from ..ml.custody import SealedEvaluator
from ..ml.features import TRUSTED_FEATURES, FeatureDef, parse_day, population_sql, readonly_connection, render_sql
from ..ml.train import CandidateSpec, candidate_record
from ..verify.leakage import check_feature, pit_pairs
from ..verify.sql_static import analyze_sql
from ..workspace import CUTOFFS, Workspace
from .analytics import catalog
from .context import ToolContext, ToolError, governed_tool, jsonable

CHECK_VERSION = "1"
FEATURE_LOG = "features.jsonl"            # registry/: proposed feature definitions + their check result
SEALED_TASK = "churn-2026-06"
MODEL_STAGES = ("model_shadow", "model_experiment", "model_scale", "retrain_promote")
MAX_FEATURE_SQL_CHARS = 10_000
_GATE_STATUS = {"ALLOW": "PASS", "REJECT": "FAIL", "HOLD": "INCONCLUSIVE"}


# --------------------------------------------------------------------------- subjects
def feature_subject(feature: FeatureDef) -> dict:
    """Evidence subject of a feature definition (same digest as verify.leakage's feature_digest)."""
    return {"kind": "feature", "name": feature.name, "feature_digest": digest(asdict(feature))}


def candidate_subject(ws: Workspace, candidate_id: str) -> dict:
    """Evidence subject of a registered candidate artifact (KeyError if unknown).

    It binds the exact artifact: a retrained file is a new subject and needs new evidence."""
    rec = candidate_record(ws, candidate_id)
    return {"kind": "candidate", "candidate_id": candidate_id, "build_id": rec["build_id"],
            "artifact_digest": rec["artifact_digest"], "feature_set_digest": rec["feature_set_digest"],
            "eval_task": SEALED_TASK}


def proposals(ws: Workspace) -> list[dict]:
    return read_jsonl(ws.registry / FEATURE_LOG)


def latest_proposal(ws: Workspace, name: str) -> dict | None:
    hits = [p for p in proposals(ws) if p.get("name") == name]
    return hits[-1] if hits else None


def feature_status(ws: Workspace, feature: FeatureDef, requested_by: str) -> dict:
    """Authenticated leakage status of one proposed definition, via the feature_accept gate (pure)."""
    subject = feature_subject(feature)
    ev = EvidenceStore(ws).for_subject(digest(subject))
    dec = evaluate_gate(load_policy(), "feature_accept", subject, ev, [], requested_by)
    return {"origin": "proposed", "feature_digest": subject["feature_digest"], "status": _GATE_STATUS[dec.verdict],
            "gate_reasons": list(dec.reasons[:3]), "evidence_ids": list(dec.evidence_ids)}


# --------------------------------------------------------------------------- tools
@governed_tool("list_features")
def list_features(ctx: ToolContext) -> dict:
    """Trusted point-in-time features (with their SQL) and the latest proposal per proposed name."""
    trusted = [{"name": f.name, "description": f.description, "origin": "trusted", "sql": f.sql}
               for f in TRUSTED_FEATURES.values()]
    latest: dict[str, dict] = {}
    for p in proposals(ctx.ws):
        latest[p["name"]] = p
    proposed = []
    for name, p in sorted(latest.items()):
        f = FeatureDef(**p["feature"])
        proposed.append({"name": name, "description": f.description, "proposed_by": p.get("principal"),
                         **feature_status(ctx.ws, f, ctx.principal)})
    return {"trusted": trusted, "proposed": proposed,
            "rules": ["Feature SQL returns (customer_id, value), may join the relation `population`, and uses "
                      "{as_of} (a DATE literal).",
                      "Event rows only if available_at < {as_of}; never mutable_current or post_outcome columns "
                      "without a known-at guard.",
                      "Only certified silver.* tables; a proposed feature counts only after its leakage check PASSes."]}


@governed_tool("propose_feature")
def propose_feature(ctx: ToolContext, name: str, sql: str, description: str) -> dict:
    """Leakage-check a proposed feature (time travel on PIT builds + static temporal check)."""
    if not isinstance(sql, str) or len(sql) > MAX_FEATURE_SQL_CHARS:
        raise ValueError(f"sql must be a string of at most {MAX_FEATURE_SQL_CHARS} characters")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description is required: say what the feature measures and why it is point-in-time safe")
    if name in TRUSTED_FEATURES:
        raise ValueError(f"{name!r} is a trusted feature; use it by name or choose a new name")
    feature = FeatureDef(name, sql, description.strip(), "proposed")     # FeatureError is a ValueError
    as_of = pit_pairs()[0][0]
    wrapped = f"WITH population AS ({population_sql(as_of)})\nSELECT * FROM ({render_sql(feature.sql, as_of)}) AS f"
    rep = analyze_sql(wrapped, catalog())
    if not rep.ok:
        raise ToolError("refused", "feature SQL refused by static analysis", violations=rep.violations)
    if rep.tier != "certified":
        raise ToolError("refused", f"features may only read certified silver.* tables; this one reads {rep.tables}")
    res = check_feature(ctx.ws, feature, catalog())
    subject = feature_subject(feature)
    keep = ("as_of", "outcome", "n_compared", "n_diff", "examples", "reason", "early_build", "late_build")
    travel = [{k: t.get(k) for k in keep} for t in res["time_travel"]]
    static = {k: res["static"].get(k) for k in ("outcome", "findings", "reason")}
    rec = EvidenceStore(ctx.ws).record(
        check_id="feature.leakage", check_version=res["check_version"], stage="feature_accept", subject=subject,
        outcome=res["outcome"], metrics=jsonable({"time_travel": travel, "static": static, "reasons": res["reasons"]}),
        producer="verifier:leakage",
        limitations=["time travel only sees leaks that changed values inside the PIT windows",
                     "static temporal check is a heuristic over catalog tags"])
    append_jsonl(ctx.ws.registry / FEATURE_LOG, {
        "name": name, "feature_digest": subject["feature_digest"], "feature": asdict(feature),
        "outcome": res["outcome"], "evidence_id": rec.evidence_id, "principal": ctx.principal, "ts": utcnow()})
    decision = decide(ctx.ws, "feature_accept", subject, requested_by=ctx.principal)
    return {"name": name, "feature_digest": subject["feature_digest"], "outcome": res["outcome"],
            "reasons": res["reasons"], "time_travel": travel, "static": static, "warnings": rep.warnings,
            "evidence_ids": [rec.evidence_id], "gate": decision.to_dict()}


@governed_tool("train_candidate")
def train_candidate(ctx: ToolContext, features: list[str], model: str, params: dict | None = None,
                    train_as_of: list[str] | None = None) -> dict:
    """Train with the trusted template on this context's build and record candidate-level leakage."""
    if isinstance(features, str) or not isinstance(features, (list, tuple)) or not all(
            isinstance(f, str) for f in features):
        raise ValueError("features must be a list of feature names (see list_features)")
    if not isinstance(train_as_of, (list, tuple)) or not all(isinstance(a, str) for a in train_as_of):
        raise ValueError("train_as_of must be a list of 'YYYY-MM-01' dates (>= 4 months with mature labels)")
    params = dict(params or {})
    if not all(isinstance(k, str) and (v is None or isinstance(v, (str, int, float, bool)))
               for k, v in params.items()):
        raise ValueError("params must map parameter names to JSON scalars")
    extra = {}
    for n in features:
        if n in TRUSTED_FEATURES:
            continue
        prop = latest_proposal(ctx.ws, n)
        if prop is None:
            raise ValueError(f"unknown feature {n!r}: not trusted and never proposed (use propose_feature)")
        extra[n] = FeatureDef(**prop["feature"])
    spec = CandidateSpec(tuple(features), model, params, tuple(train_as_of), extra_features=extra or None)
    res = ml_train.train_candidate(ctx.ws, ctx.build_id, spec, requested_by=ctx.principal)
    subject = candidate_subject(ctx.ws, res["candidate_id"])
    per = {n: ({"origin": "trusted", "status": "PASS"} if n in TRUSTED_FEATURES
               else feature_status(ctx.ws, extra[n], ctx.principal)) for n in features}
    statuses = {p["status"] for p in per.values()}
    outcome = "FAIL" if "FAIL" in statuses else "INCONCLUSIVE" if "INCONCLUSIVE" in statuses else "PASS"
    blocking = sorted(n for n, p in per.items() if p["status"] != "PASS")
    rec = EvidenceStore(ctx.ws).record(
        check_id="model.leakage", check_version=CHECK_VERSION, stage="model_shadow", subject=subject,
        outcome=outcome, metrics=jsonable({"features": per, "blocking": blocking}), producer="verifier:leakage",
        limitations=["covers feature definitions only; label construction is fixed by the trusted template"])
    return {"candidate_id": res["candidate_id"], "build_id": res["build_id"], "cv_mean": res["cv_mean"],
            "cv": [{k: f[k] for k in ("fold", "valid_as_of", "n_train", "auc", "brier", "logloss", "base_rate")}
                   for f in res["cv"]],
            "n_rows": res["n_rows"], "artifact_digest": res["artifact_digest"],
            "feature_set_digest": res["feature_set_digest"], "warnings": res["warnings"],
            "model_leakage": {"outcome": outcome, "features": per, "blocking": blocking},
            "evidence_ids": [rec.evidence_id],
            "note": "dev CV is not evidence of future performance; request_final_evaluation spends the sealed "
                    "holdout budget"}


@governed_tool("request_final_evaluation")
def request_final_evaluation(ctx: ToolContext, candidate_id: str) -> dict:
    """Sealed evaluation on custody labels (budgeted). Returns aggregate metrics only."""
    subject = candidate_subject(ctx.ws, candidate_id)
    res = SealedEvaluator(ctx.ws, task_id=SEALED_TASK).evaluate(candidate_id, ctx.build_id)
    rec = EvidenceStore(ctx.ws).record(
        check_id="model.sealed_eval", check_version=CHECK_VERSION, stage="model_shadow", subject=subject,
        outcome=res["outcome"], metrics=jsonable({k: v for k, v in res.items() if k != "limitations"}),
        producer="evaluator:custody", limitations=list(res.get("limitations") or []))
    return {**res, "evidence_ids": [rec.evidence_id]}


@governed_tool("request_release")
def request_release(ctx: ToolContext, candidate_id: str | None = None, stage: str = "model_shadow",
                    policy_id: str | None = None) -> dict:
    """Ask the gate whether a candidate (model_shadow, retrain_promote) or a registered targeting
    policy (model_experiment, model_scale) may enter `stage`. Never deploys anything."""
    if stage not in MODEL_STAGES:
        raise ValueError(f"stage must be one of {MODEL_STAGES}, got {stage!r}")
    if (candidate_id is None) == (policy_id is None):
        raise ValueError("give exactly one of candidate_id (a model) or policy_id (a targeting policy)")
    if policy_id is not None:
        subject = policy_record(ctx.ws, policy_id)["subject"]
        what = {"policy_id": policy_id}
    else:
        subject = candidate_subject(ctx.ws, candidate_id)
        what = {"candidate_id": candidate_id}
    decision = decide(ctx.ws, stage, subject, requested_by=ctx.principal)
    return {**what, "stage": stage, "verdict": decision.verdict,
            "reasons": list(decision.reasons), "gate": decision.to_dict(),
            "note": "ALLOW is permission recorded in registry/gate_decisions.jsonl; deployment is a separate, "
                    "controller-side step"}


# --------------------------------------------------------------------------- controller-side only
_PRICE_AT_SQL = """
SELECT s.customer_id, r.price_fen
FROM (SELECT customer_id, arg_max(plan, started_at) AS plan FROM silver.subscriptions
      WHERE started_at < DATE '{as_of}' GROUP BY customer_id) s
JOIN silver.ref_plan_prices r ON r.plan = s.plan AND DATE '{as_of}' BETWEEN r.valid_from AND r.valid_to
"""


def run_experiment_readout(ws: Workspace, candidate_id: str, *, experiment_id: str, k: int,
                           salt: str | None = None, score_build: str | None = None, vp=None) -> dict:
    """Controller/evaluator side, NOT an agent tool: run the July retention experiment for the
    candidate's top-k risk customers and record `experiment.readout` on the candidate subject.

    Outcomes come from the custody oracle (reality, in the benchmark world), which releases them
    once per controller-issued `experiment_id`; this function must never be exposed through
    ROLE_TOOLS or the MCP server. The assignment salt defaults to the experiment id. Prices are the
    price book at as_of, not the mutable current price."""
    from ..decision.experiment import ValueParams, assign, readout
    from ..decision.oracle import ExperimentOracle
    from ..decision.policies import top_k

    subject = candidate_subject(ws, candidate_id)
    score_build = score_build or latest_ready_build(ws, CUTOFFS["score"])
    cutoff = ml_train.ready_build_cutoff(ws, score_build)
    as_of = (parse_day(cutoff) + timedelta(days=1)).isoformat()
    oracle = ExperimentOracle(ws)
    if as_of[:7] != oracle.month:
        raise ValueError(f"score build {score_build} scores {as_of[:7]}, but the experiment runs in {oracle.month}")
    scores = ml_train.score(ws, candidate_id, score_build, as_of)
    targeted = top_k(scores, k)
    salt = salt or experiment_id
    assignments = assign(targeted, salt)
    with readonly_connection(warehouse_path(ws, score_build)) as con:
        prices = con.execute(_PRICE_AT_SQL.format(as_of=as_of)).df()
    vp = vp or ValueParams()
    ro = readout(assignments, oracle.outcomes(assignments, experiment_id=experiment_id), prices, vp)
    targeting = {"policy": "top_k_by_score", "experiment_id": experiment_id, "k": k, "salt": salt,
                 "as_of": as_of, "score_build": score_build, "value_params": asdict(vp)}
    rec = EvidenceStore(ws).record(
        check_id="experiment.readout", check_version=CHECK_VERSION, stage="model_scale", subject=subject,
        outcome=ro["outcome"], metrics=jsonable({**{k_: v for k_, v in ro.items() if k_ != "limitations"},
                                                 "targeting": targeting}),
        producer="experiment:readout", limitations=list(ro.get("limitations") or []))
    return jsonable({**ro, "targeting": targeting, "evidence_ids": [rec.evidence_id]})


# --------------------------------------------------------------------------- targeting policies (controller-side)
POLICY_LOG = "policies.jsonl"             # registry/: registered targeting policies (subject + who asked)
EXPERIMENT_LOG = "experiments.jsonl"      # registry/: pre-registered experiment designs, before any outcome
_POLICY_ID_LEN = 16


def _prices_at(ws: Workspace, build_id: str, as_of: str):
    with readonly_connection(warehouse_path(ws, build_id)) as con:
        return con.execute(_PRICE_AT_SQL.format(as_of=parse_day(as_of).isoformat())).df()


def policy_subject(ws: Workspace, policy: str, *, k: int, channel: str, as_of: str, score_build: str,
                   churn_candidate_id: str | None = None, uplift_model_id: str | None = None) -> dict:
    """Evidence subject of a targeting policy: the rule, its k, the channel economics, the July
    application (as_of, score build) and the exact model artifacts it reads."""
    from ..decision.experiment import ValueParams
    from ..decision.policies import POLICIES
    from ..ml import uplift as ml_uplift
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}; known: {sorted(POLICIES)}")
    if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
        raise ValueError(f"k must be a positive int, got {k!r}")
    vp = ValueParams.for_channel(channel)
    parse_day(as_of)
    ml_train.ready_build_cutoff(ws, score_build)
    models: dict = {}
    if policy == "risk_top_k":
        if churn_candidate_id is None or uplift_model_id is not None:
            raise ValueError("risk_top_k reads exactly one churn candidate (churn_candidate_id)")
        rec = candidate_record(ws, churn_candidate_id)
        models["churn_candidate"] = {"candidate_id": churn_candidate_id, "artifact_digest": rec["artifact_digest"]}
    else:
        if uplift_model_id is None or churn_candidate_id is not None:
            raise ValueError("uplift_value_top_k reads exactly one uplift model (uplift_model_id)")
        rec = ml_uplift.uplift_record(ws, uplift_model_id)
        models["uplift_model"] = {"model_id": uplift_model_id, "artifact_digest": rec["artifact_digest"],
                                  "build_id": rec["build_id"]}
    return {"kind": "policy", "policy": policy, "k": k, "channel": channel, "value_params": asdict(vp),
            "as_of": as_of, "score_build": score_build, "models": models}


def policy_record(ws: Workspace, policy_id: str) -> dict:
    """Registry record of a policy (KeyError if unknown; the record's subject must match its id)."""
    if not isinstance(policy_id, str) or len(policy_id) != _POLICY_ID_LEN:
        raise KeyError(f"malformed policy id {policy_id!r}")
    hits = [r for r in read_jsonl(ws.registry / POLICY_LOG) if r.get("policy_id") == policy_id]
    if not hits:
        raise KeyError(f"unknown policy {policy_id}")
    rec = hits[-1]
    if digest(rec["subject"])[:_POLICY_ID_LEN] != policy_id:
        raise RuntimeError(f"policy registry record {policy_id} does not match its subject (edited registry)")
    return rec


def _models_check(ws: Workspace, subject: dict, requested_by: str) -> dict:
    """policy.models: every model the policy reads is released (pure gate evaluation, no new decisions)."""
    from ..ml import uplift as ml_uplift
    per, statuses = {}, []
    for role, m in subject["models"].items():
        if role == "churn_candidate":
            cand_subject = candidate_subject(ws, m["candidate_id"])
            ev = EvidenceStore(ws).for_subject(digest(cand_subject))
            dec = evaluate_gate(load_policy(), "model_shadow", cand_subject, ev, [], requested_by)
            status = _GATE_STATUS[dec.verdict]
            per[role] = {"candidate_id": m["candidate_id"], "model_shadow": dec.verdict, "status": status,
                         "reasons": list(dec.reasons[:3]), "evidence_ids": list(dec.evidence_ids)}
        else:
            try:
                ml_uplift.load_uplift_model(ws, m["model_id"])
                rec = ml_uplift.uplift_record(ws, m["model_id"])
                untrusted = [f for f in rec["spec"]["features"] if f not in TRUSTED_FEATURES]
                status = "FAIL" if untrusted else "PASS"
                per[role] = {"model_id": m["model_id"], "status": status, "untrusted_features": untrusted,
                             "train_months": rec["train_months"], "artifact_digest_verified": True}
            except ml_train.CandidateIntegrityError as exc:
                status = "FAIL"
                per[role] = {"model_id": m["model_id"], "status": status, "reason": str(exc)}
        statuses.append(status)
    outcome = "FAIL" if "FAIL" in statuses else "INCONCLUSIVE" if "INCONCLUSIVE" in statuses else "PASS"
    return {"outcome": outcome, "models": per}


def register_policy(ws: Workspace, policy: str, *, k: int, channel: str, as_of: str, score_build: str,
                    requested_by: str, churn_candidate_id: str | None = None,
                    uplift_model_id: str | None = None) -> dict:
    """Register a targeting policy and record `policy.models` on its subject (controller-side)."""
    if not isinstance(requested_by, str) or not requested_by.strip():
        raise ValueError("requested_by is required")
    subject = policy_subject(ws, policy, k=k, channel=channel, as_of=as_of, score_build=score_build,
                             churn_candidate_id=churn_candidate_id, uplift_model_id=uplift_model_id)
    policy_id = digest(subject)[:_POLICY_ID_LEN]
    known = [r for r in read_jsonl(ws.registry / POLICY_LOG) if r.get("policy_id") == policy_id]
    if not known:
        append_jsonl(ws.registry / POLICY_LOG, {"policy_id": policy_id, "subject": subject,
                                                "requested_by": requested_by, "ts": utcnow()})
    check = _models_check(ws, subject, requested_by)
    rec = EvidenceStore(ws).record(
        check_id="policy.models", check_version=CHECK_VERSION, stage="model_experiment", subject=subject,
        outcome=check["outcome"], metrics=jsonable(check), producer="verifier:policy",
        limitations=["a churn candidate counts only while its model_shadow gate is ALLOW (sealed evaluation + "
                     "leakage); an uplift model must use trusted point-in-time features only"])
    return {"policy_id": policy_id, "subject": subject, "models_check": check, "evidence_ids": [rec.evidence_id]}


def _policy_scores_on_program(ws: Workspace, subject: dict, frame, score_build: str):
    """The policy's own ranking score for each program enrolment (held-out for learned uplift)."""
    import numpy as np
    import pandas as pd
    from ..decision.experiment import ValueParams
    from ..ml import uplift as ml_uplift
    vp = ValueParams(**subject["value_params"])
    prices = pd.concat([_prices_at(ws, score_build, a).assign(as_of=a) for a in sorted(frame["as_of"].unique())])
    price = frame[["as_of", "customer_id"]].merge(prices, on=["as_of", "customer_id"], how="left")["price_fen"]
    if price.isna().any():
        raise ValueError(f"{int(price.isna().sum())} program enrolments have no price-book price at their as_of")
    if subject["policy"] == "risk_top_k":
        cid = subject["models"]["churn_candidate"]["candidate_id"]
        parts = [ml_train.score(ws, cid, score_build, a).assign(as_of=a) for a in sorted(frame["as_of"].unique())]
        s = frame[["as_of", "customer_id"]].merge(pd.concat(parts), on=["as_of", "customer_id"], how="left")["score"]
        if s.isna().any():
            raise ValueError("the churn candidate did not score every program enrolment")
        return s.to_numpy(dtype=float), price.to_numpy(dtype=float), vp, "churn risk score (fixed artifact)"
    _, spec = ml_uplift.load_uplift_model(ws, subject["models"]["uplift_model"]["model_id"])
    cf = ml_uplift.cross_fit(frame, spec)
    ev = vp.expected_value(cf["p0"].to_numpy(), cf["p1"].to_numpy(), price.to_numpy(dtype=float))
    return np.asarray(ev, dtype=float), price.to_numpy(dtype=float), vp, \
        f"expected value per contact, leave-one-campaign-month-out refits of spec {spec.method}"


def validate_policy_offline(ws: Workspace, policy_id: str, *, n_boot: int | None = None) -> dict:
    """Offline validation of a registered policy on the randomised program (controller-side, no
    custody): its ranking's held-out Qini and the realised value of its operating point (top
    k / half of the eligible population), recorded as `policy.offline_qini` (verifier:uplift)."""
    from ..ml import uplift as ml_uplift
    from ..ml.features import population
    rec = policy_record(ws, policy_id)
    subject = rec["subject"]
    score_build = subject["score_build"]
    cutoff = ml_train.ready_build_cutoff(ws, score_build)
    with readonly_connection(warehouse_path(ws, score_build)) as con:
        features = list(TRUSTED_FEATURES)
        frame = ml_uplift.program_frame(con, cutoff, features)
        n_eligible = len(population(con, subject["as_of"]))
    score, price, vp, basis = _policy_scores_on_program(ws, subject, frame, score_build)
    fraction = min(1.0, subject["k"] / max(n_eligible // 2, 1))
    res = ml_uplift.offline_validation(frame, score, price=price, vp=vp, top_fraction=fraction,
                                       n_boot=n_boot or ml_uplift.BOOTSTRAP_REPS, label=subject["policy"])
    res = {**res, "ranking": basis, "policy_id": policy_id, "operating_point": {
        "k": subject["k"], "eligible_at_as_of": n_eligible, "assumed_half": n_eligible // 2, "fraction": fraction},
        "immature_months_excluded": frame.attrs.get("immature_months", [])}
    ev_rec = EvidenceStore(ws).record(
        check_id="policy.offline_qini", check_version=CHECK_VERSION, stage="model_experiment", subject=subject,
        outcome=res["outcome"], metrics=jsonable({k: v for k, v in res.items() if k != "limitations"}),
        producer="verifier:uplift", limitations=list(res["limitations"]))
    return jsonable({**res, "evidence_ids": [ev_rec.evidence_id]})


def _gate_allowed(ws: Workspace, stage: str, subject: dict) -> bool:
    """True iff the LATEST logged decision for (stage, subject) is ALLOW (a later HOLD/REJECT revokes it)."""
    sdig = digest(subject)
    mine = [d for d in read_jsonl(ws.registry / "gate_decisions.jsonl")
            if d.get("stage") == stage and d.get("subject_digest") == sdig]
    return bool(mine) and mine[-1].get("verdict") == "ALLOW"


def offline_evidence(ws: Workspace, subject: dict) -> dict | None:
    """Metrics of the latest `policy.offline_qini` record from its authorised producer (or None)."""
    recs = [r for r in EvidenceStore(ws).for_subject(digest(subject))
            if r.check_id == "policy.offline_qini" and r.producer == "verifier:uplift"]
    if not recs:
        return None
    latest = max(recs, key=lambda r: r.issued_at)
    return {"outcome": latest.outcome, "evidence_id": latest.evidence_id, **latest.metrics}


def plan_policy_experiment(ws: Workspace, design, policy_ids: dict[str, str], *,
                           require_gate: bool = True) -> dict:
    """Deterministic plan of a pre-registered policy experiment (no outcomes, no custody): the
    split, each policy's target list, the combined assignment and the pre-run power (from the
    realised value and SDs of the policy's operating point on the held-out program).

    ``policy_ids`` maps each policy name of ``design`` to a registered policy id whose subject
    matches the design (channel, k, as_of). Every policy must hold a model_experiment ALLOW,
    except in a dry run (``require_gate=False``: what each policy *would* target, for briefs)."""
    from ..decision.design import power_from_moments
    from ..decision.experiment import ValueParams
    from ..decision.policies import risk_top_k, uplift_value_top_k
    from ..ml import uplift as ml_uplift
    from ..ml.features import population
    names = list(design.policies)
    if sorted(policy_ids) != sorted(names):
        raise ValueError(f"policy_ids {sorted(policy_ids)} do not match the design's policies {sorted(names)}")
    subjects = {}
    for name in names:
        sub = policy_record(ws, policy_ids[name])["subject"]
        if (sub["policy"], sub["channel"], sub["k"], sub["as_of"]) != (name, design.channel, design.k_per_policy,
                                                                         design.as_of):
            raise ValueError(f"policy {policy_ids[name]} ({sub['policy']}, {sub['channel']}, k={sub['k']}, "
                             f"{sub['as_of']}) does not match the design")
        if require_gate and not _gate_allowed(ws, "model_experiment", sub):
            raise PermissionError(f"policy {policy_ids[name]} ({name}) has no model_experiment ALLOW; the controller "
                                  f"runs experiments only for gated policies")
        subjects[name] = sub
    builds = {s["score_build"] for s in subjects.values()}
    if len(builds) != 1:
        raise ValueError(f"policies were registered on different score builds {sorted(builds)}")
    score_build = builds.pop()
    vp = ValueParams.for_channel(design.channel)
    with readonly_connection(warehouse_path(ws, score_build)) as con:
        pop = population(con, design.as_of).astype(str).tolist()
    prices = _prices_at(ws, score_build, design.as_of)
    halves = design.split(pop)
    targets, details = {}, {}
    for name, sub in subjects.items():
        half = set(halves[name])
        if name == "risk_top_k":
            scores = ml_train.score(ws, sub["models"]["churn_candidate"]["candidate_id"], score_build, design.as_of)
            scores = scores[scores["customer_id"].astype(str).isin(half)]
            targets[name] = risk_top_k(scores, min(design.k_per_policy, len(scores)))
            details[name] = {"ranked_by": "churn score", "n_half": len(half),
                             "scores": dict(zip(scores["customer_id"].astype(str), scores["score"].astype(float)))}
        else:
            pred = ml_uplift.predict_uplift(ws, sub["models"]["uplift_model"]["model_id"], score_build, design.as_of)
            pred = pred[pred["customer_id"].isin(half)].merge(prices, on="customer_id", how="inner")
            if len(pred) != len(half):
                raise ValueError(f"{len(half) - len(pred)} customers of the uplift half have no price-book price")
            targets[name] = uplift_value_top_k(pred, design.k_per_policy, vp)
            ev = vp.expected_value(pred["p0"], pred["p1"], pred["price_fen"])
            details[name] = {"ranked_by": "expected value per contact (only positive)", "n_half": len(half),
                             "n_positive_ev": int((ev > 0).sum())}
    assignment = design.assignment(targets) if all(targets.values()) else None
    power = {}
    for name, sub in subjects.items():
        n_list = len(targets[name])
        n_treat = int(round(n_list * design.p_treat))
        n_arm = min(n_treat, n_list - n_treat)                     # the smaller expected arm
        off = offline_evidence(ws, sub)
        vt = (off or {}).get("value_top_fraction") or {}
        if n_arm < 2 or not vt.get("sd_control"):
            power[name] = {"basis": "held-out program (policy.offline_qini)", "available": False,
                           "reason": "fewer than 2 customers per arm" if n_arm < 2
                           else "no offline validation with operating-point moments for this policy"}
            continue
        power[name] = {"basis": "held-out program: realised value and SDs of the policy's operating point "
                                f"(evidence {off['evidence_id']})", "available": True,
                       **power_from_moments(vt["point"], vt["sd_control"], vt["sd_treatment"], n_arm)}
    return {"design": design.to_dict(), "design_digest": design.digest(), "score_build": score_build,
            "policy_ids": dict(policy_ids), "subjects": subjects, "n_eligible": len(pop),
            "halves": {k: len(v) for k, v in halves.items()}, "targets": targets, "details": details,
            "assignment": assignment, "prices": prices, "power": power, "value_params": asdict(vp)}


def run_policy_experiment(ws: Workspace, design, policy_ids: dict[str, str], *, requested_by: str) -> dict:
    """Controller/evaluator side, NOT an agent tool: pre-register the plan, ask the custody oracle
    ONCE for the combined assignment (one experiment id), read out each policy separately (ITT,
    value per contact with CI, SRM per stratum) and record `experiment.readout` on each policy."""
    from ..decision.experiment import ValueParams, readout
    from ..decision.oracle import ExperimentOracle
    plan = plan_policy_experiment(ws, design, policy_ids)
    assignment = plan["assignment"]
    if assignment is None:
        empty = sorted(k for k, v in plan["targets"].items() if not v)
        raise ValueError(f"policies {empty} target nobody (no customer with positive expected value); "
                         f"the experiment cannot read them out")
    oracle = ExperimentOracle(ws)
    if design.as_of[:7] != oracle.month:
        raise ValueError(f"the design targets {design.as_of[:7]}, but the experiment month is {oracle.month}")
    a_digest = oracle.assignment_digest(assignment[["customer_id", "arm"]])
    prereg = {"experiment_id": design.experiment_id, "design": design.to_dict(), "design_digest": design.digest(),
              "policy_ids": dict(policy_ids), "assignment_digest": a_digest,
              "n": {k: int((assignment["policy"] == k).sum()) for k in design.policies},
              "power": jsonable({k: {f: v.get(f) for f in ("available", "power", "assumed_value_per_contact_fen",
                                                            "se_fen", "mde_fen", "n_per_arm", "n_per_arm_for_target")}
                                 for k, v in plan["power"].items()}),
              "requested_by": requested_by, "ts": utcnow()}
    earlier = [r for r in read_jsonl(ws.registry / EXPERIMENT_LOG) if r.get("experiment_id") == design.experiment_id]
    if earlier and (earlier[0]["design_digest"], earlier[0]["assignment_digest"]) != (design.digest(), a_digest):
        raise ValueError(f"experiment {design.experiment_id} was pre-registered with another design or assignment")
    if not earlier:
        append_jsonl(ws.registry / EXPERIMENT_LOG, prereg)
    outcomes = oracle.outcomes(assignment[["customer_id", "arm"]], experiment_id=design.experiment_id)
    vp = ValueParams.for_channel(design.channel)
    per_policy = {}
    for name in design.policies:
        a = assignment[assignment["policy"] == name][["customer_id", "arm"]]
        o = outcomes[outcomes["customer_id"].isin(set(a["customer_id"]))]
        ro = readout(a, o, plan["prices"], vp, min_n_per_arm=design.min_n_per_arm,
                     expected_share_treatment=design.p_treat)
        subject = plan["subjects"][name]
        targeting = {"policy": name, "policy_id": policy_ids[name], "experiment_id": design.experiment_id,
                     "design_digest": design.digest(), "k": design.k_per_policy, "n_targeted": len(a),
                     "as_of": design.as_of, "score_build": plan["score_build"], "channel": design.channel,
                     "split_salt": design.split_salt, "treat_salt": design.treat_salt(name)}
        rec = EvidenceStore(ws).record(
            check_id="experiment.readout", check_version=CHECK_VERSION, stage="model_scale", subject=subject,
            outcome=ro["outcome"], producer="experiment:readout", limitations=list(ro.get("limitations") or []),
            metrics=jsonable({**{k_: v for k_, v in ro.items() if k_ != "limitations"}, "targeting": targeting,
                              "power_prerun": plan["power"][name]}))
        per_policy[name] = jsonable({**ro, "targeting": targeting, "power_prerun": plan["power"][name],
                                     "evidence_ids": [rec.evidence_id]})
    return {"experiment_id": design.experiment_id, "design": design.to_dict(), "design_digest": design.digest(),
            "assignment_digest": a_digest, "n_eligible": plan["n_eligible"], "halves": plan["halves"],
            "targets": plan["targets"], "details": jsonable({k: {f: v for f, v in d.items() if f != "scores"}
                                                             for k, d in plan["details"].items()}),
            "power": jsonable(plan["power"]), "readouts": per_policy}
