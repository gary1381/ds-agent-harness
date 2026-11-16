"""End-to-end scenario: every gate decision of the reference timeline, printed as it happens.

    world + builds -> analytics answers (careful vs naive) -> DE break-fix (naive fix rejected,
    correct fix allowed, drift/monitor rebuilt READY with it) -> feature proposals + leakage ->
    trusted training -> sealed evaluation -> model_shadow -> July decision: an uplift model learned
    from the randomised 2026 program, two targeting POLICIES (churn-risk top-k vs uplift-value
    top-k, app channel) with offline validation, model_experiment per policy (HOLD until a business
    owner other than the requester approves; REJECT when offline evidence fails), ONE pre-registered
    experiment with its power analysis, per-policy readouts vs evaluator truth, the 'call' channel's
    negative economics and the naive "risk x value" claim -> model_scale per policy (each needs its
    own PASSing readout; a projection is printed only from a PASSing readout's LCB95) -> drift
    (Aug/Sep vs June features and scores) -> retrain candidate proposed and held (retrain_promote
    needs a champion/challenger comparison and risk sign-off).

Trust boundary: agent steps go through the governed tools exactly as the benchmark's scripted
agents do; controller steps (merging an ALLOWed pipeline, training the uplift model, registering
and validating policies, running the experiment, recording human approvals, drift monitoring) are
labelled as such; evaluator steps (custody truth, oracle true effect and power) only *grade* and
never feed back into a gate.

Reproducibility: the scenario runs in a run-scoped registry, ``runs/demo/ws`` (wiped per run), that
shares the workspace's lake, exports and custody truth. Its evidence, approvals and holdout /
experiment ledgers therefore never touch the workspace's own registry, and a re-run tells the same
story (an approval from an earlier run cannot pre-clear a gate). Builds of the fixed pipeline are
written to the shared lake.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
import json
import shutil
import time

from . import tools as T
from .bench import tasks as BT
from .bench.agents import Session, get_agent
from .bench.run import prepare_workspace, private_workspace
from .common import append_jsonl, digest, read_jsonl, utcnow
from .decision.experiment import ValueParams
from .evidence import ApprovalStore
from .lake.build import DEFAULT_PIPELINE, build_snapshot, warehouse_path
from .lake.runner import pipeline_digest
from .ml import train as ml_train
from .ml.features import NAIVE_FEATURES, TRUSTED_FEATURES, build_features, readonly_connection
from .monitor.drift import drift_report
from .workspace import CUTOFFS, Workspace

MODELER = "agent:demo-modeler"
BUSINESS_OWNER = "user:demo-business-owner"
RISK_OFFICER = "user:demo-risk-officer"
EXPERIMENT_ID = "demo-2026-07-retention"
POLICIES = ("risk_top_k", "uplift_value_top_k")
RETRAIN_AS_OF = ["2026-04-01", "2026-05-01", "2026-06-01", "2026-07-01", "2026-08-01"]
GOOD_FEATURE_SQL = """
SELECT p.customer_id, COUNT(t.ticket_id) AS value
FROM population p
LEFT JOIN silver.tickets t
  ON t.customer_id = p.customer_id AND t.category = 'bug'
 AND t.created_at >= {as_of} - INTERVAL 90 DAY AND t.created_at < {as_of} AND t.available_at < {as_of}
