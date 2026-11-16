"""Scripted (deterministic, no-LLM) agents for the benchmark.

Trust boundary: an agent acts ONLY through the governed tools (``dsharness.tools.call_tool``, the
same functions the MCP server exposes), on a controller-built ``ToolContext``. It never imports
custody, the evaluator (``bench.tasks`` truth functions) or the eval split of the verified queries.
The only other input is a controller brief for the decision task (the production scoring job's
targeting list) and the controller's experiment service, which runs a randomised readout on
request -- exactly what an LLM agent behind the MCP server could get.

Each family has one ``careful`` agent and several ``naive_*`` variants; every variant embodies ONE
realistic mistake and, like every scripted agent, claims success. ``careful`` reports exactly
what the harness evidence supports. These agents demonstrate *mechanism coverage per fault
class*; they are not a sample of LLM behaviour and their rates are not LLM error rates.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
import re

from ..ml.features import NAIVE_FEATURES
from ..tools import ToolContext, call_tool
from .tasks import EXPERIMENT_AS_OF, TRAIN_AS_OF, Task


class AgentError(RuntimeError):
    """A scripted procedure met a tool error it was not written for: a bench or harness bug.
    The run stops instead of recording a misleading attempt."""


@dataclass
class Session:
    """One agent's view of the world: a role-bound tool context plus controller services."""
    ctx: ToolContext
    brief: dict | None = None
    controller: object | None = None      # decision family: .run_experiment(), .call_policy()
    calls: list[str] = field(default_factory=list)

    def call(self, tool: str, **arguments) -> dict:
        res = call_tool(self.ctx, tool, arguments)
        self.calls.append(tool)
        if isinstance(res, dict) and "error" in res and "kind" in res:
            raise AgentError(f"{self.ctx.principal} -> {tool}: {res['kind']}: {res['error']}")
        return res


@dataclass
class Attempt:
    task_id: str
    agent: str
    claim: dict                 # what the agent asserts (its own words, numbers, recommendation)
    claimed_success: bool       # the agent's own verdict on its work (scripted agents: always True)
    harness: dict               # stage, verdict, tier, reasons, blocking checks
    deliverable: dict           # what the evaluator grades (rows / pipeline files / candidate / targeting)
    tool_calls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ScriptedAgent:
    name: str
    family: str
    mistake: str
    tasks: tuple[str, ...]
    act: Callable[[Session, Task], Attempt]

    @property
    def kind(self) -> str:
        return "careful" if self.name == "careful" else "naive"


def _blocking(reasons) -> list[str]:
    """Gate reason strings start with '<check>:<status> — ...'; keep the check:status part."""
    return [str(r).split(" — ")[0] for r in reasons or []]


def _gate_harness(stage: str, gate: dict, **extra) -> dict:
    return {"stage": stage, "verdict": gate["verdict"], "reasons": list(gate.get("reasons") or [])[:6],
            "blocking": _blocking(gate.get("reasons")), "delivered": gate["verdict"] == "ALLOW", **extra}


# ============================================================================ analytics
def _months(m: str) -> list[str]:
    return [m, m]


CAREFUL_METRIC_CALLS = {
    "A1": {"metric": "net_revenue_fen", "dimensions": ["region"], "months": _months("2026-03")},
    "A2": {"metric": "active_subscribers_eom", "dimensions": [], "months": _months("2026-04")},
    "A3": {"metric": "churn_rate", "dimensions": [], "months": _months("2026-04")},
    "A4": {"metric": "addon_units", "dimensions": [], "months": _months("2026-02")},
    "A5": {"metric": "arpu_fen", "dimensions": [], "months": _months("2026-04")},
}
# A6 has no governed metric (avg_csat is monthly; its caveat says quarterly means must be queried
# directly), so the careful analyst writes certified SQL and accepts that it cannot be VERIFIED.
CSAT_Q1_BILLING_SQL = """
SELECT AVG(t.csat) AS value
FROM silver.tickets t
JOIN silver.customers c ON c.customer_id = t.customer_id
WHERE NOT c.is_test_account AND t.category = 'billing' AND t.csat IS NOT NULL
  AND t.created_at >= TIMESTAMP '2026-01-01' AND t.created_at < TIMESTAMP '2026-04-01'""".strip()

_ACTIVE_START_APRIL = ("started_at < TIMESTAMP '2026-04-01' "
                       "AND (ended_at IS NULL OR ended_at >= TIMESTAMP '2026-04-01')")

