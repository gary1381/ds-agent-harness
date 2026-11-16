"""E1 real-agent census and harness A/B (docs/experiments/E1/PREREGISTRATION.md).

Real agent CLIs answer analytics questions in two arms, each attempt in its own bwrap sandbox:

- N (no harness, good docs): a copy of the certified score warehouse plus DATA_CATALOG.md, with
  Python/duckdb. Delivered = the agent claims success.
- H (harness): only the analyst MCP tools, served from outside the sandbox over a unix socket.
  Delivered = the final rows equal a submit_answer graded VERIFIED / ALLOW.

Truth: the S set (A1-A6) uses the bench's analytics truth; the blind B set uses each author's SQL
over the evaluator database (custody truth tables + static customer attributes), which agents
never see. Grading follows the pre-registered tolerance rule (grade_values).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import threading
import time

import duckdb
import yaml

from ..common import digest, read_jsonl, utcnow
from ..context.catalog import DEFAULT_CATALOG
from ..workspace import Workspace
from . import sandbox as sb
from . import tasks as BT
from .live import extract_final_answer, harness_from_audit, parse_cli_output
from .run import prepare_workspace, private_workspace

ARMS = ("N", "H")
SERVER = "dsh-analyst"
TIMEOUT_S = 1200
AGENTS = {
    "claude": {"cli": "claude", "model": "sonnet"},
    "codex": {"cli": "codex", "model": "gpt-6-luna", "effort": "high"},
}
WAREHOUSE_SCHEMAS = ("bronze_safe", "staging", "silver")
REL_TOL = 0.0005                   # pre-registered: 0.05% relative, or half a unit in the last reported place
MIN_SIG_DIGITS = 3
_INFRA = re.compile(r"rate.?limit|usage limit|429|overloaded|529|503|ECONNRESET|connection (refused|reset|error)|"
                    r"network error|timed out waiting|stream disconnected|try again later", re.I)
_HARNESS_WORDS = re.compile(r"metric_call|submit_answer|query_metric|verify/|sql_static|dsh-|MCP", re.I)

PROMPT_N = """You are a data analyst at a subscription company. Answer the business question below from the
company's data warehouse: the DuckDB file `warehouse.duckdb` in your working directory (open it
read-only), documented in `DATA_CATALOG.md`. Python 3 with duckdb and pandas is available as `python`.
Work only from these files.

Question: {question}

When you are done, reply with ONLY one JSON object, no prose:
{{"answer_rows": [<one object per row: the key columns the question asks for (e.g. "month", "region"), plus the number in a column named "value">],
  "claimed_success": <true if you stand behind the answer as correct and would publish it to the business, else false>,
  "notes": "<one sentence>"}}
"""
PROMPT_H = """You are a data analyst at a subscription company. Answer the business question below using
only the tools of the MCP server `{server}` (start with its instructions and tool descriptions).
Submit your final numbers with submit_answer before you reply.

Question: {question}

When you are done, reply with ONLY one JSON object, no prose:
{{"answer_rows": [<exactly the rows you submitted>], "tier": "<the tier submit_answer returned>",
  "claimed_success": <true if you stand behind the answer as correct and would publish it to the business, else false>,
  "notes": "<one sentence>"}}
"""


class CensusError(RuntimeError):
    pass


# ============================================================================ questions
@dataclass(frozen=True)
class Question:
    qid: str
    qset: str                       # S | B
    author: str
    lang: str
    question: str
    keys: tuple[str, ...] = ()
    unit: str = "other"
    months: tuple[str, str] | None = None
    truth_sql: str | None = None    # B set
    task_id: str | None = None      # S set (bench analytics task)


def load_questions(path: Path) -> list[Question]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    out = []
    for q in raw:
        if q.get("dropped"):
            continue
        months = tuple(q["months"]) if q.get("months") else None
        out.append(Question(q["qid"], q["set"], q.get("author", "designer"), q.get("lang", "en"),
                            q["question"], tuple(q.get("keys") or ()), q.get("unit", "other"), months,
                            q.get("truth_sql"), q.get("task_id")))
    ids = [q.qid for q in out]
    if len(ids) != len(set(ids)):
        raise CensusError("duplicate question ids")
    return out


def s_set_questions() -> list[dict]:
    """The designer's eval questions A1-A6 as question-bank entries."""
    out = []
    for tid, t in BT.build_tasks().items():
        if t.family == "analytics":
            out.append({"qid": f"S-{tid}", "set": "S", "author": "designer", "lang": "en", "question": t.prompt,
                        "keys": list(t.keys), "unit": "other", "months": list(t.months) if t.months else None,
                        "task_id": tid})
    return out


