"""Scripted benchmark runner: agents act through the governed tools, the evaluator grades their
claims against truth, and the report compares "delivered as success" with and without the harness.

Trust boundary and isolation. Every (family, agent) runs in its own private workspace under
``runs/bench/<run_id>/agents/``: it shares the base workspace's lake, source exports and custody
truth files (symlinks, read-only use), but has its own registry (evidence, approvals, audit), runs/
and custody ledgers (holdout budget, experiment releases). One agent's evidence can therefore never
satisfy or block another agent's gate. Grading (``bench.tasks``) is evaluator-side and never reads
a harness verdict; the harness never sees truth.

Per (task, agent) the report records:
  correct                 the claim matches truth within the task's tolerance (evaluator)
  claimed_success         the agent's own claim (scripted agents always claim success)
  harness verdict         tier (analytics) / gate verdict of the stage the deliverable asks for
  delivered_as_trusted    gate ALLOW (analytics: tier VERIFIED, which coincides with ALLOW)
and classifies: silent failure without harness = wrong AND claimed; silent failure with harness =
wrong AND delivered; caught = wrong AND NOT delivered (REJECT or HOLD); false block = correct AND
NOT delivered, split into HOLD-for-human-review (the harness's cost), REJECT, and "withheld by
design" (a decision whose own recommendation is NOT to take the gated action).

These are deterministic scripted agents: the numbers show which fault classes the harness's
mechanisms catch, not error rates of LLM agents (see ``POWER_NOTE``).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import time

from .. import tools as T
from ..common import digest, file_digest, utcnow
from ..context.catalog import load_catalog
from ..context.semantic import load_semantic_model
from ..evidence import ApprovalStore, load_policy
from ..lake.build import (DEFAULT_PIPELINE, build_id_for, build_snapshot, latest_ready_build, load_report,
                          warehouse_path)
from ..lake.runner import pipeline_digest
from ..ml import train as ml_train
from ..ml.features import readonly_connection
from ..workspace import CUTOFFS, PIT_CUTOFFS, Workspace
from ..world.build import build_world
from . import tasks as BT
from .agents import TRUSTED_SPEC, AgentError, Attempt, ScriptedAgent, Session, select_agents

ROLES = {"analytics": ("analyst", "score"), "de": ("data_engineer", "score"),
         "ml": ("data_scientist", "train"), "decision": ("data_scientist", "score")}
SERVED_READY = (CUTOFFS["train"], CUTOFFS["score"], *PIT_CUTOFFS)
POWER_NOTE = ("Scripted agents demonstrate mechanism coverage: each naive variant plants one known fault class "
              "and the run shows whether the harness's checks stop it. They are not a sample of LLM behaviour, so "
              "the rates below are not LLM error rates and carry no sampling interval. Estimating a difference in "
              "silent-failure rate between arms (e.g. 10% vs 5%) needs roughly 400+ independent items per arm "
              "(about 430 unpaired, per the design doc's sample-size discipline); with n items and 0 failures the "
              "95% upper bound is only about 3/n. That is future work with live agents (bench/live.py).")
CLASSES = ("correct_delivered", "silent_failure", "caught_reject", "caught_hold", "false_block_hold",
           "false_block_reject", "correct_withheld_by_design")
ROI_USE_CASE = {"analytics": "analytics_qa", "de": "pipeline_breakfix", "ml": "churn_model",
                "decision": "experiment_readout"}


class BenchError(RuntimeError):
    """The bench could not run as specified (missing builds, broken setup)."""


# ============================================================================ workspaces
def private_workspace(base: Workspace, root: Path) -> Workspace:
    """Shares base's lake, sources and custody truth files; own registry, runs and custody ledgers."""
    root = Path(root)
    if root.exists():
        raise BenchError(f"{root} already exists; private workspaces start empty")
    root.mkdir(parents=True)
    (root / "lake").symlink_to(base.lake.resolve(), target_is_directory=True)
    (root / "sources").symlink_to(base.sources.resolve(), target_is_directory=True)
    (root / "custody").mkdir()
    for f in base.custody.iterdir():
        if f.suffix in (".parquet", ".json"):
            (root / "custody" / f.name).symlink_to(f.resolve())
    return Workspace(root).ensure()


