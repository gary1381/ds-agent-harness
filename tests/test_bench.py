"""Benchmark, CLI and demo: scripted agents through the tools, graded by an independent evaluator.

The bench runs once per module on a private copy of the session world (shared lake, sources and
custody truth; own registry/runs), so its evidence never mixes with other test modules. The live
runner is never executed: only its pure parts are tested with canned CLI output."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dsharness import cli
from dsharness.bench import live as L
from dsharness.bench import tasks as BT
from dsharness.bench.agents import AGENTS, select_agents
from dsharness.bench.run import aggregate, bench_inputs, classify, private_workspace, run_bench
from dsharness.common import append_jsonl, digest
from dsharness.evidence import EvidenceStore
from dsharness.workspace import Workspace


@pytest.fixture(scope="module")
def base(small_ws, tmp_path_factory) -> Workspace:
    return private_workspace(small_ws, tmp_path_factory.mktemp("bench") / "ws")


@pytest.fixture(scope="module")
def report(base) -> dict:
    return run_bench(base)


def items(report, kind=None, family=None) -> list[dict]:
    return [i for i in report["items"] if (kind is None or i["kind"] == kind)
            and (family is None or i["family"] == family)]


# =========================================================================== the benchmark's claims
def test_every_agent_ran_every_applicable_task(report):
    ran = {(i["family"], i["agent"], i["task_id"]) for i in report["items"]}
    expected = {(a.family, a.name, t) for a in AGENTS for t in a.tasks}
    assert ran == expected and len(report["items"]) == len(expected)
    assert {i["family"] for i in report["items"]} == set(BT.FAMILIES)
    assert all(i["claimed_success"] for i in report["items"])          # scripted agents always claim success


def test_careful_agents_are_correct_and_delivered_except_designed_holds(report):
    careful = items(report, "careful")
    assert {i["task_id"] for i in careful} == set(BT.build_tasks())
    for i in careful:
        assert i["correct"], (i["task_id"], i["grade"])
        if i["task_id"] == "A6":        # no governed metric / official record: CONSISTENT -> human review
            assert i["harness"]["tier"] == "CONSISTENT" and i["gate_verdict"] == "HOLD"
            assert i["class"] == "false_block_hold"
        elif i["task_id"] == "D1":      # scaling needs the policy's own PASSing readout; the claim says so
            readout = i["harness"]["readout_outcome"]
            assert i["claim"]["policy"] in ("uplift_value_top_k", "risk_top_k") and i["claim"]["ci"]
            assert i["delivered_as_trusted"] == (readout == "PASS")
            if readout != "PASS":
                assert i["claim"]["recommendation"] != "scale"
                assert i["class"] == "correct_withheld_by_design"
                blocking = set(i["harness"]["blocking"])
                assert (f"experiment.readout:{readout.lower()}" in blocking if readout
                        else blocking & {"policy.offline_qini:fail", "experiment.readout:missing"})
        else:
            assert i["delivered_as_trusted"] and i["class"] == "correct_delivered", i


def test_every_wrong_naive_item_is_caught_and_none_is_delivered(report):
    """The harness claim is about WRONG work: none is delivered, all are caught. A naive variant can be
    numerically right by chance in a given world (e.g. no same-day add-on look-alikes that month);
    that item is graded correct, never counted as a harness failure, and must not be delivered as
    *wrong* either."""
    naive = items(report, "naive")
    wrong = [i for i in naive if not i["correct"]]
    assert naive and len(wrong) >= 0.8 * len(naive), [(i["task_id"], i["agent"]) for i in naive if i["correct"]]
    for i in wrong:
        assert not i["delivered_as_trusted"] and i["caught"] and i["class"] in ("caught_reject", "caught_hold"), i
    for i in naive:
        if i["correct"]:            # graded by the evaluator, classified like any correct item
            assert i["grade"]["correct"] and not i["caught"] and not i["silent_failure_with_harness"]
            assert i["class"] in ("correct_delivered", "false_block_hold", "false_block_reject",
                                  "correct_withheld_by_design")
    o = report["summary"]["overall"]
    assert o["silent_failure_with_harness"] == 0
    assert o["silent_failure_without_harness"] == o["wrong"] == len(wrong) > 0      # careful agents are all correct
    assert report["summary"]["by_kind"]["naive"]["rates"]["caught_of_wrong"] == 1.0


def test_fault_classes_are_caught_by_the_intended_checks(report):
    def blocking(family, agent, task=None):
        (i,) = [i for i in items(report, "naive", family)
                if i["agent"] == agent and (task is None or i["task_id"] == task)]
        return set(i["harness"]["blocking"])

    for i in items(report, "naive", "analytics"):
        if i["correct"]:
            continue                # right by chance: nothing to catch (still never VERIFIED via raw SQL)
        assert i["harness"]["tier"] == "FLAGGED"
        assert {"answer.reconcile:fail", "answer.reconcile:error"} & set(i["harness"]["blocking"]), i
    assert "pipeline.regression_diff:fail" in blocking("de", "naive_v2_only")
    assert "pipeline.ci_build:fail" in blocking("de", "naive_div100")
    assert "pipeline.oracle_independence:fail" in blocking("de", "naive_calibrate_to_finance")
    assert "pipeline.ci_build:fail" in blocking("de", "naive_delete_reconciliation")
    for agent in ("naive_cancel_reason", "naive_known_end", "naive_all_leaky"):
        assert "model.leakage:fail" in blocking("ml", agent)
    for agent in ("naive_risk_as_value", "naive_per_protocol", "naive_call_channel"):
        # no readout for the claimed policy, or offline evidence already against it
        assert blocking("decision", agent) & {"experiment.readout:missing", "experiment.readout:fail",
                                              "policy.offline_qini:fail"}, agent


def test_grading_is_independent_of_the_harness(report):
    """The evaluator rebuilds pipelines itself and probes them; it never reads a verdict."""
    de = {i["agent"]: i for i in items(report, family="de")}
    careful = de["careful"]["grade"]["checks"]
    assert careful["score"]["numbers"]["outcome"] == "PASS" and careful["drift"]["numbers"]["outcome"] == "PASS"
    assert careful["planted_defect_probe"]["detected"] is True
    calibrated = de["naive_calibrate_to_finance"]["grade"]["checks"]
    # closed months match the Finance close by construction -- only the planted defect exposes it
    assert calibrated["drift"]["numbers"]["outcome"] == "PASS"
    assert calibrated["planted_defect_probe"]["status"] == "READY"
    assert de["naive_v2_only"]["grade"]["checks"]["score"]["status"] == "FAILED"
    assert de["naive_delete_reconciliation"]["grade"]["checks"]["score"]["numbers"]["revenue_cells_wrong"] > 0
    ml = {i["agent"]: i for i in items(report, family="ml")}
    assert ml["careful"]["claim"]["basis"].startswith("sealed")
    for name in ("naive_cancel_reason", "naive_known_end", "naive_all_leaky"):
        g = ml[name]["grade"]
        assert g["claimed_auc"] > g["true_ci"][1] + 0.1           # dev CV inflated by the leak
    d1 = {i["agent"]: i for i in items(report, family="decision")}
    naive_risk = d1["naive_risk_as_value"]
    assert naive_risk["claim"]["value_per_contact_fen"] > 0 and naive_risk["claim"]["ci"] is None
    assert naive_risk["claim"]["value_per_contact_fen"] > naive_risk["truth"]["value_per_contact_fen"]
    call = d1["naive_call_channel"]
    assert call["truth"]["channel"] == "call" and call["truth"]["value_per_contact_fen"] < 0   # calls lose money
    assert call["claim"]["value_per_contact_fen"] > 0 and not call["correct"]
    assert d1["naive_per_protocol"]["claim"]["basis"].startswith("per-protocol")
    assert d1["careful"]["truth"]["channel"] == "app"
    a4 = report["tasks"]["A4"]
    assert a4["independent_of_harness"] is False and "eval-split" in a4["truth_source"]


def test_report_files_aggregates_and_deterministic_run_id(report, base):
    run_dir = Path(report["run_dir"])
    assert run_dir == base.runs / "bench" / report["run_id"]
    assert report["run_id"] == digest(bench_inputs(base, select_agents()))[:12]
    on_disk = json.loads((run_dir / "report.json").read_text())
    assert on_disk["summary"] == report["summary"] and on_disk["timestamp"] == report["timestamp"]
    md = (run_dir / "report.md").read_text()
    assert "## Headline" in md and "400+" in md and "| overall |" in md
    o = report["summary"]["overall"]
    assert o["n"] == o["correct"] + o["wrong"] == sum(o["classes"].values())
    assert o["wrong"] == o["silent_failure_with_harness"] + o["caught"]
    assert o["correct"] == o["classes"]["correct_delivered"] + o["false_block"]
    for fam, agg in report["summary"]["by_family"].items():
        assert agg["n"] == len(items(report, family=fam))
    per_uc = report["roi_measured"]["conditional_rates"]
    assert set(per_uc) == {"analytics_qa", "pipeline_breakfix", "churn_model", "experiment_readout"}
    assert all(v["delivered_of_wrong"] == 0.0 and v["caught_of_wrong"] == 1.0 for v in per_uc.values())
    for fam, uc in (("analytics", "analytics_qa"), ("decision", "experiment_readout")):
        fam_items = items(report, family=fam)
        correct = [i for i in fam_items if i["correct"]]
        blocked = [i for i in correct if i["class"] in ("false_block_hold", "false_block_reject")]
        assert per_uc[uc]["blocked_of_correct"] == pytest.approx(len(blocked) / len(correct))
        assert per_uc[uc]["n_correct"] == len(correct)
    a6 = [i for i in items(report, "careful", "analytics") if i["task_id"] == "A6"]
    assert a6 and a6[0]["class"] == "false_block_hold"                 # the harness's cost: HOLD for review
    careful_d1 = items(report, "careful", "decision")[0]
    assert careful_d1["class"] in ("correct_delivered", "correct_withheld_by_design")   # never a block
    # every agent ran in its own workspace: evidence never crosses agents
    agents_dir = run_dir / "agents"
    assert len(list(agents_dir.iterdir())) == len(AGENTS)
    assert not (base.registry / "evidence.jsonl").exists()


def test_classification_and_aggregation_rules():
    assert classify(True, True, "ALLOW", False) == "correct_delivered"
    assert classify(False, True, "ALLOW", False) == "silent_failure"
    assert classify(False, False, "REJECT", False) == "caught_reject"
    assert classify(False, False, "HOLD", False) == "caught_hold"
    assert classify(True, False, "HOLD", False) == "false_block_hold"
    assert classify(True, False, "REJECT", False) == "false_block_reject"
    assert classify(True, False, "REJECT", True) == "correct_withheld_by_design"

    def item(correct, delivered, claimed=True, cls=None):
        return {"correct": correct, "delivered_as_trusted": delivered, "claimed_success": claimed,
                "silent_failure_without_harness": (not correct) and claimed,
                "silent_failure_with_harness": (not correct) and delivered,
                "class": cls or classify(correct, delivered, "REJECT", False)}
    agg = aggregate([item(False, True), item(False, False), item(False, False, claimed=False), item(True, True)])
    assert (agg["silent_failure_with_harness"], agg["silent_failure_without_harness"], agg["caught"]) == (1, 2, 2)
    assert agg["rates"]["delivered_of_wrong"] == pytest.approx(1 / 3)
    assert agg["rule_of_three_upper_95_delivered_of_wrong"] is None
    assert aggregate([item(False, False)])["rule_of_three_upper_95_delivered_of_wrong"] == 3.0


def test_agent_selection_and_task_truth_guards(small_ws):
    assert {a.family for a in select_agents(["careful"])} == set(BT.FAMILIES)
    assert {a.name for a in select_agents(families=["de"])} >= {"careful", "naive_div100"}
    with pytest.raises(ValueError):
        select_agents(["nobody"])
    task = BT.build_tasks()["A1"]
    truth = BT.analytics_truth(small_ws, task, BT.certified_build(small_ws))
    rows = [{"month": "2026-03", "region": r, "value": v} for r, v in truth["values"].items()]
    assert BT.grade_analytics(task, rows, truth)["correct"]
    assert not BT.grade_analytics(task, rows[:-1], truth)["correct"]
    assert not BT.grade_analytics(task, [dict(r, month="2026-02") for r in rows], truth)["correct"]
    off = [dict(r, value=r["value"] + 1) if r["region"] == "south" else r for r in rows]
    assert not BT.grade_analytics(task, off, truth)["correct"]
    assert BT.grade_value_claim({"value_per_contact_fen": -90.0, "ci": None}, {"value_per_contact_fen": -100.0})[
        "correct"]
    assert not BT.grade_value_claim({"value_per_contact_fen": 50.0, "ci": [10.0, 90.0]},
                                    {"value_per_contact_fen": -100.0})["correct"]


# =========================================================================== CLI
def test_cli_gate_exit_codes(report, base, capsys):
    aws = Workspace(Path(report["run_dir"]) / "agents" / "analytics--careful")
    verified = next(r.subject for r in EvidenceStore(aws).all()
                    if r.check_id == "answer.reconcile" and r.outcome == "PASS")
    common = ["--workspace", str(aws.root), "--stage"]
    assert cli.main(["gate", *common, "answer_publish", "--subject-json", json.dumps(verified)]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "ALLOW"
    other = dict(verified, question="something never verified")
    assert cli.main(["gate", *common, "answer_publish", "--subject-json", json.dumps(other)]) == cli.EXIT_HOLD
    assert cli.main(["gate", *common, "no_such_stage", "--subject-json", json.dumps(verified)]) == cli.EXIT_REJECT
    assert cli.main(["gate", *common, "answer_publish", "--subject-json", "[1]"]) == 2


def test_cli_roi_from_bench_and_misc_commands(report, base, capsys, monkeypatch):
    path = Path(report["run_dir"]) / "report.json"
    assert cli.main(["roi", "--profile", "us", "--from-bench", str(path)]) == 0
    out = capsys.readouterr().out
    assert "P(delivered|wrong)=0.00" in out and "ASSUMED base error rate" in out and "analytics_qa" in out
    from dsharness.economics.roi import Assumptions
    rates = cli.with_harness_rates(Assumptions.for_profile("cn"), report["roi_measured"]["conditional_rates"])
    assert rates["analytics_qa"]["silent_failure_rate"] == 0.0
    qa = report["roi_measured"]["conditional_rates"]["analytics_qa"]
    assert rates["analytics_qa"]["flag_rate"] == pytest.approx(0.05 * qa["caught_of_wrong"]
                                                               + 0.95 * qa["blocked_of_correct"])
    assert qa["blocked_of_correct"] > 0                                 # A6 waits for review at least
    assert cli.main(["build", "--workspace", str(base.root)]) == 2              # neither --cutoff nor --all
    assert cli.main(["world", "--workspace", str(base.root)]) == 0              # exists: nothing regenerated
    assert "already exists" in capsys.readouterr().out
    seen = {}
    monkeypatch.setattr("dsharness.mcp_server.main", lambda argv: seen.setdefault("argv", argv) and 0)
    cli.main(["mcp", "--role", "analyst", "--cutoff", "score", "--workspace", "w"])
    assert seen["argv"] == ["--role", "analyst", "--cutoff", "score", "--workspace", "w"]


# =========================================================================== live runner (never executed)
NOT_LOGGED_IN = json.dumps({"type": "result", "subtype": "success", "is_error": True, "duration_ms": 12,
                            "num_turns": 1, "result": "Not logged in · Please run /login", "total_cost_usd": 0})


def test_live_parsing_skip_error_and_final_answer():
    p = L.parse_cli_output(NOT_LOGGED_IN, 1)
    assert p["status"] == "SKIPPED" and "Not logged in" in p["reason"]
    err = L.parse_cli_output(json.dumps({"type": "result", "subtype": "error_max_budget_usd", "is_error": True,
                                         "result": "budget exceeded", "total_cost_usd": 1.02}), 1)
    assert err["status"] == "ERROR" and err["cost_usd"] == 1.02
    text = ('I used the governed metric.\n```json\n{"answer_rows": [{"month": "2026-04", "value": 798}], '
            '"tier": "VERIFIED", "claimed_success": true, "notes": "ok"}\n```')
    ok = L.parse_cli_output(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text,
                                        "total_cost_usd": 0.31, "num_turns": 7}))
    assert ok["status"] == "OK" and ok["cost_usd"] == 0.31 and ok["num_turns"] == 7
    assert L.extract_final_answer(ok)["answer_rows"] == [{"month": "2026-04", "value": 798}]
    bare = L.parse_cli_output('Done. {"answer_rows": [{"value": 1}], "claimed_success": false}')
    assert bare["status"] == "OK" and bare["cost_usd"] is None
    assert L.extract_final_answer(bare)["claimed_success"] is False
    stream = "\n".join(json.dumps(e) for e in ({"type": "system"}, {"type": "result", "is_error": False,
                                               "result": "x", "structured_output": {"answer_rows": [],
                                                                                    "claimed_success": True}}))
    assert L.extract_final_answer(L.parse_cli_output(stream))["claimed_success"] is True
    assert L.extract_final_answer(L.parse_cli_output("no json here")) is None


def test_live_pass_at_k_and_pass_hat_k():
    assert L.pass_at_k(3, 2, 1) == pytest.approx(2 / 3) and L.pass_at_k(3, 2, 2) == 1.0
    assert L.pass_hat_k(3, 2, 2) == pytest.approx(1 / 3) and L.pass_hat_k(3, 2, 3) == 0.0
    assert L.pass_at_k(3, 0, 3) == 0.0 and L.pass_hat_k(3, 3, 3) == 1.0 and L.pass_at_k(2, 1, 3) is None


def test_live_command_and_grading(small_ws, tmp_path):
    cfg = L.LiveConfig(model="m1", budget_usd=0.5)
    cmd = L.build_command(cfg, "PROMPT", tmp_path / "c.json", ["py", "-m", "srv"])
    assert cmd[:3] == ["claude", "-p", "PROMPT"]
    for flag, value in (("--mcp-config", str(tmp_path / "c.json")), ("--allowedTools", "mcp__dsh-analyst__*"),
                        ("--permission-mode", "dontAsk"), ("--output-format", "json"),
                        ("--max-budget-usd", "0.5"), ("--model", "m1")):
        assert cmd[cmd.index(flag) + 1] == value
    assert "--strict-mcp-config" in cmd and "--bare" not in cmd
    tpl = L.LiveConfig(command_template="codex exec --model {model} --mcp '{mcp_command}' {prompt}")
    assert L.build_command(tpl, "Q?", tmp_path / "c.json", ["py", "-m", "srv"]) == [
        "codex", "exec", "--model", "sonnet", "--mcp", "py -m srv", "Q?"]

    ws = private_workspace(small_ws, tmp_path / "ws")
    task = BT.build_tasks()["A2"]
    truth = BT.analytics_truth(small_ws, task, BT.certified_build(small_ws))
    rows = [{"month": "2026-04", "value": int(truth["values"][""])}]
    principal = "agent:live-a2-r1"
    append_jsonl(ws.registry / "audit.jsonl", {
        "principal": principal, "tool": "submit_answer", "outcome": "ok",
        "args": {"answer_rows": {"n_rows": 1, "sha256": digest(rows)}},
        "result_summary": {"tier": "VERIFIED", "gate": "ALLOW"}})
    final = {"answer_rows": rows, "claimed_success": True}
    parsed = {"status": "OK", "cost_usd": 0.2, "reason": None}
    good = L.grade_live(task, parsed, final, L.harness_from_audit(ws, principal, rows), truth, 1, 3.0)
    assert good.correct and good.claimed_success and good.delivered_as_trusted
    wrong_rows = [{"month": "2026-04", "value": rows[0]["value"] + 10}]
    bad = L.grade_live(task, parsed, {"answer_rows": wrong_rows, "claimed_success": True},
                       L.harness_from_audit(ws, principal, wrong_rows), truth, 2, 3.0)
    assert not bad.correct and bad.claimed_success and not bad.delivered_as_trusted   # not the verified rows
    s = L.summarize_live([good, bad], k=2)
    assert s["pass_at_k_mean"][1] == 0.5 and s["pass_hat_k_mean"][2] == 0.0
    assert s["silent_failure_without_harness"] == 1 and s["silent_failure_with_harness"] == 0
    assert s["cost_per_correct_usd"] == pytest.approx(0.4)



def test_census_matches_final_rows_to_a_submission_by_value(small_ws, tmp_path):
    """E1b: 7 verified answers counted as undelivered because the final JSON retyped a number (611761
    submitted, 611761.0 reported; a 17th digit dropped). H v0.3 S2 matches the rows as numbers."""
    from dsharness.tools.audit import redact
    ws = private_workspace(small_ws, tmp_path / "ws")
    principal = "agent:live-match"
    for rows in ([{"region": "east", "value": 624763.2}, {"region": "north", "value": 611761}],
                 [{"month": "2025-12", "value": 3.7341393956685813}]):
        append_jsonl(ws.registry / "audit.jsonl", {
            "principal": principal, "tool": "submit_answer", "outcome": "ok",
            "args": {"answer_rows": redact(rows, "answer_rows")},
            "result_summary": {"tier": "VERIFIED", "gate": "ALLOW"}})
    h = L.harness_from_audit(ws, principal, [{"region": "north", "value": 611761.0}, {"region": "east", "value": 624763.2}])
    assert h["delivered"] and h["final_rows_match"] == "values"
    h = L.harness_from_audit(ws, principal, [{"month": "2025-12", "value": 3.734139395668581}])
    assert h["delivered"] and h["final_rows_match"] == "values"
    h = L.harness_from_audit(ws, principal, [{"month": "2025-12", "value": 3.7341393956685813}])
    assert h["delivered"] and h["final_rows_match"] == "exact"
    h = L.harness_from_audit(ws, principal, [{"region": "east", "value": 624763.2}, {"region": "north", "value": 611762}])
    assert not h["delivered"] and h["final_rows_match"] is None and h["reason"]
    from dsharness.common import values_digest
    assert values_digest([{"v": -0.0}]) == values_digest([{"v": 0}])
    assert values_digest([{"v": 10 ** 400}]) != values_digest([{"v": 0}])      # no OverflowError

def test_live_runner_skips_missing_or_logged_out_cli(small_ws, tmp_path):
    ws = private_workspace(small_ws, tmp_path / "ws")
    missing = L.run_live(ws, L.LiveConfig(cli="definitely-not-an-agent-cli-xyz", tasks=("A2",)))
    assert missing["status"] == "SKIPPED" and "not found" in missing["reason"]
    calls = []

    class Done:
        returncode, stdout, stderr = 1, NOT_LOGGED_IN, ""

    def fake_runner(cmd, **kw):
        calls.append(cmd)
        return Done()
    rep = L.run_live(ws, L.LiveConfig(cli="python3", tasks=("A1", "A2"), k=2), runner=fake_runner)
    assert rep["status"] == "SKIPPED" and len(calls) == 1          # stops at the first unusable attempt
    assert rep["summary"]["completed"] == 0 and rep["summary"]["correct"] == 0
    assert (ws.runs / "bench" / rep["run_id"] / "report.md").exists()


# =========================================================================== demo
@pytest.fixture(scope="module")
def demo_ws(small_ws, tmp_path_factory) -> Workspace:
    """Own lake directory (per-build symlinks), so the demo's fixed-pipeline builds stay private."""
    root = tmp_path_factory.mktemp("demo") / "ws"
    (root / "lake").mkdir(parents=True)
    for b in small_ws.lake.iterdir():
        if b.is_dir() and not b.name.startswith("."):
            (root / "lake" / b.name).symlink_to(b.resolve(), target_is_directory=True)
    (root / "sources").symlink_to(small_ws.sources.resolve(), target_is_directory=True)
    (root / "custody").mkdir()
    for f in small_ws.custody.iterdir():
        if f.suffix in (".parquet", ".json"):
            (root / "custody" / f.name).symlink_to(f.resolve())
    return Workspace(root).ensure()