# ============================================================================ evaluator db
def build_evaluator_db(ws: Workspace, path: Path) -> Path:
    """Custody truth tables + static customer attributes. Evaluator-only (never in a sandbox)."""
    path = Path(path)
    path.unlink(missing_ok=True)
    certified = BT.certified_build(ws)
    wh = ws.lake / certified / "warehouse.duckdb"
    con = duckdb.connect(str(path))
    try:
        for name in ("truth_customer_month", "truth_revenue", "truth_program"):
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet(?)", [str(ws.custody / f"{name}.parquet")])
        con.execute(f"ATTACH '{_sql_str(wh)}' AS wh (READ_ONLY)")
        con.execute("""
            CREATE TABLE dim_customer AS
            SELECT c.customer_id, c.region, c.channel, s.plan,
                   strftime(c.created_at, '%Y-%m') AS signup_month, c.is_test_account AS is_test
            FROM wh.silver.customers c LEFT JOIN wh.silver.subscriptions s USING (customer_id)""")
        con.execute("DETACH wh")
    finally:
        con.close()
    return path


def _sql_str(p) -> str:
    return str(p).replace("'", "''")


def compute_truth(q: Question, ws: Workspace, evaldb: Path, certified: str) -> dict[str, float]:
    if q.truth_sql is None:
        task = BT.build_tasks()[q.task_id]
        return BT.analytics_truth(ws, task, certified)["values"]
    con = duckdb.connect(str(evaldb), read_only=True)
    try:
        df = con.execute(q.truth_sql).df()
    finally:
        con.close()
    missing = [k for k in (*q.keys, "value") if k not in df.columns]
    if missing:
        raise CensusError(f"{q.qid}: truth SQL lacks columns {missing}")
    vals = {}
    for _, r in df.iterrows():
        key = "|".join(_norm_key(r[k]) for k in q.keys)
        v = r["value"]
        if v is None or (isinstance(v, float) and not math.isfinite(v)):
            raise CensusError(f"{q.qid}: truth value for {key!r} is not finite")
        if key in vals:
            raise CensusError(f"{q.qid}: truth has several rows for {key!r}")
        vals[key] = float(v)
    if not vals:
        raise CensusError(f"{q.qid}: truth SQL returned no rows")
    return vals


# Key spellings that mean the same category (format, not substance): Chinese labels and spacing.
KEY_ALIASES = {"北": "north", "北区": "north", "北部": "north", "南": "south", "南区": "south", "南部": "south",
               "东": "east", "东区": "east", "东部": "east", "西": "west", "西区": "west", "西部": "west",
               "自然": "organic", "自然流量": "organic", "付费搜索": "paid_search", "推荐": "referral",
               "基础": "basic", "基础版": "basic", "高级": "plus", "专业": "pro", "专业版": "pro",
               "对照": "control", "对照组": "control", "实验": "treatment", "实验组": "treatment", "处理组": "treatment",
               "yes": "true", "no": "false", "1": "true", "0": "false"}


def _norm_key(v) -> str:
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m")
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = re.sub(r"[\s\-]+", "_", str(v).strip().lower())
    return KEY_ALIASES.get(s, s)


# ============================================================================ grading
def _decimals_and_sig(v) -> tuple[int, int]:
    """Decimal places and significant digits of a reported number (as the agent wrote it)."""
    try:
        d = Decimal(repr(v) if isinstance(v, float) else str(v)).normalize()
    except InvalidOperation:
        return 0, 0
    sign, digits, exp = d.as_tuple()
    sig = len(digits) if any(digits) else 1
    return max(0, -exp), sig


