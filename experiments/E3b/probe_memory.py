"""E3b v2 memorisation probe (DESIGN.md, revised plan row 8): do the agents remember the source data's autumn?

Before the census, each agent is asked, with no data and no tools, for the September-November 2011 figures of the
public UCI Online Retail II dataset (the source of the warehouse; its years are shifted by 14 to 2023-2025). The
answers are stored now and scored after the census against the realised Sep-Nov 2025 figures of the sealed months
(the same lines, shifted): an answer within 10% of a realised value means the agent may remember it. Accuracy in the
census is then not read as forecasting skill; the gate's effect is read within requests.

    .venv/bin/python experiments/E3b/probe_memory.py --out experiments/E3b/runs/probe_memory --reps 2
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench import sandbox as sb  # noqa: E402
from dsharness.bench.census import AGENTS, parse_codex_output  # noqa: E402
from dsharness.bench.live import parse_cli_output  # noqa: E402

PROMPT = """Answer from memory only; there are no files and no internet, and do not use any tool.

The UCI Machine Learning Repository dataset "Online Retail II" holds the transactions of a UK-based online gift
retailer from December 2009 to December 2011. For September, October and November 2011, what were, as best you
remember:
1. gross product sales in GBP per month (sum of Quantity x Price over non-cancelled product lines);
2. the number of distinct customers (Customer ID) with a purchase, per month;
3. the number of distinct invoices, per month?

If you do not know, give your best estimate and say so. Reply with JSON only:
{"sales_gbp": {"2011-09": n, "2011-10": n, "2011-11": n}, "customers": {...}, "invoices": {...},
 "confidence": "remember | estimate | no idea", "basis": "<one sentence>"}"""


def ask(agent: str, d: Path, timeout_s: int = 600) -> dict:
    if d.exists():
        shutil.rmtree(d)
    work, home = d / "work", d / "home"
    work.mkdir(parents=True)
    spec, cli = AGENTS[agent], sb.find_cli(AGENTS[agent]["cli"])
    if spec["cli"] == "claude":
        sb.prepare_home(home, cli)
        cmd = [str(cli.binary), "-p", PROMPT, "--model", spec["model"], "--output-format", "json",
               "--permission-mode", "dontAsk", "--tools", "Read", "--allowedTools", "Read", "--strict-mcp-config"]
    else:
        sb.prepare_home(home, cli, codex_config=f'model = "{spec["model"]}"\nmodel_reasoning_effort = '
                                                f'"{spec.get("effort", "high")}"\nweb_search = "disabled"\n')
        cmd = [str(cli.binary), "exec", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox",
               "--json", "-o", f"{sb.HOME}/last_message.txt", PROMPT]
    try:
        p = subprocess.run(sb.bwrap_command(cmd, work=work, home=home, cli=cli), capture_output=True, text=True,
                           timeout=timeout_s, stdin=subprocess.DEVNULL)
        out, err, rc = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired:
        out, err, rc = "", f"timed out after {timeout_s}s", 124
    (d / "stdout.txt").write_text(out)
    if spec["cli"] == "claude":
        res = parse_cli_output(out, rc, err)
    else:
        lm = home / "last_message.txt"
        res = parse_codex_output(out, rc, lm.read_text() if lm.exists() else None, err)
    text = res.get("text") or ""
    try:
        parsed = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except ValueError:
        parsed = None
    return {"agent": agent, "status": res["status"], "text": text, "answer": parsed}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--reps", type=int, default=2)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    rows = [ask(agent, a.out / f"{agent}_r{k}") | {"rep": k} for agent in AGENTS for k in range(1, a.reps + 1)]
    (a.out / "answers.json").write_text(json.dumps(rows, indent=1))
    for r in rows:
        print(r["agent"], r["rep"], r["status"], json.dumps(r["answer"])[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