def prepare_workspace(ws: Workspace, *, world_cfg=None, log: Callable[[str], None] | None = None) -> dict:
    """Build the world (sources + custody) and the default-pipeline builds that are missing.
    Train, score and PIT builds must end READY; drift/monitor FAILED is the planted D8 break."""
    log = log or (lambda _m: None)
    ws.ensure()
    cutoffs = [*CUTOFFS.values(), *PIT_CUTOFFS]
    world_missing = not (ws.custody / "world_meta.json").exists() or any(
        not (ws.source_dir(c) / "_export_manifest.json").exists() for c in cutoffs)
    if world_missing:
        log(f"building the synthetic world into {ws.root} ...")
        build_world(ws, world_cfg)
    pdig = pipeline_digest(DEFAULT_PIPELINE)
    status = {}
    for c in cutoffs:
        bid = build_id_for(c, pdig)
        if world_missing or not (ws.lake / bid / "build_report.json").exists():
            t0 = time.perf_counter()
            status[c] = build_snapshot(ws, c).status
            log(f"built {bid}: {status[c]} ({time.perf_counter() - t0:.1f}s)")
        else:
            status[c] = load_report(ws, bid)["status"]
    broken = [c for c in SERVED_READY if status[c] != "READY"]
    if broken:
        raise BenchError(f"default-pipeline builds {broken} are not READY: {status}")
    return {"built_world": world_missing, "builds": status, "pipeline_digest": pdig}


def bench_inputs(ws: Workspace, agents: list[ScriptedAgent]) -> dict:
    """Everything a run's outcome depends on; the run id is its digest (no wall clock)."""
    here = Path(__file__).parent
    code = {p.name: file_digest(p) for p in sorted(here.glob("*.py"))}
    tools_code = {p.name: file_digest(p) for p in sorted((here.parent / "tools").glob("*.py"))}
    return {"world_meta": file_digest(ws.custody / "world_meta.json"),
            "exports": {c: file_digest(ws.source_dir(c) / "_export_manifest.json")
                        for c in [*CUTOFFS.values(), *PIT_CUTOFFS]},
            "pipeline_digest": pipeline_digest(DEFAULT_PIPELINE), "semantic_digest": load_semantic_model().digest,
            "catalog_digest": load_catalog().digest, "policy_digest": digest(load_policy()),
            "ml_code_version": ml_train.CODE_VERSION, "bench_code": digest(code), "tools_code": digest(tools_code),
            "agents": [f"{a.family}/{a.name}" for a in agents]}