def value_ok(got: float, truth: float, unit: str) -> tuple[bool, str | None]:
    """Pre-registered rule; share<->percent scale accepted, fen<->CNY is not (labelled)."""
    cands = [(got, None)]
    if unit in ("share", "percent", "other") or abs(truth) <= 1.0:
        cands += [(got / 100.0, "percent->share"), (got * 100.0, "share->percent")]
    for g, note in cands:
        if g == truth:
            return True, note
        dec, sig = _decimals_and_sig(got)
        half = 0.5 * 10.0 ** (-dec)
        if note == "percent->share":
            half /= 100.0
        elif note == "share->percent":
            half *= 100.0
        tol = max(REL_TOL * abs(truth), half)
        if sig >= MIN_SIG_DIGITS and abs(g - truth) <= tol:
            return True, note
    if unit in ("fen", "cny"):
        for g, note in ((got * 100.0, "cny-given-for-fen"), (got / 100.0, "fen-given-for-cny")):
            if truth and abs(g - truth) <= REL_TOL * abs(truth):
                return False, note
    return False, None


def answer_map(q: Question, rows) -> tuple[dict[str, float] | None, str | None]:
    if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
        return None, "answer has no rows"
    lower = [{str(k).lower(): v for k, v in r.items()} for r in rows]
    cols: list[str] = []
    for r in lower:
        cols += [c for c in r if c not in cols]
    missing = [k for k in q.keys if k not in cols]
    if missing:
        return None, f"answer rows lack key columns {missing}"
    if "value" in cols:
        vcol = "value"
    else:
        num = [c for c in cols if c not in (*q.keys, "month") and any(BT._is_number(r.get(c)) for r in lower)]
        if len(num) != 1:
            return None, f"cannot tell which column holds the value ({num})"
        vcol = num[0]
    out = {}
    for r in lower:
        if q.months and "month" in r and "month" not in q.keys and r.get("month") is not None:
            m = str(r["month"])[:7]
            if not (q.months[0] <= m <= q.months[1]):
                return None, f"row for month {m} is outside the asked period {q.months}"
        v = r.get(vcol)
        if isinstance(v, str):
            try:
                v = float(v.replace(",", "").rstrip("%"))
            except ValueError:
                return None, f"value {r.get(vcol)!r} is not a number"
        if not BT._is_number(v):
            return None, f"value {v!r} is not a finite number"
        key = "|".join(_norm_key(r.get(k)) for k in q.keys)
        if key in out:
            return None, f"several rows for {key or 'the single value'}"
        out[key] = v
    return out, None


def grade_values(q: Question, rows, truth: dict[str, float]) -> dict:
    got, problem = answer_map(q, rows)
    if got is None:
        return {"correct": False, "reason": problem, "expected": truth}
    if set(got) != set(truth):
        return {"correct": False, "reason": f"keys {sorted(got)[:6]} != truth keys {sorted(truth)[:6]}",
                "expected": truth, "got": got}
    bad, notes = {}, set()
    for k, t in truth.items():
        ok, note = value_ok(got[k], t, q.unit)
        if note:
            notes.add(note)
        if not ok:
            bad[k] = {"got": got[k], "expected": t}
    return {"correct": not bad, "reason": None if not bad else f"{len(bad)} value(s) differ from truth",
            "mismatches": bad, "notes": sorted(notes), "expected": truth, "got": got}


# ============================================================================ arm N materials
def render_catalog_md(catalog_path: Path = DEFAULT_CATALOG, semantic_path: Path | None = None) -> str:
    from ..context.semantic import DEFAULT_SEMANTIC_MODEL
    cat = yaml.safe_load(Path(catalog_path).read_text(encoding="utf-8"))
    sem = yaml.safe_load(Path(semantic_path or DEFAULT_SEMANTIC_MODEL).read_text(encoding="utf-8"))

    def clean(s) -> str:
        s = " ".join(str(s).split())
        s = (s.replace("Use the net_revenue_fen / addon_units metrics rather than re-deriving them.",
                       "Compute revenue and add-on units with the official definitions under 'Metric definitions'.")
              .replace("the net_revenue_fen metric", "the official net revenue definition")
              .replace("Use governed metrics or silver.* instead.",
                       "Use silver.* and the official metric definitions instead."))
        return s

    L = ["# Data catalog", "", clean(sem.get("description", "")), "",
         "Tiers: **certified** = silver.* (reviewed SQL, contract-checked, reconciled to the finance close); "
         "**staging** = staging.* (renamed raw data, duplicates and unit errors not fixed); "
         "**uncertified** = bronze_safe.* (raw source export without personal data).", "",
         "Column timing tags: static (fixed once the row exists), event_time (when the event happened), "
         "availability_time (when the row became known to the warehouse), mutable_current (current state, "
         "overwritten in place), post_outcome (exists only after the outcome happened).", "", "## Data rules", ""]
    for name, r in (cat.get("rules") or {}).items():
        L.append(f"- **{name}**: {clean(r.get('description', ''))}")
    L += ["", "## Metric definitions", ""]
    for name, m in sem["metrics"].items():
        L.append(f"- **{m.get('label', name)}** (`{name}`): {clean(m.get('description', ''))}")
    L += ["", "Dimensions:", ""]
    for name, d in sem["dimensions"].items():
        L.append(f"- **{name}**: {clean(d.get('description', ''))}")
    L += ["", "## Tables", ""]
    for fq, t in cat["tables"].items():
        L += [f"### {fq}", "", f"- tier: {t['tier']}", f"- grain: {clean(t.get('grain', ''))}",
              f"- {clean(t.get('description', ''))}"]
        for c in t.get("caveats") or []:
            L.append(f"- caveat: {clean(c)}")
        L += ["", "| column | description | timing |", "|---|---|---|"]
        for col, spec in (t.get("columns") or {}).items():
            spec = spec if isinstance(spec, dict) else {"description": spec}
            desc = clean(spec.get("description", ""))
            for c in spec.get("caveats") or []:
                desc += f" Caveat: {clean(c)}"
            L.append(f"| {col} | {desc.replace('|', '/')} | {spec.get('temporal', '')} |")
        L.append("")
    text = "\n".join(L) + "\n"
    leaks = _HARNESS_WORDS.findall(text)
    if leaks:
        raise CensusError(f"catalog render mentions harness internals: {sorted(set(leaks))}")
    return text