GROUP BY p.customer_id""".strip()
DRIFT_FEATURES = ["tenure_months", "new_customer_3m", "plan", "channel", "last_charge_fen", "minutes_last_month",
                  "minutes_3m_avg", "tickets_90d", "billing_tickets_90d", "addons_90d"]


class DemoError(RuntimeError):
    """A step of the scenario did not behave as the reference timeline requires."""


def _ok(res: dict, what: str) -> dict:
    if isinstance(res, dict) and "error" in res and "kind" in res:
        raise DemoError(f"{what}: {res['kind']}: {res['error']}")
    return res


def _why(reasons, n: int = 2) -> str:
    return "; ".join(str(r).split(" — ")[0] for r in list(reasons or [])[:n]) or "-"


def _fen(x) -> str:
    return "n/a" if x is None else f"{x:+,.0f}"


def _ci(ci) -> str:
    return "n/a" if not ci else f"[{ci[0]:+,.0f}, {ci[1]:+,.0f}]"


def _model_experiment_sequence(ws: Workspace, policy_id: str, subject: dict, ds) -> list[str]:
    """No approval -> the modeler approves its own request -> an independent business owner approves."""
    approvals = ApprovalStore(ws)
    seq = [_ok(T.request_release(ds, policy_id=policy_id, stage="model_experiment"), "model_experiment")]
    approvals.record(stage="model_experiment", approver=MODELER, role="business_owner", requested_by=MODELER,
                     subject=subject)
    seq.append(_ok(T.request_release(ds, policy_id=policy_id, stage="model_experiment"), "model_experiment"))
    approvals.record(stage="model_experiment", approver=BUSINESS_OWNER, role="business_owner", requested_by=MODELER,
                     subject=subject)
    seq.append(_ok(T.request_release(ds, policy_id=policy_id, stage="model_experiment"), "model_experiment"))
    return seq


def _decision_story(base: Workspace, ws: Workspace, cid: str, score_build: str, tasks: dict, log) -> dict:
    """Section 8: uplift model -> policies -> offline validation -> model_experiment -> pre-registered
    design + power -> ONE experiment -> per-policy readouts vs evaluator truth -> channel economics."""
    from .decision.design import PolicyExperimentDesign
    from .decision.oracle import ExperimentOracle
    from .ml import uplift as U
    from .ml.features import population
    out: dict = {"score_build": score_build, "policies": {}}

    # 8a. the uplift model, learned from the randomised 2026 program (controller-side training job)
    up = U.train_uplift_model(ws, score_build, U.UpliftSpec(), requested_by=MODELER, validate=False)
    cutoff = ml_train.ready_build_cutoff(ws, score_build)
    log(f"   uplift model {up['model_id']} ({up['describe']['method']}, trusted features at each campaign start): "
        f"{up['n_rows']} program enrolments of {', '.join(up['train_months'])} {up['n_by_arm']}; ITT on `arm`; "
        f"{', '.join(up['immature_months_excluded']) or 'no month'} excluded (labels immature at {cutoff})")
    out["uplift_model"] = {k: up[k] for k in ("model_id", "train_months", "immature_months_excluded", "n_rows",
                                               "n_by_arm", "describe")}

    # 8b. register both policies (app channel), record policy.models and the offline validation
    with readonly_connection(warehouse_path(ws, score_build)) as con:
        n_eligible = len(population(con, BT.EXPERIMENT_AS_OF))
    k = BT.design_k(n_eligible)
    log(f"   {n_eligible} customers eligible on {BT.EXPERIMENT_AS_OF}; pre-registered k = {k} per policy, channel "
        f"app (contact 50 fen + 10% offer charged only to retained treated customers)")
    ds = T.make_context(ws, "score", "data_scientist", MODELER)
    try:
        for name, models in (("risk_top_k", {"churn_candidate_id": cid}),
                             ("uplift_value_top_k", {"uplift_model_id": up["model_id"]})):
            reg = T.ds.register_policy(ws, name, k=k, channel="app", as_of=BT.EXPERIMENT_AS_OF,
                                       score_build=score_build, requested_by=MODELER, **models)
            off = T.ds.validate_policy_offline(ws, reg["policy_id"])
            vt = off.get("value_top_fraction") or {}
            q = off["qini_coefficient"] or {}
            qtxt = (f"{q['point']:+.4f} [{q['ci'][0]:+.4f}, {q['ci'][1]:+.4f}]" if q else "n/a")
            log(f"   {name:19s} policy {reg['policy_id']}: models {reg['models_check']['outcome']}; held-out Qini "
                f"{qtxt}, top {vt.get('fraction', 0):.0%} realised {_fen(vt.get('point'))} fen/contact "
                f"{_ci(vt.get('ci'))} -> policy.offline_qini {off['outcome']}")
            seq = _model_experiment_sequence(ws, reg["policy_id"], reg["subject"], ds)
            log(f"      model_experiment: {seq[0]['verdict']} (no approval) -> {seq[1]['verdict']} (modeler "
                f"approves itself) -> {seq[2]['verdict']} (business owner)  [{_why(seq[2]['reasons'], 2)}]")
            out["policies"][name] = {"policy_id": reg["policy_id"], "subject": reg["subject"],
                                     "models": reg["models_check"]["outcome"], "offline": off["outcome"],
                                     "offline_reasons": off["reasons"], "qini": q, "value_top": vt,
                                     "model_experiment": [x["verdict"] for x in seq]}
    finally:
        ds.close()
    risk_seq = out["policies"]["risk_top_k"]["model_experiment"]
    out["gates"] = {"model_experiment_before": risk_seq[0], "model_experiment_self_approved": risk_seq[1],
                    "model_experiment_after": risk_seq[2],
                    "model_experiment": {n: p["model_experiment"][-1] for n, p in out["policies"].items()}}

    # 8c. pre-registered design over the cleared policies + power, BEFORE any outcome
    cleared = tuple(n for n in POLICIES if out["policies"][n]["model_experiment"][-1] == "ALLOW")
    blocked = [n for n in POLICIES if n not in cleared]
    if blocked:
        log(f"   not experimented (the gate did not clear them): {', '.join(blocked)} -- evidence, not approvals, "
            f"decides this")
    out["experimented"] = list(cleared)
    oracle = ExperimentOracle(base)                         # evaluator: grading and design diagnostics only
    vp = ValueParams.for_channel("app")
    preds = U.predict_uplift(ws, up["model_id"], score_build, BT.EXPERIMENT_AS_OF)
    rq = BT.grade_uplift_ranking(base, preds, "experiment")
    out["uplift_ranking_truth"] = rq
    log(f"   evaluator: learned uplift vs the TRUE July uplift: Spearman {rq['spearman']:+.2f}; its top 30% holds "
        f"{rq['share_of_oracle_uplift_captured']['30%']:.0%} of the uplift an oracle's top 30% holds (random "
        f"targeting: {rq['random_targeting_share']['30%']:.0%})")
    if blocked:
        dry = T.ds.plan_policy_experiment(
            ws, PolicyExperimentDesign(f"{EXPERIMENT_ID}-dry-run", BT.EXPERIMENT_AS_OF, POLICIES, k, "app"),
            {n: out["policies"][n]["policy_id"] for n in POLICIES}, require_gate=False)
        for n in blocked:
            would = dry["targets"][n]
            t = BT.decision_truth(base, would, "app") if would else None
            out["policies"][n]["truth_if_run"] = t
            log(f"   evaluator: {n} would have contacted {len(would)} customers worth "
                f"{_fen(t['value_per_contact_fen']) if t else 'n/a'} fen/contact (truth) -> blocking it "
                + ("avoided a loss" if t and t["value_per_contact_fen"] < 0 else "cost a gain" if t else "changed nothing"))
    if not cleared:
        log("   no policy cleared for an experiment: nothing is run, nothing can be scaled")
        out["experiment"] = None
        return out
    design = PolicyExperimentDesign(EXPERIMENT_ID, BT.EXPERIMENT_AS_OF, cleared, k, "app")
    ids = {n: out["policies"][n]["policy_id"] for n in cleared}
    plan = T.ds.plan_policy_experiment(ws, design, ids)
    empty = [n for n in cleared if not plan["targets"][n]]
    if empty:        # pre-registered rule: a policy that would contact nobody is not experimented
        log(f"   {', '.join(empty)} would contact nobody (no customer with a positive expected value): not experimented")
        cleared = tuple(n for n in cleared if n not in empty)
        out["experimented"] = list(cleared)
        if not cleared:
            out["experiment"] = None
            return out
        design = PolicyExperimentDesign(EXPERIMENT_ID, BT.EXPERIMENT_AS_OF, cleared, k, "app")
        ids = {n: ids[n] for n in cleared}
        plan = T.ds.plan_policy_experiment(ws, design, ids)
    log(f"   design {design.digest()[:12]} ({EXPERIMENT_ID}): split {plan['halves']}, targets "
        f"{ {n: len(t) for n, t in plan['targets'].items()} }, 50/50 within each list; one oracle call")
    for n in cleared:
        pw = plan["power"][n]
        if pw.get("available"):
            log(f"   pre-run power {n}: {pw['n_per_arm']}/arm, held-out value {_fen(pw['assumed_value_per_contact_fen'])}"
                f" fen/contact -> P(PASS) {pw['power']:.2f}; MDE at 80% power {pw['mde_fen']:,.0f} fen/contact"
                + (f"; n/arm for 80% {pw['n_per_arm_for_target']:,}" if pw.get("n_per_arm_for_target")
                   else f" ({pw['note']})" if pw.get("note") else ""))
        else:
            log(f"   pre-run power {n}: unavailable ({pw.get('reason')})")
    # evaluator diagnostics: the same design with the TRUE potential outcomes, and a perfect uplift model
    diag = {}
    all_ids = population_ids(ws, score_build)
    parts = design.split(all_ids)
    for n in cleared:
        part = plan["details"][n]["n_half"]
        best = oracle.oracle_targets(parts[n], k, vp)
        n_arm = max(len(plan["targets"][n]) // 2, 2)
        perfect: dict = {"n": len(best)}
        if len(best) >= 4:
            tp = oracle.true_power(best, max(len(best) // 2, 2), vp)
            perfect.update(value_per_contact_fen=oracle.true_effect(best, vp)["value_per_contact_fen"],
                           power=tp["power"], itt_power=tp["itt_power"])
        diag[n] = {"oracle_power_of_this_list": oracle.true_power(plan["targets"][n], n_arm, vp)["power"],
                   "perfect_uplift_list": perfect, "part": part}
        log(f"   evaluator: P(PASS) of the {n} list from TRUE outcomes {diag[n]['oracle_power_of_this_list']:.2f}; a "
            f"perfect uplift model in the same part would contact {perfect['n']} customers worth "
            f"{_fen(perfect.get('value_per_contact_fen'))} fen/contact: P(value PASS) "
            f"{perfect.get('power', float('nan')):.2f}, P('churn went down' ITT CI > 0) "
            f"{perfect.get('itt_power', float('nan')):.2f}")
    out["diagnostics"] = diag

    # 8d. ONE experiment, per-policy readouts, evaluator truth
    res = T.ds.run_policy_experiment(ws, design, ids, requested_by="controller:demo")
    out["experiment"] = {k_: res[k_] for k_ in ("experiment_id", "design_digest", "assignment_digest", "n_eligible",
                                               "halves")}
    for n in cleared:
        ro = res["readouts"][n]
        truth = BT.decision_truth(base, res["targets"][n], "app")
        vpc = ro["value_per_contact_fen"]
        inside = vpc["ci"][0] <= truth["value_per_contact_fen"] <= vpc["ci"][1]
        log(f"   {n}: n {ro['n']} (SRM {ro['srm']['outcome']}), ITT churn reduction {ro['itt']['diff']:+.4f} "
            f"[{ro['itt']['ci'][0]:+.4f}, {ro['itt']['ci'][1]:+.4f}], value {_fen(vpc['point'])} fen/contact "
            f"{_ci(vpc['ci'])} -> readout {ro['outcome']}")
        log(f"      evaluator truth: {_fen(truth['value_per_contact_fen'])} fen/contact "
            f"({'inside' if inside else 'OUTSIDE'} the CI); segments {truth['segments']}")
        out["policies"][n].update(readout=ro["outcome"], readout_reasons=ro["reasons"], value_per_contact_fen=vpc,
                                  itt=ro["itt"], n=ro["n"], srm=ro["srm"]["outcome"], targets=len(res["targets"][n]),
                                  truth=truth, truth_inside_ci=inside, evidence_ids=ro["evidence_ids"],
                                  power_prerun=plan["power"][n].get("power"), readout_full=ro)

    # 8e. channel economics: the phone call loses money for every targeting rule (evaluator truth)
    call = ValueParams.for_channel("call")
    call_rows = {n: BT.decision_truth(base, res["targets"][n], "call")["value_per_contact_fen"] for n in cleared}
    best_call = oracle.oracle_targets(all_ids, len(all_ids), call)       # every customer worth a call
    call_best = oracle.true_effect(best_call, call)["expected_value_fen"] if best_call else 0.0
    out["call_channel"] = {"true_value_per_contact_fen": call_rows,
                           "unprofitable_for_every_policy": all(v < 0 for v in call_rows.values()),
                           "perfect_model_profitable_customers": len(best_call),
                           "perfect_model_value_fen_per_month": call_best}
    log(f"   'call' channel (1500 fen + 20% offer), evaluator truth per contact: "
        + ", ".join(f"{n} {_fen(v)}" for n, v in call_rows.items())
        + f" -> unprofitable for every policy: {out['call_channel']['unprofitable_for_every_policy']} (even a "
          f"perfect model finds only {len(best_call)} of {len(all_ids)} customers worth a call, "
          f"CNY {call_best / 100:,.0f}/month in total)")

    # 8f. the naive strategist: risk x retained value, no experiment
    if "risk_top_k" in cleared:
        risk_targets = res["targets"]["risk_top_k"]
        scores = plan["details"]["risk_top_k"]["scores"]
        brief = {"channel": "app", "value_params": asdict(vp),
                 "policies": {"risk_top_k": {"policy_id": out["policies"]["risk_top_k"]["policy_id"],
                                             "targeted_ids": risk_targets,
                                             "scores": {c: scores[c] for c in risk_targets}}}}
        naive_ctx = T.make_context(ws, "score", "data_scientist", "agent:demo-naive-strategist")
        try:
            naive = get_agent("decision", "naive_risk_as_value").act(Session(naive_ctx, brief=brief), tasks["D1"])
        finally:
            naive_ctx.close()
        truth = BT.decision_truth(base, risk_targets, "app")
        g = BT.grade_value_claim(naive.claim, truth)
        log(f"   naive strategist: {naive.claim['value_per_contact_fen']:+,.0f} fen/contact (risk x retained value, no "
            f"experiment) -> {'correct' if g['correct'] else 'WRONG'} vs truth {_fen(truth['value_per_contact_fen'])}; "
            f"its model_scale request: {naive.harness['verdict']} [{_why(naive.harness['reasons'], 2)}]")
        out["naive"] = {"claim_fen": naive.claim["value_per_contact_fen"], "correct": g["correct"],
                        "true_value_per_contact_fen": truth["value_per_contact_fen"],
                        "model_scale": naive.harness["verdict"]}
    return out


def population_ids(ws: Workspace, build_id: str) -> list[str]:
    from .ml.features import population
    with readonly_connection(warehouse_path(ws, build_id)) as con:
        return population(con, BT.EXPERIMENT_AS_OF).astype(str).tolist()


def _scale_story(ws: Workspace, decision: dict, log) -> dict:
    """Section 9: two distinct sign-offs per policy, then each policy's own model_scale verdict, and a
    projection ONLY from a PASSing readout's LCB95."""
    from .economics.roi import decision_value
    approvals = ApprovalStore(ws)
    out: dict = {"policies": {}, "projection": None}
    ds = T.make_context(ws, "score", "data_scientist", MODELER)
    try:
        for name, p in decision["policies"].items():
            for approver, role in ((BUSINESS_OWNER, "business_owner"), (RISK_OFFICER, "risk")):
                approvals.record(stage="model_scale", approver=approver, role=role, requested_by=MODELER,
                                 subject=p["subject"])
            r = _ok(T.request_release(ds, policy_id=p["policy_id"], stage="model_scale"), "model_scale")
            out["policies"][name] = {"verdict": r["verdict"], "reasons": [str(x)[:220] for x in r["reasons"]],
                                     "readout": p.get("readout")}
            log(f"   {name:19s} readout {p.get('readout') or 'none (not experimented)':13s} + business owner + risk "
                f"sign-off -> model_scale {r['verdict']}  [{_why(r['reasons'], 2)}]")
    finally:
        ds.close()
    passing = [n for n, p in decision["policies"].items()
               if p.get("readout") == "PASS" and out["policies"][n]["verdict"] == "ALLOW"]
    if not passing:
        log("   projection at scale: none -- no policy has a PASSing readout, so no decision value is counted "
            "(ROI decision channel = 0)")
        return out
    proj = {}
    for n in passing:
        p = decision["policies"][n]
        parts = len(decision["experimented"])
        contacts = p["targets"] * parts            # the same rule applied to the whole month (projection)
        dv = decision_value(p["readout_full"], contacts)
        proj[n] = {"contacts_per_month": contacts, "lcb95_fen_per_contact": dv["lcb95_fen_per_contact"],
                   "value_cny_per_month": dv["value_per_month"], "label": "PROJECTION from the readout LCB95"}
        log(f"   PROJECTION {n}: {dv['lcb95_fen_per_contact']:,.0f} fen/contact (readout LCB95) x {contacts} contacts/"
            f"month = CNY {dv['value_per_month']:,.0f}/month (assumes the per-contact effect transfers to the whole "
            f"month under the same rule)")
    out["projection"] = proj
    return out