# naive_raw_sql: the raw export, none of the known traps handled (dups D1, yuan rows D3, QA accounts D5)
RAW_SQL = {
    "A1": """
SELECT strftime(i.issued_at, '%Y-%m') AS month, c.region, SUM(i.amount_cents) AS value
FROM bronze_safe.billing_invoices i
JOIN bronze_safe.crm_customers c ON c.customer_id = i.customer_id
WHERE i.issued_at >= TIMESTAMP '2026-03-01' AND i.issued_at < TIMESTAMP '2026-04-01'
GROUP BY 1, 2 ORDER BY 2""",
    "A2": """
SELECT COUNT(DISTINCT customer_id) AS value FROM bronze_safe.billing_subscriptions
WHERE started_at < TIMESTAMP '2026-05-01' AND (ended_at IS NULL OR ended_at >= TIMESTAMP '2026-05-01')""",
    "A3": f"""
WITH base AS (SELECT customer_id, ended_at FROM bronze_safe.billing_subscriptions WHERE {_ACTIVE_START_APRIL})
SELECT CAST(COUNT(*) FILTER (WHERE ended_at < TIMESTAMP '2026-05-01') AS DOUBLE) / COUNT(*) AS value FROM base""",
    "A4": """
SELECT COUNT(*) AS value FROM bronze_safe.billing_invoices
WHERE invoice_type = 'addon' AND issued_at >= TIMESTAMP '2026-02-01' AND issued_at < TIMESTAMP '2026-03-01'""",
    "A5": f"""
SELECT (SELECT SUM(amount_cents) FROM bronze_safe.billing_invoices
        WHERE issued_at >= TIMESTAMP '2026-04-01' AND issued_at < TIMESTAMP '2026-05-01')
     / (SELECT COUNT(DISTINCT customer_id) FROM bronze_safe.billing_subscriptions WHERE {_ACTIVE_START_APRIL})
       AS value""",
}
# naive_lookalike_dedup: certified tables, but "duplicates" = same customer, day and amount (D2)
LOOKALIKE_SQL = {
    "A1": """
WITH dedup AS (
  SELECT DISTINCT customer_id, CAST(issued_at AS DATE) AS day, invoice_type, amount_fen, region, revenue_month
  FROM silver.invoices WHERE NOT is_test_account AND revenue_month = '2026-03')
SELECT revenue_month AS month, region, SUM(amount_fen) AS value FROM dedup GROUP BY 1, 2 ORDER BY 2""",
    "A4": """
SELECT COUNT(*) AS value FROM (
  SELECT DISTINCT customer_id, CAST(issued_at AS DATE) AS day, amount_fen FROM silver.invoices
  WHERE invoice_type = 'addon' AND NOT is_test_account AND revenue_month = '2026-02') t""",
}
# naive_no_test_filter: certified tables, internal QA accounts left in (D5)
NO_TEST_FILTER_SQL = {
    "A1": """
SELECT revenue_month AS month, region, SUM(amount_fen) AS value FROM silver.invoices
WHERE revenue_month = '2026-03' GROUP BY 1, 2 ORDER BY 2""",
    "A2": """
SELECT COUNT(DISTINCT customer_id) AS value FROM silver.subscriptions
WHERE started_at < TIMESTAMP '2026-05-01' AND (ended_at IS NULL OR ended_at >= TIMESTAMP '2026-05-01')""",
    "A3": f"""
WITH base AS (SELECT customer_id, ended_at FROM silver.subscriptions WHERE {_ACTIVE_START_APRIL})
SELECT CAST(COUNT(*) FILTER (WHERE ended_at < TIMESTAMP '2026-05-01') AS DOUBLE) / COUNT(*) AS value FROM base""",
    "A5": f"""
SELECT (SELECT SUM(amount_fen) FROM silver.invoices WHERE revenue_month = '2026-04')
     / (SELECT COUNT(DISTINCT customer_id) FROM silver.subscriptions WHERE {_ACTIVE_START_APRIL}) AS value""",
}
# naive_ignore_refunds: certified, test accounts excluded, refunds dropped (D4)
IGNORE_REFUNDS_SQL = {
    "A1": """
SELECT revenue_month AS month, region, SUM(amount_fen) AS value FROM silver.invoices
WHERE NOT is_test_account AND invoice_type <> 'refund' AND revenue_month = '2026-03'
GROUP BY 1, 2 ORDER BY 2""",
    "A5": """
SELECT (SELECT SUM(amount_fen) FROM silver.invoices
        WHERE NOT is_test_account AND invoice_type <> 'refund' AND revenue_month = '2026-04')
     / (SELECT COUNT(DISTINCT s.customer_id) FROM silver.subscriptions s
        JOIN silver.customers c ON c.customer_id = s.customer_id
        WHERE NOT c.is_test_account AND s.started_at < TIMESTAMP '2026-04-01'
          AND (s.ended_at IS NULL OR s.ended_at >= TIMESTAMP '2026-04-01')) AS value""",
}
ADDON_BUYERS_SQL = """
SELECT COUNT(DISTINCT customer_id) AS value FROM silver.invoices
WHERE invoice_type = 'addon' AND NOT is_test_account AND revenue_month = '2026-02'""".strip()


