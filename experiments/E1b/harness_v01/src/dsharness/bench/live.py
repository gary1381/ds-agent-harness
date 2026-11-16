"""Optional live-agent runner: a real CLI agent answers the analytics tasks through the analyst MCP
server, and the evaluator grades it exactly like a scripted agent.

Never run by the test suite: it spends money and needs a logged-in CLI. Tests cover only the pure
parts (command construction, output parsing, grading, pass@k / pass^k) with canned JSON.

Default agent: Claude Code in print mode, one process per attempt:

    claude -p <task prompt> --strict-mcp-config --mcp-config <cfg> --allowedTools "mcp__dsh-analyst__*"
           --permission-mode dontAsk --output-format json --max-budget-usd <b> --model <m>

``--command-template`` plugs in any other CLI (Codex, ...): a shell-style string whose tokens may
contain {prompt}, {mcp_config} (path of the JSON config), {mcp_command} (the server command line),
{server}, {allowed_tools}, {model}, {budget_usd}. If its stdout is not Claude-style JSON, the whole
stdout is taken as the agent's final text and the cost is unknown.

Isolation: every attempt gets a private workspace (own registry/audit, shared read-only lake), its
own MCP server process bound to the analyst role and a principal unique to the attempt, and runs in
an empty temporary directory with only the MCP tools allowed, so the agent cannot read the
repository or the custody zone. ``--bare`` additionally skips local hooks/CLAUDE.md (it requires
ANTHROPIC_API_KEY auth). The harness verdict is read back from the attempt's audit log: the tier
of the submit_answer call whose rows are exactly the rows of the agent's final answer.

A CLI that is missing or not logged in is reported as SKIPPED (exit 0), never as a failure of the
agent and never as a pass.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
import json
from math import comb
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from ..common import digest, read_jsonl, utcnow
from ..workspace import Workspace
from . import tasks as BT
from .run import prepare_workspace, private_workspace

SERVER = "dsh-analyst"
ALLOWED_TOOLS = f"mcp__{SERVER}__*"
DEFAULT_MODEL = "sonnet"
DEFAULT_BUDGET_USD = 1.0
DEFAULT_TIMEOUT_S = 900
_SKIP_PATTERNS = ("not logged in", "please run /login", "invalid api key", "authentication_error",
                  "oauth token has expired", "credit balance is too low")
FINAL_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answer_rows": {"type": "array", "items": {"type": "object"}},
                   "tier": {"type": "string"}, "claimed_success": {"type": "boolean"},
                   "notes": {"type": "string"}},
    "required": ["answer_rows", "claimed_success"]}
PROMPT = """You are a data analyst at a subscription company. Answer the business question below using
only the tools of the MCP server `{server}` (start with its instructions and tool descriptions).
Submit your final numbers with submit_answer before you reply.

Question: {question}

When you are done, reply with ONLY one JSON object, no prose:
{{"answer_rows": [<exactly the rows you submitted>], "tier": "<the tier submit_answer returned>",
  "claimed_success": <true if you stand behind the answer as correct, else false>, "notes": "<one sentence>"}}
