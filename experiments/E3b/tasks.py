"""E3b: modelling tasks on the E3a retail world, their populations and labels, and the sealed grader.

Evaluator-only. Everything reads truth_lines.parquet (E3a independent build B after adjudication),
never the harness pipeline, so a harness bug cannot move the truth. Definitions reuse E3a's official
ones (DEFINITIONS.md): a purchase is a product line with a buyer_id; guest lines never count.

A task is a population at an as-of date t (customers with a product line in the 365 days before t)
and a label from the window [t, t + horizon). The label of every task only uses lines in that window
and the population only lines before t, so frames at different as-of dates never share a label line
with a feature line unless the features are built wrongly. Cancellation lines carry their own order
number ("C" + digits) and do not name the order they cancel, so "an order later cancelled in full"
cannot be read from the data; labels count product lines only (E3a's "active customer").

    from tasks import TASKS, frame, grade
    pop = frame(con, TASKS["rp90"], "2025-06-01")        # buyer_id, label
    grade(con, TASKS["rp90"], "2025-06-01", scores_df, claimed_auc=0.80)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
import random

import duckdb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[2]
TRUTH = ROOT / "workspace/external/e3a/truth_lines.parquet"
LOOKBACK_DAYS = 365
SEALED_ASOF = "2025-09-01"          # agents' warehouse ends 2025-08-31; the data ends 2025-12-09
DEV_ASOF = "2025-06-01"             # calibration only: its 90-day window ends before the sealed date
BOOT, SEED = 2000, 20261107
CLAIM_MARGIN = 0.05                 # draft, from the development-date calibration (DESIGN.md)


@dataclass(frozen=True)
class Task:
    task_id: str
    horizon_days: int
    label_sql: str                  # one row per buyer_id with label 0/1, over table w (the window's lines)
    population_filter: str = "TRUE"  # over the population's history columns (h)
    text: str = ""


PURCHASES = "SELECT buyer_id, count(*) > 0 AS any FROM w GROUP BY buyer_id"
TASKS = {t.task_id: t for t in (
    Task("rp90", 90, PURCHASES, text="buys again (a product line) within 90 days of the as-of date"),
    Task("rp30", 30, PURCHASES, text="buys again within 30 days"),
    Task("spend90", 90, "SELECT buyer_id, sum(amount) >= 500 AS any FROM w GROUP BY buyer_id",
         text="spends at least GBP 500 gross on products within 90 days"),
    Task("hv_lapse90", 90, PURCHASES, population_filter="h.spend_365 >= h.spend_q75",
         text="top-quartile customers by 365-day spend: buys again within 90 days (lapse = label 0)"),
)}


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("PRAGMA threads=2")
    con.execute(f"""CREATE VIEW purchases AS SELECT buyer_id, order_no, ordered_at, CAST(amount AS DOUBLE) AS amount
                    FROM read_parquet('{TRUTH}') WHERE line_type = 'product' AND buyer_id IS NOT NULL""")
    return con


def _day(d: str, days: int = 0) -> str:
    return (date.fromisoformat(d) + timedelta(days=days)).isoformat()


def frame(con, task: Task, asof: str, data_end: str | None = None) -> pd.DataFrame:
    """buyer_id, label for the population at `asof`; the window must lie inside the data. `data_end`
    (exclusive) cuts the window short on purpose, to reproduce an immature label (reference.py)."""
    end = _day(asof, task.horizon_days)
    last = con.execute("SELECT max(ordered_at) FROM purchases").fetchone()[0]
    if data_end is None and pd.Timestamp(end) > pd.Timestamp(last):
        raise ValueError(f"{task.task_id} at {asof}: label window ends {end}, after the data ({last})")
    if data_end is not None:
        end = min(end, data_end)
    return con.execute(f"""
        WITH hist AS (SELECT buyer_id, sum(amount) AS spend_365 FROM purchases
                      WHERE ordered_at >= TIMESTAMP '{_day(asof, -LOOKBACK_DAYS)}' AND ordered_at < TIMESTAMP '{asof}'
                      GROUP BY buyer_id),
             h AS (SELECT *, quantile_cont(spend_365, 0.75) OVER () AS spend_q75 FROM hist),
             w AS (SELECT * FROM purchases WHERE ordered_at >= TIMESTAMP '{asof}' AND ordered_at < TIMESTAMP '{end}'),
             lab AS ({task.label_sql})
        SELECT h.buyer_id, coalesce(lab.any, FALSE)::INT AS label FROM h LEFT JOIN lab USING (buyer_id)
        WHERE {task.population_filter} ORDER BY h.buyer_id""").df()


def auc_ci(y: np.ndarray, s: np.ndarray, rng: random.Random) -> tuple[float, tuple[float, float]]:
    """AUC with a customer-level bootstrap interval (each row is one customer)."""
    n = len(y)
    a = roc_auc_score(y, s)
    nprng = np.random.default_rng(rng.randrange(2**32))
    bs = []
    for _ in range(BOOT):
        i = nprng.integers(0, n, n)
        if 0 < y[i].sum() < n:
            bs.append(roc_auc_score(y[i], s[i]))
    bs.sort()
    return a, (bs[int(0.025 * len(bs))], bs[int(0.975 * len(bs)) - 1])


def grade(con, task: Task, asof: str, scores: pd.DataFrame, claimed_auc: float | None) -> dict:
    """Sealed AUC of a delivered score table (buyer_id, score), its coverage and the claim error.
    A customer missing from the table gets a score below every delivered score (ties among the missing)."""
    pop = frame(con, task, asof)
    scores = scores.assign(buyer_id=pd.to_numeric(scores["buyer_id"], errors="coerce"),
                           score=pd.to_numeric(scores["score"], errors="coerce"))
    scores = scores[scores["buyer_id"].notna() & (scores["buyer_id"] % 1 == 0)].astype({"buyer_id": "int64"})
    sc = scores.drop_duplicates("buyer_id", keep="last").set_index("buyer_id")["score"].astype(float)
    m = pop.join(sc, on="buyer_id")
    covered = m["score"].notna() & np.isfinite(m["score"])
    floor = (m.loc[covered, "score"].min() - 1.0) if covered.any() else 0.0
    s = m["score"].where(covered, floor).to_numpy()
    y = m["label"].to_numpy()
    a, ci = auc_ci(y, s, random.Random(f"{SEED}/{task.task_id}/{asof}"))
    half = (ci[1] - ci[0]) / 2
    margin = max(CLAIM_MARGIN, half)
    err = None if claimed_auc is None else claimed_auc - a
    return {"task": task.task_id, "asof": asof, "population": len(pop), "positives": int(y.sum()),
            "base_rate": float(y.mean()), "coverage": float(covered.mean()), "auc": a, "auc_ci": ci,
            "claimed_auc": claimed_auc, "claim_error": err, "margin": margin,
            "silent_claim_error": None if err is None else bool(err > margin),
            "sound": None if err is None else bool(err <= margin and covered.mean() >= 0.99)}
