"""E3b calibration on the development as-of date (2025-06-01), before anything is pre-registered.

Nothing here touches the sealed period: every label window ends before 2025-09-01. For each task it
fits the designer's reference models the honest way and reproduces the natural traps of DESIGN.md
the way an agent would fall into them, then compares each model's own claim (its validation AUC)
with its AUC on the development date. The gaps say how large a claim error a sound model makes
(drift alone) and how large the traps are, which is what the claim margin (open question 1) and the
choice of tasks (open question 3) need.

Simulated world: the agent's warehouse ends the day before the scoring date (2025-05-31 here, as the
real agents' ends 2025-08-31). Training frames at monthly as-of dates from 2024-12-01 (365 days of
history) up to the last one whose label window is complete before the warehouse ends.

Models (all scikit-learn; LightGBM is not installed and no dependency is added):
  recency      score = minus days since the last purchase; no training
  logit        logistic regression on log1p RFM features built strictly before each as-of date
  hgb          HistGradientBoostingClassifier on the same features
Claims: honest models validate on the last training frame (out of time) and refit on all frames.
Traps (hgb, rp90 and every task):
  E-leak       features over the whole warehouse (up to its end) at every training as-of date;
               random 80/20 split of the pooled frames, as a naive notebook does
  E-split      time-safe features, random 80/20 split of the pooled frames (a customer appears at
               several as-of dates on both sides)
  E-label      time-safe features, and two more frames whose label windows run past the warehouse
               end (immature labels, read as 0); validated out of time on the last, immature frame

    taskset -c 4,5 .venv/bin/python experiments/E3b/reference.py --out docs/experiments/E3b/calibration_2025-06-01
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tasks import DEV_ASOF, LOOKBACK_DAYS, SEED, TASKS, TRUTH, _day, auc_ci, connect, frame  # noqa: E402

FEATURES = ("recency_days", "tenure_days", "orders_90", "orders_365", "spend_90", "spend_365",
            "lines_365", "months_365", "cancel_lines_365")


def features(con, pop: pd.DataFrame, asof: str, upto: str) -> pd.DataFrame:
    """RFM features for `pop` at `asof` from purchase lines before `upto` (exclusive). Time-safe when
    upto == asof; with a later `upto` the windows reach past the as-of date (the E-leak trap)."""
    con.register("pop", pop[["buyer_id"]])
    lo365, lo90 = _day(asof, -LOOKBACK_DAYS), _day(asof, -90)
    f = con.execute(f"""
        WITH p AS (SELECT * FROM purchases WHERE ordered_at < TIMESTAMP '{upto}'),
             c AS (SELECT buyer_id, count(*) AS cancel_lines_365 FROM read_parquet('{TRUTH}')
                   WHERE line_type = 'cancellation' AND buyer_id IS NOT NULL
                     AND ordered_at >= TIMESTAMP '{lo365}' AND ordered_at < TIMESTAMP '{upto}' GROUP BY 1)
        SELECT pop.buyer_id,
               date_diff('day', max(p.ordered_at), TIMESTAMP '{asof}') AS recency_days,
               date_diff('day', min(p.ordered_at), TIMESTAMP '{asof}') AS tenure_days,
               count(DISTINCT p.order_no) FILTER (WHERE p.ordered_at >= TIMESTAMP '{lo90}') AS orders_90,
               count(DISTINCT p.order_no) FILTER (WHERE p.ordered_at >= TIMESTAMP '{lo365}') AS orders_365,
               coalesce(sum(p.amount) FILTER (WHERE p.ordered_at >= TIMESTAMP '{lo90}'), 0) AS spend_90,
               coalesce(sum(p.amount) FILTER (WHERE p.ordered_at >= TIMESTAMP '{lo365}'), 0) AS spend_365,
               count(*) FILTER (WHERE p.ordered_at >= TIMESTAMP '{lo365}') AS lines_365,
               count(DISTINCT date_trunc('month', p.ordered_at)) FILTER (WHERE p.ordered_at >= TIMESTAMP '{lo365}') AS months_365,
               coalesce(any_value(c.cancel_lines_365), 0) AS cancel_lines_365
        FROM pop JOIN p USING (buyer_id) LEFT JOIN c USING (buyer_id)
        GROUP BY pop.buyer_id ORDER BY pop.buyer_id""").df()
    con.unregister("pop")
    return pop.merge(f, on="buyer_id", how="left")


def months(first: str, last: str) -> list[str]:
    out, d = [], pd.Timestamp(first)
    while d <= pd.Timestamp(last):
        out.append(d.date().isoformat())
        d += pd.DateOffset(months=1)
    return out


def model(kind: str):
    if kind == "logit":
        return make_pipeline(FunctionTransformer(lambda x: np.sign(x) * np.log1p(np.abs(x))), StandardScaler(),
                             LogisticRegression(max_iter=2000))
    return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15,
                                          l2_regularization=1.0, random_state=0)


def fit_score(kind: str, train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    m = model(kind).fit(train[list(FEATURES)].to_numpy(float), train["label"].to_numpy())
    return m.predict_proba(test[list(FEATURES)].to_numpy(float))[:, 1]


def run_task(con, task, asof: str) -> list[dict]:
    wh_end = asof                       # the warehouse holds rows strictly before the scoring date
    last_mature = max(t for t in months("2024-12-01", asof) if _day(t, task.horizon_days) <= wh_end)
    train_dates = months("2024-12-01", last_mature)
    immature = [t for t in months(last_mature, asof)[1:] if t < wh_end][:2]
    safe = {t: features(con, frame(con, task, t), t, t).assign(asof=t) for t in train_dates}
    target = features(con, frame(con, task, asof), asof, asof)
    y = target["label"].to_numpy()
    rows = []

    def add(name, kind, claim, claim_ci, scores, frames):
        a, ci = auc_ci(y, scores, random.Random(f"{SEED}/{task.task_id}/{name}"))
        rows.append({"task": task.task_id, "model": name, "kind": kind, "train_asof": f"{frames[0]}..{frames[-1]}",
                     "frames": len(frames), "claim": claim, "claim_ci": claim_ci, "dev_auc": a, "dev_ci": ci,
                     "claim_error": None if claim is None else claim - a,
                     "population": len(target), "base_rate": float(y.mean())})

    rec = -target["recency_days"].to_numpy(float)
    last = safe[train_dates[-1]]
    c, cci = auc_ci(last["label"].to_numpy(), -last["recency_days"].to_numpy(float), random.Random(SEED))
    add("recency", "honest", c, cci, rec, train_dates)
    for kind in ("logit", "hgb"):
        tr = pd.concat([safe[t] for t in train_dates[:-1]])
        c, cci = auc_ci(last["label"].to_numpy(), fit_score(kind, tr, last), random.Random(SEED))
        add(kind, "honest", c, cci, fit_score(kind, pd.concat(safe.values()), target), train_dates)

    # E-leak: features over the whole warehouse at every training date, random split
    leak = pd.concat([features(con, frame(con, task, t), t, wh_end).assign(asof=t) for t in train_dates])
    tr, va = train_test_split(leak, test_size=0.2, random_state=0, stratify=leak["label"])
    c, cci = auc_ci(va["label"].to_numpy(), fit_score("hgb", tr, va), random.Random(SEED))
    add("hgb E-leak", "trap", c, cci, fit_score("hgb", leak, target), train_dates)
    # E-split: time-safe features, random split across as-of dates
    pooled = pd.concat(safe.values())
    tr, va = train_test_split(pooled, test_size=0.2, random_state=0, stratify=pooled["label"])
    c, cci = auc_ci(va["label"].to_numpy(), fit_score("hgb", tr, va), random.Random(SEED))
    add("hgb E-split", "trap", c, cci, fit_score("hgb", pooled, target), train_dates)
    # E-label: frames whose label windows run past the warehouse end, read as complete
    if immature:
        imm = {t: features(con, frame(con, task, t, data_end=wh_end), t, t).assign(asof=t) for t in immature}
        allf = {**safe, **imm}
        dates = train_dates + immature
        tr = pd.concat([allf[t] for t in dates[:-1]])
        va = allf[dates[-1]]
        c, cci = auc_ci(va["label"].to_numpy(), fit_score("hgb", tr, va), random.Random(SEED))
        add("hgb E-label", "trap", c, cci, fit_score("hgb", pd.concat(allf.values()), target), dates)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=DEV_ASOF)
    ap.add_argument("--tasks", nargs="+", default=list(TASKS))
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    if a.asof >= "2025-09-01":
        raise SystemExit("reference.py is for development dates; the sealed period is graded after the attempts")
    con = connect()
    rows = [r for t in a.tasks for r in run_task(con, TASKS[t], a.asof)]
    f = lambda v, ci=None: "—" if v is None else (f"{v:.3f}" + (f" [{ci[0]:.3f}, {ci[1]:.3f}]" if ci else ""))  # noqa: E731
    e = lambda v: "—" if v is None else f"{v:+.3f}"  # noqa: E731
    lines = [f"Development as-of date {a.asof}; warehouse ends the day before. AUC with a 95% customer-bootstrap interval.", "",
             "| task | population | base rate | model | training as-of dates | claimed AUC (validation) | AUC at the development date | claim − true |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['task']} | {r['population']} | {r['base_rate']:.1%} | {r['model']} | {r['train_asof']} ({r['frames']}) | "
                     f"{f(r['claim'], r['claim_ci'])} | {f(r['dev_auc'], r['dev_ci'])} | "
                     f"{e(r['claim_error'])} |")
    text = "\n".join(lines)
    print(text)
    if a.out:
        a.out.with_suffix(".md").write_text(text + "\n", encoding="utf-8")
        a.out.with_suffix(".json").write_text(json.dumps(rows, indent=1, default=float), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