# ============================================================================ decision controller
class DecisionController:
    """Controller/evaluator-side services for one decision-family workspace: the modelling team's
    churn candidate (trained, sealed-evaluated, model_shadow) and uplift model, the two registered
    targeting policies with their offline validation, the business owner's model_experiment
    sign-off and the scale sign-offs (recorded up front, so model_scale isolates the evidence), a
    dry-run brief of what each policy would target, and the experimentation platform that runs the
    pre-registered July experiment ONCE for the policies the gate cleared. Not an agent tool."""
    MODELER = "agent:bench-modeler"
    BUSINESS_OWNER = "user:bench-business-owner"
    RISK_OFFICER = "user:bench-risk-officer"
    POLICIES = ("risk_top_k", "uplift_value_top_k")

    def __init__(self, ws: Workspace, agent_principal: str):
        self.ws = ws
        self.agent_principal = agent_principal
        self.score_build = latest_ready_build(ws, CUTOFFS["score"])
        self.brief: dict | None = None
        self._result: dict | None = None
        self._call: dict | None = None

    def _ok(self, res: dict, what: str) -> dict:
        if "error" in res and "kind" in res:
            raise BenchError(f"decision setup: {what}: {res['kind']}: {res['error']}")
        return res

    def _gate_policy(self, policy_id: str) -> str:
        """Business-owner sign-off for the experiment, the modeler's model_experiment request, and the
        scale sign-offs by two distinct people (so model_scale isolates the evidence requirement)."""
        subject = T.ds.policy_record(self.ws, policy_id)["subject"]
        approvals = ApprovalStore(self.ws)
        approvals.record(stage="model_experiment", approver=self.BUSINESS_OWNER, role="business_owner",
                         requested_by=self.MODELER, subject=subject)
        ctx = T.make_context(self.ws, "score", "data_scientist", self.MODELER)
        try:
            verdict = self._ok(T.request_release(ctx, policy_id=policy_id, stage="model_experiment"),
                               "model_experiment")["verdict"]
        finally:
            ctx.close()
        for approver, role in ((self.BUSINESS_OWNER, "business_owner"), (self.RISK_OFFICER, "risk")):
            approvals.record(stage="model_scale", approver=approver, role=role, requested_by=self.agent_principal,
                             subject=subject)
        return verdict

    def setup(self) -> dict:
        from ..decision.design import PolicyExperimentDesign
        from ..decision.experiment import ValueParams
        from ..ml import uplift as ml_uplift
        from ..ml.features import population
        ctx = T.make_context(self.ws, "train", "data_scientist", self.MODELER)
        try:
            names = [f["name"] for f in self._ok(T.list_features(ctx), "list_features")["trusted"]]
            cand = self._ok(T.train_candidate(ctx, names, **TRUSTED_SPEC), "train_candidate")
            cid = cand["candidate_id"]
            self._ok(T.request_final_evaluation(ctx, cid), "sealed evaluation")
            shadow = self._ok(T.request_release(ctx, cid, "model_shadow"), "model_shadow")["verdict"]
        finally:
            ctx.close()
        if shadow != "ALLOW":
            raise BenchError(f"decision setup: the churn candidate is not released to shadow: {shadow}")
        up = ml_uplift.train_uplift_model(self.ws, self.score_build, ml_uplift.UpliftSpec(),
                                          requested_by=self.MODELER, validate=False)
        with readonly_connection(warehouse_path(self.ws, self.score_build)) as con:
            n_eligible = len(population(con, BT.EXPERIMENT_AS_OF))
        self.k = BT.design_k(n_eligible)
        policies = {}
        for name, models in (("risk_top_k", {"churn_candidate_id": cid}),
                             ("uplift_value_top_k", {"uplift_model_id": up["model_id"]})):
            reg = T.ds.register_policy(self.ws, name, k=self.k, channel=BT.DECISION_CHANNEL,
                                       as_of=BT.EXPERIMENT_AS_OF, score_build=self.score_build,
                                       requested_by=self.MODELER, **models)
            off = T.ds.validate_policy_offline(self.ws, reg["policy_id"])
            policies[name] = {"policy_id": reg["policy_id"], "models": reg["models_check"]["outcome"],
                              "offline": {"outcome": off["outcome"], "qini": off["qini_coefficient"],
                                          "value_top": off.get("value_top_fraction")},
                              "model_experiment": self._gate_policy(reg["policy_id"])}
        self.candidate_id, self.uplift_model_id, self.policy_ids = cid, up["model_id"], {
            n: p["policy_id"] for n, p in policies.items()}
        # dry run of the full A/B design: what each registered policy would target (no outcomes)
        dry = T.ds.plan_policy_experiment(
            self.ws, PolicyExperimentDesign(BT.EXPERIMENT_ID, BT.EXPERIMENT_AS_OF, self.POLICIES, self.k,
                                            BT.DECISION_CHANNEL), self.policy_ids, require_gate=False)
        preds = ml_uplift.predict_uplift(self.ws, up["model_id"], self.score_build, BT.EXPERIMENT_AS_OF)
        p0 = dict(zip(preds["customer_id"], preds["p0"].astype(float)))
        p1 = dict(zip(preds["customer_id"], preds["p1"].astype(float)))
        risk = dry["details"]["risk_top_k"]["scores"]
        for name in self.POLICIES:
            ids = dry["targets"][name]
            policies[name]["targeted_ids"] = ids
            policies[name]["scores"] = {c: risk[c] for c in ids} if name == "risk_top_k" else {}
            policies[name]["p0"] = {c: p0[c] for c in ids}
            policies[name]["p1"] = {c: p1[c] for c in ids}
        self.brief = {"as_of": BT.EXPERIMENT_AS_OF, "channel": BT.DECISION_CHANNEL, "k": self.k,
                      "experiment_id": BT.EXPERIMENT_ID, "score_build": self.score_build,
                      "value_params": asdict(ValueParams.for_channel(BT.DECISION_CHANNEL)), "policies": policies}
        return self.brief

    def run_experiment(self) -> dict:
        """The pre-registered July experiment over the policies cleared by model_experiment (once)."""
        from ..decision.design import PolicyExperimentDesign
        if self.brief is None:
            raise BenchError("run_experiment before setup")
        if self._result is None:
            # pre-registered rule: policies the gate cleared that would contact at least one customer
            cleared = tuple(n for n in self.POLICIES if self.brief["policies"][n]["model_experiment"] == "ALLOW"
                            and self.brief["policies"][n]["targeted_ids"])
            if not cleared:
                raise BenchError("no policy is cleared for an experiment")
            design = PolicyExperimentDesign(BT.EXPERIMENT_ID, BT.EXPERIMENT_AS_OF, cleared, self.k, BT.DECISION_CHANNEL)
            self._result = T.ds.run_policy_experiment(self.ws, design, {n: self.policy_ids[n] for n in cleared},
                                                      requested_by="controller:bench")
        return self._result

    def call_policy(self) -> dict:
        """Register, validate and gate the risk policy on the phone-call channel (on request)."""
        from ..decision.design import PolicyExperimentDesign
        from ..decision.experiment import ValueParams
        if self.brief is None:
            raise BenchError("call_policy before setup")
        if self._call is None:
            reg = T.ds.register_policy(self.ws, "risk_top_k", k=self.k, channel="call", as_of=BT.EXPERIMENT_AS_OF,
                                       score_build=self.score_build, requested_by=self.MODELER,
                                       churn_candidate_id=self.candidate_id)
            off = T.ds.validate_policy_offline(self.ws, reg["policy_id"])
            verdict = self._gate_policy(reg["policy_id"])
            dry = T.ds.plan_policy_experiment(
                self.ws, PolicyExperimentDesign(f"{BT.EXPERIMENT_ID}-call", BT.EXPERIMENT_AS_OF, ("risk_top_k",),
                                                self.k, "call"), {"risk_top_k": reg["policy_id"]}, require_gate=False)
            self._call = {"policy_id": reg["policy_id"], "offline": off["outcome"], "model_experiment": verdict,
                          "targeted_ids": dry["targets"]["risk_top_k"],
                          "value_params": asdict(ValueParams.for_channel("call"))}
        return self._call