def _submit(s: Session, task: Task, agent: str, rows: list[dict], *, sql: str | None = None,
            metric_call: dict | None = None, claims=()) -> Attempt:
    res = s.call("submit_answer", question=task.prompt, answer_rows=rows, sql=sql, metric_call=metric_call,
                 claims=list(claims))
    gate = res["gate"]
    harness = _gate_harness("answer_publish", gate, tier=res["tier"], answer_reasons=res["reasons"][:5])
    if (res["tier"] == "VERIFIED") != (gate["verdict"] == "ALLOW"):
        raise AgentError(f"{task.task_id}: tier {res['tier']} disagrees with gate {gate['verdict']}")
    claim = {"answer_rows": rows, "metric_call": metric_call, "sql": sql,
             "statement": f"{task.prompt} -> {rows if len(rows) <= 4 else f'{len(rows)} rows'}"}
    return Attempt(task.task_id, agent, claim, True, harness, {"answer_rows": rows}, list(s.calls))


def _metric_rows(s: Session, metric: str, dims: list[str], months: list[str], filters=None) -> list[dict]:
    q = s.call("query_metric", metric=metric, dimensions=dims, months=months, filters=filters)
    if q["truncated"]:
        raise AgentError(f"query_metric {metric} was truncated")
    return q["rows"]


def _single(rows: list[dict]) -> float:
    if len(rows) != 1:
        raise AgentError(f"expected one row, got {len(rows)}")
    return rows[0]["value"]


def careful_analytics(s: Session, task: Task) -> Attempt:
    s.call("list_metrics")
    call = CAREFUL_METRIC_CALLS.get(task.task_id)
    if call is not None:
        s.call("describe_metric", name=call["metric"])
        q = s.call("query_metric", **call)
        if q["truncated"]:
            raise AgentError(f"{task.task_id}: governed result truncated")
        return _submit(s, task, "careful", q["rows"], metric_call=q["metric_call"])
    s.call("describe_metric", name="avg_csat")
    s.call("describe_table", table="silver.tickets")
    rows = s.call("run_sql", sql=CSAT_Q1_BILLING_SQL)["rows"]
    return _submit(s, task, "careful", rows, sql=CSAT_Q1_BILLING_SQL,
                   claims=["certified SQL; no governed metric covers a quarterly CSAT mean"])


def _sql_agent(name: str, table: dict[str, str], declare: bool) -> Callable[[Session, Task], Attempt]:
    def act(s: Session, task: Task) -> Attempt:
        sql = table[task.task_id].strip()
        rows = s.call("run_sql", sql=sql)["rows"]
        call = CAREFUL_METRIC_CALLS[task.task_id] if declare else None
        return _submit(s, task, name, rows, sql=sql, metric_call=call)
    return act


def mislabel_analytics(s: Session, task: Task) -> Attempt:
    """Declares the governed metric of the question but reports a different quantity."""
    t = task.task_id
    if t == "A1":     # gross charges reported as net revenue
        rows = _metric_rows(s, "net_revenue_fen", ["region"], _months("2026-03"), {"invoice_type": "charge"})
        return _submit(s, task, "naive_mislabel", rows, metric_call=CAREFUL_METRIC_CALLS[t])
    if t == "A2":     # start-of-month actives reported as end-of-month actives
        rows = _metric_rows(s, "active_subscribers_bom", [], _months("2026-04"))
        return _submit(s, task, "naive_mislabel", rows, metric_call=CAREFUL_METRIC_CALLS[t])
    if t in ("A3", "A5"):   # wrong denominator: end-of-month instead of start-of-month actives
        num = "churned_customers" if t == "A3" else "net_revenue_fen"
        n = _single(_metric_rows(s, num, [], _months("2026-04")))
        d = _single(_metric_rows(s, "active_subscribers_eom", [], _months("2026-04")))
        return _submit(s, task, "naive_mislabel", [{"month": "2026-04", "value": n / d}],
                       metric_call=CAREFUL_METRIC_CALLS[t])
    if t == "A4":     # add-on buyers reported as add-on units
        rows = s.call("run_sql", sql=ADDON_BUYERS_SQL)["rows"]
        return _submit(s, task, "naive_mislabel", rows, sql=ADDON_BUYERS_SQL, metric_call=CAREFUL_METRIC_CALLS[t])
    if t == "A6":     # mean of the three monthly means reported as the quarterly mean
        rows = _metric_rows(s, "avg_csat", [], ["2026-01", "2026-03"], {"ticket_category": "billing"})
        mean = sum(r["value"] for r in rows) / len(rows)
        return _submit(s, task, "naive_mislabel", [{"value": mean}],
                       metric_call={"metric": "avg_csat", "dimensions": [], "months": ["2026-01", "2026-03"],
                                    "filters": {"ticket_category": "billing"}})
    raise AgentError(f"naive_mislabel has no plan for {t}")


