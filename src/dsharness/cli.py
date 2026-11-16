"""`dsh`: command line for the DS Harness reference implementation.

    dsh world [--small] [--force]                 synthetic world: source exports + custody truth
    dsh build --cutoff C | --all [--pipeline DIR]  governed lake builds (exit 0 only if all READY)
    dsh bench [--agents ...] [--families ...]      scripted benchmark -> runs/bench/<run_id>/report.{json,md}
    dsh demo                                       end-to-end scenario -> runs/demo/summary.json
    dsh mcp --role R [--cutoff C]                  MCP server (stdio) for one role
    dsh gate --stage S --subject-json F            policy step for CI: exit 0 ALLOW, 3 HOLD, 4 REJECT
    dsh pipeline-check --changed DIR --rationale R CI checks of a proposed pipeline dir (records evidence)
    dsh evidence export --out DIR                  registry -> in-toto statements (UNSIGNED-LOCAL)
    dsh roi [--profile cn|us] [--from-bench F]     portfolio ROI, optionally with measured overrides
    dsh live [--tasks ...] [--k K] [--model M]     optional real-agent run (SKIPPED if the CLI is unavailable)
    dsh readout SPEC... [--out F]                  re-check evaluation conclusions: exit 0 ALLOW, 3 HOLD, 4 REJECT

Every command takes --workspace (default: $DSH_WORKSPACE or ./workspace). Errors go to stderr with a
non-zero exit; a configuration problem is never reported as success.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .workspace import CUTOFFS, PIT_CUTOFFS, Workspace

EXIT_HOLD = 3
EXIT_REJECT = 4


def _ws(args) -> Workspace:
    return Workspace(Path(args.workspace).resolve()) if args.workspace else Workspace.default()


def _print(msg: str) -> None:
    print(msg, flush=True)


def _err(msg: str, code: int = 1) -> int:
    print(f"dsh: {msg}", file=sys.stderr)
    return code


# ============================================================================ commands
def cmd_world(args) -> int:
    from .lake.build import build_snapshot
    from .world.build import build_world
    from .world.generator import WorldConfig
    ws = _ws(args)
    if (ws.custody / "world_meta.json").exists() and not args.force:
        _print(f"world already exists in {ws.root} (use --force to regenerate it; that also rebuilds the "
               f"default-pipeline lake builds, which depend on the exports)")
        return 0
    cfg = WorldConfig(n_initial=700, monthly_signups=50, n_test_accounts=10) if args.small else None
    res = build_world(ws, cfg)
    _print(f"world written to {ws.root}: exports {sorted(res['exports'])}")
    _print(f"planted defects: {json.dumps(res['defects'])}")
    if args.force:
        for c in [*CUTOFFS.values(), *PIT_CUTOFFS]:
            rep = build_snapshot(ws, c)
            _print(f"rebuilt {rep.build_id}: {rep.status}")
    return 0


def cmd_build(args) -> int:
    from .lake.build import build_snapshot
    from .tools.context import resolve_cutoff
    ws = _ws(args)
    if args.all == bool(args.cutoff):
        return _err("give exactly one of --cutoff or --all", 2)
    cutoffs = [*CUTOFFS.values(), *PIT_CUTOFFS] if args.all else [resolve_cutoff(args.cutoff)]
    pipeline = Path(args.pipeline).resolve() if args.pipeline else None
    failed = []
    for c in cutoffs:
        if not (ws.source_dir(c) / "_export_manifest.json").exists():
            return _err(f"no source export for cutoff {c} in {ws.sources} (run `dsh world` first)")
        rep = build_snapshot(ws, c, pipeline)
        _print(f"{rep.build_id}: {rep.status}" + (f"  reasons: {rep.reasons[:4]}" if rep.reasons else ""))
        if rep.status != "READY":
            failed.append(rep.build_id)
    if failed:
        _print(f"{len(failed)} build(s) FAILED: {failed}. With the default pipeline the drift/monitor builds fail "
               f"by design (billing schema v2, D8) until a pipeline change passes CI.")
        return 1
    return 0


def cmd_bench(args) -> int:
    from .bench.run import run_bench
    ws = _ws(args)
    report = run_bench(ws, agents=args.agents or None, families=args.families or None,
                       log=None if args.quiet else _print)
    o = report["summary"]["overall"]
    _print(f"\nbench run {report['run_id']}: {o['n']} items, {o['correct']} correct, "
           f"silent failures without harness {o['silent_failure_without_harness']}, with harness "
           f"{o['silent_failure_with_harness']}, caught {o['caught']}, correct-but-not-delivered {o['false_block']} "
           f"({o['false_block_hold_for_review']} HOLD for review, {o['correct_withheld_by_design']} by design)")
    _print(f"report: {Path(report['run_dir']) / 'report.md'}")
    if args.strict:
        careful_wrong = [i for i in report["items"] if i["kind"] == "careful" and not i["correct"]]
        if o["silent_failure_with_harness"] or careful_wrong:
            return _err(f"strict: {o['silent_failure_with_harness']} silent failure(s) with the harness, "
                        f"{len(careful_wrong)} careful item(s) wrong")
    return 0


def cmd_demo(args) -> int:
    from .demo import run_demo
    summary = run_demo(_ws(args), log=_print)
    _print(f"\nsummary: {summary['summary_path']}")
    return 0


def cmd_mcp(args) -> int:
    from . import mcp_server
    argv = ["--role", args.role]
    for flag, value in (("--cutoff", args.cutoff), ("--workspace", args.workspace), ("--principal", args.principal),
                        ("--task-id", args.task_id)):
        if value:
            argv += [flag, value]
    return mcp_server.main(argv)


def _load_subject(spec: str) -> dict:
    if spec == "-":
        text = sys.stdin.read()
    elif spec.lstrip().startswith("{"):
        text = spec
    else:
        text = Path(spec).read_text(encoding="utf-8")
    subject = json.loads(text)
    if not isinstance(subject, dict) or not subject:
        raise ValueError("the subject must be a non-empty JSON object")
    return subject


def cmd_gate(args) -> int:
    from .evidence import decide
    try:
        subject = _load_subject(args.subject_json)
    except (OSError, ValueError) as exc:
        return _err(f"cannot read subject: {exc}", 2)
    decision = decide(_ws(args), args.stage, subject, requested_by=args.requested_by,
                      policy_path=args.policy)
    print(json.dumps(decision.to_dict(), indent=2))
    return {"ALLOW": 0, "HOLD": EXIT_HOLD}.get(decision.verdict, EXIT_REJECT)


def cmd_pipeline_check(args) -> int:
    """Run the pipeline_merge CI checks for a proposed pipeline directory (e.g. a pull request's
    checkout) against the governed pipeline. Evidence goes to the workspace registry (producer
    ci:pipeline); exit 0 means the checks ran, whatever the verdict -- `dsh gate` enforces it."""
    from .common import canonical_json
    from .tools import make_context, propose_pipeline_change
    from .tools.de import changed_pipeline_files
    ws = _ws(args)
    try:
        files = changed_pipeline_files(Path(args.changed).resolve())
    except (OSError, ValueError) as exc:
        return _err(f"cannot read the proposed pipeline: {exc}", 2)
    try:
        ctx = make_context(ws, "score", "data_engineer", args.principal)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        return _err(f"cannot start the checks: {exc} (run `dsh world` and `dsh build --cutoff score` first)", 2)
    try:
        res = propose_pipeline_change(ctx, files, args.rationale)
    finally:
        ctx.close()
    if "error" in res:
        print(json.dumps(res, indent=2, default=str), file=sys.stderr)
        return _err(f"{res['kind']}: {res['error']}", EXIT_REJECT if res["kind"] == "refused" else 2)
    if args.subject_out:     # canonical bytes: sha256 of the file == the subject digest the evidence binds to
        Path(args.subject_out).write_bytes(canonical_json(res["subject"]))
    summary = {"change_id": res["change_id"], "files": sorted(files), "subject": res["subject"],
               "checks": {"pipeline.ci_build": res["ci"]["outcome"], "pipeline.regression_diff": res["diff"]["outcome"],
                          "pipeline.oracle_independence": res["ci"]["oracle_independence"]["outcome"]},
               "ci_reasons": res["ci"]["reasons"], "evidence_ids": res["evidence_ids"],
               "gate_preview": {"verdict": res["gate"]["verdict"], "reasons": res["gate"]["reasons"]}}
    print(json.dumps(summary, indent=2, default=str))
    return 0


def cmd_evidence(args) -> int:
    from .evidence.attest import AttestError, export_registry
    try:
        res = export_registry(_ws(args), Path(args.out))
    except AttestError as exc:
        return _err(str(exc), 2)
    _print(f"wrote {res['n_statements']} in-toto statements (UNSIGNED-LOCAL) to {res['out_dir']}: {res['counts']}")
    if res["problems"]:
        for problem in res["problems"][:20]:
            print(f"dsh: not exported: {problem}", file=sys.stderr)
        return _err(f"{len(res['problems'])} registry line(s) could not be exported faithfully")
    return 0


def cmd_roi(args) -> int:
    import pandas as pd
    from .economics.roi import Assumptions, assumptions_table, portfolio_estimate
    a = Assumptions.for_profile(args.profile)
    measured = None
    if args.from_bench:
        try:
            rep = json.loads(Path(args.from_bench).read_text())
        except (OSError, ValueError) as exc:
            return _err(f"cannot read bench report {args.from_bench}: {exc}", 2)
        block = (rep.get("roi_measured") if isinstance(rep, dict) else None) or {}
        cond = block.get("conditional_rates")
        if not cond:
            return _err(f"{args.from_bench} has no roi_measured.conditional_rates block (a scripted bench report)")
        measured = with_harness_rates(a, cond)
        _print(f"harness behaviour from {args.from_bench} ({block.get('basis')}):")
        for uc, m in measured.items():
            r = cond[uc]
            _print(f"  {uc}: P(delivered|wrong)={r['delivered_of_wrong']:.2f} "
                   f"P(caught|wrong)={r['caught_of_wrong']:.2f} P(blocked|correct)={r['blocked_of_correct']:.2f} "
                   f"-> with an ASSUMED base error rate {m['_base']:.2f}: silent_failure_rate "
                   f"{m['silent_failure_rate']:.3f}, flag_rate {m['flag_rate']:.3f}")
            del m["_base"]
        _print("The base error rate stays an assumption: scripted agents fix the wrong/correct mix by construction.")
    df = portfolio_estimate(a, measured)
    cols = ["use_case", "handled_per_month", "gross_value", "llm_cost", "review_cost", "platform_cost",
            "error_cost", "net_value", "payback_months", "silent_failure_rate", "measured_fields"]
    with pd.option_context("display.width", 200, "display.max_columns", 20, "display.float_format", "{:,.2f}".format):
        _print(f"profile {a.profile} ({a.currency}), loaded cost per hour {a.loaded_cost_per_hour:,.1f}")
        _print(df[cols].to_string(index=False))
        if args.assumptions:
            _print(assumptions_table(a).to_string(index=False))
    _print("Every input not listed under measured_fields is an ASSUMPTION (see `dsh roi --assumptions`).")
    return 0


def with_harness_rates(a, cond: dict) -> dict:
    """Per-use-case overrides: the assumption's silent_failure_rate is read as the base rate of wrong
    work claimed as success; the bench's conditional rates say what the harness does with it."""
    out = {}
    for uc in a.use_cases:
        r = cond.get(uc.name)
        if not r or None in (r.get("delivered_of_wrong"), r.get("caught_of_wrong"), r.get("blocked_of_correct")):
            continue
        base = uc.silent_failure_rate
        out[uc.name] = {"_base": base, "silent_failure_rate": base * r["delivered_of_wrong"],
                        "flag_rate": base * r["caught_of_wrong"] + (1 - base) * r["blocked_of_correct"]}
    return out


