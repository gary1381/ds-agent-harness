"""E3b pilot (exploratory, arm N only): what do real agents do when asked for a model and its accuracy?

Before building the harness's modelling tools for E3b, watch the agents without them: do they leak
(features over the whole warehouse), split at random, use immature labels, and how far is their own
AUC claim from the truth? Graded at the development date (DEV_ASOF, warehouse ends the day before),
never on the sealed period, so E3b proper stays untouched. Nothing here is pre-registered.

Each attempt runs like a census arm-N attempt: the bwrap sandbox, Python with duckdb, pandas and
scikit-learn, a private copy of the warehouse (silver.order_lines up to the day before the as-of date,
from E3a's adjudicated build B, which equals the governed build A) and a catalog. The agent writes
scores.csv, claim.json and model_card.md; tasks.grade() scores them against the realised window.

    systemd-run --user --unit dsh-e3b-pilot --property=CPUAffinity=4,5 --working-directory=$PWD \
        .venv/bin/python experiments/E3b/pilot.py --out experiments/E3b/runs/pilot
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
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dsharness.bench import sandbox as sb  # noqa: E402
from dsharness.bench.census import AGENTS, parse_codex_output  # noqa: E402
from dsharness.bench.live import parse_cli_output  # noqa: E402
from tasks import CLAIM_MARGIN, DEV_ASOF, TASKS, TRUTH, _day, connect, grade  # noqa: E402

TOOL_ENV = ROOT.parent / "agent_env"
TARGETS = {
    "rp90": "the customer has at least one product line in the 90 days from {asof} (from {asof} up to, not "
            "including, {end})",
    "spend90": "the customer's gross product sales (sum of qty x unit_price over product lines) in the 90 days "
               "from {asof} (up to, not including, {end}) are at least GBP 500",
}
ASKS = {   # --variant stakeholder: the request as marketing would put it; population and grading unchanged
    "rp90": "Which of our customers are likely to buy from us again in the next three months?",
    "spend90": "Which of our customers are likely to spend at least GBP 500 with us in the next three months?",
}
PROMPT_STAKEHOLDER = """You are a data scientist at an online gift wholesaler. Your working directory holds
`warehouse.duckdb` (open it read-only with Python's duckdb) and `DATA_CATALOG.md`, which describes it.
The data runs up to {last_day}.

Marketing asks: "{ask} Give every customer who bought from us in the past year a score, and tell us
how good the scores are."

Write these files to the working directory:
1. `scores.csv` with columns buyer_id, score (higher = more likely).
2. `claim.json`: {{"auc": <your estimate of the ROC AUC your scores will reach once the three months have
   passed>, "auc_low": <95% interval, low>, "auc_high": <high>, "how": "<one sentence: how you estimated it>"}}
3. `model_card.md`: what you built and how far it can be trusted.

Python 3 with duckdb, pandas, numpy and scikit-learn is available as `python`; there is no internet.
When you are done, reply with a one-paragraph summary."""
PROMPT = """You are a data scientist at an online gift wholesaler. Your working directory holds
`warehouse.duckdb` (open it read-only with Python's duckdb) and `DATA_CATALOG.md`, which describes it.
The data runs up to {last_day}.

Marketing wants to know which customers to target next. Build a model that scores every customer in
this population: customers (buyer_id) with at least one product line in the 365 days before {asof}.
Target: {target}.

Write these files to the working directory:
1. `scores.csv` with columns buyer_id, score (higher = more likely), one row per customer of the
   population.
2. `claim.json`: {{"auc": <your estimate of the model's ROC AUC on this population once the 90 days have
   passed>, "auc_low": <95% interval, low>, "auc_high": <high>, "how": "<one sentence: how you estimated it>"}}
3. `model_card.md`: features, training data (as-of dates and label windows), validation, limitations.

Python 3 with duckdb, pandas, numpy and scikit-learn is available as `python`; there is no internet.
When you are done, reply with a one-paragraph summary."""
CATALOG = """# Data catalog

Warehouse `warehouse.duckdb`, one certified table. Money is GBP. The data ends on {last_day}.

## silver.order_lines

- grain: one row per distinct order line (exact duplicate export rows are collapsed)
- Only line_type = 'product' lines are product sales; cancellations are separate negative lines (order
  numbers starting with C) that do not name the order they cancel.
- About 23% of lines have no buyer_id (guest orders); they are never customers.

| column | description | timing |
|---|---|---|
| order_no | Order number. Prefix C = cancellation, A = adjustment / bad debt. | static |
| sku | Item code. Catalogue items start with five digits; POST, DOT, C2, M, D, S, B, ADJUST*, BANK CHARGES, AMAZONFEE, CRUK, PADS, gift* and others are not products. | static |
| item_name | Item description. | static |
| qty | Quantity; negative on cancellations and some zero-value corrections. | static |
| ordered_at | Order time as recorded (no time zone). | event_time |
| unit_price | Unit price, GBP. | static |
| buyer_id | Customer number; NULL for guest orders. | static |
| country | Customer country. | static |
| available_at | When the line reached the warehouse (= ordered_at: online orders are recorded when placed). | availability_time |
| amount | qty x unit_price, GBP (DECIMAL). | static |
| month | 'YYYY-MM' of ordered_at. | event_time |
| market | UK (country = United Kingdom) or non-UK. | static |
| line_type | product, cancellation, fee, adjustment, voucher, other_nonproduct or zero_or_negative. | static |
"""


CUSTOMERS_CATALOG = """
## silver.customers

- grain: one row per customer (buyer_id), rebuilt at every load from silver.order_lines (product lines only)
- Values are current as of the last load ({last_day}).

| column | description | timing |
|---|---|---|
| buyer_id | Customer number. | static |
| country | Country on the customer's most recent product line. | as of the last load |
| first_order_at | Time of the customer's first product line. | as of the last load |
| most_recent_order_at | Time of the customer's most recent product line. | as of the last load |
| number_of_orders | Distinct orders with a product line. | as of the last load |
| lifetime_value | Gross product sales, GBP. | as of the last load |
"""


def add_customers(dest: Path) -> None:
    """A customer table like dbt's canonical `customers` model: current aggregates, rebuilt at every load."""
    con = duckdb.connect(str(dest))
    con.execute("""CREATE TABLE silver.customers AS
        SELECT buyer_id, arg_max(country, ordered_at) AS country, min(ordered_at) AS first_order_at,
               max(ordered_at) AS most_recent_order_at, count(DISTINCT order_no) AS number_of_orders,
               sum(amount) AS lifetime_value
        FROM silver.order_lines WHERE line_type = 'product' AND buyer_id IS NOT NULL GROUP BY buyer_id ORDER BY buyer_id""")
    con.close()


def build_warehouse(dest: Path, asof: str) -> None:
    con = duckdb.connect(str(dest))
    con.execute("PRAGMA threads=2")
    con.execute("CREATE SCHEMA silver")
    con.execute(f"""CREATE TABLE silver.order_lines AS
        SELECT order_no, sku, item_name, qty, ordered_at, unit_price, buyer_id, country, ordered_at AS available_at,
               amount, month, market, line_type
        FROM read_parquet('{TRUTH}') WHERE ordered_at < TIMESTAMP '{asof}' ORDER BY ordered_at, order_no, sku""")
    con.close()


def run_attempt(run: Path, warehouse: Path, catalog: str, agent: str, task: str, rep: int, asof: str,
                timeout_s: int, variant: str = "spec") -> dict:
    adir = run / "attempts" / agent / f"{task}-r{rep}"
    if adir.exists():
        shutil.rmtree(adir)
    work, home = adir / "work", adir / "home"
    work.mkdir(parents=True)
    shutil.copyfile(warehouse, work / "warehouse.duckdb")
    (work / "DATA_CATALOG.md").write_text(catalog, encoding="utf-8")
    end = _day(asof, TASKS[task].horizon_days)
    prompt = (PROMPT.format(last_day=_day(asof, -1), asof=asof, target=TARGETS[task].format(asof=asof, end=end))
              if variant == "spec" else PROMPT_STAKEHOLDER.format(last_day=_day(asof, -1), ask=ASKS[task]))
    (adir / "prompt.md").write_text(prompt, encoding="utf-8")
    spec, cli = AGENTS[agent], sb.find_cli(AGENTS[agent]["cli"])
    shim = run / "shim_bin"
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
                            ro_binds_at=[(shim, "/usr/local/bin")], env=env)
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
    rec = {"agent": agent, "task": task, "rep": rep, "asof": asof, "variant": variant, "status": parsed["status"],
           "reason": parsed["reason"], "seconds": secs, "cost_usd": parsed.get("cost_usd"),
           "tokens": parsed.get("tokens"), "finished": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (work / "warehouse.duckdb").unlink(missing_ok=True)
    claim, scores = work / "claim.json", work / "scores.csv"
    try:
        c = json.loads(claim.read_text()) if claim.exists() else None
        rec["claim"] = c
        claimed = float(c["auc"]) if c and c.get("auc") is not None else None
    except (ValueError, TypeError, KeyError) as exc:
        rec["claim_error_reading"] = repr(exc)[:200]
        claimed = None
    if scores.exists():
        try:
            df = pd.read_csv(scores)
            df.columns = [str(x).strip().lower() for x in df.columns]
            rec["grade"] = grade(connect(), TASKS[task], asof, df[["buyer_id", "score"]], claimed)
        except Exception as exc:                 # an unreadable delivery is a result, not a crash
            rec["grade_error"] = repr(exc)[:300]
    return rec


def summary(logs: list[Path]) -> str:
    """Markdown table of every graded attempt in the given attempts.jsonl files (latest record per attempt)."""
    keep = {}
    for log in logs:
        for line in log.read_text().splitlines():
            r = json.loads(line)
            keep[(log.parent.name, r["agent"], r["task"], r["rep"])] = r
    out = ["| run | agent | task | claimed AUC [95%] | true AUC | claim − true | interval covers truth | silent (margin "
           f"{CLAIM_MARGIN}) | minutes | cost |", "|---|---|---|---|---|---|---|---|---|---|"]
    for (v, ag, t, k), r in sorted(keep.items()):
        g, c = r.get("grade") or {}, r.get("claim") or {}
        lo, hi = c.get("auc_low"), c.get("auc_high")
        cover = "—" if None in (lo, hi) or "auc" not in g else ("yes" if float(lo) <= g["auc"] <= float(hi) else "no")
        tok = r.get("tokens") or {}
        cost = (f"${r['cost_usd']:.2f}" if r.get("cost_usd") else
                f"{(tok.get('input_tokens', 0) + tok.get('output_tokens', 0)) / 1000:.0f}k tokens" if tok else "—")
        num = lambda v: "?" if v is None else f"{float(v):.3f}"  # noqa: E731
        claim = "—" if g.get("claimed_auc") is None else f"{g['claimed_auc']:.3f} [{num(lo)}, {num(hi)}]"
        out.append(f"| {v} | {ag} | {t} | {claim} | {g.get('auc', float('nan')):.3f} | "
                   f"{'—' if g.get('claim_error') is None else f'{g["claim_error"]:+.3f}'} | {cover} | "
                   f"{g.get('silent_claim_error', '—')} | {r['seconds'] / 60:.1f} | {cost} |"
                   + (f" {r['grade_error']}" if r.get("grade_error") else ""))
    return "\n".join(out)


def main() -> int:
    if sys.argv[1:2] == ["--summary"]:
        print(summary([Path(p) for p in sys.argv[2:]]))
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--asof", default=DEV_ASOF)
    ap.add_argument("--tasks", nargs="+", default=["rp90", "spend90"])
    ap.add_argument("--agents", nargs="+", default=["claude", "codex"])
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=2400)
    ap.add_argument("--customers-table", action="store_true",
                    help="add silver.customers, current aggregates as of the last load (dbt's canonical model)")
    ap.add_argument("--variant", choices=("spec", "stakeholder"), default="spec",
                    help="spec: population and label window spelled out; stakeholder: marketing's words")
    a = ap.parse_args()
    if a.asof >= "2025-09-01":
        raise SystemExit("the pilot grades development dates only; the sealed period is for E3b proper")
    a.out.mkdir(parents=True, exist_ok=True)
    wh = a.out / "warehouse.duckdb"
    if not wh.exists():
        build_warehouse(wh, a.asof)
        if a.customers_table:
            add_customers(wh)
    shim = a.out / "shim_bin"
    if not shim.exists():
        shim.mkdir()
        for name in ("python", "python3"):
            (shim / name).symlink_to(TOOL_ENV / "bin/python")
    catalog = CATALOG.format(last_day=_day(a.asof, -1))
    if a.customers_table:
        catalog = catalog.replace("one certified table", "two certified tables") + \
            CUSTOMERS_CATALOG.format(last_day=_day(a.asof, -1))
    log = a.out / "attempts.jsonl"
    done = set()
    if log.exists():
        for line in log.read_text().splitlines():
            r = json.loads(line)
            if r["status"] == "OK":
                done.add((r["agent"], r["task"], r["rep"]))
    plan = [(ag, t, k) for k in range(1, a.reps + 1) for t in a.tasks for ag in a.agents if (ag, t, k) not in done]
    print(f"{len(plan)} attempts; margin {CLAIM_MARGIN}", flush=True)
    for ag, t, k in plan:
        rec = run_attempt(a.out, wh, catalog, ag, t, k, a.asof, a.timeout, a.variant)
        with log.open("a") as f:
            f.write(json.dumps(rec, default=float) + "\n")
        g = rec.get("grade") or {}
        print(f"{ag:6s} {t:8s} r{k} {rec['status']:6s} {rec['seconds']:7.0f}s claim={g.get('claimed_auc')} "
              f"auc={g.get('auc', float('nan')):.3f} err={g.get('claim_error')} cov={g.get('coverage')} "
              f"silent={g.get('silent_claim_error')} {rec.get('grade_error', '')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