def _decision_headline(decision: dict, scale: dict) -> dict:
    pol = {}
    for n, p in decision["policies"].items():
        vpc = p.get("value_per_contact_fen") or {}
        truth = p.get("truth") or p.get("truth_if_run") or {}
        pol[n] = {"offline": p["offline"], "model_experiment": p["model_experiment"][-1],
                  "readout": p.get("readout"), "value_per_contact_fen": vpc.get("point"), "ci": vpc.get("ci"),
                  "true_value_per_contact_fen": truth.get("value_per_contact_fen"),
                  "truth_inside_ci": p.get("truth_inside_ci"), "power_prerun": p.get("power_prerun"),
                  "power_oracle": (decision.get("diagnostics", {}).get(n) or {}).get("oracle_power_of_this_list"),
                  "model_scale": scale["policies"][n]["verdict"]}
    rq = decision.get("uplift_ranking_truth") or {}
    return {"policies": pol, "experimented": decision["experimented"],
            "uplift_model": decision["uplift_model"]["model_id"],
            "uplift_ranking_spearman_vs_truth": rq.get("spearman"),
            "call_channel_unprofitable": (decision.get("call_channel") or {}).get("unprofitable_for_every_policy"),
            "naive_claim_fen": (decision.get("naive") or {}).get("claim_fen"),
            "naive_correct": (decision.get("naive") or {}).get("correct"),
            "projection": scale["projection"]}