# ============================================================================ running + grading
def _principal(agent: ScriptedAgent) -> str:
    return f"agent:bench-{agent.family}-{agent.name}".replace("_", "-")


def run_agent(aws: Workspace, agent: ScriptedAgent, tasks: dict[str, BT.Task]) -> list[tuple[Attempt, float]]:
    role, cutoff = ROLES[agent.family]
    principal = _principal(agent)
    controller, brief = None, None
    if agent.family == "decision":
        controller = DecisionController(aws, principal)
        brief = controller.setup()
    out = []
    for tid in agent.tasks:
        ctx = T.make_context(aws, cutoff, role, principal, task_id=f"bench-{tid}")
        try:
            t0 = time.perf_counter()
            attempt = agent.act(Session(ctx, brief=brief, controller=controller), tasks[tid])
            out.append((attempt, time.perf_counter() - t0))
        finally:
            ctx.close()
    return out


class Evaluator:
    """Truth side. Caches per-task truth; grades deliverables, never harness verdicts."""

    def __init__(self, ws: Workspace, work_dir: Path):
        self.ws = ws
        self.work_dir = Path(work_dir)
        self.certified = BT.certified_build(ws)
        self._analytics: dict[str, dict] = {}
        self._decision: dict[str, dict] = {}

    def grade(self, aws: Workspace, task: BT.Task, attempt: Attempt) -> tuple[dict, dict]:
        """(truth summary, grade) for one attempt."""
        d = attempt.deliverable
        if task.family == "analytics":
            if task.task_id not in self._analytics:
                self._analytics[task.task_id] = BT.analytics_truth(self.ws, task, self.certified)
            truth = self._analytics[task.task_id]
            return truth, BT.grade_analytics(task, d["answer_rows"], truth)
        if task.family == "de":
            g = BT.grade_pipeline_change(self.ws, d["files"], self.work_dir / f"{task.task_id}-{attempt.agent}")
            return {"source": task.truth_source}, g
        if task.family == "ml":
            truth = BT.model_truth(aws, d["candidate_id"], d["build_id"])
            return truth, BT.grade_model_claim(attempt.claim, truth)
        if task.family == "decision":
            key = digest({"targeted": sorted(d["targeted"]), "channel": d["channel"]})
            if key not in self._decision:
                self._decision[key] = BT.decision_truth(self.ws, d["targeted"], d["channel"])
            truth = self._decision[key]
            return truth, BT.grade_value_claim(attempt.claim, truth)
        raise BenchError(f"no grader for family {task.family}")


