"""Sealed final evaluation of churn candidates against custody ground truth.

Trust boundary: this is the ONLY library code path that reads custody labels. It receives a
candidate id and a build id, never data; it scores through the trusted template
(`ml.train.score`, which verifies the registered artifact digest before unpickling), and
only then reads the holdout labels. What leaves this module is aggregate: metrics,
bootstrap intervals, per-region AUC and coverage counts -- never per-customer labels or
scores. Every look at the holdout spends budget from an append-only ledger in `custody/`;
a sealed set that can be queried without limit becomes a training set by attrition.

"Every look" includes the looks that end in an ERROR: everything that can be checked without
custody (candidate integrity, the scoring build, score coverage of the build's own population)
is checked first and refused for free; once the holdout labels have been read, the call is
charged, and an ERROR from that point carries a fixed reason with no holdout-derived number
(the detail stays in the custody ledger for the evaluator's operators).

Preconditions are enforced, not assumed: the scoring build must have its cutoff on the day
before the holdout month (a later build would leak June into features), and the candidate
must have been trained on a build that ends before the holdout month.

Production mapping: the evaluator runs under its own service identity with the custody
mount; candidates score in a sandbox without it and hand over only the score table.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
import fcntl

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import rankdata
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from ..common import append_jsonl, read_jsonl, utcnow
from ..lake.build import warehouse_path
from ..workspace import Workspace
from . import features as F
from .features import FeatureError
from .train import CandidateIntegrityError, candidate_record, ready_build_cutoff, score

# task id -> holdout month start. A new task id must not be a fresh budget for an old holdout.
TASKS = {"churn-2026-06": "2026-06-01"}
LEDGER = "ledger.jsonl"   # under custody/


class NotEvaluable(Exception):
    """The candidate cannot be evaluated, found BEFORE any custody read: refused without spending
    budget. The message may describe the candidate and the scoring build, never the holdout."""


# Fixed reasons for an ERROR after the holdout labels were read: no counts, rates or ids.
HOLDOUT_UNSCORED = ("some holdout customers are outside the scoring build's population and have no score; "
                    "refusing a biased subset (the look was charged; no holdout-derived counts are released)")
HOLDOUT_SINGLE_CLASS = "the holdout has a single label class; no metric is defined (the look was charged)"
HOLDOUT_FAILED = ("the sealed evaluation failed after reading the holdout (the look was charged; the detail is "
                  "recorded for the evaluator's operators and not released)")


class SealedEvaluator:
    FINAL_AS_OF = "2026-06-01"
    BUDGET_PER_TASK = 3
    BOOTSTRAP_REPS = 500
    BOOTSTRAP_SEED = 20260601
    ECE_BINS = 10
    CRITERION = "auc_ci95_lo > 0.5"

    def __init__(self, ws: Workspace, task_id: str = "churn-2026-06"):
        if TASKS.get(task_id) != self.FINAL_AS_OF:
            raise ValueError(f"unknown sealed task {task_id!r}; known: {sorted(TASKS)}")
        self.ws = ws
        self.task_id = task_id
        self._ledger = ws.custody / LEDGER

    # ------------------------------------------------------------------ budget
    def _consumed(self, entries: list[dict]) -> list[dict]:
        return [e for e in entries if e.get("task_id") == self.task_id and e.get("status") == "consumed"]

    def budget_used(self) -> int:
        return len(self._consumed(read_jsonl(self._ledger)))

    def budget_remaining(self) -> int:
        return max(0, self.BUDGET_PER_TASK - self.budget_used())

    @contextmanager
    def _locked(self):
        """Exclusive lock over check-evaluate-append, so concurrent calls cannot overspend."""
        self._ledger.parent.mkdir(parents=True, exist_ok=True)
        with open(self._ledger.with_suffix(".lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield read_jsonl(self._ledger)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _log(self, status: str, candidate_id: str, build_id: str, **extra) -> None:
        append_jsonl(self._ledger, {"task_id": self.task_id, "status": status, "candidate_id": candidate_id,
                                    "build_id": build_id, "ts": utcnow(), **extra})

    def _budget(self, used: int) -> dict:
        return {"used": used, "limit": self.BUDGET_PER_TASK, "remaining": max(0, self.BUDGET_PER_TASK - used)}

    def _error(self, reason: str, candidate_id: str, build_id: str, used: int, status: str) -> dict:
        self._log(status, candidate_id, build_id, reason=reason)
        return {"outcome": "ERROR", "reason": reason, "task_id": self.task_id, "candidate_id": candidate_id,
                "build_id": build_id, "budget": self._budget(used)}

    # ------------------------------------------------------------------ API
    def evaluate(self, candidate_id: str, build_id: str) -> dict:
        with self._locked() as entries:
            consumed = self._consumed(entries)
            used = len(consumed)
            try:
                rec = candidate_record(self.ws, candidate_id)
            except KeyError as exc:
                return self._error(f"unknown candidate: {exc}", candidate_id, build_id, used, "rejected")
            # Re-evaluating the identical artifact reveals nothing new: return the recorded result.
            for e in consumed:
                if (e["candidate_id"], e["build_id"], e.get("artifact_digest")) == (
                        candidate_id, build_id, rec["artifact_digest"]):
                    return {**e["result"], "cached": True, "budget": self._budget(used)}
            if used >= self.BUDGET_PER_TASK:
                return self._error("holdout budget exhausted", candidate_id, build_id, used, "refused")
            problem = self._precondition_problem(rec, build_id)
            if problem:
                return self._error(problem, candidate_id, build_id, used, "rejected")
            try:
                result, internal = self._evaluate_sealed(candidate_id, build_id, rec)
            except CandidateIntegrityError as exc:
                return self._error(f"candidate integrity: {exc}", candidate_id, build_id, used, "rejected")
            except (FeatureError, duckdb.Error) as exc:
                return self._error(f"scoring failed: {type(exc).__name__}: {str(exc).splitlines()[0][:300]}",
                                   candidate_id, build_id, used, "rejected")
            except NotEvaluable as exc:            # found before the custody read: nothing was looked at
                return self._error(str(exc), candidate_id, build_id, used, "rejected")
            # _evaluate_sealed returns only after reading the holdout labels: an ERROR is a look too
            used += 1
            result["budget"] = self._budget(used)
            self._log("consumed", candidate_id, build_id, artifact_digest=rec["artifact_digest"], result=result,
                      **({"internal_reason": internal} if internal else {}))
            return result

    def _precondition_problem(self, rec: dict, build_id: str) -> str | None:
        last_known_day = (F.parse_day(self.FINAL_AS_OF) - timedelta(days=1)).isoformat()
        try:
            cutoff = ready_build_cutoff(self.ws, build_id)
        except (ValueError, RuntimeError, FileNotFoundError) as exc:
            return f"scoring build unusable: {exc}"
        if cutoff != last_known_day:
            return (f"sealed evaluation scores the population at {self.FINAL_AS_OF} on a build with cutoff "
                    f"{last_known_day}; build {build_id} has cutoff {cutoff}")
        if rec["build_cutoff"] > last_known_day:
            return (f"candidate was trained on build {rec['build_id']} (cutoff {rec['build_cutoff']}), which "
                    f"overlaps the holdout month starting {self.FINAL_AS_OF}")
        late = [a for a in rec["spec"]["train_as_of"] if a >= self.FINAL_AS_OF]
        if late:
            return f"candidate training months {late} overlap the holdout month"
        return None

    def _holdout_labels(self) -> pd.DataFrame:
        """customer_id, churned for non-test customers at risk in the holdout month (custody)."""
        month = self.FINAL_AS_OF[:7]
        truth = pd.read_parquet(self.ws.custody / "truth_customer_month.parquet",
                                columns=["customer_id", "month", "at_risk", "is_test", "churned"])
        sel = truth[(truth["month"] == month) & truth["at_risk"] & ~truth["is_test"]]
        if sel.empty:
            raise RuntimeError(f"custody has no at-risk customers for {month}")
        return pd.DataFrame({"customer_id": sel["customer_id"].astype(str).values,
                             "churned": sel["churned"].astype(int).values})

    def _evaluate_sealed(self, candidate_id: str, build_id: str, rec: dict) -> tuple[dict, str | None]:
        """(result, internal detail for the ledger). Raises before the custody read (NotEvaluable,
        CandidateIntegrityError, FeatureError, duckdb.Error: no budget spent) and when custody cannot
        be opened (OSError: nothing was read); once the holdout labels are read it always returns, and
        an ERROR result carries a fixed reason only."""
        scores = score(self.ws, candidate_id, build_id, self.FINAL_AS_OF)       # before any custody read
        if candidate_record(self.ws, candidate_id)["artifact_digest"] != rec["artifact_digest"]:
            raise CandidateIntegrityError(f"candidate {candidate_id} was re-registered during evaluation")
        with F.readonly_connection(warehouse_path(self.ws, build_id)) as con:
            regions = con.execute("SELECT customer_id, region FROM silver.customers").df()
            population = F.population(con, self.FINAL_AS_OF).astype(str)
        scores = scores.assign(customer_id=scores["customer_id"].astype(str))
        regions = regions.assign(customer_id=regions["customer_id"].astype(str))
        self._check_scores(scores, population, build_id)                        # still no custody read
        base = {"task_id": self.task_id, "candidate_id": candidate_id, "build_id": build_id,
                "as_of": self.FINAL_AS_OF, "artifact_digest": rec["artifact_digest"],
                "feature_origins": rec.get("feature_origins", {})}
        try:
            return self._score_holdout(base, scores, regions)
        except OSError:              # custody could not be opened: nothing was read, so nothing is charged
            raise
        except Exception as exc:     # after the custody read only a fixed reason may leave this module
            return ({**base, "outcome": "ERROR", "reason": HOLDOUT_FAILED},
                    f"{type(exc).__name__}: {str(exc).splitlines()[0][:300] if str(exc) else ''}")

    @staticmethod
    def _check_scores(scores: pd.DataFrame, population: pd.Series, build_id: str) -> None:
        """Scores must be one finite probability per member of the scoring build's own population at
        FINAL_AS_OF. Checked against the build, not custody, so a refusal here reveals nothing sealed."""
        ids = scores["customer_id"]
        if ids.duplicated().any():
            raise NotEvaluable(f"scores repeat {int(ids.duplicated().sum())} customer ids")
        p = pd.to_numeric(scores["score"], errors="coerce").to_numpy(dtype=float)
        if not np.all(np.isfinite(p)) or np.any((p < 0) | (p > 1)):
            raise NotEvaluable("scores must be finite probabilities in [0, 1]")
        pop, have = set(population), set(ids)
        missing, extra = len(pop - have), len(have - pop)
        if missing or extra:
            raise NotEvaluable(f"scores do not cover build {build_id}'s own population at the holdout month: "
                               f"{missing} population members unscored, {extra} scores outside it")

    def _score_holdout(self, base: dict, scores: pd.DataFrame, regions: pd.DataFrame) -> tuple[dict, str | None]:
        labels = self._holdout_labels()                                          # the custody read
        joined = labels.merge(scores, on="customer_id", how="left", validate="one_to_one")
        n_unscored = int(joined["score"].isna().sum())
        if n_unscored:
            return ({**base, "outcome": "ERROR", "reason": HOLDOUT_UNSCORED},
                    f"{n_unscored} holdout customers have no score")
        joined = joined.merge(regions, on="customer_id", how="left", validate="one_to_one")
        y = joined["churned"].to_numpy(dtype=int)
        p = joined["score"].to_numpy(dtype=float)
        if len(np.unique(y)) < 2:
            return {**base, "outcome": "ERROR", "reason": HOLDOUT_SINGLE_CLASS}, "holdout has a single label class"

        auc_ci, brier_ci, reps_used = self._bootstrap(y, p)
        metrics = {
            "auc": float(roc_auc_score(y, p)), "brier": float(brier_score_loss(y, p)),
            "logloss": float(log_loss(y, p, labels=[0, 1])), "ece": expected_calibration_error(y, p, self.ECE_BINS),
            "base_rate": float(y.mean()), "n": int(len(y)), "n_positive": int(y.sum()),
            "auc_ci95": auc_ci, "brier_ci95": brier_ci,
            "per_region_auc": self._per_region(joined),
        }
        if auc_ci[0] > 0.5:
            outcome = "PASS"
        elif auc_ci[1] <= 0.5:
            outcome = "FAIL"
        else:
            outcome = "INCONCLUSIVE"
        return {**base, "outcome": outcome, "criterion": self.CRITERION, "metrics": metrics,
                "bootstrap": {"reps": self.BOOTSTRAP_REPS, "reps_used": reps_used, "seed": self.BOOTSTRAP_SEED,
                              "unit": "customer"},
                "coverage": {"n_holdout": int(len(labels)), "n_scored": int(len(scores)),
                             "n_scored_outside_holdout": int(len(scores) - len(labels))},
                "limitations": [
                    "one holdout month; the interval reflects customer sampling, not month-to-month drift",
                    "synthetic world: demonstrates the mechanism, not real-world accuracy"]}, None

    def _bootstrap(self, y: np.ndarray, p: np.ndarray) -> tuple[list[float], list[float], int]:
        rng = np.random.default_rng(self.BOOTSTRAP_SEED)
        aucs, briers = [], []
        n = len(y)
        for _ in range(self.BOOTSTRAP_REPS):
            idx = rng.integers(0, n, n)
            yb, pb = y[idx], p[idx]
            briers.append(float(np.mean((pb - yb) ** 2)))
            if 0 < yb.sum() < n:              # AUC undefined for a single-class resample
                aucs.append(rank_auc(yb, pb))
        if len(aucs) < 0.9 * self.BOOTSTRAP_REPS:
            raise RuntimeError(f"only {len(aucs)} of {self.BOOTSTRAP_REPS} bootstrap resamples had both classes")
        return _ci95(aucs), _ci95(briers), len(aucs)

    @staticmethod
    def _per_region(joined: pd.DataFrame) -> dict:
        out = {}
        for region, g in joined.groupby(joined["region"].fillna("unknown"), sort=True):
            y = g["churned"].to_numpy(dtype=int)
            auc = float(roc_auc_score(y, g["score"].to_numpy(dtype=float))) if 0 < y.sum() < len(y) else None
            out[str(region)] = {"auc": auc, "n": int(len(y)), "n_positive": int(y.sum())}
        return out


def _ci95(values) -> list[float]:
    return [float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))]


def rank_auc(y, p) -> float:
    """Mann-Whitney AUC with average ranks for ties (same value as sklearn's roc_auc_score,
    without its per-call validation overhead -- used inside the bootstrap loop)."""
    y = np.asarray(y, dtype=int)
    r = rankdata(np.asarray(p, dtype=float))
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        raise ValueError("AUC needs both classes")
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def expected_calibration_error(y, p, bins: int = 10) -> float:
    """Equal-width-bin ECE: sum_b (n_b / n) * |mean(y_b) - mean(p_b)|."""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    idx = np.clip(np.digitize(p, np.linspace(0.0, 1.0, bins + 1)[1:-1]), 0, bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.sum() / len(y) * abs(y[m].mean() - p[m].mean())
    return float(total)
