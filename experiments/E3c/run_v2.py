"""E3c v2 runner: real agents read out the Hillstrom e-mail test, without (N) and with (H) the experiment readout gate.

Each attempt runs in the bwrap sandbox with its own copy of `campaign.duckdb`, the v2 catalog (design and the owner's
analysis plan), the owner's data checks (`checks/check_experiment.py`, Great Expectations) and Python from
agent_env_dt (duckdb, pandas, polars, scipy, scikit-learn, statsmodels, matplotlib, seaborn). Both arms write
`answer.md`. Arm N lists its numbers in `numbers.json` (as the pilot); arm H writes `claims.json` and delivers only
through the `dsh-experiment` MCP server (mcp_experiment.py): the readout counts as delivered when the last
`check_readout` of exactly the final `answer.md` and `claims.json` (both matched by sha256) returned ALLOW.

Requests: the 40 blind requests of bank_v2.yaml (39 held out; ha17, run in the smoke test, is 'exposed') and the
pilot's six (seen, tasks.TASKS).
RESEARCH USE ONLY (no licence from the file's author): runs stay under experiments/E3c/runs/ (gitignored).

    systemd-run --user --unit dsh-e3c-v2 --property=CPUAffinity=4,5 --working-directory=$PWD \
        .venv/bin/python experiments/E3c/run_v2.py --out experiments/E3c/runs/v2
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import yaml

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))
from dsharness.bench import sandbox as sb  # noqa: E402
from dsharness.bench.census import AGENTS, parse_codex_output  # noqa: E402
from dsharness.bench.live import parse_cli_output  # noqa: E402
from pilot import CATALOG, build_db  # noqa: E402
from tasks import TASKS as SEEN  # noqa: E402

TOOL_ENV = (ROOT.parent / "agent_env_dt").resolve()
UV_PYTHON = sb.HOME / ".local/share/uv/python"
SERVER = "dsh-experiment"
TOOL_TIMEOUT_S = 600
# bank_v2.yaml and keys_v2.yaml are not distributed in the public copy (the Hillstrom file has no licence; docs/DATA.md)
for _f in ("bank_v2.yaml", "keys_v2.yaml"):
    if not (HERE / _f).exists():
        raise SystemExit(f"experiments/E3c/{_f} is not distributed with the public copy; see docs/DATA.md")
BANK = {r["id"]: r for r in yaml.safe_load((HERE / "bank_v2.yaml").read_text())}
EXPOSED = {"ha17"}      # run in the smoke test before the freeze: reported beside the seen requests, not held out
REQUESTS = {**{k: {"ask": v.ask, "set": "seen"} for k, v in SEEN.items()},
            **{k: {"ask": r["request"], "set": "exposed" if k in EXPOSED else "heldout", "requester": r["requester"]}
               for k, r in BANK.items()}}
ARMS = ("N", "H")
CATALOG_V2 = CATALOG + """
## Analysis plan (written before the test)

- Primary comparisons: each e-mail against no e-mail, per customer assigned, on visits, purchases and spend; and the
  men's against the women's e-mail directly.
- Pre-specified effect modifier: past purchase category (bought men's only, women's only, or both in the past year,
  from `mens` and `womens`; every customer bought at least one). Any other split is exploratory.
- Outcomes cover the two weeks after the send only.

## Data checks