def classify(correct: bool, delivered: bool, verdict: str, by_design: bool) -> str:
    if correct:
        if delivered:
            return "correct_delivered"
        if by_design:
            return "correct_withheld_by_design"
        return "false_block_hold" if verdict == "HOLD" else "false_block_reject"
    if delivered:
        return "silent_failure"
    return "caught_reject" if verdict == "REJECT" else "caught_hold"


def make_item(task: BT.Task, agent: ScriptedAgent, attempt: Attempt, truth: dict, grade: dict,
              seconds: float) -> dict:
    h = attempt.harness
    delivered = bool(h["delivered"])
    correct = bool(grade["correct"])
    by_design = task.family == "decision" and attempt.claim.get("recommendation") != "scale"
    # analytics: the tier says how the answer was stopped (FLAGGED even when an ERROR check makes the gate HOLD)
    block = {"FLAGGED": "REJECT", "CONSISTENT": "HOLD"}.get(h.get("tier"), h["verdict"])
    cls = classify(correct, delivered, block, by_design)
    return {"task_id": task.task_id, "family": task.family, "agent": agent.name, "kind": agent.kind,
            "mistake": agent.mistake, "claim": _claim_summary(attempt.claim),
            "claimed_success": attempt.claimed_success, "harness": h,
            "verdict": f"{h['tier']} / {h['verdict']}" if h.get("tier") else h["verdict"],
            "gate_verdict": h["verdict"], "delivered_as_trusted": delivered, "correct": correct,
            "grade": grade, "truth": _truth_summary(task, truth),
            "silent_failure_without_harness": (not correct) and attempt.claimed_success,
            "silent_failure_with_harness": (not correct) and delivered,
            "caught": (not correct) and not delivered, "false_block": correct and not delivered,
            "by_design": by_design and correct and not delivered, "class": cls,
            "tool_calls": attempt.tool_calls, "seconds": round(seconds, 2)}


def _claim_summary(claim: dict) -> dict:
    out = {k: v for k, v in claim.items() if k not in ("answer_rows", "sql")}
    if "answer_rows" in claim:
        out["answer_rows"] = claim["answer_rows"][:8]
        out["sql_digest"] = digest(claim["sql"])[:12] if claim.get("sql") else None
    return out


def _truth_summary(task: BT.Task, truth: dict) -> dict:
    if task.family == "analytics":
        return {"values": truth["values"], "source": truth["source"],
                "independent_of_harness": truth["independent_of_harness"]}
    if task.family == "ml":
        return {k: truth.get(k) for k in ("outcome", "auc", "auc_ci95", "n", "n_positive", "reason")}
    if task.family == "decision":
        return {k: truth[k] for k in ("value_per_contact_fen", "mean_effect", "expected_retained", "n", "segments",
                                      "channel")}
    return truth


# ============================================================================ aggregation
def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def aggregate(items: list[dict]) -> dict:
    n = len(items)
    correct = sum(i["correct"] for i in items)
    wrong = n - correct
    counts = {c: sum(i["class"] == c for i in items) for c in CLASSES}
    swo = sum(i["silent_failure_without_harness"] for i in items)
    sw = sum(i["silent_failure_with_harness"] for i in items)
    caught = counts["caught_reject"] + counts["caught_hold"]
    fb = counts["false_block_hold"] + counts["false_block_reject"] + counts["correct_withheld_by_design"]
    return {"n": n, "correct": correct, "wrong": wrong,
            "claimed_success": sum(i["claimed_success"] for i in items),
            "delivered_as_trusted": sum(i["delivered_as_trusted"] for i in items),
            "silent_failure_without_harness": swo, "silent_failure_with_harness": sw,
            "caught": caught, "caught_reject": counts["caught_reject"], "caught_hold": counts["caught_hold"],
            "false_block": fb, "false_block_hold_for_review": counts["false_block_hold"],
            "false_block_reject": counts["false_block_reject"],
            "correct_withheld_by_design": counts["correct_withheld_by_design"],
            "classes": counts,
            "rates": {"correct": _rate(correct, n),
                      "silent_failure_without_harness": _rate(swo, n),
                      "silent_failure_with_harness": _rate(sw, n),
                      "caught_of_wrong": _rate(caught, wrong),
                      "delivered_of_wrong": _rate(sw, wrong),
                      "false_block_of_correct": _rate(fb, correct),
                      "hold_for_review_of_correct": _rate(counts["false_block_hold"], correct)},
            "rule_of_three_upper_95_delivered_of_wrong": (3 / wrong) if wrong and sw == 0 else None}