"""


@dataclass
class LiveConfig:
    model: str = DEFAULT_MODEL
    budget_usd: float = DEFAULT_BUDGET_USD
    k: int = 1
    tasks: tuple[str, ...] = ("A1", "A2", "A3", "A4", "A5", "A6")
    cli: str = "claude"
    command_template: str | None = None
    timeout_s: int = DEFAULT_TIMEOUT_S
    json_schema: bool = False
    bare: bool = False

    def validate(self) -> None:
        if self.k < 1:
            raise ValueError("k must be >= 1")
        if not self.budget_usd > 0:
            raise ValueError("budget_usd must be positive")
        known = {t.task_id for t in BT.build_tasks().values() if t.family == "analytics"}
        bad = set(self.tasks) - known
        if bad or not self.tasks:
            raise ValueError(f"live tasks must be analytics tasks {sorted(known)}, got {list(self.tasks)}")


# ============================================================================ command
def mcp_server_args(ws: Workspace, principal: str, task_id: str) -> list[str]:
    return [sys.executable, "-m", "dsharness.mcp_server", "--role", "analyst", "--cutoff", "score",
            "--workspace", str(ws.root), "--principal", principal, "--task-id", task_id]


def mcp_config(ws: Workspace, principal: str, task_id: str) -> dict:
    args = mcp_server_args(ws, principal, task_id)
    return {"mcpServers": {SERVER: {"command": args[0], "args": args[1:]}}}


def build_command(cfg: LiveConfig, prompt: str, config_path: Path, server_cmd: list[str]) -> list[str]:
    if cfg.command_template:
        values = {"prompt": prompt, "mcp_config": str(config_path), "mcp_command": shlex.join(server_cmd),
                  "server": SERVER, "allowed_tools": ALLOWED_TOOLS, "model": cfg.model,
                  "budget_usd": f"{cfg.budget_usd:g}"}
        try:
            return [tok.format(**values) for tok in shlex.split(cfg.command_template)]
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError(f"bad command template {cfg.command_template!r}: {exc}") from exc
    cmd = [cfg.cli, "-p", prompt, "--strict-mcp-config", "--mcp-config", str(config_path),
           "--allowedTools", ALLOWED_TOOLS, "--permission-mode", "dontAsk", "--output-format", "json",
           "--max-budget-usd", f"{cfg.budget_usd:g}", "--model", cfg.model]
    if cfg.json_schema:
        cmd += ["--json-schema", json.dumps(FINAL_ANSWER_SCHEMA)]
    if cfg.bare:
        cmd.append("--bare")
    return cmd


# ============================================================================ parsing
def parse_cli_output(stdout: str, returncode: int = 0, stderr: str = "") -> dict:
    """{status: OK | SKIPPED | ERROR, text, structured, cost_usd, is_error, num_turns, reason}."""
    out = {"status": "OK", "text": None, "structured": None, "cost_usd": None, "is_error": False,
           "num_turns": None, "reason": None, "subtype": None}
    payload = None
    s = (stdout or "").strip()
    if s:
        try:
            payload = json.loads(s)
        except json.JSONDecodeError:
            lines = [ln for ln in s.splitlines() if ln.strip().startswith("{")]
            events = []
            for ln in lines:                       # stream-json: one event per line
                try:
                    events.append(json.loads(ln))
                except json.JSONDecodeError:
                    pass
            payload = events or None
    if isinstance(payload, list):
        results = [e for e in payload if isinstance(e, dict) and e.get("type") == "result"]
        payload = results[-1] if results else None
    if isinstance(payload, dict) and ("result" in payload or "is_error" in payload or "total_cost_usd" in payload):
        out["text"] = payload.get("result")
        out["structured"] = payload.get("structured_output")
        cost = payload.get("total_cost_usd")
        out["cost_usd"] = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
        out["is_error"] = bool(payload.get("is_error"))
        out["num_turns"] = payload.get("num_turns")
        out["subtype"] = payload.get("subtype")
    else:
        out["text"] = s or None
    blob = " ".join(str(x) for x in (out["text"], stderr) if x).lower()
    if any(p in blob for p in _SKIP_PATTERNS) and (out["is_error"] or returncode != 0 or not out["text"]
                                                   or len(str(out["text"])) < 200):
        out.update(status="SKIPPED", reason=f"agent CLI unavailable: {str(out['text'] or stderr).strip()[:200]}")
    elif out["is_error"] or returncode != 0:
        out.update(status="ERROR", reason=f"agent CLI error (exit {returncode}, subtype {out['subtype']}): "
                                          f"{str(out['text'] or stderr).strip()[:300]}")
    return out


_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_final_answer(parsed: dict) -> dict | None:
    """The agent's final JSON object: structured output if present, else the last JSON object in
    its reply (fenced or bare). None when there is no parseable object with answer_rows."""
    cands = []
    if isinstance(parsed.get("structured"), dict):
        cands.append(parsed["structured"])
    text = parsed.get("text") or ""
    if isinstance(text, str):
        cands += [m for m in _FENCE.findall(text)][::-1]
        dec = json.JSONDecoder()
        top: list = []             # top-level JSON objects in reading order (nested ones are skipped)
        i = text.find("{")
        while i >= 0:
            try:
                obj, end = dec.raw_decode(text, i)
            except json.JSONDecodeError:
                i = text.find("{", i + 1)
                continue
            top.append(obj)
            i = text.find("{", end)
        cands += top[::-1]
    for c in cands:
        if isinstance(c, str):
            try:
                c = json.loads(c)
            except json.JSONDecodeError:
                continue
        if isinstance(c, dict) and isinstance(c.get("answer_rows"), list):
            return c
    return None


def harness_from_audit(ws: Workspace, principal: str, final_rows: list | None) -> dict:
    """The harness verdict on what the agent delivered: the submit_answer call whose rows digest
    equals the final answer's. Another submission (or none) means nothing it reported was verified."""
    subs = [a for a in read_jsonl(ws.registry / "audit.jsonl")
            if a.get("principal") == principal and a.get("tool") == "submit_answer" and a.get("outcome") == "ok"]
    base = {"stage": "answer_publish", "n_submissions": len(subs)}
    if not subs:
        return {**base, "tier": None, "verdict": "NO_SUBMISSION", "delivered": False,
                "reason": "the agent never submitted an answer"}
    want = digest(final_rows) if final_rows is not None else None
    match = [a for a in subs if (a.get("args") or {}).get("answer_rows", {}).get("sha256") == want]
    chosen = match[-1] if match else subs[-1]
    summary = chosen.get("result_summary") or {}
    tier, gate = summary.get("tier"), summary.get("gate")
    delivered = bool(match) and tier == "VERIFIED" and gate == "ALLOW"
    return {**base, "tier": tier, "verdict": gate, "delivered": delivered, "final_rows_submitted": bool(match),
            "reason": None if match else "the final answer's rows are not the rows of any submission"}