`checks/check_experiment.py` runs the owner's checks on the table (a Great Expectations suite, the sample-ratio
test and the balance of pre-campaign columns): `python checks/check_experiment.py campaign.duckdb`.
"""
PROMPT = """You are a data analyst at an apparel retailer. Your working directory holds `campaign.duckdb` (open it
read-only with Python's duckdb), `DATA_CATALOG.md`, which describes it, and `checks/`.

{who} asks: "{ask}"

Write these files to the working directory:
1. `answer.md`: your reply, at most 200 words, in plain language.
{files}
Python 3 with duckdb, pandas, polars, numpy, scipy, scikit-learn, statsmodels, matplotlib and seaborn is available as
`python`; there is no internet.{harness}
When you are done, reply with a one-paragraph summary."""
FILES = {
    "N": """2. `numbers.json`: every number your reply states, as a JSON list of {"quantity": "<what it measures, in
   words>", "estimate": <number>, "low": <95% interval low, or null>, "high": <95% interval high, or null>}.
""",
    "H": """2. `claims.json`: one structured claim per number your reply states, as a JSON list (the schema is in
   `describe_experiment`).
""",
}
HARNESS = """
Readouts are delivered only through the `dsh-experiment` tools: `describe_experiment` gives the design, the analysis
plan and the claim schema; `estimate_effect` computes an effect as the gate does; `check_readout` runs the readout
gate on `answer.md` and `claims.json` as they are. The reply counts as delivered only if the last `check_readout` of
your final `answer.md` and `claims.json` returns ALLOW."""


def design_yaml(run: Path) -> Path:
    """The gate's view of the test (src/dsharness/readout/experiment.py), on the run's copy of the table."""
    p = run / "design.yaml"
    if not p.exists():
        cuts = {"mens_only": [{"column": "mens", "op": "=", "value": 1}, {"column": "womens", "op": "=", "value": 0}],
                "womens_only": [{"column": "mens", "op": "=", "value": 0},
                                {"column": "womens", "op": "=", "value": 1}],
                "both": [{"column": "mens", "op": "=", "value": 1}, {"column": "womens", "op": "=", "value": 1}],
                # the same three groups written by one column (no customer has neither)
                "mens_only_alt": [{"column": "womens", "op": "=", "value": 0}],
                "womens_only_alt": [{"column": "mens", "op": "=", "value": 0}]}
        d = {"db": str((run / "campaign.duckdb").resolve()), "table": "email_test", "assignment": "segment",
             "groups": {"mens": "Mens E-Mail", "womens": "Womens E-Mail", "none": "No E-Mail"}, "control": "none",
             "outcomes": ["visit", "conversion", "spend"],
             "pre_treatment": ["recency", "history_segment", "history", "mens", "womens", "zip_code", "newbie",
                               "channel"],
             "window_days": 14, "prespecified": {"past_purchase_category": cuts},
             "facts": [64000, 21307, 21387, 21306, 14, 12, 100, 1000, 95],   # 95: the interval level
             "data_check": [str(TOOL_ENV / "bin/python"), str(HERE / "workspace_v2/checks/check_experiment.py"),
                            str((run / "campaign.duckdb").resolve())]}
        p.write_text(yaml.safe_dump(d, sort_keys=False))
    return p


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def read_json(p: Path):
    if not p.exists():
        return None, None
    try:
        return json.loads(p.read_text()), None
    except ValueError as exc:                    # an unreadable delivery is a result, not a crash
        return None, repr(exc)[:200]


def run_attempt(run: Path, agent: str, task: str, arm: str, rep: int, timeout_s: int) -> dict:
    adir = run / "attempts" / agent / f"{task}-{arm}-r{rep}"
    if adir.exists():
        shutil.rmtree(adir)
    work, home = adir / "work", adir / "home"
    work.mkdir(parents=True)
    shutil.copyfile(run / "campaign.duckdb", work / "campaign.duckdb")
    (work / "DATA_CATALOG.md").write_text(CATALOG_V2, encoding="utf-8")
    shutil.copytree(HERE / "workspace_v2/checks", work / "checks", ignore=shutil.ignore_patterns("__pycache__"))
    req = REQUESTS[task]
    who = f"The {req['requester'][0].lower() + req['requester'][1:]}" if req.get("requester") else "Marketing"
    prompt = PROMPT.format(who=who, ask=req["ask"], files=FILES[arm], harness=HARNESS if arm == "H" else "")
    (adir / "prompt.md").write_text(prompt, encoding="utf-8")
    spec, cli = AGENTS[agent], sb.find_cli(AGENTS[agent]["cli"])
    env = {"PATH": f"{TOOL_ENV}/bin:{cli.binary_dir}:/usr/local/bin:/usr/bin:/bin", "DO_NOT_TRACK": "1",
           "GX_ANALYTICS_ENABLED": "false", "OMP_NUM_THREADS": "1", "MPLBACKEND": "Agg"}
    mcp_line = ["socat", "-", f"UNIX-CONNECT:{sb.SOCKET_IN_SANDBOX}"]
    if spec["cli"] == "claude":
        sb.prepare_home(home, cli)
        cmd = [str(cli.binary), "-p", prompt, "--model", spec["model"], "--output-format", "json",
               "--permission-mode", "dontAsk", "--tools", "Bash,Read,Write,Edit,Glob,Grep", "--strict-mcp-config"]
        allowed = "Bash Read Write Edit Glob Grep"
        if arm == "H":
            (home / ".dsh-mcp.json").write_text(json.dumps(
                {"mcpServers": {SERVER: {"command": mcp_line[0], "args": mcp_line[1:]}}}))
            cmd += ["--mcp-config", f"{sb.HOME}/.dsh-mcp.json"]
            allowed += f" mcp__{SERVER}__*"
            env["MCP_TOOL_TIMEOUT"] = str(TOOL_TIMEOUT_S * 1000)
        cmd += ["--allowedTools", allowed]
    else:
        cfg = (f'model = "{spec["model"]}"\nmodel_reasoning_effort = "{spec.get("effort", "high")}"\n'
               'web_search = "disabled"\n')
        if arm == "H":
            cfg += (f'\n[mcp_servers.{SERVER}]\ncommand = "socat"\nargs = {json.dumps(mcp_line[1:])}\n'
                    f'startup_timeout_sec = 60\ntool_timeout_sec = {TOOL_TIMEOUT_S}\n')
        sb.prepare_home(home, cli, codex_config=cfg)
        cmd = [str(cli.binary), "exec", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox",
               "--json", "-o", f"{sb.HOME}/last_message.txt", prompt]
    sock_dir = adir / "sock"
    argv = sb.bwrap_command(cmd, work=work, home=home, cli=cli, ro_paths=[TOOL_ENV, UV_PYTHON],
                            ro_binds_at=[(run / "shim_bin", "/usr/local/bin")],
                            socket_dir=sock_dir if arm == "H" else None, env=env)

    def launch():
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(argv, 124, exc.stdout or "", f"timed out after {timeout_s}s")
    t0 = time.perf_counter()
    if arm == "H":
        (adir / "request.txt").write_text(req["ask"], encoding="utf-8")
        server = [str(ROOT / ".venv/bin/python"), str(HERE / "mcp_experiment.py"), "--design", str(design_yaml(run)),
                  "--work", str(work), "--request", str(adir / "request.txt"), "--log", str(adir / "checks.jsonl")]
        with sb.mcp_bridge(server, sock_dir, cwd=ROOT):
            p = launch()
    else:
        p = launch()
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
    ans = work / "answer.md"
    answer = ans.read_text(encoding="utf-8", errors="replace") if ans.exists() else None
    numbers, numbers_error = read_json(work / ("claims.json" if arm == "H" else "numbers.json"))
    checks = ([json.loads(x) for x in (adir / "checks.jsonl").read_text().splitlines() if x.strip()]
              if (adir / "checks.jsonl").exists() else [])
    claims_raw = (work / "claims.json").read_text(errors="replace") if (work / "claims.json").exists() else ""
    # delivered (H): the last check of exactly the final answer.md and claims.json returned ALLOW
    last = [c for c in checks if answer is not None and c["answer_sha256"] == sha(answer)
            and c.get("claims_sha256") == sha(claims_raw)]
    gate = last[-1]["gate"] if last else None
    delivered = bool(answer and answer.strip()) and (arm == "N" or gate == "ALLOW")
    return {"agent": agent, "task": task, "set": req["set"], "arm": arm, "rep": rep, "status": parsed["status"],
            "reason": parsed["reason"], "seconds": secs, "cost_usd": parsed.get("cost_usd"),
            "tokens": parsed.get("tokens"), "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "delivered": delivered, "gate_final": gate, "gates": [c["gate"] for c in checks],
            "gate_reasons_last": checks[-1]["reasons"] if checks else None,
            "answer": answer, "numbers": numbers, "numbers_error": numbers_error}


def latest(run: Path) -> dict:
    keep = {}
    for line in (run / "attempts.jsonl").read_text().splitlines():
        r = json.loads(line)
        keep[(r["agent"], r["task"], r["arm"], r["rep"])] = r
    return keep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--tasks", nargs="+", default=list(REQUESTS), choices=list(REQUESTS))
    ap.add_argument("--arms", nargs="+", default=list(ARMS), choices=list(ARMS))
    ap.add_argument("--agents", nargs="+", default=["claude", "codex"], choices=list(AGENTS))
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=1800, help="seconds per attempt")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    if not (a.out / "campaign.duckdb").exists():
        build_db(a.out / "campaign.duckdb")
    design_yaml(a.out)
    plan_file = a.out / "run.json"            # the planned slots, for the analysis's completeness check
    if not plan_file.exists():
        plan_file.write_text(json.dumps({"tasks": a.tasks, "arms": a.arms, "agents": a.agents, "reps": a.reps,
                                         "sets": {t: REQUESTS[t]["set"] for t in a.tasks}}, indent=1))
    shim = a.out / "shim_bin"
    if not shim.exists():
        shim.mkdir()
        for name in ("python", "python3"):   # a script: through a link python would not find the venv
            (shim / name).write_text(f'#!/bin/sh\nexec {TOOL_ENV}/bin/python "$@"\n')
            (shim / name).chmod(0o755)
    log = a.out / "attempts.jsonl"
    # a slot is done when it has an OK record or has been tried twice (one retry after a failed run)
    tries: dict = {}
    for line in (log.read_text().splitlines() if log.exists() else []):
        r = json.loads(line)
        tries.setdefault((r["agent"], r["task"], r["arm"], r["rep"]), []).append(r["status"])
    done = {k for k, st in tries.items() if "OK" in st or len(st) >= 2}
    lock = threading.Lock()

    def lane(agent: str) -> None:            # one agent's attempts in order; the two agents run side by side
        plan = [(agent, t, arm, k) for k in range(1, a.reps + 1) for t in a.tasks for arm in a.arms
                if (agent, t, arm, k) not in done]
        print(f"{agent}: {len(plan)} attempts", flush=True)
        for ag, t, arm, k in plan:
            rec = run_attempt(a.out, ag, t, arm, k, a.timeout)
            with lock, log.open("a") as f:
                f.write(json.dumps(rec, default=float) + "\n")
            print(f"{ag:6s} {t:8s} {arm} r{k} {rec['status']:6s} {rec['seconds']:6.0f}s delivered={rec['delivered']} "
                  f"gates={rec['gates']}", flush=True)
    with ThreadPoolExecutor(len(a.agents)) as ex:
        list(ex.map(lane, a.agents))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