def summarize(items: list[dict]) -> dict:
    fams = sorted({i["family"] for i in items}, key=BT.FAMILIES.index)
    return {"overall": aggregate(items),
            "by_family": {f: aggregate([i for i in items if i["family"] == f]) for f in fams},
            "by_kind": {k: aggregate([i for i in items if i["kind"] == k])
                        for k in ("careful", "naive") if any(i["kind"] == k for i in items)}}


def catching_checks(items: list[dict]) -> dict:
    """For each naive fault class: the harness checks that blocked it (gate reasons)."""
    out: dict[str, dict] = {}
    for i in items:
        if i["kind"] != "naive":
            continue
        e = out.setdefault(f"{i['family']}/{i['agent']}", {"mistake": i["mistake"], "items": 0, "caught": 0,
                                                           "checks": {}})
        e["items"] += 1
        e["caught"] += int(i["caught"])
        for b in i["harness"].get("blocking", []):
            e["checks"][b] = e["checks"].get(b, 0) + 1
    return out


def roi_conditional_rates(summary: dict) -> dict:
    """Per use case, the harness's conditional behaviour measured by the bench: P(delivered | wrong),
    P(caught | wrong), P(blocked | correct) (HOLD for review or REJECT; decisions withheld by the
    agent's own recommendation excluded). The *mix* of wrong vs correct items is set by the scripted
    variants, so only these conditional rates carry over; `dsh roi` combines them with an assumed base
    error rate."""
    out = {}
    for fam, agg in summary["by_family"].items():
        wrong, correct = agg["wrong"], agg["correct"]
        blocked = agg["false_block_hold_for_review"] + agg["false_block_reject"]
        out[ROI_USE_CASE[fam]] = {"delivered_of_wrong": _rate(agg["silent_failure_with_harness"], wrong),
                                  "caught_of_wrong": _rate(agg["caught"], wrong),
                                  "blocked_of_correct": _rate(blocked, correct),
                                  "n_wrong": wrong, "n_correct": correct}
    return out


# ============================================================================ entry point
def run_bench(ws: Workspace, agents=None, families=None, *, log: Callable[[str], None] | None = None) -> dict:
    """Run the scripted benchmark on `ws`; write runs/bench/<run_id>/report.{json,md}; return the report."""
    log = log or (lambda _m: None)
    started = time.perf_counter()
    prep = prepare_workspace(ws, log=log)
    selected = select_agents(agents, families)
    tasks = BT.build_tasks()
    inputs = bench_inputs(ws, selected)
    run_id = digest(inputs)[:12]
    run_dir = ws.runs / "bench" / run_id
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    evaluator = Evaluator(ws, run_dir / "evaluator")
    items: list[dict] = []
    for agent in selected:
        aws = private_workspace(ws, run_dir / "agents" / f"{agent.family}--{agent.name}")
        t0 = time.perf_counter()
        try:
            attempts = run_agent(aws, agent, tasks)
        except AgentError as exc:
            raise BenchError(f"{agent.family}/{agent.name}: {exc}") from exc
        for attempt, secs in attempts:
            task = tasks[attempt.task_id]
            truth, grade = evaluator.grade(aws, task, attempt)
            item = make_item(task, agent, attempt, truth, grade, secs)
            items.append(item)
            log(f"  {task.task_id:3s} {agent.family:9s} {agent.name:28s} correct={str(item['correct']):5s} "
                f"verdict={item['verdict']:18s} -> {item['class']}")
        log(f"{agent.family}/{agent.name}: {len(attempts)} task(s) in {time.perf_counter() - t0:.1f}s")
    summary = summarize(items)
    report = {"run_id": run_id, "timestamp": utcnow(), "workspace": str(ws.root), "run_dir": str(run_dir),
              "inputs": inputs, "prepare": prep, "power_note": POWER_NOTE,
              "tasks": {t.task_id: t.to_dict() for t in tasks.values()},
              "agents": [{"family": a.family, "name": a.name, "kind": a.kind, "mistake": a.mistake,
                          "tasks": list(a.tasks)} for a in selected],
              "summary": summary, "catching_checks": catching_checks(items),
              "naive_correct_by_chance": [{"task_id": i["task_id"], "family": i["family"], "agent": i["agent"],
                                           "class": i["class"], "verdict": i["verdict"]}
                                          for i in items if i["kind"] == "naive" and i["correct"]],
              "roi_measured": {"basis": "scripted agents (mechanism coverage, not LLM error rates)",
                               "conditional_rates": roi_conditional_rates(summary)},
              "items": items, "seconds": round(time.perf_counter() - started, 1)}
    write_report(run_dir, report)
    return report