def pass_at_k(n: int, c: int, k: int) -> float | None:
    """Unbiased pass@k: P(at least one of k sampled attempts is correct)."""
    if n < k:
        return None
    if n - c < k:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float | None:
    """pass^k: P(all k sampled attempts are correct) -- the reliability measure."""
    if n < k:
        return None
    return comb(c, k) / comb(n, k)


# ============================================================================ attempts
@dataclass
class LiveAttempt:
    task_id: str
    rep: int
    status: str                      # OK | SKIPPED | ERROR
    correct: bool = False
    claimed_success: bool = False
    delivered_as_trusted: bool = False
    cost_usd: float | None = None
    seconds: float = 0.0
    final_answer: dict | None = None
    harness: dict = field(default_factory=dict)
    grade: dict = field(default_factory=dict)
    reason: str | None = None


def grade_live(task: BT.Task, parsed: dict, final: dict | None, harness: dict, truth: dict, rep: int,
               seconds: float) -> LiveAttempt:
    """Grade one finished CLI run (pure: no process, no tools)."""
    att = LiveAttempt(task.task_id, rep, parsed["status"], cost_usd=parsed["cost_usd"], seconds=round(seconds, 1),
                      final_answer=final, harness=harness, reason=parsed["reason"])
    if parsed["status"] == "SKIPPED":
        return att
    if final is None:
        att.grade = {"correct": False, "reason": "no final JSON answer with answer_rows"}
        return att
    att.claimed_success = final.get("claimed_success") is True
    att.grade = BT.grade_analytics(task, final["answer_rows"], truth)
    att.correct = bool(att.grade["correct"])
    att.delivered_as_trusted = bool(harness.get("delivered"))
    return att