def prepare_warehouse_copy(ws: Workspace, dest: Path) -> Path:
    """The certified score warehouse with only the agent-visible schemas."""
    src = ws.lake / BT.certified_build(ws) / "warehouse.duckdb"
    shutil.copyfile(src, dest)
    con = duckdb.connect(str(dest))
    try:
        for (schema,) in con.execute("SELECT DISTINCT table_schema FROM information_schema.tables").fetchall():
            if schema not in WAREHOUSE_SCHEMAS:
                con.execute(f'DROP SCHEMA "{schema}" CASCADE')
        con.execute("CHECKPOINT")
    finally:
        con.close()
    return dest


# ============================================================================ attempts
@dataclass
class Attempt:
    agent: str
    arm: str
    qid: str
    rep: int
    status: str = "OK"              # OK | ERROR | SKIPPED
    correct: bool = False
    claimed_success: bool = False
    delivered: bool = False
    harness: dict = field(default_factory=dict)
    grade: dict = field(default_factory=dict)
    final_answer: dict | None = None
    cost_usd: float | None = None
    tokens: dict | None = None
    num_turns: int | None = None
    seconds: float = 0.0
    reason: str | None = None
    attempt_dir: str = ""
    started: str = ""


def parse_codex_output(stdout: str, returncode: int, last_message: str | None, stderr: str = "") -> dict:
    events = []
    for ln in (stdout or "").splitlines():
        if ln.startswith("{"):
            try:
                events.append(json.loads(ln))
            except json.JSONDecodeError:
                pass
    usage: dict[str, int] = {}
    errors = []
    for e in events:
        if e.get("type") == "turn.completed":
            for k, v in (e.get("usage") or {}).items():
                usage[k] = usage.get(k, 0) + int(v or 0)
        if e.get("type") in ("error", "turn.failed"):
            errors.append(str(e.get("message") or e.get("error") or e)[:300])
    out = {"status": "OK", "text": last_message, "structured": None, "cost_usd": None, "tokens": usage or None,
           "num_turns": sum(1 for e in events if e.get("type") == "turn.completed") or None, "reason": None}
    blob = " ".join([*(errors or []), stderr[-500:] if stderr else ""]).lower()
    if "not logged in" in blob or "401" in blob and "unauthorized" in blob:
        out.update(status="SKIPPED", reason=f"codex unavailable: {blob[:200]}")
    elif returncode != 0 or (not last_message and errors):
        out.update(status="ERROR", reason=f"codex error (exit {returncode}): {'; '.join(errors)[:300] or stderr[-300:]}")
    return out