def run_demo(ws: Workspace | None = None, *, log: Callable[[str], None] | None = None) -> dict:
    base = ws or Workspace.default()
    log = log or (lambda _m: None)
    t_start = time.perf_counter()
    S: dict = {"workspace": str(base.root), "started_at": utcnow(), "steps": {}}

    def section(title: str) -> None:
        log(f"\n== {title}")

    # ------------------------------------------------------------------ 0. world + builds
    section("0. world and governed builds")
    prep = prepare_workspace(base, log=log)
    log("   " + ", ".join(f"{c}: {s}" for c, s in prep["builds"].items())
        + "  (drift/monitor FAILED = planted billing schema v2 break)")
    demo_root = base.runs / "demo"
    shutil.rmtree(demo_root / "ws", ignore_errors=True)
    ws = private_workspace(base, demo_root / "ws")
    S["steps"]["prepare"] = {**prep, "demo_registry": str(ws.registry)}
    tasks = BT.build_tasks()
    certified = BT.certified_build(base)

    # ------------------------------------------------------------------ 1. analytics
    section("1. analytics: 'What was net revenue by region for March 2026?'")
    a1 = tasks["A1"]
    truth = BT.analytics_truth(base, a1, certified)
    analytics = {}
    for name, label in (("careful", "careful (governed metric + declared metric_call)"),
                        ("naive_raw_sql", "naive (raw bronze_safe SQL)")):
        ctx = T.make_context(ws, "score", "analyst", f"agent:demo-analyst-{name.replace('_', '-')}")
        try:
            att = get_agent("analytics", name).act(Session(ctx), a1)
        finally:
            ctx.close()
        g = BT.grade_analytics(a1, att.deliverable["answer_rows"], truth)
        h = att.harness
        analytics[name] = {"tier": h["tier"], "gate": h["verdict"], "correct_vs_custody": g["correct"],
                           "reasons": h["answer_reasons"][:2], "answer_rows": att.deliverable["answer_rows"]}
        log(f"   {label}: tier {h['tier']}, answer_publish {h['verdict']}; vs custody truth: "
            f"{'correct' if g['correct'] else 'WRONG'}" + ("" if h["verdict"] == "ALLOW"
                                                           else f"  [{h['answer_reasons'][0][:240]}]"))
    S["steps"]["analytics"] = analytics

    # ------------------------------------------------------------------ 2. DE break-fix
    section("2. data engineering: billing export switched to schema v2 (D8)")
    de = T.make_context(ws, "score", "data_engineer", "agent:demo-engineer")
    try:
        st = _ok(T.pipeline_status(de, "drift"), "pipeline_status")
        failing = [m for m in st["models"] if m["status"] == "ERROR"]
        log(f"   drift build {st['build_id']}: {st['status']} ({failing[0]['name']}: "
            f"{str(failing[0]['error'])[:90]})" if failing else f"   drift build: {st['status']}")
        de_steps = {"drift_status_before": st["status"]}
        for name, label in (("naive_div100", "naive fix (divide amount_minor by 100)"),
                            ("careful", "correct fix (COLUMNS regex: amount_cents | amount_minor)")):
            att = get_agent("de", name).act(Session(de), BT.build_tasks()["E1"])
            h = att.harness
            de_steps[name] = {"verdict": h["verdict"], "checks": h["checks"], "change_id": h["change_id"]}
            log(f"   {label}: pipeline_merge {h['verdict']}  checks {h['checks']}")
    finally:
        de.close()
    if de_steps["careful"]["verdict"] != "ALLOW" or de_steps["naive_div100"]["verdict"] == "ALLOW":
        raise DemoError(f"unexpected pipeline_merge verdicts: {de_steps}")
    # controller merge: only the exact pipeline digest the gate ALLOWed may be built and served
    change_id = de_steps["careful"]["change_id"]
    fixed = ws.runs / change_id / "pipeline"
    pdig = pipeline_digest(fixed)
    subject = {"kind": "pipeline_change", "change_id": change_id,
               "base_pipeline_digest": pipeline_digest(DEFAULT_PIPELINE), "pipeline_digest": pdig}
    decisions = read_jsonl(ws.registry / "gate_decisions.jsonl")
    if not any(d.get("stage") == "pipeline_merge" and d.get("verdict") == "ALLOW"
               and d.get("subject_digest") == digest(subject) for d in decisions):
        raise DemoError(f"no pipeline_merge ALLOW for change {change_id} with pipeline digest {pdig[:12]}")
    fixed_builds = {}
    for name in ("drift", "monitor"):
        rep = build_snapshot(base, CUTOFFS[name], fixed)
        fixed_builds[name] = {"build_id": rep.build_id, "status": rep.status}
        if rep.status != "READY":
            raise DemoError(f"fixed pipeline: {name} build {rep.build_id} is {rep.status}: {rep.reasons[:3]}")
    de_steps.update(merged_pipeline_digest=pdig, fixed_builds=fixed_builds)
    log(f"   controller merge of {change_id} (pipeline {pdig[:8]}): "
        + ", ".join(f"{k} {v['build_id']} {v['status']}" for k, v in fixed_builds.items()))
    S["steps"]["data_engineering"] = de_steps

    # ------------------------------------------------------------------ 3-7. features, training, gates
    section("3. feature proposals and leakage checks")
    ds = T.make_context(ws, "train", "data_scientist", MODELER)
    try:
        props = {}
        proposals = [("bug_tickets_90d", GOOD_FEATURE_SQL, "Bug tickets created and available in the 90 days "
                                                           "before as_of")]
        proposals += [(n, NAIVE_FEATURES[n].sql, NAIVE_FEATURES[n].description)
                      for n in ("has_cancel_reason", "minutes_last_month_naive", "current_price_fen")]
        for name, sql, desc in proposals:
            r = _ok(T.propose_feature(ds, name, sql, desc), f"propose_feature {name}")
            tt = [t["outcome"] for t in r["time_travel"]]
            props[name] = {"outcome": r["outcome"], "gate": r["gate"]["verdict"], "time_travel": tt,
                           "static": r["static"]["outcome"]}
            log(f"   {name:26s} leakage {r['outcome']:5s} (time travel {'/'.join(tt)}, static "
                f"{r['static']['outcome']}) -> feature_accept {r['gate']['verdict']}")
        S["steps"]["features"] = props

        section("4-5. train the careful candidate, sealed evaluation")
        features = [*TRUSTED_FEATURES, "bug_tickets_90d"]
        cand = _ok(T.train_candidate(ds, features, "logreg", {"C": 0.5}, list(BT.TRAIN_AS_OF)), "train_candidate")
        cid = cand["candidate_id"]
        log(f"   candidate {cid}: {len(features)} features, dev CV AUC {cand['cv_mean']['auc']:.3f} "
            f"(model.leakage {cand['model_leakage']['outcome']})")
        ev = _ok(T.request_final_evaluation(ds, cid), "request_final_evaluation")
        m = ev.get("metrics") or {}
        log(f"   sealed June-2026 evaluation: {ev['outcome']}, AUC {m.get('auc', float('nan')):.3f} "
            f"[{m.get('auc_ci95', [0, 0])[0]:.3f}, {m.get('auc_ci95', [0, 0])[1]:.3f}], n={m.get('n')}, "
            f"budget {ev['budget']['used']}/{ev['budget']['limit']}")

        section("6-7. promotion gates (the model)")
        gates = {"model_shadow": _ok(T.request_release(ds, cid, "model_shadow"), "model_shadow")}
        log(f"   model_shadow: {gates['model_shadow']['verdict']}  (a model is shadowed; experiments and scaling "
            f"gate a targeting POLICY, section 8)")
        if gates["model_shadow"]["verdict"] != "ALLOW":
            raise DemoError(f"model_shadow is {gates['model_shadow']['verdict']}: {gates['model_shadow']['reasons'][:3]}")
    finally:
        ds.close()
    S["steps"]["model"] = {"candidate_id": cid, "features": features, "cv_mean": cand["cv_mean"],
                           "model_leakage": cand["model_leakage"]["outcome"],
                           "sealed": {k: ev.get(k) for k in ("outcome", "metrics", "budget")},
                           "gates": {k: {"verdict": v["verdict"], "reasons": [str(r)[:200] for r in v["reasons"][:3]]}
                                     for k, v in gates.items()}}

    # ------------------------------------------------------------------ 8. July decision: policies
    section("8. July 2026 decision: risk vs uplift targeting (app channel), offline validation, "
            "pre-registered experiment")
    score_build = BT.certified_build(base)
    decision = _decision_story(base, ws, cid, score_build, tasks, log)
    gates.update(decision.pop("gates"))
    S["steps"]["decision"] = decision

    # ------------------------------------------------------------------ 9. scale per policy
    section("9. model_scale per policy (each policy needs ITS OWN PASSing readout)")
    scale = _scale_story(ws, decision, log)
    S["steps"]["model_scale"] = scale

    # ------------------------------------------------------------------ 10. drift
    section("10. drift: August/September populations vs the June reference (controller monitoring)")
    train_build = ml_train.candidate_record(ws, cid)["build_id"]
    frames = {}
    with readonly_connection(base.lake / train_build / "warehouse.duckdb") as con:
        frames["2026-06 (ref)"] = build_features(con, BT.SEALED_AS_OF, DRIFT_FEATURES)
    windows = {"2026-08": (fixed_builds["drift"]["build_id"], "2026-09-01"),
               "2026-09": (fixed_builds["monitor"]["build_id"], "2026-10-01")}
    for label, (bid, as_of) in windows.items():
        with readonly_connection(base.lake / bid / "warehouse.duckdb") as con:
            frames[label] = build_features(con, as_of, DRIFT_FEATURES)
    ref = frames["2026-06 (ref)"]
    ref_scores = ml_train.score(ws, cid, train_build, BT.SEALED_AS_OF).rename(columns={"score": "model_score"})
    drift = {}
    for label, (bid, as_of) in windows.items():
        cur_scores = ml_train.score(ws, cid, bid, as_of).rename(columns={"score": "model_score"})
        rep = drift_report(ref.merge(ref_scores, on="customer_id"),
                           frames[label].merge(cur_scores, on="customer_id"), [*DRIFT_FEATURES, "model_score"])
        drift[label] = {"build_id": bid, "as_of": as_of, "n": int(len(frames[label])), "outcome": rep["outcome"],
                        "flagged": rep["flagged"], "watch": rep["watch"],
                        "psi": {k: round(v, 4) for k, v in rep["psi"].items()}}
        top = ", ".join(f"{f} {rep['psi'][f]:.2f}" for f in rep["flagged"][:4]) or "none"
        log(f"   {label} (as_of {as_of}, n={len(frames[label])}): drift {rep['outcome']}; PSI>0.2: {top}; "
            f"score PSI {rep['psi']['model_score']:.2f}")
    S["steps"]["drift"] = drift

    # ------------------------------------------------------------------ 11. retrain proposal
    section("11. retrain proposed on the monitor build -- not auto-promoted")
    mon = T.ToolContext(ws, fixed_builds["monitor"]["build_id"], "data_scientist", MODELER)
    try:
        re_cand = _ok(T.train_candidate(mon, features, "logreg", {"C": 0.5}, RETRAIN_AS_OF), "retrain")
        promo = _ok(T.request_release(mon, re_cand["candidate_id"], "retrain_promote"), "retrain_promote")
    finally:
        mon.close()
    log(f"   challenger {re_cand['candidate_id']} (train {RETRAIN_AS_OF[0]}..{RETRAIN_AS_OF[-1]}, dev CV AUC "
        f"{re_cand['cv_mean']['auc']:.3f}): retrain_promote {promo['verdict']}  [{_why(promo['reasons'], 4)}]")
    if promo["verdict"] == "ALLOW":
        raise DemoError("a retrained model was promoted without champion/challenger evidence")
    S["steps"]["retrain"] = {"candidate_id": re_cand["candidate_id"], "build_id": re_cand["build_id"],
                             "train_as_of": RETRAIN_AS_OF, "cv_mean": re_cand["cv_mean"],
                             "retrain_promote": promo["verdict"], "reasons": [str(r)[:200] for r in promo["reasons"]]}

    # ------------------------------------------------------------------ summary
    S["finished_at"] = utcnow()
    S["seconds"] = round(time.perf_counter() - t_start, 1)
    S["headline"] = {
        "analytics": {k: f"{v['tier']} / {v['gate']} ({'correct' if v['correct_vs_custody'] else 'wrong'})"
                      for k, v in analytics.items()},
        "pipeline_merge": {k: de_steps[k]["verdict"] for k in ("naive_div100", "careful")},
        "fixed_builds": {k: v["status"] for k, v in fixed_builds.items()},
        "features": {k: v["gate"] for k, v in props.items()},
        "sealed_auc": (ev.get("metrics") or {}).get("auc"),
        "gates": {"model_shadow": gates["model_shadow"]["verdict"],
                  "model_experiment_before": gates["model_experiment_before"],
                  "model_experiment_self_approved": gates["model_experiment_self_approved"],
                  "model_experiment_after": gates["model_experiment_after"],
                  "model_experiment": gates["model_experiment"],
                  "model_scale": {k: v["verdict"] for k, v in scale["policies"].items()},
                  "retrain_promote": promo["verdict"]},
        "decision": _decision_headline(decision, scale),
        "drift_flagged": {k: v["flagged"] for k, v in drift.items()},
    }
    path = demo_root / "summary.json"
    S["summary_path"] = str(path)
    path.write_text(json.dumps(S, indent=2, default=str))
    append_jsonl(demo_root / "runs.jsonl", {"ts": S["finished_at"], "seconds": S["seconds"], "headline": S["headline"]})
    log(f"\ndemo finished in {S['seconds']}s; registry of this run: {ws.registry}")
    return S


if __name__ == "__main__":
    run_demo(log=print)