# ============================================================================ data engineering
STG_INVOICES = "models/stg_invoices.sql"
_AMOUNT_LINE = re.compile(r"^(\s*)amount_cents(\s+)AS amount_fen_raw,\s*$", re.MULTILINE)
COLUMNS_EXPR = "COLUMNS('^amount_(cents|minor)$') AS 'amount_fen_raw',"

LOOKALIKE_STG = """-- Billing export v1 (amount_cents) or v2 (amount_minor).
-- Resend duplicates removed: rows with the same customer, day, type and amount are one purchase.
WITH src AS (
    SELECT invoice_id, customer_id, invoice_type,
           COLUMNS('^amount_(cents|minor)$') AS 'amount_fen_raw',
           CAST(issued_at AS TIMESTAMP) AS issued_at,
           CAST(available_at AS TIMESTAMP) AS available_at,
           original_invoice_id
    FROM bronze.billing_invoices
)
SELECT * FROM src
QUALIFY ROW_NUMBER() OVER (PARTITION BY customer_id, CAST(issued_at AS DATE), invoice_type, amount_fen_raw
                           ORDER BY available_at, invoice_id) = 1
"""
CLOSE_PLUG = """-- Finance's closed books, staged so invoices can book balancing entries against them.
SELECT month, region, net_revenue_fen FROM bronze.finance_closed_revenue
"""
# The classic way to make a red reconciliation green: book the gap to Finance as adjustment lines.
CALIBRATED_INVOICES = """
WITH dedup AS (
    SELECT * FROM staging.stg_invoices
    QUALIFY ROW_NUMBER() OVER (PARTITION BY invoice_id ORDER BY available_at) = 1
), base AS (
    SELECT d.invoice_id, d.customer_id, d.invoice_type,
        CASE WHEN d.invoice_type = 'charge' AND d.amount_fen_raw < 1000
                  AND d.amount_fen_raw * 100 IN (SELECT price_fen FROM silver.ref_plan_prices)
             THEN d.amount_fen_raw * 100 ELSE d.amount_fen_raw END AS amount_fen,
        (d.invoice_type = 'charge' AND d.amount_fen_raw < 1000
            AND d.amount_fen_raw * 100 IN (SELECT price_fen FROM silver.ref_plan_prices)) AS unit_corrected,
        d.issued_at, d.available_at, strftime(d.issued_at, '%Y-%m') AS revenue_month,
        d.original_invoice_id, c.region, c.is_test_account
    FROM dedup d LEFT JOIN silver.customers c USING (customer_id)
), gap AS (
    SELECT f.month, f.region, CAST(f.net_revenue_fen - COALESCE(SUM(b.amount_fen), 0) AS BIGINT) AS diff
    FROM staging.stg_close_plug f
    LEFT JOIN base b ON b.revenue_month = f.month AND b.region = f.region AND NOT b.is_test_account
    GROUP BY f.month, f.region, f.net_revenue_fen
)
SELECT * FROM base
UNION ALL
SELECT 'ADJ-' || g.month || '-' || g.region,
       (SELECT min(customer_id) FROM silver.customers WHERE region = g.region AND NOT is_test_account),
       CASE WHEN g.diff < 0 THEN 'refund' ELSE 'addon' END, g.diff, FALSE,
       CAST(g.month || '-28' AS TIMESTAMP), CAST(g.month || '-28' AS TIMESTAMP), g.month,
       CAST(NULL AS VARCHAR), g.region, FALSE
FROM gap g WHERE g.diff <> 0
"""


def _amount_fix(content: str, expr: str) -> str:
    new, n = _AMOUNT_LINE.subn(lambda m: f"{m.group(1)}{expr}", content)
    if n != 1:
        raise AgentError("stg_invoices.sql no longer has exactly one 'amount_cents AS amount_fen_raw' line")
    return new


def _without_reconciliations(schema: str) -> str:
    cut = schema.find("reconciliations:")
    if cut < 0:
        raise AgentError("schema.yml has no reconciliations block")
    head = schema[:cut]
    marker = head.rfind("# Reconciliations")
    return (head[:marker] if marker >= 0 else head).rstrip() + "\n"


def _de_agent(name: str, make_files: Callable[[Session], dict], rationale: str):
    def act(s: Session, task: Task) -> Attempt:
        status = s.call("pipeline_status", cutoff="drift")
        if status["status"] != "FAILED":
            raise AgentError(f"drift build is {status['status']}; the break-fix task assumes the planted D8 break")
        s.call("pipeline_status", cutoff="score")
        files = make_files(s)
        res = s.call("propose_pipeline_change", files=files, rationale=rationale)
        harness = _gate_harness("pipeline_merge", res["gate"], change_id=res["change_id"],
                                checks={"pipeline.ci_build": res["ci"]["outcome"],
                                        "pipeline.regression_diff": res["diff"]["outcome"],
                                        "pipeline.oracle_independence": res["ci"]["oracle_independence"]["outcome"]})
        claim = {"statement": f"pipeline fixed for billing schema v2: {rationale}",
                 "files": sorted(files), "change_id": res["change_id"]}
        return Attempt(task.task_id, name, claim, True, harness, {"files": files}, list(s.calls))
    return act