def summarize_live(attempts: list[LiveAttempt], k: int) -> dict:
    done = [a for a in attempts if a.status != "SKIPPED"]
    per_task = {}
    for tid in sorted({a.task_id for a in attempts}):
        xs = [a for a in done if a.task_id == tid]
        n, c = len(xs), sum(a.correct for a in xs)
        per_task[tid] = {"n": n, "correct": c,
                         "pass_at_k": {j: pass_at_k(n, c, j) for j in range(1, k + 1)},
                         "pass_hat_k": {j: pass_hat_k(n, c, j) for j in range(1, k + 1)}}
    costs = [a.cost_usd for a in done if a.cost_usd is not None]
    n_correct = sum(a.correct for a in done)
    total = sum(costs) if costs else None
    wrong = [a for a in done if not a.correct]

    def mean(key, j):
        vals = [t[key][j] for t in per_task.values() if t[key][j] is not None]
        return sum(vals) / len(vals) if vals else None
    return {"attempts": len(attempts), "completed": len(done), "skipped": len(attempts) - len(done),
            "correct": n_correct, "per_task": per_task,
            "pass_at_k_mean": {j: mean("pass_at_k", j) for j in range(1, k + 1)},
            "pass_hat_k_mean": {j: mean("pass_hat_k", j) for j in range(1, k + 1)},
            "silent_failure_without_harness": sum(a.claimed_success for a in wrong),
            "silent_failure_with_harness": sum(a.delivered_as_trusted for a in wrong),
            "caught": sum(not a.delivered_as_trusted for a in wrong),
            "false_block": sum(a.correct and not a.delivered_as_trusted for a in done),
            "total_cost_usd": total, "cost_known_for": len(costs),
            "cost_per_correct_usd": (total / n_correct) if total is not None and n_correct else None}


# ============================================================================ runner
def cli_available(cfg: LiveConfig) -> tuple[bool, str | None]:
    exe = shlex.split(cfg.command_template)[0] if cfg.command_template else cfg.cli
    if shutil.which(exe) is None:
        return False, f"agent CLI {exe!r} not found on PATH"
    return True, None


def run_live(ws: Workspace, cfg: LiveConfig | None = None, *, log: Callable[[str], None] | None = None,
             runner: Callable = subprocess.run) -> dict:
    """Run k attempts per task with a real agent CLI; write runs/bench/live-<id>/report.{json,md}."""
    cfg = cfg or LiveConfig()
    cfg.validate()
    log = log or (lambda _m: None)
    run_id = "live-" + digest({"cfg": asdict(cfg), "world": (ws.custody / "world_meta.json").read_text()
                               if (ws.custody / "world_meta.json").exists() else None})[:12]
    run_dir = ws.runs / "bench" / run_id
    shutil.rmtree(run_dir, ignore_errors=True)
    report = {"run_id": run_id, "timestamp": utcnow(), "workspace": str(ws.root), "config": asdict(cfg),
              "status": "OK", "reason": None, "attempts": [], "summary": None,
              "note": "Live agents: k repetitions per task; pass@k = P(any of k correct), pass^k = P(all k correct). "
                      "A handful of tasks x k is a smoke test, not an error-rate estimate (~400+ items per arm "
                      "are needed to compare silent-failure rates)."}
    ok, why = cli_available(cfg)
    if not ok:
        report.update(status="SKIPPED", reason=why)
        log(f"SKIPPED: {why}")
        _write(run_dir, report)
        return report
    prepare_workspace(ws, log=log)
    run_dir.mkdir(parents=True)
    tasks = BT.build_tasks()
    certified = BT.certified_build(ws)
    attempts: list[LiveAttempt] = []
    for tid in cfg.tasks:
        task = tasks[tid]
        truth = BT.analytics_truth(ws, task, certified)
        for rep in range(1, cfg.k + 1):
            aws = private_workspace(ws, run_dir / "agents" / f"{tid}-r{rep}")
            principal = f"agent:live-{tid.lower()}-r{rep}"
            cfg_path = run_dir / "mcp" / f"{tid}-r{rep}.json"
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps(mcp_config(aws, principal, f"live-{tid}"), indent=2))
            cmd = build_command(cfg, PROMPT.format(server=SERVER, question=task.prompt), cfg_path,
                                mcp_server_args(aws, principal, f"live-{tid}"))
            t0 = time.perf_counter()
            with tempfile.TemporaryDirectory(prefix="dsh-live-") as cwd:
                try:
                    proc = runner(cmd, capture_output=True, text=True, timeout=cfg.timeout_s, cwd=cwd)
                    parsed = parse_cli_output(proc.stdout, proc.returncode, proc.stderr)
                except subprocess.TimeoutExpired:
                    parsed = parse_cli_output("", 124, f"timed out after {cfg.timeout_s}s")
            final = extract_final_answer(parsed) if parsed["status"] == "OK" else None
            harness = ({} if parsed["status"] == "SKIPPED"
                       else harness_from_audit(aws, principal, final["answer_rows"] if final else None))
            att = grade_live(task, parsed, final, harness, truth, rep, time.perf_counter() - t0)
            attempts.append(att)
            log(f"{tid} rep {rep}: {att.status} correct={att.correct} claimed={att.claimed_success} "
                f"harness={harness.get('tier')}/{harness.get('verdict')} cost={att.cost_usd}")
            if att.status == "SKIPPED":     # the CLI cannot run at all: stop instead of looping
                report.update(status="SKIPPED", reason=att.reason)
                break
        if report["status"] == "SKIPPED":
            break
    report["attempts"] = [asdict(a) for a in attempts]
    report["summary"] = summarize_live(attempts, cfg.k)
    _write(run_dir, report)
    return report


