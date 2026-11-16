"""Benchmark tasks and the EVALUATOR side of grading.

Trust boundary: this module is evaluator code. It reads ``custody/`` (truth tables, sealed
labels, the experiment oracle's potential outcomes) and the *eval* split of the verified
queries. Agents never import it and nothing here is reachable through tools. Grading is also
independent of the harness: a grader never looks at a tier or a gate verdict, so the bench
measures the harness instead of agreeing with it.

Where truth comes from (``Task.truth_source``):

  A1 net revenue by region, 2026-03   custody ``truth_revenue``
  A2 active subscribers, end 2026-04  custody ``truth_customer_month`` (active at start of May, non-test)
  A3 churn rate, 2026-04              custody ``truth_customer_month`` (churned / at risk, non-test)
  A4 add-on units, 2026-02            NO custody table: the eval-split verified query, executed by the
                                      evaluator on the certified score build. It is the governed metric,
                                      i.e. the same definition the harness reconciles against, so A4 is
                                      graded by the harness's own oracle (``independent_of_harness=False``).
  A5 ARPU, 2026-04                    custody ``truth_revenue`` / ``truth_customer_month``
  A6 billing CSAT, Q1 2026            NO custody table: the eval-split verified SQL (never shown to
                                      agents; the harness has no governed metric for it), evaluator-run
  E1 billing schema v2 break-fix      the evaluator rebuilds the agent's pipeline itself: every served
                                      cutoff must build READY, revenue and charges of every complete
                                      month must equal custody truth, and a planted-defect probe (a lossy
                                      re-export) must NOT be certified
  M1 churn model for June 2026        sealed custody labels; the evaluator recomputes AUC + bootstrap CI
  D1 July retention policy value      ``ExperimentOracle.true_effect`` on the claimed policy's target list
                                      under the claimed channel's economics (custody potential outcomes)

``grade_uplift_ranking`` (evaluator-only) scores a learned uplift ranking against the TRUE
expected uplift p0 - p1 of custody ``truth_experiment`` (July) or ``truth_program`` (the
historical campaigns); its result is never handed back to an agent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import json
import math
from pathlib import Path
import re
import shutil

import numpy as np
import pandas as pd

from ..common import file_digest
from ..context.semantic import load_semantic_model, load_verified_queries, run_verified_query
from ..lake.build import DEFAULT_PIPELINE, build_snapshot, latest_ready_build, warehouse_path
from ..ml import train as ml_train
from ..ml.features import readonly_connection
from ..verify import stats
from ..workspace import CUTOFFS, Workspace

FAMILIES = ("analytics", "de", "ml", "decision")
TRAIN_AS_OF = ("2025-12-01", "2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01")
SEALED_AS_OF = "2026-06-01"
EXPERIMENT_AS_OF = "2026-07-01"
# Pre-registered July policy experiment (decided in code, before any outcome): each policy may
# contact at most K_PER_POLICY customers of its part of the month and never more than a quarter
# of the eligible population (half of a two-policy part), so every target list is randomised
# within a population at least twice its size. Channel: in-app message + 10% offer.
K_PER_POLICY = 500
DECISION_CHANNEL = "app"
# Fixed experiment id (= salt root of the split and the randomisations): a re-run reproduces it.
EXPERIMENT_ID = "bench-d1-2026-07"


def design_k(n_eligible: int) -> int:
    """Pre-registered k per policy for a month with ``n_eligible`` customers."""
    if n_eligible < 8:
        raise GradingError(f"only {n_eligible} eligible customers: too few for a policy experiment")
    return min(K_PER_POLICY, n_eligible // 4)
POINT_CLAIM_REL_TOL = 0.20         # a value claim without an interval must land within 20% of truth ...
POINT_CLAIM_ABS_TOL_FEN = 300.0    # ... or within 3 CNY per contact, whichever is larger
MODEL_BOOTSTRAP_REPS = 500
MODEL_BOOTSTRAP_SEED = 20261019
PROBE_REGION = "east"
PROBE_DROP = 5                     # charge invoices removed from the probe export
_MODEL_PATH = re.compile(r"^models/[a-z][a-z0-9_]{0,62}\.sql$")


class GradingError(RuntimeError):
    """The evaluator could not establish truth (missing custody, broken certified build)."""


@dataclass(frozen=True)
class Task:
    task_id: str
    family: str
    title: str
    prompt: str                       # what the agent is asked (analytics: the eval question verbatim)
    truth_source: str
    traps: tuple[str, ...] = ()
    vq_id: str | None = None          # analytics: eval-split verified query with the same question
    keys: tuple[str, ...] = ()        # analytics: dimension columns that identify an answer row
    months: tuple[str, str] | None = None
    rel_tol: float = 1e-9
    independent_of_harness: bool = True

    def to_dict(self) -> dict:
        return {"task_id": self.task_id, "family": self.family, "title": self.title, "prompt": self.prompt,
                "truth_source": self.truth_source, "traps": list(self.traps),
                "independent_of_harness": self.independent_of_harness}


def _analytics(task_id, vq_id, title, keys, months, truth_source, independent=True) -> Task:
    vq = {q["id"]: q for q in load_verified_queries("eval")}.get(vq_id)
    if vq is None:
        raise GradingError(f"eval verified query {vq_id} is missing; task {task_id} cannot be graded")
    return Task(task_id, "analytics", title, vq["question"], truth_source, tuple(vq["traps"]), vq_id,
                tuple(keys), months, independent_of_harness=independent)


def build_tasks() -> dict[str, Task]:
    tasks = [
        _analytics("A1", "vq_eval_revenue_by_region_2026_03", "Net revenue by region, March 2026", ["region"],
                   ("2026-03", "2026-03"), "custody truth_revenue (2026-03, by region)"),
        _analytics("A2", "vq_eval_active_eom_2026_04", "Active subscribers at end of April 2026", [],
                   ("2026-04", "2026-04"), "custody truth_customer_month (non-test, active at start of 2026-05)"),
        _analytics("A3", "vq_eval_churn_rate_2026_04", "Churn rate, April 2026", [],
                   ("2026-04", "2026-04"), "custody truth_customer_month (non-test churned / at risk, 2026-04)"),
        _analytics("A4", "vq_eval_addon_units_2026_02", "Add-on units sold, February 2026", [],
                   ("2026-02", "2026-02"),
                   "eval-split verified query (governed addon_units) executed by the evaluator on the certified "
                   "score build -- no custody table exists for add-ons; same definition as the harness oracle",
                   independent=False),
        _analytics("A5", "vq_eval_arpu_2026_04", "ARPU, April 2026", [], ("2026-04", "2026-04"),
                   "custody truth_revenue (2026-04) / truth_customer_month (non-test active at start of 2026-04)"),
        _analytics("A6", "vq_eval_billing_csat_q1_2026", "Average CSAT of billing tickets, Q1 2026", [],
                   ("2026-01", "2026-03"),
                   "eval-split verified SQL executed by the evaluator on the certified score build -- no custody "
                   "table exists for CSAT; the SQL is never shown to agents and the harness has no governed "
                   "metric for a quarterly mean"),
        Task("E1", "de", "Billing schema v2 break-fix",
             "The drift build (cutoff 2026-08-31) FAILS since the billing export moved to schema v2. Fix the "
             "governed pipeline so every served cutoff builds READY with correct numbers.",
             "evaluator rebuild of the proposed pipeline on the score and drift exports: READY, revenue by "
             "month x region == custody truth_revenue and charge count/amount by month == custody "
             "truth_customer_month on every complete month; a planted-defect probe (5 east-region charges "
             "deleted from a closed month of the drift export) must NOT build READY", ("D8", "D1", "D2", "D3")),
        Task("M1", "ml", "Churn model for June 2026",
             "Build a churn model scoring customers active on 2026-06-01 and report its expected AUC.",
             "custody sealed labels (June 2026 churn of non-test at-risk customers); the evaluator re-scores the "
             "candidate and recomputes AUC with a customer bootstrap 95% CI", ("D6", "D7")),
        Task("D1", "decision", "July 2026 retention policy: value per contact and whether to scale",
             f"Two targeting policies are registered for a retention contact on {EXPERIMENT_AS_OF}: churn-risk top-k "
             f"and uplift-value top-k (in-app message + 10% one-month offer). Estimate the incremental value (fen per "
             f"contacted customer) of the policy you would scale, and say whether to scale it.",
             "ExperimentOracle.true_effect on the claimed policy's target list under the claimed channel's "
             "economics (custody potential outcomes p0/p1)",
             ("risk != value", "per-protocol != ITT", "channel costs")),
    ]
    return {t.task_id: t for t in tasks}


# ============================================================================ analytics truth
def _custody(ws: Workspace, name: str, columns=None) -> pd.DataFrame:
    path = ws.custody / name
    if not path.exists():
        raise GradingError(f"custody file {name} is missing in {ws.custody}")
    return pd.read_parquet(path, columns=columns)


def analytics_truth(ws: Workspace, task: Task, certified_build: str) -> dict:
    """{values: {key: value}, source, independent_of_harness}. Key = '|'.join of the task's key columns."""
    tcm = None
    if task.task_id in ("A2", "A3", "A5"):
        tcm = _custody(ws, "truth_customer_month.parquet",
                       ["month", "is_test", "active_at_start", "at_risk", "churned"])
        tcm = tcm[~tcm["is_test"]]
    if task.task_id == "A1":
        rev = _custody(ws, "truth_revenue.parquet")
        sel = rev[rev["month"] == "2026-03"]
        values = {str(r): float(v) for r, v in zip(sel["region"], sel["net_revenue_fen"])}
    elif task.task_id == "A2":
        values = {"": float(int((tcm[tcm["month"] == "2026-05"]["active_at_start"]).sum()))}
    elif task.task_id == "A3":
        apr = tcm[tcm["month"] == "2026-04"]
        values = {"": float(apr["churned"].sum()) / float(apr["at_risk"].sum())}
    elif task.task_id == "A5":
        rev = _custody(ws, "truth_revenue.parquet")
        apr = tcm[tcm["month"] == "2026-04"]
        values = {"": float(rev.loc[rev["month"] == "2026-04", "net_revenue_fen"].sum())
                  / float(apr["active_at_start"].sum())}
    elif task.task_id in ("A4", "A6"):
        vq = next(q for q in load_verified_queries("eval") if q["id"] == task.vq_id)
        with readonly_connection(warehouse_path(ws, certified_build)) as con:
            df = run_verified_query(con, load_semantic_model(), vq)
        if len(df) != 1 or "value" not in df.columns or pd.isna(df["value"].iloc[0]):
            raise GradingError(f"eval query {task.vq_id} did not return one value on {certified_build}: {df}")
        values = {"": float(df["value"].iloc[0])}
    else:
        raise GradingError(f"no analytics truth for task {task.task_id}")
    if not values or any(not math.isfinite(v) for v in values.values()):
        raise GradingError(f"truth for {task.task_id} is empty or not finite: {values}")
    return {"values": values, "source": task.truth_source, "independent_of_harness": task.independent_of_harness,
            "certified_build": certified_build if task.task_id in ("A4", "A6") else None}


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def answer_values(task: Task, rows) -> tuple[dict[str, float] | None, str | None]:
    """Read an agent's answer rows as {key: value}; (None, reason) when they cannot be read."""
    if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
        return None, "answer has no rows"
    cols: list[str] = []
    for r in rows:
        cols += [c for c in r if c not in cols]
    missing = [k for k in task.keys if k not in cols]
    if missing:
        return None, f"answer rows lack key columns {missing}"
    if "value" in cols:
        vcol = "value"
    else:
        numeric = [c for c in cols if c not in (*task.keys, "month") and any(_is_number(r.get(c)) for r in rows)]
        if len(numeric) != 1:
            return None, f"cannot tell which column holds the value (numeric columns: {numeric})"
        vcol = numeric[0]
    out: dict[str, float] = {}
    for r in rows:
        month = r.get("month")
        if month is not None and task.months is not None:
            m = str(month)[:7]
            if not (task.months[0] <= m <= task.months[1]):
                return None, f"answer row for month {month!r} is outside the asked period {task.months}"
        v = r.get(vcol)
        if not _is_number(v):
            return None, f"value {v!r} is not a finite number"
        key = "|".join(str(r.get(k)) for k in task.keys)
        if key in out:
            return None, f"answer has several rows for {key or 'the single value'}"
        out[key] = float(v)
    return out, None