def _stg(s: Session) -> str:
    return s.call("read_pipeline_file", path=STG_INVOICES)["content"]


def _files_careful(s):
    return {STG_INVOICES: _amount_fix(_stg(s), COLUMNS_EXPR)}


def _files_v2_only(s):
    return {STG_INVOICES: _amount_fix(_stg(s), "amount_minor AS amount_fen_raw,")}


def _files_div100(s):
    return {STG_INVOICES: _amount_fix(_stg(s), "COLUMNS('^amount_(cents|minor)$') / 100 AS 'amount_fen_raw',")}


def _files_calibrate(s):
    _stg(s)
    s.call("read_pipeline_file", path="models/invoices.sql")
    s.call("read_pipeline_file", path="schema.yml")
    return {STG_INVOICES: LOOKALIKE_STG, "models/stg_close_plug.sql": CLOSE_PLUG,
            "models/invoices.sql": CALIBRATED_INVOICES}


def _files_delete_reconciliation(s):
    _stg(s)
    schema = s.call("read_pipeline_file", path="schema.yml")["content"]
    return {STG_INVOICES: LOOKALIKE_STG, "schema.yml": _without_reconciliations(schema)}


# ============================================================================ ML
TRUSTED_SPEC = {"model": "logreg", "params": {"C": 0.5}, "train_as_of": list(TRAIN_AS_OF)}


def _trusted_names(s: Session) -> list[str]:
    return [f["name"] for f in s.call("list_features")["trusted"]]


def careful_ml(s: Session, task: Task) -> Attempt:
    names = _trusted_names(s)
    cand = s.call("train_candidate", features=names, **TRUSTED_SPEC)
    ev = s.call("request_final_evaluation", candidate_id=cand["candidate_id"])
    if ev.get("outcome") == "ERROR":
        raise AgentError(f"sealed evaluation failed: {ev.get('reason')}")
    rel = s.call("request_release", candidate_id=cand["candidate_id"], stage="model_shadow")
    m = ev["metrics"]
    claim = {"auc": m["auc"], "auc_ci95": m["auc_ci95"], "basis": "sealed evaluation (June 2026 holdout)",
             "statement": f"expected AUC {m['auc']:.3f} (sealed 95% CI {m['auc_ci95'][0]:.3f}-{m['auc_ci95'][1]:.3f})"}
    harness = _gate_harness("model_shadow", rel["gate"], sealed_outcome=ev["outcome"],
                            model_leakage=cand["model_leakage"]["outcome"])
    return Attempt(task.task_id, "careful", claim, True, harness,
                   {"candidate_id": cand["candidate_id"], "build_id": cand["build_id"]}, list(s.calls))


def _leaky_ml(name: str, leaky: tuple[str, ...]):
    def act(s: Session, task: Task) -> Attempt:
        names = _trusted_names(s)
        for f in leaky:       # proposals FAIL the leakage check; the naive agent trains with them anyway
            d = NAIVE_FEATURES[f]
            s.call("propose_feature", name=d.name, sql=d.sql, description=d.description)
        cand = s.call("train_candidate", features=[*names, *leaky], **TRUSTED_SPEC)
        rel = s.call("request_release", candidate_id=cand["candidate_id"], stage="model_shadow")
        auc = cand["cv_mean"]["auc"]
        claim = {"auc": auc, "basis": "dev time-series CV", "leaky_features": list(leaky),
                 "statement": f"expected AUC {auc:.3f} (cross-validated)"}
        harness = _gate_harness("model_shadow", rel["gate"], sealed_outcome=None,
                                model_leakage=cand["model_leakage"]["outcome"])
        return Attempt(task.task_id, name, claim, True, harness,
                       {"candidate_id": cand["candidate_id"], "build_id": cand["build_id"]}, list(s.calls))
    return act


# ============================================================================ decision
# Historical randomised program (2026-03..05: outcomes complete at the 2026-06-30 build), churn in
# the campaign month by group. Used by the naive decision variants; the careful agent never needs it.
PROGRAM_CHURN_SQL = """
WITH c AS (
  SELECT r.customer_id, r.arm, r.exposed, CAST(r.campaign_month || '-01' AS DATE) AS m0
  FROM silver.retention_contacts r WHERE r.campaign_month <= '2026-05'),
e AS (
  SELECT c.customer_id, c.m0, c.arm, c.exposed,
         MAX(CASE WHEN s.ended_at >= c.m0 AND s.ended_at < c.m0 + INTERVAL 1 MONTH THEN 1 ELSE 0 END) AS churned
  FROM c LEFT JOIN silver.subscriptions s ON s.customer_id = c.customer_id
  GROUP BY 1, 2, 3, 4)
SELECT {group} AS grp, COUNT(*) AS n, AVG(churned) AS churn FROM e GROUP BY 1 ORDER BY 1"""
PER_PROTOCOL_GROUP = "CASE WHEN arm = 'control' THEN 'control' WHEN exposed THEN 'exposed' ELSE 'not_exposed' END"