def cmd_live(args) -> int:
    from .bench.live import LiveConfig, run_live
    cfg = LiveConfig(model=args.model, budget_usd=args.budget_usd, k=args.k, tasks=tuple(args.tasks),
                     cli=args.cli, command_template=args.command_template, timeout_s=args.timeout_s,
                     json_schema=args.json_schema, bare=args.bare)
    report = run_live(_ws(args), cfg, log=_print)
    s = report.get("summary") or {}
    _print(f"live run {report['run_id']}: {report['status']}"
           + (f" ({report['reason']})" if report.get("reason") else "")
           + (f"; {s.get('correct')}/{s.get('completed')} correct, pass@1 {s.get('pass_at_k_mean', {}).get(1)}, "
              f"cost per correct ${s.get('cost_per_correct_usd')}" if s.get("completed") else ""))
    return 0


def cmd_readout(args) -> int:
    from .readout import ReadoutError, Spec, decide, render
    decisions = []
    for path in args.specs:
        try:
            d = decide(Spec.load(Path(path)))
        except (OSError, ValueError, ReadoutError) as exc:
            return _err(f"{path}: {exc}", 2)
        decisions.append(d)
        _print(render(d))
    if args.out:
        Path(args.out).write_text(json.dumps(decisions, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    verdicts = {d["verdict"] for d in decisions}
    return EXIT_REJECT if "REJECT" in verdicts else EXIT_HOLD if "HOLD" in verdicts else 0


# ============================================================================ parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dsh", description="DS Harness reference implementation",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--workspace", default=None, help="workspace root (default $DSH_WORKSPACE or ./workspace)")
        sp.set_defaults(fn=fn)
        return sp

    sp = add("world", cmd_world, "generate the synthetic world (source exports + custody)")
    sp.add_argument("--small", action="store_true", help="small world (700 initial customers) for quick runs")
    sp.add_argument("--force", action="store_true", help="regenerate an existing world and rebuild its lake")

    sp = add("build", cmd_build, "build governed lake snapshots")
    sp.add_argument("--cutoff", help="train | score | drift | monitor | YYYY-MM-DD")
    sp.add_argument("--all", action="store_true", help="every cutoff (incl. point-in-time snapshots)")
    sp.add_argument("--pipeline", help="pipeline directory (default: the governed pipeline)")

    sp = add("bench", cmd_bench, "run the scripted benchmark")
    sp.add_argument("--agents", nargs="*", help="agent names, e.g. careful naive_raw_sql (default: all)")
    sp.add_argument("--families", nargs="*", help="analytics de ml decision (default: all)")
    sp.add_argument("--quiet", action="store_true")
    sp.add_argument("--strict", action="store_true",
                    help="exit 1 if any wrong item is delivered as trusted or a careful item is wrong")

    add("demo", cmd_demo, "run the end-to-end scenario")

    sp = add("mcp", cmd_mcp, "serve one role's governed tools over MCP (stdio)")
    sp.add_argument("--role", required=True, choices=["analyst", "data_scientist", "data_engineer"])
    sp.add_argument("--cutoff", default=None)
    sp.add_argument("--principal", default=None)
    sp.add_argument("--task-id", default=None)

    sp = add("gate", cmd_gate, "evaluate a promotion gate (CI policy step)")
    sp.add_argument("--stage", required=True)
    sp.add_argument("--subject-json", required=True, help="path to a JSON file, '-' for stdin, or inline JSON")
    sp.add_argument("--requested-by", default="ci:gate-request",
                    help="identity asking for promotion (self-approval and self-evidence are refused)")
    sp.add_argument("--policy", default=None, help="policy YAML (default: the packaged gates.yaml)")

    sp = add("pipeline-check", cmd_pipeline_check, "run pipeline_merge CI checks for a proposed pipeline directory")
    sp.add_argument("--changed", required=True,
                    help="proposed pipeline directory (schema.yml + models/*.sql), e.g. a PR checkout's pipeline")
    sp.add_argument("--rationale", required=True, help="why the change is needed (recorded with the change)")
    sp.add_argument("--principal", default="human:cli",
                    help="who proposes the change (not a producer identity such as ci:*)")
    sp.add_argument("--subject-out", default=None,
                    help="write the gated subject as canonical JSON (its sha256 is the subject digest)")

    sp = add("evidence", cmd_evidence, "export the registry as in-toto statements")
    sp.add_argument("action", choices=["export"])
    sp.add_argument("--out", required=True, help="output directory (not inside registry/ or custody/)")

    sp = add("roi", cmd_roi, "portfolio ROI estimate")
    sp.add_argument("--profile", choices=["cn", "us"], default="cn")
    sp.add_argument("--from-bench", default=None, help="bench report.json whose roi_measured block overrides inputs")
    sp.add_argument("--assumptions", action="store_true", help="also print every input with its source")

    sp = add("live", cmd_live, "optional: grade a real agent CLI on the analytics tasks")
    sp.add_argument("--tasks", nargs="*", default=["A1", "A2", "A3", "A4", "A5", "A6"])
    sp.add_argument("--k", type=int, default=1, help="repetitions per task (pass@k, pass^k)")
    sp.add_argument("--model", default="sonnet")
    sp.add_argument("--budget-usd", type=float, default=1.0, help="max spend per attempt")
    sp.add_argument("--cli", default="claude")
    sp.add_argument("--command-template", default=None,
                    help="generic agent command; placeholders {prompt} {mcp_config} {mcp_command} {server} "
                         "{allowed_tools} {model} {budget_usd}")
    sp.add_argument("--timeout-s", type=int, default=900)
    sp.add_argument("--json-schema", action="store_true", help="pass --json-schema for the final answer (claude)")
    sp.add_argument("--bare", action="store_true", help="claude --bare (skips local hooks/CLAUDE.md; API-key auth)")

    sp = add("readout", cmd_readout, "re-check evaluation conclusions against their per-item result files")
    sp.add_argument("specs", nargs="+", help="readout spec YAML files (one claim each)")
    sp.add_argument("--out", default=None, help="write all decisions as JSON")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        return _err("interrupted", 130)
    except BrokenPipeError:            # e.g. `dsh roi | head`: the reader left; not an error of the command
        import os
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


if __name__ == "__main__":
    sys.exit(main())