def _write(run_dir: Path, report: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    (run_dir / "report.md").write_text(render_live_markdown(report))


def render_live_markdown(r: dict) -> str:
    L = [f"# DS Harness live-agent run `{r['run_id']}`", "", f"- timestamp: {r['timestamp']}",
         f"- status: **{r['status']}**" + (f" -- {r['reason']}" if r.get("reason") else ""),
         f"- model `{r['config']['model']}`, k={r['config']['k']}, budget ${r['config']['budget_usd']} per attempt",
         "", f"> {r['note']}", ""]
    s = r.get("summary")
    if not s:
        return "\n".join(L) + "\n"
    L += ["| task | n | correct | " + " | ".join(f"pass@{j}" for j in s["pass_at_k_mean"]) + " | "
          + " | ".join(f"pass^{j}" for j in s["pass_hat_k_mean"]) + " |",
          "|---|---|---|" + "---|" * (2 * len(s["pass_at_k_mean"]))]
    for tid, t in s["per_task"].items():
        cells = [_f(v) for v in t["pass_at_k"].values()] + [_f(v) for v in t["pass_hat_k"].values()]
        L.append(f"| {tid} | {t['n']} | {t['correct']} | " + " | ".join(cells) + " |")
    L += ["", f"- completed {s['completed']}/{s['attempts']} attempts, {s['correct']} correct",
          f"- silent failures: without harness {s['silent_failure_without_harness']}, with harness "
          f"{s['silent_failure_with_harness']}; caught {s['caught']}; correct but not delivered {s['false_block']}",
          f"- total cost ${_f(s['total_cost_usd'])} (known for {s['cost_known_for']} attempts); "
          f"cost per correct answer ${_f(s['cost_per_correct_usd'])}", "",
          "| task | rep | status | correct | claimed | harness tier / gate | cost $ | reason |",
          "|---|---|---|---|---|---|---|---|"]
    for a in r["attempts"]:
        h = a["harness"] or {}
        reason = (a.get("reason") or (a.get("grade") or {}).get("reason") or "").replace("|", "/")[:80]
        L.append(f"| {a['task_id']} | {a['rep']} | {a['status']} | {a['correct']} | {a['claimed_success']} | "
                 f"{h.get('tier')} / {h.get('verdict')} | {_f(a['cost_usd'])} | {reason} |")
    return "\n".join(L) + "\n"


def _f(v) -> str:
    return "-" if v is None else f"{v:.3g}" if isinstance(v, float) else str(v)