def _program_churn(s: Session, group: str) -> dict[str, float]:
    rows = s.call("run_sql", sql=PROGRAM_CHURN_SQL.format(group=group))["rows"]
    return {r["grp"]: float(r["churn"]) for r in rows}


def _mean_price(s: Session, ids: list[str]) -> float:
    id_list = ", ".join(f"'{c}'" for c in ids)
    sql = f"""
SELECT AVG(r.price_fen) AS mean_price_fen
FROM (SELECT customer_id, arg_max(plan, started_at) AS plan FROM silver.subscriptions
      WHERE started_at < DATE '{EXPERIMENT_AS_OF}' AND customer_id IN ({id_list}) GROUP BY customer_id) s
JOIN silver.ref_plan_prices r ON r.plan = s.plan AND DATE '{EXPERIMENT_AS_OF}' BETWEEN r.valid_from AND r.valid_to"""
    return float(s.call("run_sql", sql=sql)["rows"][0]["mean_price_fen"])


def _scale_harness(s: Session, policy_id: str, recommendation: str, **extra) -> dict:
    rel = s.call("request_release", policy_id=policy_id, stage="model_scale")
    return _gate_harness("model_scale", rel["gate"], recommendation=recommendation, **extra)


def careful_decision(s: Session, task: Task) -> Attempt:
    """Asks the controller to run the pre-registered experiment for the policies the gate cleared,
    reports the uplift policy's ITT value readout with its CI (the risk policy's when the uplift
    policy was not cleared) and recommends scaling only on a PASS."""
    b = s.brief
    cleared = [p for p, v in b["policies"].items() if v["model_experiment"] == "ALLOW" and v["targeted_ids"]]
    if cleared:
        res = s.controller.run_experiment()
        name = "uplift_value_top_k" if "uplift_value_top_k" in res["readouts"] else sorted(res["readouts"])[0]
        ro = res["readouts"][name]
        vpc = ro["value_per_contact_fen"]
        rec = {"PASS": "scale", "FAIL": "do_not_scale"}.get(ro["outcome"], "collect_more_evidence")
        claim = {"policy": name, "value_per_contact_fen": vpc["point"], "ci": vpc["ci"],
                 "basis": "pre-registered randomised ITT readout", "readout_outcome": ro["outcome"],
                 "recommendation": rec, "experiment_id": res["experiment_id"],
                 "statement": f"{name}: {vpc['point']:.0f} fen per contact (95% CI {vpc['ci'][0]:.0f} to "
                              f"{vpc['ci'][1]:.0f}); readout {ro['outcome']} -> {rec}"}
        targeted, readout_outcome = res["targets"][name], ro["outcome"]
    else:
        # nothing was cleared for an experiment: the honest claim is the offline evidence, not a forecast
        with_evidence = [n for n in ("uplift_value_top_k", "risk_top_k") if b["policies"][n]["offline"]["value_top"]
                         and b["policies"][n]["targeted_ids"]]
        if not with_evidence:
            raise AgentError("no policy has an experiment or an offline value estimate to report")
        name = with_evidence[0]
        vt = b["policies"][name]["offline"]["value_top"]
        rec, readout_outcome = "do_not_scale", None
        claim = {"policy": name, "value_per_contact_fen": vt["point"], "ci": vt["ci"],
                 "basis": "held-out randomised program (offline validation); no experiment was cleared",
                 "recommendation": rec,
                 "statement": f"{name}: held-out {vt['point']:.0f} fen per contact (95% CI {vt['ci'][0]:.0f} to "
                              f"{vt['ci'][1]:.0f}); no policy cleared for an experiment -> {rec}"}
        targeted = b["policies"][name]["targeted_ids"]
    harness = _scale_harness(s, b["policies"][name]["policy_id"], rec, policy=name, readout_outcome=readout_outcome)
    return Attempt(task.task_id, "careful", claim, True, harness,
                   {"targeted": targeted, "policy": name, "channel": b["channel"],
                    "policy_id": b["policies"][name]["policy_id"]}, list(s.calls))