def write_report(run_dir: Path, report: dict) -> None:
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, default=str, allow_nan=False))
    (run_dir / "report.md").write_text(render_markdown(report))


# ============================================================================ markdown
def _fmt(v, digits=3) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.{digits}g}" if abs(v) < 1e4 else f"{v:,.0f}"
    return str(v)


def _claim_text(i: dict) -> str:
    c = i["claim"]
    if "statement" in c:
        s = str(c["statement"])
    else:
        s = json.dumps(c, default=str)
    s = s.replace("|", "/").replace("\n", " ")
    return s if len(s) <= 90 else s[:87] + "..."


def _truth_text(i: dict) -> str:
    t, g = i["truth"], i["grade"]
    if i["family"] == "analytics":
        vals = t["values"]
        s = ", ".join(f"{k or 'value'}={_fmt(v, 6)}" for k, v in vals.items())
    elif i["family"] == "ml":
        s = f"sealed AUC {_fmt(t.get('auc'))} CI [{_fmt((t.get('auc_ci95') or [None])[0])}, " \
            f"{_fmt((t.get('auc_ci95') or [None, None])[1])}]"
    elif i["family"] == "decision":
        s = f"true {t['value_per_contact_fen']:.0f} fen/contact ({t.get('channel', 'app')})"
    else:
        s = "; ".join(g.get("reasons") or ["all served cutoffs correct; probe refused"])
    return s.replace("|", "/")[:110]