class Census:
    def __init__(self, ws: Workspace, run_dir: Path, questions: list[Question], agents: list[str],
                 arms: list[str], k: int, *, workers: int = 2, log=print):
        self.ws, self.run_dir, self.questions = ws, Path(run_dir), questions
        self.agents, self.arms, self.k, self.workers, self.log = agents, arms, k, workers, log
        self.lock = threading.Lock()
        self.records_path = self.run_dir / "attempts.jsonl"
        self.truth: dict[str, dict[str, float]] = {}
        self.clis = {a: sb.find_cli(AGENTS[a]["cli"]) for a in agents}

    # ---------------------------------------------------------------- setup
    def prepare(self) -> None:
        prepare_workspace(self.ws, log=self.log)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.certified = BT.certified_build(self.ws)
        self.evaldb = build_evaluator_db(self.ws, self.run_dir / "evaluator.duckdb")
        for q in self.questions:
            self.truth[q.qid] = compute_truth(q, self.ws, self.evaldb, self.certified)
        (self.run_dir / "truth.json").write_text(json.dumps(self.truth, indent=1, sort_keys=True))
        self.catalog_md = render_catalog_md()
        self.warehouse = prepare_warehouse_copy(self.ws, self.run_dir / "warehouse_N.duckdb")
        (self.run_dir / "DATA_CATALOG.md").write_text(self.catalog_md)

    def done_keys(self) -> set[tuple]:
        return {(r["agent"], r["arm"], r["qid"], r["rep"]) for r in read_jsonl(self.records_path)
                if r.get("status") not in ("SKIPPED", "RETRY")} if self.records_path.exists() else set()

    def plan(self) -> list[tuple]:
        done = self.done_keys()
        jobs = [(a, arm, q, rep) for rep in range(1, self.k + 1) for q in self.questions
                for a in self.agents for arm in self.arms if (a, arm, q.qid, rep) not in done]
        random.Random(20261106).shuffle(jobs)           # interleave agents/arms/questions over time
        return jobs

    # ---------------------------------------------------------------- one attempt
    def run_attempt(self, agent: str, arm: str, q: Question, rep: int) -> Attempt:
        spec, cli = AGENTS[agent], self.clis[agent]
        adir = self.run_dir / "attempts" / agent / arm / f"{q.qid}-r{rep}"
        if adir.exists():
            shutil.rmtree(adir)
        work, home = adir / "work", adir / "home"
        work.mkdir(parents=True)
        att = Attempt(agent, arm, q.qid, rep, attempt_dir=str(adir), started=utcnow())
        principal = f"agent:census-{agent}-{arm}-{q.qid}-r{rep}".lower()
        sock_dir = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / f"dsh-{digest([str(adir)])[:10]}"
        server_argv = None
        if arm == "N":
            shutil.copyfile(self.warehouse, work / "warehouse.duckdb")
            (work / "DATA_CATALOG.md").write_text(self.catalog_md)
            prompt = PROMPT_N.format(question=q.question)
        else:
            aws = private_workspace(self.ws, adir / "harness_ws")
            server_argv = [sys.executable, "-m", "dsharness.mcp_server", "--role", "analyst", "--cutoff", "score",
                           "--workspace", str(aws.root), "--principal", principal, "--task-id", f"census-{q.qid}"]
            prompt = PROMPT_H.format(server=SERVER, question=q.question)
        ro = []
        env = {}
        if arm == "N":
            tool_env = Path(__file__).resolve().parents[4] / "agent_env"
            ro += [tool_env, sb.HOME / ".local/share/uv/python"]
            env["PATH"] = f"{tool_env}/bin:{cli.binary_dir}:/usr/local/bin:/usr/bin:/bin"
            shim = self.run_dir / "shim_bin"            # python/python3 -> tool env, mounted over /usr/local/bin
            if not shim.exists():
                shim.mkdir()
                for name in ("python", "python3"):
                    (shim / name).symlink_to(tool_env / "bin/python")
        mcp_line = ["socat", "-", f"UNIX-CONNECT:{sb.SOCKET_IN_SANDBOX}"]
        if spec["cli"] == "claude":
            sb.prepare_home(home, cli)
            cmd = [str(cli.binary), "-p", prompt, "--model", spec["model"], "--output-format", "json",
                   "--permission-mode", "dontAsk"]
            if arm == "N":
                cmd += ["--tools", "Bash,Read,Write,Edit,Glob,Grep", "--allowedTools", "Bash Read Write Edit Glob Grep",
                        "--strict-mcp-config"]
            else:
                (home / ".dsh-mcp.json").write_text(json.dumps(
                    {"mcpServers": {SERVER: {"command": mcp_line[0], "args": mcp_line[1:]}}}))
                cmd += ["--tools", "", "--strict-mcp-config", "--mcp-config", f"{sb.HOME}/.dsh-mcp.json",
                        "--allowedTools", f"mcp__{SERVER}__*"]
        else:
            cfg = (f'model = "{spec["model"]}"\nmodel_reasoning_effort = "{spec.get("effort", "high")}"\n'
                   'web_search = "disabled"\n')
            if arm == "H":
                cfg += (f'\n[mcp_servers.{SERVER}]\ncommand = "socat"\nargs = {json.dumps(mcp_line[1:])}\n'
                        'tool_timeout_sec = 300\n')
            sb.prepare_home(home, cli, codex_config=cfg)
            cmd = [str(cli.binary), "exec", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox",
                   "--json", "-o", f"{sb.HOME}/last_message.txt", prompt]
        argv = sb.bwrap_command(cmd, work=work, home=home, cli=cli, ro_paths=ro,
                                ro_binds_at=[(self.run_dir / "shim_bin", "/usr/local/bin")] if arm == "N" else [],
                                socket_dir=sock_dir if arm == "H" else None, env=env)
        t0 = time.perf_counter()

        def launch():
            try:
                return subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_S,
                                      stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired as exc:
                return subprocess.CompletedProcess(argv, 124, exc.stdout or "", f"timed out after {TIMEOUT_S}s")
        if arm == "H":
            with sb.mcp_bridge(server_argv, sock_dir, cwd=Path.cwd()):
                proc = launch()
        else:
            proc = launch()
        att.seconds = round(time.perf_counter() - t0, 1)
        (adir / "stdout.txt").write_text(proc.stdout if isinstance(proc.stdout, str) else str(proc.stdout))
        (adir / "stderr.txt").write_text(proc.stderr if isinstance(proc.stderr, str) else str(proc.stderr))
        if spec["cli"] == "claude":
            parsed = parse_cli_output(proc.stdout, proc.returncode, proc.stderr)
        else:
            lm = home / "last_message.txt"
            parsed = parse_codex_output(proc.stdout, proc.returncode, lm.read_text() if lm.exists() else None,
                                        proc.stderr)
            att.tokens = parsed.get("tokens")
        att.status, att.reason, att.cost_usd = parsed["status"], parsed["reason"], parsed.get("cost_usd")
        if att.status == "ERROR" and _INFRA.search(f"{att.reason} {proc.stderr[-2000:] if isinstance(proc.stderr, str) else ''}"):
            att.status = "RETRY"                  # infrastructure, not the agent: retried on the next pass
        att.num_turns = parsed.get("num_turns")
        final = extract_final_answer(parsed) if parsed["status"] == "OK" else None
        att.final_answer = final
        if final is not None:
            att.claimed_success = final.get("claimed_success") is True
            att.grade = grade_values(q, final.get("answer_rows"), self.truth[q.qid])
            att.correct = bool(att.grade["correct"])
        else:
            att.grade = {"correct": False, "reason": parsed["reason"] or "no final JSON answer with answer_rows"}
        if arm == "H":
            att.harness = harness_from_audit(Workspace(adir / "harness_ws"), principal,
                                             final.get("answer_rows") if final else None)
            att.delivered = bool(att.harness.get("delivered"))
        else:
            att.delivered = att.claimed_success
        (work / "warehouse.duckdb").unlink(missing_ok=True)           # keep transcripts, drop data copies
        return att

    # ---------------------------------------------------------------- loop
    def run(self, limit: int | None = None) -> None:
        jobs = self.plan()[:limit] if limit else self.plan()
        qmap = {q.qid: q for q in self.questions}
        self.log(f"{len(jobs)} attempts to run with {self.workers} workers")

        def one(job):
            agent, arm, q, rep = job
            try:
                att = self.run_attempt(agent, arm, q, rep)
            except Exception as exc:                     # a harness/runner fault, never an agent result
                att = Attempt(agent, arm, q.qid, rep, status="RETRY", reason=f"runner: {type(exc).__name__}: {exc}"[:400])
            with self.lock:
                with self.records_path.open("a") as f:
                    f.write(json.dumps(asdict(att), default=str) + "\n")
            self.log(f"{agent:6} {arm} {q.qid:8} r{rep} {att.status:7} correct={att.correct!s:5} "
                     f"claimed={att.claimed_success!s:5} delivered={att.delivered!s:5} "
                     f"tier={att.harness.get('tier')} {att.seconds:.0f}s cost={att.cost_usd}")
            return att
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            list(ex.map(one, [(a, arm, qmap[q.qid], rep) for a, arm, q, rep in jobs]))