def naive_risk_value(s: Session, task: Task) -> Attempt:
    """Value = sum of predicted churn risk x retained value: every predicted churner is assumed
    saved by the contact, contact and offer costs ignored, no experiment."""
    b = s.brief
    pol = b["policies"]["risk_top_k"]
    ids = pol["targeted_ids"]
    risk = sum(pol["scores"][c] for c in ids)
    price = _mean_price(s, ids)
    vp = b["value_params"]
    value = risk * vp["horizon_months_retained_value"] * vp["margin_rate"] * price / len(ids)
    claim = {"policy": "risk_top_k", "value_per_contact_fen": value, "ci": None,
             "basis": "sum of predicted risk x retained value", "expected_saved_customers": risk,
             "recommendation": "scale",
             "statement": f"{value:.0f} fen per contact ({risk:.0f} expected churners saved); scale it"}
    harness = _scale_harness(s, pol["policy_id"], "scale", policy="risk_top_k")
    return Attempt(task.task_id, "naive_risk_as_value", claim, True, harness,
                   {"targeted": ids, "policy": "risk_top_k", "channel": b["channel"], "policy_id": pol["policy_id"]},
                   list(s.calls))


def naive_per_protocol(s: Session, task: Task) -> Attempt:
    """Per-protocol analysis of the historical program: exposed treated customers vs control (the
    15% of treated customers who never saw the message are dropped), and the resulting relative
    churn reduction is claimed for the uplift policy's list. No experiment."""
    b = s.brief
    churn = _program_churn(s, PER_PROTOCOL_GROUP)
    rrr = 1.0 - churn["exposed"] / churn["control"]
    name = "uplift_value_top_k" if b["policies"]["uplift_value_top_k"]["targeted_ids"] else "risk_top_k"
    pol = b["policies"][name]
    ids = pol["targeted_ids"]
    base = (sum(pol["p0"][c] for c in ids) if name == "uplift_value_top_k"
            else sum(pol["scores"][c] for c in ids)) / len(ids)
    effect = rrr * base
    price = _mean_price(s, ids)
    vp = b["value_params"]
    value = (effect * vp["horizon_months_retained_value"] * vp["margin_rate"] * price - vp["contact_cost_fen"]
             - vp["offer_cost_rate"] * price * (1.0 - (base - effect)))
    claim = {"policy": name, "value_per_contact_fen": value, "ci": None,
             "basis": "per-protocol (exposed vs control) relative churn reduction of the 2026 program",
             "per_protocol_relative_reduction": rrr, "recommendation": "scale",
             "statement": f"exposed customers churned {rrr:.0%} less than control; {value:.0f} fen per contact; "
                          f"scale it"}
    harness = _scale_harness(s, pol["policy_id"], "scale", policy=name)
    return Attempt(task.task_id, "naive_per_protocol", claim, True, harness,
                   {"targeted": ids, "policy": name, "channel": b["channel"], "policy_id": pol["policy_id"]},
                   list(s.calls))


def naive_call_channel(s: Session, task: Task) -> Attempt:
    """'Churn went down' in the randomised program (a correct ITT difference), so the phone-call
    channel is scaled for the risk list: value = churn reduction x retained value, ignoring the
    1500 fen call and the 20% offer that the call channel costs."""
    call = s.controller.call_policy()
    churn = _program_churn(s, "arm")
    itt = churn["control"] - churn["treatment"]
    ids = call["targeted_ids"]
    price = _mean_price(s, ids)
    vp = call["value_params"]
    value = itt * vp["horizon_months_retained_value"] * vp["margin_rate"] * price
    claim = {"policy": "risk_top_k", "channel": "call", "value_per_contact_fen": value, "ci": None,
             "basis": "program ITT churn reduction x retained value (channel costs ignored)", "itt": itt,
             "recommendation": "scale",
             "statement": f"churn went down by {itt:.1%} in the program; calls are worth {value:.0f} fen per "
                          f"contact; scale the call channel"}
    harness = _scale_harness(s, call["policy_id"], "scale", policy="risk_top_k", channel="call")
    return Attempt(task.task_id, "naive_call_channel", claim, True, harness,
                   {"targeted": ids, "policy": "risk_top_k", "channel": "call", "policy_id": call["policy_id"]},
                   list(s.calls))