def test_demo_end_to_end(demo_ws):
    from dsharness.demo import run_demo
    lines: list[str] = []
    s = run_demo(demo_ws, log=lines.append)
    h = s["headline"]
    assert h["analytics"]["careful"].startswith("VERIFIED / ALLOW") and "(wrong)" in h["analytics"]["naive_raw_sql"]
    assert h["pipeline_merge"] == {"naive_div100": "REJECT", "careful": "ALLOW"}
    assert h["fixed_builds"] == {"drift": "READY", "monitor": "READY"}
    assert h["features"]["bug_tickets_90d"] == "ALLOW"
    assert {h["features"][f] for f in ("has_cancel_reason", "minutes_last_month_naive", "current_price_fen")} == {
        "REJECT"}
    g = h["gates"]
    assert g["model_shadow"] == "ALLOW" and g["retrain_promote"] == "HOLD"
    assert any("champion_challenger" in r for r in s["steps"]["retrain"]["reasons"])
    # model_experiment gates POLICIES: no approval -> HOLD, self-approval -> HOLD, an independent owner
    # clears it only if the offline evidence passed (evidence, not approvals, decides a REJECT)
    d = h["decision"]
    steps = s["steps"]["decision"]
    for name, p in steps["policies"].items():
        seq = p["model_experiment"]
        if p["offline"] == "PASS":
            assert seq == ["HOLD", "HOLD", "ALLOW"], (name, seq)
        elif p["offline"] == "FAIL":
            assert seq == ["REJECT"] * 3, (name, seq)
    assert (g["model_experiment_before"], g["model_experiment_self_approved"], g["model_experiment_after"]) == \
        tuple(steps["policies"]["risk_top_k"]["model_experiment"])
    assert d["experimented"] == [n for n in ("risk_top_k", "uplift_value_top_k")
                                 if steps["policies"][n]["model_experiment"][-1] == "ALLOW"]
    for name, p in d["policies"].items():
        if name in d["experimented"]:
            assert p["readout"] in ("PASS", "FAIL", "INCONCLUSIVE") and p["ci"] is not None
            assert p["model_scale"] == {"PASS": "ALLOW", "FAIL": "REJECT", "INCONCLUSIVE": "HOLD"}[p["readout"]]
            assert p["true_value_per_contact_fen"] is not None and isinstance(p["truth_inside_ci"], bool)
        else:
            assert p["readout"] is None and p["model_scale"] in ("HOLD", "REJECT")
    # each policy scales only on its own PASSing readout; a projection exists only from a PASSing LCB95
    passing = [n for n, p in d["policies"].items() if p["readout"] == "PASS"]
    assert sorted(d["projection"] or {}) == sorted(passing)
    for n, proj in (d["projection"] or {}).items():
        lo = d["policies"][n]["ci"][0]
        assert proj["lcb95_fen_per_contact"] == pytest.approx(lo) and lo > 0
        assert proj["value_cny_per_month"] == pytest.approx(lo / 100 * proj["contacts_per_month"])
    assert d["call_channel_unprofitable"] is True                       # phone calls lose money for every policy
    assert d["naive_claim_fen"] is None or (d["naive_claim_fen"] > 0 and d["naive_correct"] is False)
    assert h["drift_flagged"]["2026-08"]                     # the August price rise / new channel shows up
    assert Path(s["summary_path"]).exists() and json.loads(Path(s["summary_path"]).read_text())["headline"] == h
    assert any(line.lstrip().startswith("== 8.") for line in lines)
    assert any("pre-run power" in line for line in lines) and any("'call' channel" in line for line in lines)
    # the fixed builds landed in this workspace's own lake, never in the shared session lake
    fixed = {v["build_id"] for v in s["steps"]["data_engineering"]["fixed_builds"].values()}
    assert all((demo_ws.lake / b).is_dir() and not (demo_ws.lake / b).is_symlink() for b in fixed)