# ============================================================================ summary
def summarize(records: list[dict], *, boot: int = 2000, seed: int = 20261106) -> dict:
    latest: dict[tuple, dict] = {}
    for r in records:                              # a retried attempt replaces its earlier RETRY record
        latest[(r["agent"], r["arm"], r["qid"], r["rep"])] = r
    recs = [r for r in latest.values() if r.get("status") not in ("SKIPPED", "RETRY")]
    groups: dict[tuple, list[dict]] = {}
    for r in recs:
        groups.setdefault((r["agent"], r["arm"]), []).append(r)
        groups.setdefault(("pooled", r["arm"]), []).append(r)
    out = {}
    for (agent, arm), rs in sorted(groups.items()):
        out[f"{agent}/{arm}"] = _metrics(rs)
        if arm == "H":
            claimed = [{**r, "delivered": r.get("claimed_success")} for r in rs]
            out[f"{agent}/H-claimed"] = _metrics(claimed)
    rng = random.Random(seed)
    for agent in sorted({k.split("/")[0] for k in out}):
        n_rs = groups.get((agent, "N"), [])
        h_rs = groups.get((agent, "H"), [])
        if n_rs and h_rs:
            out[f"{agent}/diff_M1_H_minus_N"] = _paired_boot(n_rs, h_rs, rng, boot)
    return out