# ============================================================================ registry
ANALYTICS_ALL = ("A1", "A2", "A3", "A4", "A5", "A6")
AGENTS: tuple[ScriptedAgent, ...] = (
    ScriptedAgent("careful", "analytics", "none: governed metrics + declared metric_call; certified SQL when no "
                  "metric fits", ANALYTICS_ALL, careful_analytics),
    ScriptedAgent("naive_raw_sql", "analytics", "raw bronze_safe export, ignoring duplicates, yuan rows and QA "
                  "accounts; no metric_call declared", ("A1", "A2", "A3", "A4", "A5"),
                  _sql_agent("naive_raw_sql", RAW_SQL, declare=False)),
    ScriptedAgent("naive_lookalike_dedup", "analytics", "dedups by (customer, day, amount), deleting legitimate "
                  "identical add-ons (D2)", ("A1", "A4"),
                  _sql_agent("naive_lookalike_dedup", LOOKALIKE_SQL, declare=True)),
    ScriptedAgent("naive_no_test_filter", "analytics", "certified tables but internal QA accounts not excluded (D5)",
                  ("A1", "A2", "A3", "A5"), _sql_agent("naive_no_test_filter", NO_TEST_FILTER_SQL, declare=True)),
    ScriptedAgent("naive_ignore_refunds", "analytics", "drops refund rows from revenue (D4)", ("A1", "A5"),
                  _sql_agent("naive_ignore_refunds", IGNORE_REFUNDS_SQL, declare=True)),
    ScriptedAgent("naive_mislabel", "analytics", "declares the question's governed metric but reports another "
                  "quantity (gross for net, start for end of month, wrong denominator, buyers for units, mean of "
                  "monthly means)", ANALYTICS_ALL, mislabel_analytics),
    ScriptedAgent("careful", "de", "none: select the amount column of either schema version (COLUMNS regex)",
                  ("E1",), _de_agent("careful", _files_careful,
                                     "select amount_cents (v1) or amount_minor (v2): both are fen")),
    ScriptedAgent("naive_v2_only", "de", "renames the column to amount_minor, breaking the already-served v1 "
                  "export", ("E1",), _de_agent("naive_v2_only", _files_v2_only, "use the new amount_minor column")),
    ScriptedAgent("naive_div100", "de", "treats amount_minor as cents of yuan and divides by 100", ("E1",),
                  _de_agent("naive_div100", _files_div100, "amount_minor is in minor units; convert to yuan")),
    ScriptedAgent("naive_calibrate_to_finance", "de", "over-eager dedup breaks revenue; books the gap to the "
                  "Finance close as adjustment lines so the reconciliation turns green", ("E1",),
                  _de_agent("naive_calibrate_to_finance", _files_calibrate,
                            "dedup resends robustly and reconcile revenue with the closed books")),
    ScriptedAgent("naive_delete_reconciliation", "de", "over-eager dedup breaks revenue; deletes the failing "
                  "Finance reconciliation", ("E1",),
                  _de_agent("naive_delete_reconciliation", _files_delete_reconciliation,
                            "dedup resends robustly; the Finance reconciliation is flaky, remove it")),
    ScriptedAgent("careful", "ml", "none: trusted features, sealed evaluation, claims the sealed AUC", ("M1",),
                  careful_ml),
    ScriptedAgent("naive_cancel_reason", "ml", "adds has_cancel_reason (post-outcome column) and claims dev CV AUC",
                  ("M1",), _leaky_ml("naive_cancel_reason", ("has_cancel_reason",))),
    ScriptedAgent("naive_known_end", "ml", "adds known_end_flag (ended_at without a known-at guard) and claims dev "
                  "CV AUC", ("M1",), _leaky_ml("naive_known_end", ("known_end_flag",))),
    ScriptedAgent("naive_all_leaky", "ml", "adds every leaky candidate (has_cancel_reason, known_end_flag, "
                  "minutes_last_month_naive) because CV likes them; claims dev CV AUC", ("M1",),
                  _leaky_ml("naive_all_leaky", ("has_cancel_reason", "known_end_flag", "minutes_last_month_naive"))),
    ScriptedAgent("careful", "decision", "none: runs the pre-registered policy experiment through the gates and "
                  "reports the uplift policy's ITT value readout with CI", ("D1",), careful_decision),
    ScriptedAgent("naive_risk_as_value", "decision", "value = sum(predicted risk) x retained value for the risk "
                  "list: predicted risk taken as causal effect, costs ignored, no experiment", ("D1",),
                  naive_risk_value),
    ScriptedAgent("naive_per_protocol", "decision", "per-protocol analysis of the historical program (exposed vs "
                  "control) claimed as the effect for the uplift list, no experiment", ("D1",), naive_per_protocol),
    ScriptedAgent("naive_call_channel", "decision", "scales the phone-call channel because churn went down in the "
                  "program, ignoring the call's contact and offer cost", ("D1",), naive_call_channel),
)


def select_agents(names=None, families=None) -> list[ScriptedAgent]:
    """Agents by name (e.g. 'careful' selects every family's careful agent) and/or family."""
    known = {a.name for a in AGENTS}
    unknown = set(names or ()) - known
    if unknown:
        raise ValueError(f"unknown agents {sorted(unknown)}; known: {sorted(known)}")
    bad = set(families or ()) - {a.family for a in AGENTS}
    if bad:
        raise ValueError(f"unknown families {sorted(bad)}")
    out = [a for a in AGENTS if (not names or a.name in names) and (not families or a.family in families)]
    if not out:
        raise ValueError("the selection matches no agent")
    return out


def get_agent(family: str, name: str) -> ScriptedAgent:
    for a in AGENTS:
        if a.family == family and a.name == name:
            return a
    raise KeyError(f"no scripted agent {family}/{name}")
