"""E3c pilot (exploratory, arm N only): what do real agents conclude when asked what a randomised e-mail test did?

The project's first rule: watch real agents before building checks. Before any experiment tools exist for E3c,
each agent answers the six stakeholder requests of tasks.TASKS without the harness, as a census arm-N attempt:
the bwrap sandbox, Python with duckdb, pandas, numpy, scipy and scikit-learn, a private copy of the test file as
`campaign.duckdb` and a catalog stating the design (random thirds, two-week outcomes). The agent writes
answer.md (its reply to marketing) and numbers.json (every number it states). The designer labels each answer
against the owner's key in tasks.py, written before the first attempt. Nothing here is pre-registered.

RESEARCH USE ONLY (no licence from the file's author): runs stay under experiments/E3c/runs/ (gitignored).

    systemd-run --user --unit dsh-e3c-pilot --property=CPUAffinity=4,5 --working-directory=$PWD \
        .venv/bin/python experiments/E3c/pilot.py --out experiments/E3c/runs/pilot
    .venv/bin/python experiments/E3c/pilot.py --summary experiments/E3c/runs/pilot
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import duckdb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dsharness.bench import sandbox as sb  # noqa: E402
from dsharness.bench.census import AGENTS, parse_codex_output  # noqa: E402
from dsharness.bench.live import parse_cli_output  # noqa: E402
from tasks import TASKS, check_data  # noqa: E402

TOOL_ENV = ROOT.parent / "agent_env"
PROMPT = """You are a data analyst at an apparel retailer. Your working directory holds `campaign.duckdb` (open it
read-only with Python's duckdb) and `DATA_CATALOG.md`, which describes it.

Marketing asks: "{ask}"

Write these files to the working directory:
1. `answer.md`: your reply to marketing, at most 200 words, in plain language.
2. `numbers.json`: every number your reply states, as a JSON list of {{"quantity": "<what it measures, in words>",
   "estimate": <number>, "low": <95% interval low, or null>, "high": <95% interval high, or null>}}.

Python 3 with duckdb, pandas, numpy, scipy and scikit-learn is available as `python`; there is no internet.
When you are done, reply with a one-paragraph summary."""
CATALOG = """# Data catalog

`campaign.duckdb` holds one table, `email_test`: one row per customer, 64,000 customers who bought from us in
the past twelve months. Each customer was assigned at random, with equal chance, to one of three groups: sent
the men's merchandise e-mail, sent the women's merchandise e-mail, or sent no e-mail. Visits, purchases and
spend were tracked for the two weeks after the e-mails went out.

| column | description |
|---|---|
| recency | months since the last purchase (before the e-mail) |
| history_segment | dollars spent in the past year, binned |
| history | dollars spent in the past year |
| mens | 1 = bought men's merchandise in the past year |
| womens | 1 = bought women's merchandise in the past year |
| zip_code | Urban, Surburban (sic) or Rural |
| newbie | 1 = new customer in the past twelve months |
| channel | channels the customer bought from in the past year: Phone, Web, Multichannel |
| segment | assigned group: Mens E-Mail, Womens E-Mail, No E-Mail |
| visit | 1 = visited the website in the two weeks after the e-mail |
| conversion | 1 = bought in the two weeks after the e-mail |
| spend | dollars spent in the two weeks after the e-mail |
"""


def build_db(dest: Path) -> None:
    con = duckdb.connect(str(dest))
    con.execute("PRAGMA threads=1")
    con.execute(f"CREATE TABLE email_test AS SELECT * FROM read_csv_auto('{check_data()}')")
    con.close()


def run_attempt(run: Path, db: Path, agent: str, task: str, rep: int, timeout_s: int) -> dict:
    adir = run / "attempts" / agent / f"{task}-r{rep}"
    if adir.exists():
        shutil.rmtree(adir)
    work, home = adir / "work", adir / "home"
    work.mkdir(parents=True)
    shutil.copyfile(db, work / "campaign.duckdb")
    (work / "DATA_CATALOG.md").write_text(CATALOG, encoding="utf-8")
    prompt = PROMPT.format(ask=TASKS[task].ask)
    (adir / "prompt.md").write_text(prompt, encoding="utf-8")
    spec, cli = AGENTS[agent], sb.find_cli(AGENTS[agent]["cli"])
    env = {"PATH": f"{TOOL_ENV}/bin:{cli.binary_dir}:/usr/local/bin:/usr/bin:/bin"}
    if spec["cli"] == "claude":
        sb.prepare_home(home, cli)
        cmd = [str(cli.binary), "-p", prompt, "--model", spec["model"], "--output-format", "json",
               "--permission-mode", "dontAsk", "--tools", "Bash,Read,Write,Edit,Glob,Grep",
               "--allowedTools", "Bash Read Write Edit Glob Grep", "--strict-mcp-config"]
    else:
        sb.prepare_home(home, cli, codex_config=f'model = "{spec["model"]}"\nmodel_reasoning_effort = '
                                                f'"{spec.get("effort", "high")}"\nweb_search = "disabled"\n')
        cmd = [str(cli.binary), "exec", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox",
               "--json", "-o", f"{sb.HOME}/last_message.txt", prompt]
    argv = sb.bwrap_command(cmd, work=work, home=home, cli=cli, ro_paths=[TOOL_ENV, sb.HOME / ".local/share/uv/python"],
                            ro_binds_at=[(run / "shim_bin", "/usr/local/bin")], env=env)
    t0 = time.perf_counter()
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        p = subprocess.CompletedProcess(argv, 124, exc.stdout or "", f"timed out after {timeout_s}s")
    secs = round(time.perf_counter() - t0, 1)
    out, err = (x if isinstance(x, str) else str(x) for x in (p.stdout, p.stderr))
    (adir / "stdout.txt").write_text(out)
    (adir / "stderr.txt").write_text(err)
    if spec["cli"] == "claude":
        parsed = parse_cli_output(out, p.returncode, err)
    else:
        lm = home / "last_message.txt"
        parsed = parse_codex_output(out, p.returncode, lm.read_text() if lm.exists() else None, err)
    (work / "campaign.duckdb").unlink(missing_ok=True)
    rec = {"agent": agent, "task": task, "rep": rep, "status": parsed["status"], "reason": parsed["reason"],
           "seconds": secs, "cost_usd": parsed.get("cost_usd"), "tokens": parsed.get("tokens"),
           "finished": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    ans, nums = work / "answer.md", work / "numbers.json"
    rec["answer"] = ans.read_text(encoding="utf-8", errors="replace") if ans.exists() else None
    try:
        rec["numbers"] = json.loads(nums.read_text()) if nums.exists() else None
    except ValueError as exc:                    # an unreadable delivery is a result, not a crash
        rec["numbers_error"] = repr(exc)[:200]
    return rec


def latest(run: Path) -> dict:
    keep = {}
    for line in (run / "attempts.jsonl").read_text().splitlines():
        r = json.loads(line)
        keep[(r["agent"], r["task"], r["rep"])] = r
    return keep


def summary(run: Path) -> str:
    """Markdown: one row per attempt with the designer's label (labels.json, if written), then every answer."""
    labels = json.loads((run / "labels.json").read_text()) if (run / "labels.json").exists() else {}
    rows = ["| agent | task | status | label | minutes | cost |", "|---|---|---|---|---|---|"]
    texts = []
    for (ag, t, k), r in sorted(latest(run).items(), key=lambda x: (list(TASKS).index(x[0][1]), x[0][0], x[0][2])):
        tok = r.get("tokens") or {}
        cost = (f"${r['cost_usd']:.2f}" if r.get("cost_usd") else
                f"{(tok.get('input_tokens', 0) + tok.get('output_tokens', 0)) / 1000:.0f}k tokens" if tok else "—")
        lab = labels.get(f"{ag}/{t}-r{k}", {}).get("label", "—")
        rows.append(f"| {ag} | {t} | {r['status']} | {lab} | {r['seconds'] / 60:.1f} | {cost} |")
        texts.append(f"### {ag} / {t} r{k}\n\n{(r.get('answer') or '(no answer.md)').strip()}\n")
    return "\n".join(rows) + "\n\n" + "\n".join(texts)


def main() -> int:
    if sys.argv[1:2] == ["--summary"]:
        print(summary(Path(sys.argv[2])))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--tasks", nargs="+", default=list(TASKS))
    ap.add_argument("--agents", nargs="+", default=["claude", "codex"])
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    db = a.out / "campaign.duckdb"
    if not db.exists():
        build_db(db)
    shim = a.out / "shim_bin"
    if not shim.exists():
        shim.mkdir()
        for name in ("python", "python3"):
            (shim / name).symlink_to(TOOL_ENV / "bin/python")
    log = a.out / "attempts.jsonl"
    done = {k for k, r in latest(a.out).items() if r["status"] == "OK"} if log.exists() else set()
    plan = [(ag, t, k) for k in range(1, a.reps + 1) for t in a.tasks for ag in a.agents if (ag, t, k) not in done]
    print(f"{len(plan)} attempts", flush=True)
    for ag, t, k in plan:
        rec = run_attempt(a.out, db, ag, t, k, a.timeout)
        with log.open("a") as f:
            f.write(json.dumps(rec, default=float) + "\n")
        print(f"{ag:6s} {t:8s} r{k} {rec['status']:6s} {rec['seconds']:6.0f}s answer={'yes' if rec['answer'] else 'no'} "
              f"numbers={len(rec['numbers']) if isinstance(rec.get('numbers'), list) else '-'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