def _metrics(rs: list[dict]) -> dict:
    n = len(rs)
    correct = sum(r["correct"] for r in rs)
    silent = sum((not r["correct"]) and r["delivered"] for r in rs)
    held_correct = sum(r["correct"] and not r["delivered"] for r in rs)
    cd = sum(r["correct"] and r["delivered"] for r in rs)
    costs = [r["cost_usd"] for r in rs if r.get("cost_usd") is not None]
    toks = [sum((r.get("tokens") or {}).values()) for r in rs if r.get("tokens")]
    by_q: dict[tuple, list[bool]] = {}           # pass^3 is per agent x question; pooled = mean over those cells
    for r in rs:
        by_q.setdefault((r["agent"], r["qid"]), []).append(r["correct"])
    full = [v for v in by_q.values() if len(v) >= 3]
    return {"attempts": n, "questions": len({r["qid"] for r in rs}), "accuracy": correct / n if n else None,
            "M1_silent_error_rate": silent / n if n else None, "silent_errors": silent,
            "M2_false_hold_rate": held_correct / correct if correct else None,
            "abstention_rate": sum(not r.get("claimed_success") for r in rs) / n if n else None,
            "process_failures": sum(r.get("status") == "ERROR" for r in rs),
            "M3_usd_per_correct_delivered": (sum(costs) / cd) if costs and cd else None,
            "M3_tokens_per_correct_delivered": (sum(toks) / cd) if toks and cd else None,
            "total_usd": sum(costs) if costs else None,
            "M4_pass_hat_3": (sum(all(v[:3]) for v in full) / len(full)) if full else None,
            "pass_at_1": correct / n if n else None,
            "median_seconds": sorted(r.get("seconds", 0) for r in rs)[n // 2] if n else None}


def _paired_boot(n_rs: list[dict], h_rs: list[dict], rng: random.Random, boot: int) -> dict:
    def per_q(rs):
        d: dict[str, list[int]] = {}
        for r in rs:
            d.setdefault(r["qid"], []).append(int((not r["correct"]) and r["delivered"]))
        return d
    nq, hq = per_q(n_rs), per_q(h_rs)
    qs = sorted(set(nq) & set(hq))
    if not qs:
        return {}

    def rate(d, sample):
        xs = [x for q in sample for x in d[q]]
        return sum(xs) / len(xs)
    point = rate(hq, qs) - rate(nq, qs)
    diffs = sorted(rate(hq, s) - rate(nq, s) for s in ([rng.choice(qs) for _ in qs] for _ in range(boot)))
    return {"questions": len(qs), "point": point, "ci95": [diffs[int(0.025 * boot)], diffs[int(0.975 * boot) - 1]],
            "ratio_H_over_N": (rate(hq, qs) / rate(nq, qs)) if rate(nq, qs) else None}