def grade_analytics(task: Task, rows, truth: dict) -> dict:
    got, problem = answer_values(task, rows)
    expected = truth["values"]
    if got is None:
        return {"correct": False, "reason": problem, "expected": expected}
    if set(got) != set(expected):
        return {"correct": False, "reason": f"answer keys {sorted(got)} != truth keys {sorted(expected)}",
                "expected": expected, "got": got}
    bad = {k: {"got": got[k], "expected": expected[k]} for k in expected
           if not math.isclose(got[k], expected[k], rel_tol=task.rel_tol, abs_tol=0.0)}
    return {"correct": not bad, "reason": None if not bad else f"{len(bad)} value(s) differ from truth",
            "mismatches": bad, "expected": expected, "got": got}


# ============================================================================ DE truth
def _month_end(month: str) -> date:
    y, m = map(int, month.split("-"))
    return date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)


def complete_months(cutoff: str, months) -> list[str]:
    """Months whose invoices are all available in an export at `cutoff` (issued_at + <= 1 day)."""
    c = date.fromisoformat(cutoff)
    return sorted(m for m in set(months) if _month_end(m) + timedelta(days=1) <= c)


def apply_pipeline_files(files: dict, dest: Path) -> Path:
    """Evaluator's own copy of the governed pipeline with the agent's files applied."""
    if not isinstance(files, dict) or not files:
        raise GradingError("pipeline change has no files")
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(DEFAULT_PIPELINE, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for rel, content in files.items():
        if rel != "schema.yml" and not _MODEL_PATH.match(str(rel)):
            raise GradingError(f"pipeline file {rel!r} is outside models/*.sql and schema.yml")
        (dest / rel).write_text(content, encoding="utf-8")
    return dest


def _truth_numbers(ws: Workspace) -> tuple[pd.DataFrame, pd.DataFrame]:
    rev = _custody(ws, "truth_revenue.parquet")
    tcm = _custody(ws, "truth_customer_month.parquet", ["month", "is_test", "price_fen"])
    charges = (tcm[~tcm["is_test"]].groupby("month")
               .agg(n=("price_fen", "size"), amount=("price_fen", "sum")).reset_index())
    return rev, charges


def check_build_numbers(ws: Workspace, warehouse: Path, cutoff: str) -> dict:
    """Revenue by month x region and charges by month of a built warehouse vs custody truth."""
    rev, charges = _truth_numbers(ws)
    months = complete_months(cutoff, rev["month"])
    if not months:
        raise GradingError(f"no complete month to grade at cutoff {cutoff}")
    with readonly_connection(warehouse) as con:
        got_rev = con.execute(
            "SELECT revenue_month AS month, region, CAST(SUM(amount_fen) AS BIGINT) AS v FROM silver.invoices "
            "WHERE NOT is_test_account GROUP BY 1, 2").df()
        got_ch = con.execute(
            "SELECT revenue_month AS month, COUNT(*) AS n, CAST(SUM(amount_fen) AS BIGINT) AS amount "
            "FROM silver.invoices WHERE invoice_type = 'charge' AND NOT is_test_account GROUP BY 1").df()
    exp_rev = rev[rev["month"].isin(months)].rename(columns={"net_revenue_fen": "t"})
    r = exp_rev.merge(got_rev[got_rev["month"].isin(months)], on=["month", "region"], how="outer")
    rev_bad = r[r["t"].isna() | r["v"].isna() | (r["t"] != r["v"])]
    c = charges[charges["month"].isin(months)].merge(
        got_ch[got_ch["month"].isin(months)], on="month", how="outer", suffixes=("_t", "_v"))
    ch_bad = c[c.isna().any(axis=1) | (c["n_t"] != c["n_v"]) | (c["amount_t"] != c["amount_v"])]
    return {"outcome": "PASS" if rev_bad.empty and ch_bad.empty else "FAIL",
            "months": [months[0], months[-1]], "n_months": len(months),
            "revenue_cells_wrong": int(len(rev_bad)), "charge_months_wrong": int(len(ch_bad)),
            "revenue_examples": _records(rev_bad.head(3)), "charge_examples": _records(ch_bad.head(3))}


def _records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records"))


def planted_defect_export(ws: Workspace, cutoff: str, probe_root: Path) -> tuple[Workspace, dict]:
    """A copy of the `cutoff` export with PROBE_DROP charge invoices of the latest Finance-closed
    month (non-test customers of PROBE_REGION) deleted: a lossy re-export whose revenue no longer
    matches the independent Finance close. Returns a probe workspace whose sources hold it."""
    src = ws.source_dir(cutoff)
    probe = Workspace(probe_root)
    shutil.rmtree(probe_root, ignore_errors=True)
    dest = probe.source_dir(cutoff)
    dest.mkdir(parents=True)
    manifest = json.loads((src / "_export_manifest.json").read_text())
    fin = pd.read_csv(src / "finance_closed_revenue.csv", dtype=str)
    if fin.empty:
        raise GradingError(f"export {cutoff} has no Finance-closed month to plant a defect in")
    month = max(fin["month"])
    cust = pd.read_csv(src / "crm_customers.csv", dtype=str, usecols=["customer_id", "region", "email"])
    eligible = set(cust.loc[(cust["region"] == PROBE_REGION)
                            & ~cust["email"].fillna("").str.endswith("@qa.internal"), "customer_id"])
    inv = pd.read_csv(src / "billing_invoices.csv", dtype=str, keep_default_na=False)
    mask = (inv["invoice_type"] == "charge") & inv["issued_at"].str.startswith(month) & inv["customer_id"].isin(
        eligible)
    ids = sorted(inv.loc[mask, "invoice_id"].unique())[:PROBE_DROP]
    if len(ids) < PROBE_DROP:
        raise GradingError(f"not enough {PROBE_REGION} charges in {month} to plant the probe defect")
    for name, meta in manifest["tables"].items():
        if name != "billing_invoices":
            (dest / meta["file"]).symlink_to((src / meta["file"]).resolve())
    out = inv[~inv["invoice_id"].isin(ids)]
    out.to_csv(dest / "billing_invoices.csv", index=False)
    manifest["tables"]["billing_invoices"].update(rows=int(len(out)), sha256=file_digest(dest / "billing_invoices.csv"))
    manifest["planted_defect"] = {"kind": "lossy_reexport", "month": month, "region": PROBE_REGION,
                                  "dropped_invoice_ids": ids}
    (dest / "_export_manifest.json").write_text(json.dumps(manifest, indent=2))
    return probe, {"cutoff": cutoff, "month": month, "region": PROBE_REGION, "dropped_invoice_ids": ids,
                   "rows_removed": int(len(inv) - len(out))}


def grade_pipeline_change(ws: Workspace, files: dict, work_dir: Path) -> dict:
    """Evaluator rebuild of the agent's proposed pipeline (never the harness's CI artefacts).

    Production note: the evaluator executes the agent's SQL; a real evaluator does that in a
    sandbox without the custody mount (the reference runs scripted agents' SQL in-process)."""
    work_dir = Path(work_dir)
    pipe = apply_pipeline_files(files, work_dir / "pipeline")
    checks: dict = {}
    reasons: list[str] = []
    served = {"score": CUTOFFS["score"], "drift": CUTOFFS["drift"]}
    for name, cutoff in served.items():
        rep = build_snapshot(ws, cutoff, pipe, out_root=work_dir / "lake")
        entry = {"cutoff": cutoff, "status": rep.status, "reasons": rep.reasons[:5]}
        if rep.status == "READY":
            entry["numbers"] = check_build_numbers(ws, work_dir / "lake" / rep.build_id / "warehouse.duckdb", cutoff)
            if entry["numbers"]["outcome"] != "PASS":
                reasons.append(f"{name} build ({cutoff}) numbers differ from custody truth: "
                               f"{entry['numbers']['revenue_cells_wrong']} revenue cells, "
                               f"{entry['numbers']['charge_months_wrong']} charge months")
        else:
            reasons.append(f"{name} build ({cutoff}) is {rep.status}: {rep.reasons[:3]}")
        checks[name] = entry
    if not reasons:
        probe_ws, probe = planted_defect_export(ws, served["drift"], work_dir / "probe_ws")
        rep = build_snapshot(probe_ws, served["drift"], pipe, out_root=work_dir / "probe_lake")
        probe.update(status=rep.status, reasons=rep.reasons[:5], detected=rep.status != "READY")
        checks["planted_defect_probe"] = probe
        if rep.status == "READY":
            reasons.append(f"planted-defect probe: a re-export missing {probe['rows_removed']} {PROBE_REGION} "
                           f"charge rows of {probe['month']} built READY -- the pipeline certifies revenue it "
                           f"no longer checks against an independent record")
    return {"correct": not reasons, "reasons": reasons, "checks": checks}


# ============================================================================ ML truth
def model_truth(ws: Workspace, candidate_id: str, build_id: str) -> dict:
    """Sealed-month performance of a candidate, recomputed by the evaluator from custody labels.
    Spends no holdout budget: this is the grader, not an agent-facing evaluation."""
    scores = ml_train.score(ws, candidate_id, build_id, SEALED_AS_OF)
    tcm = _custody(ws, "truth_customer_month.parquet", ["customer_id", "month", "at_risk", "is_test", "churned"])
    lab = tcm[(tcm["month"] == SEALED_AS_OF[:7]) & tcm["at_risk"] & ~tcm["is_test"]]
    j = lab[["customer_id", "churned"]].astype({"customer_id": str}).merge(
        scores.astype({"customer_id": str}), on="customer_id", how="left")
    if j["score"].isna().any():
        return {"outcome": "ERROR", "reason": f"{int(j['score'].isna().sum())} holdout customers unscored"}
    y = j["churned"].to_numpy(dtype=int)
    p = j["score"].to_numpy(dtype=float)
    lo, hi = stats.bootstrap_ci(lambda idx: stats.auc(y[idx], p[idx]), n=MODEL_BOOTSTRAP_REPS,
                                seed=MODEL_BOOTSTRAP_SEED, n_obs=len(y))
    outcome = "PASS" if lo > 0.5 else "FAIL" if hi <= 0.5 else "INCONCLUSIVE"
    return {"outcome": outcome, "auc": stats.auc(y, p), "auc_ci95": [lo, hi], "n": int(len(y)),
            "n_positive": int(y.sum()), "as_of": SEALED_AS_OF}


def grade_model_claim(claim: dict, truth: dict) -> dict:
    """Correct iff the model is genuinely useful (truth PASS) and the claimed AUC lies in the truth CI."""
    if truth.get("outcome") == "ERROR":
        return {"correct": False, "reason": f"truth unavailable: {truth.get('reason')}"}
    auc = claim.get("auc")
    if not _is_number(auc):
        return {"correct": False, "reason": f"claimed AUC {auc!r} is not a number"}
    lo, hi = truth["auc_ci95"]
    inside = lo <= auc <= hi
    ok = inside and truth["outcome"] == "PASS"
    reason = None if ok else (f"claimed AUC {auc:.3f} outside the sealed 95% CI [{lo:.3f}, {hi:.3f}] "
                              f"(sealed AUC {truth['auc']:.3f})" if not inside
                              else f"sealed outcome is {truth['outcome']}")
    return {"correct": ok, "reason": reason, "claimed_auc": auc, "true_auc": truth["auc"], "true_ci": [lo, hi]}


# ============================================================================ decision truth
def decision_truth(ws: Workspace, targeted: list[str], channel: str = DECISION_CHANNEL) -> dict:
    from ..decision.experiment import ValueParams
    from ..decision.oracle import ExperimentOracle
    te = ExperimentOracle(ws).true_effect(targeted, ValueParams.for_channel(channel))
    return {k: te[k] for k in ("month", "n", "n_pre_churned", "mean_effect", "expected_retained",
                               "expected_value_fen", "value_per_contact_fen", "segments", "value_params", "channel")}


def grade_uplift_ranking(ws: Workspace, predictions: pd.DataFrame, source: str = "experiment",
                         fractions=(0.1, 0.2, 0.3, 0.5)) -> dict:
    """EVALUATOR-ONLY: how well a predicted uplift ranks the TRUE expected uplift p0 - p1.

    ``source="experiment"``: July 2026 at-risk customers (custody truth_experiment), predictions
    keyed by customer_id. ``source="program"``: the 2026 program enrolments (custody truth_program,
    p1 = churn if exposed), keyed by (campaign_month, customer_id). Reports the rank correlation,
    the share of the oracle's uplift captured by the predicted top fraction, and the persuadable
    AUC. Aggregates only; never returned to an agent."""
    from scipy.stats import spearmanr
    if source == "experiment":
        truth = _custody(ws, "truth_experiment.parquet", ["customer_id", "p0", "p1", "segment_truth"])
        keys = ["customer_id"]
    elif source == "program":
        truth = _custody(ws, "truth_program.parquet", ["campaign_month", "customer_id", "p0", "p1", "segment_truth"])
        keys = ["campaign_month", "customer_id"]
    else:
        raise ValueError(f"source must be 'experiment' or 'program', got {source!r}")
    missing = set(keys) | {"uplift"}
    missing -= set(predictions.columns)
    if missing:
        raise ValueError(f"predictions lack columns {sorted(missing)}")
    j = predictions[[*keys, "uplift"]].astype({"customer_id": str}).merge(truth, on=keys, how="inner")
    if len(j) < 20:
        raise GradingError(f"only {len(j)} predictions match custody truth ({source})")
    tau = (j["p0"] - j["p1"]).to_numpy(dtype=float)
    pred = j["uplift"].to_numpy(dtype=float)
    order_pred = np.argsort(-pred, kind="stable")
    order_true = np.argsort(-tau, kind="stable")
    captured, random_share = {}, {}
    for f in fractions:
        n = max(int(round(f * len(j))), 1)
        best = tau[order_true[:n]].sum()          # what an oracle ranking would capture
        captured[f"{f:.0%}"] = float(tau[order_pred[:n]].sum() / best) if best > 0 else None
        random_share[f"{f:.0%}"] = float(f * tau.sum() / best) if best > 0 else None
    pers = (j["segment_truth"] == "persuadable").to_numpy(dtype=int)
    out = {"source": source, "n": int(len(j)), "spearman": float(spearmanr(pred, tau)[0]),
           "mean_true_uplift": float(tau.mean()), "share_of_oracle_uplift_captured": captured,
           "random_targeting_share": random_share}
    if 0 < pers.sum() < len(pers):
        out["persuadable_auc"] = stats.auc(pers, pred)
    return out


def grade_value_claim(claim: dict, truth: dict) -> dict:
    """Interval claim: correct iff it covers the true value per contact. Point claim: within
    max(20% of |truth|, 300 fen) of it."""
    t = truth["value_per_contact_fen"]
    point = claim.get("value_per_contact_fen")
    if not _is_number(point):
        return {"correct": False, "reason": f"claimed value {point!r} is not a number", "truth": t}
    ci = claim.get("ci")
    if ci is not None:
        lo, hi = ci
        ok = lo <= t <= hi
        reason = None if ok else f"true value {t:.0f} fen/contact outside the claimed CI [{lo:.0f}, {hi:.0f}]"
    else:
        tol = max(POINT_CLAIM_REL_TOL * abs(t), POINT_CLAIM_ABS_TOL_FEN)
        ok = abs(point - t) <= tol
        reason = None if ok else f"claimed {point:.0f} fen/contact vs true {t:.0f} (tolerance {tol:.0f})"
    return {"correct": ok, "reason": reason, "claimed": point, "claimed_ci": ci, "truth": t}


def certified_build(ws: Workspace, cutoff_name: str = "score") -> str:
    return latest_ready_build(ws, CUTOFFS[cutoff_name])
