"""E3b v2 runner: real agents forecast Sep–Nov 2025 for a UK gift wholesaler, without (N) and with (H) the forecast gate.

Each attempt runs in the bwrap sandbox with its own copy of `warehouse.duckdb` (silver.order_lines up to 2025-08-31,
warehouse_v2.py), `DATA_CATALOG.md` (the authors' description, author_brief.md, word for word), `CLAIMS.md` (the
agents' part of docs/experiments/E3b/CLAIMS_SPEC.md) and Python from agent_env_dt (duckdb, pandas, polars, scipy,
scikit-learn, lightgbm, statsmodels, matplotlib, seaborn). Both arms write `answer.md`, `claims.json`, the files the
claims name and `predict.py` (DESIGN.md, revised plan row 7). Arm H also has the `dsh-model` MCP server
(mcp_model.py): the reply counts as delivered when the last `check_forecast` of exactly the final answer, claims,
named files and code (all matched by sha256) returned ALLOW. Arm N's reply is delivered when `answer.md` is not empty.

Requests: the 40 blind requests of bank_v2.yaml (held out) and the pilot's two stakeholder asks (seen).
The sealed months are never read here: the warehouses end before 2025-09-01.

    systemd-run --user --unit dsh-e3b-v2 --property=CPUAffinity=4,5 --working-directory=$PWD \
        .venv/bin/python experiments/E3b/run_v2.py --out experiments/E3b/runs/v2
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
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
from dsharness.readout import predict_run as P  # noqa: E402
import warehouse_v2 as W  # noqa: E402

TOOL_ENV = (ROOT.parent / "agent_env_dt").resolve()
UV_PYTHON = sb.HOME / ".local/share/uv/python"
SERVER = "dsh-model"
TOOL_TIMEOUT_S = 1800            # one check_forecast runs predict.py up to 12 times, two at a time
SPEC = ROOT / "docs/experiments/E3b/CLAIMS_SPEC.md"
BANK = {r["id"]: r for r in yaml.safe_load((HERE / "bank_v2.yaml").read_text())}
SEEN = {"rp90": "Which of our customers are likely to buy from us again in the next three months?",
        "spend90": "Which of our customers are likely to spend at least GBP 500 with us in the next three months?"}
REQUESTS = {**{k: {"ask": v, "set": "seen"} for k, v in SEEN.items()},
            **{k: {"ask": r["request"], "set": "heldout", "requester": r["requester"]} for k, r in BANK.items()}}
ARMS = ("N", "H")


def catalog() -> str:
    brief = (ROOT / "docs/experiments/E3b/author_brief.md").read_text().splitlines()
    quoted = [x[2:] if x.startswith("> ") else "" for x in brief if x.startswith(">")]
    assert quoted and quoted[0].startswith("Warehouse `warehouse.duckdb`")
    return "\n".join(quoted) + "\n"


def claims_md() -> str:
    """CLAIMS_SPEC.md without what concerns only the gate, the truth script and the two implementations."""
    s = SPEC.read_text()
    head, _, rest = s.partition("## The data `realise` reads")
    s = ("# `claims.json`: typed claims, and how each is checked against what happened\n\n"
         "Every forecast, list or figure from the data that your reply delivers is one claim in `claims.json`. "
         "After the period, each claim is realised on the order lines by the rules below.\n\n"
         "## The data `realise` reads" + rest)
    s = re.sub(r"## Backtest origins.*?(?=## `predict.py`)", "", s, flags=re.S)
    s = re.sub(r"\nThe gate runs it, in the agent's sandbox environment:.*?(?=\n## )", "\n", s, flags=re.S)
    s = s.replace(" (both arms)\n", "\n")
    s = s.replace("Random seeds are fixed, so two runs give the same output.",
                  "Random seeds are fixed, so two runs give the same output. It is rerun with only your source files "
                  f"({', '.join(P.CODE_SUFFIXES)}) next to it: no data, saved model or delivered file.")
    s = s.replace("The agents' warehouse ends 2025-08-31; the\ntruth table runs past 2025-11-30.",
                  "Your warehouse ends 2025-08-31.")
    s = re.sub(r"## Edge cases \(settled[^\n]*\n\nBoth implementations follow these\. Numbers are Luna's points\.",
               "## Edge cases", s)
    s = re.sub(r"### Settled after the first comparison[^\n]*\n", "", s)
    s = re.sub(r"\n### Agreement.*", "\n", s, flags=re.S)
    s = s.replace("(the gate: the cut date; the truth\n    script: 2025-12-01 or later)", "")
    s = re.sub(r"14\. \*Backtest shift:\*.*?(?=\n15\.)", "14. (not used)", s, flags=re.S)
    s = s.replace("; comparisons between\n    implementations allow 0.01 for money and 1e-6 for shares.", ".")
    left = [w for w in ("gate", "truth", "Luna", "designer", "sealed", "implementations") if w in s]
    assert not left, f"CLAIMS.md still mentions {left}: update claims_md()"
    return s


PROMPT = """You are a data scientist at a UK online gift wholesaler. Today is 2025-09-01. Your working directory holds
`warehouse.duckdb` (open it read-only with Python's duckdb; the data ends on 2025-08-31), `DATA_CATALOG.md`, which
describes it, and `CLAIMS.md`.

{who} asks: "{ask}"

Write these files to the working directory:
1. `answer.md`: your reply, at most 300 words, in plain language; and any file the request asks for.
2. `claims.json`: every forecast, list or figure from the data that your reply states, as typed claims (CLAIMS.md).
   Where the data cannot give what is asked, say so with a `not_identifiable` claim.
3. `predict.py`: the procedure behind your claims. `python predict.py --asof 2025-09-01 --warehouse warehouse.duckdb
   --out <an empty directory>` must rebuild `claims.json` and every file its claims name, from lines before `--asof`
   only (CLAIMS.md, `predict.py`).

Python 3 with duckdb, pandas, polars, numpy, scipy, scikit-learn, lightgbm, statsmodels, matplotlib and seaborn is
available as `python`; there is no internet.{harness}
When you are done, reply with a one-paragraph summary."""
HARNESS = """
Forecast replies are delivered only through the `dsh-model` tools: `describe_task` gives the `predict.py` contract
and the gate's checks; `realise_claim` computes a claim over a past window as the gate does; `check_forecast` runs
the forecast gate on `answer.md`, `claims.json`, the files they name and `predict.py` as they are. The reply counts
as delivered only if the last `check_forecast` of your final files returns ALLOW."""


def sha(b: bytes | str) -> str:
    return hashlib.sha256(b.encode() if isinstance(b, str) else b).hexdigest()


def read_json(p: Path):
    if not p.exists():
        return None, None
    try:
        return json.loads(p.read_text()), None
    except ValueError as exc:                    # an unreadable delivery is a result, not a crash
        return None, repr(exc)[:200]


def named_files(doc) -> list[str]:
    if not isinstance(doc, dict) or not isinstance(doc.get("claims"), list):
        return []
    return sorted({c["file"] for c in doc["claims"] if isinstance(c, dict) and isinstance(c.get("file"), str)})


def run_attempt(run: Path, agent: str, task: str, arm: str, rep: int, timeout_s: int) -> dict:
    adir = run / "attempts" / agent / f"{task}-{arm}-r{rep}"
    if adir.exists():
        shutil.rmtree(adir)
    work, home = adir / "work", adir / "home"
    work.mkdir(parents=True)
    shutil.copyfile(W.path(run / "warehouses", W.DATA_END), work / "warehouse.duckdb")
    (work / "DATA_CATALOG.md").write_text(catalog(), encoding="utf-8")
    (work / "CLAIMS.md").write_text(claims_md(), encoding="utf-8")
    req = REQUESTS[task]
    who = f"The {req['requester'][0].lower() + req['requester'][1:]}" if req.get("requester") else "Marketing"
    prompt = PROMPT.format(who=who, ask=req["ask"], harness=HARNESS if arm == "H" else "")
    (adir / "prompt.md").write_text(prompt, encoding="utf-8")
    spec, cli = AGENTS[agent], sb.find_cli(AGENTS[agent]["cli"])
    env = {"PATH": f"{TOOL_ENV}/bin:{cli.binary_dir}:/usr/local/bin:/usr/bin:/bin", "DO_NOT_TRACK": "1",
           "OMP_NUM_THREADS": "1", "MPLBACKEND": "Agg"}
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
        server = [str(ROOT / ".venv/bin/python"), str(HERE / "mcp_model.py"), "--work", str(work),
                  "--warehouses", str(run / "warehouses"), "--adir", str(adir), "--request", str(adir / "request.txt")]
        with sb.mcp_bridge(server, sock_dir, cwd=ROOT, timeout_s=60):
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
    (work / "warehouse.duckdb").unlink(missing_ok=True)
    ans = work / "answer.md"
    answer = ans.read_text(encoding="utf-8", errors="replace") if ans.exists() else None
    claims, claims_error = read_json(work / "claims.json")
    raw = (work / "claims.json").read_text(errors="replace") if (work / "claims.json").exists() else ""
    files = {f: (work / f).read_bytes() if (work / f).is_file() else None for f in named_files(claims)}
    files_sha = sha(b"".join(f.encode() + b"\0" + (b or b"<missing>") + b"\0" for f, b in files.items()))
    checks = ([json.loads(x) for x in (adir / "checks.jsonl").read_text().splitlines() if x.strip()]
              if (adir / "checks.jsonl").exists() else [])
    # delivered (H): the last check of exactly the final answer, claims, named files and code returned ALLOW
    final = (sha(answer) if answer is not None else None, sha(raw), files_sha, P.code_sha(work))
    last = [c for c in checks if (c["answer_sha256"], c["claims_sha256"], c["files_sha256"], c["code_sha256"]) == final]
    gate = last[-1]["verdict"] if last else None
    delivered = bool(answer and answer.strip()) and (arm == "N" or gate == "ALLOW")
    return {"agent": agent, "task": task, "set": req["set"], "arm": arm, "rep": rep, "status": parsed["status"],
            "reason": parsed["reason"], "seconds": secs, "cost_usd": parsed.get("cost_usd"),
            "tokens": parsed.get("tokens"), "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "delivered": delivered, "gate_final": gate, "gates": [c["verdict"] for c in checks],
            "gate_reasons_last": checks[-1]["reasons"] if checks else None, "answer": answer, "claims": claims,
            "claims_error": claims_error, "files": {f: b is not None for f, b in files.items()},
            "predict_py": (work / "predict.py").exists(), "code_sha256": final[3]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--tasks", nargs="+", default=list(REQUESTS), choices=list(REQUESTS))
    ap.add_argument("--arms", nargs="+", default=list(ARMS), choices=list(ARMS))
    ap.add_argument("--agents", nargs="+", default=["claude", "codex"], choices=list(AGENTS))
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=3600, help="seconds per attempt")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    W.ensure(a.out / "warehouses")
    plan_file = a.out / "run.json"            # the planned slots, for the analysis's completeness check
    if not plan_file.exists():
        plan_file.write_text(json.dumps({"tasks": a.tasks, "arms": a.arms, "agents": a.agents, "reps": a.reps,
                                         "sets": {t: REQUESTS[t]["set"] for t in a.tasks},
                                         "claims_md_sha256": sha(claims_md()), "catalog_sha256": sha(catalog())},
                                        indent=1))
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