def render_markdown(r: dict) -> str:
    s = r["summary"]
    L = [f"# DS Harness scripted benchmark — run `{r['run_id']}`", "",
         f"- timestamp: {r['timestamp']}  ", f"- workspace: `{r['workspace']}`  ",
         f"- pipeline `{r['inputs']['pipeline_digest'][:8]}`, semantic `{r['inputs']['semantic_digest'][:8]}`, "
         f"policy `{r['inputs']['policy_digest'][:8]}`, runtime {r['seconds']}s", "",
         f"> {r['power_note']}", "",
         "## Headline", "",
         "| scope | items | correct | wrong | silent failure w/o harness | silent failure with harness | caught "
         "(REJECT or FLAGGED / HOLD) | correct but not delivered (HOLD for review / REJECT / by design) |",
         "|---|---|---|---|---|---|---|---|"]
    rows = [("overall", s["overall"]), *[(f"family: {k}", v) for k, v in s["by_family"].items()],
            *[(f"agents: {k}", v) for k, v in s["by_kind"].items()]]
    for name, a in rows:
        L.append(f"| {name} | {a['n']} | {a['correct']} | {a['wrong']} | {a['silent_failure_without_harness']} | "
                 f"{a['silent_failure_with_harness']} | {a['caught']} ({a['caught_reject']} / {a['caught_hold']}) | "
                 f"{a['false_block']} ({a['false_block_hold_for_review']} / {a['false_block_reject']} / "
                 f"{a['correct_withheld_by_design']}) |")
    o = s["overall"]
    L += ["", f"Without the harness every wrong claim is delivered as success "
              f"({o['silent_failure_without_harness']}/{o['wrong']} wrong items). With it, "
              f"{o['silent_failure_with_harness']} wrong item(s) were delivered as trusted"
              + (f" (illustrative rule-of-three bound {o['rule_of_three_upper_95_delivered_of_wrong']:.2f} if these "
                 f"were a random sample, which they are not)" if o["rule_of_three_upper_95_delivered_of_wrong"] else "")
              + ".", "",
          "## Items", "",
          "| task | agent | mistake | claim | correct | truth | harness | delivered | class | blocking checks |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for i in r["items"]:
        mistake = i["mistake"] if i["kind"] == "naive" else "-"
        blocking = ", ".join(i["harness"].get("blocking", [])[:4]).replace("|", "/")
        L.append(f"| {i['task_id']} | {i['agent']} | {mistake[:60]} | {_claim_text(i)} | {_fmt(i['correct'])} | "
                 f"{_truth_text(i)} | {i['verdict']} | {_fmt(i['delivered_as_trusted'])} | {i['class']} | "
                 f"{blocking or '-'} |")
    lucky = r.get("naive_correct_by_chance") or []
    if lucky:
        L += ["", "## Naive items that were right by chance", "",
              "Graded correct by the evaluator (the planted mistake did not change the number in this world); "
              "they are never counted as a harness failure. A block of such an item is a false block of a correct "
              "number reached by an unsound method.", "",
              "| task | agent | verdict | class |", "|---|---|---|---|"]
        L += [f"| {x['task_id']} | {x['agent']} | {x['verdict']} | {x['class']} |" for x in lucky]
    L += ["", "## Which checks caught each fault class", "", "| fault class | items | caught | checks (count) |",
          "|---|---|---|---|"]
    for name, e in r["catching_checks"].items():
        checks = ", ".join(f"{k} ({v})" for k, v in sorted(e["checks"].items()))
        L.append(f"| {name} | {e['items']} | {e['caught']} | {checks or '-'} |")
    L += ["", "## Truth sources (evaluator side only)", "", "| task | truth | independent of the harness's oracle |",
          "|---|---|---|"]
    for t in r["tasks"].values():
        L.append(f"| {t['task_id']} {t['title']} | {t['truth_source']} | {_fmt(t['independent_of_harness'])} |")
    L += ["", "## Reading the numbers", "",
          "- Agents act only through the governed tools (the functions the MCP server exposes). In the decision "
          "task the controller registers two targeting policies (churn-risk top-k, uplift-value top-k), validates "
          "them offline on the randomised 2026 program and runs ONE pre-registered July experiment for the "
          "policies the gate cleared when the agent asks: an agent cannot run or re-run an experiment, and the "
          "oracle never lets a customer into a second July experiment.",
          "- `correct` comes from the evaluator (custody truth tables, sealed labels, the experiment oracle's true "
          "effect, or -- for add-on units and CSAT, which have no custody table -- the eval-split verified queries "
          "executed by the evaluator on the certified build). A4's truth is the governed metric itself, so it "
          "cannot reveal an error in the metric definition.",
          "- `delivered` = harness ALLOW (analytics: tier VERIFIED). Every scripted agent claims success, so "
          "'silent failure without harness' counts every wrong item.",
          "- 'HOLD for review' is the harness's cost on correct work: e.g. a correct quarterly CSAT answer has no "
          "governed metric or official record to reconcile with, so it is CONSISTENT and waits for a human.",
          "- 'by design': the careful decision agent reports its policy's randomised readout; when the readout is "
          "FAIL or INCONCLUSIVE (or the policy never cleared offline validation) its own recommendation is not to "
          "scale, and model_scale correctly refuses (scaling needs that policy's own PASSing readout). That is "
          "agreement, not a block of correct work.",
          "- A naive variant can be numerically right by chance in a given world (its planted mistake did not move "
          "the number that month). Such items are graded correct and listed above; the harness's claims are about "
          "wrong items: none delivered, all caught.",
          "- The data-engineering grade rebuilds the proposed pipeline outside the harness, checks every complete "
          "month against custody truth, and plants a lossy re-export: a pipeline calibrated to the Finance close "
          "certifies it READY, which is why it is graded wrong even though its closed months match.",
          "- minutes_last_month_naive appears only inside naive_all_leaky: late usage rows carry no label "
          "information in this world, so an AUC claim built on it alone need not be wrong. The harness still "
          "rejects it (time travel), which would be a false block for such a claim and a correct block for "
          "point-in-time reproducibility."]
    return "\n".join(L) + "\n"
