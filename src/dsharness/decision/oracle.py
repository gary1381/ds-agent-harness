"""Experiment oracle: plays "the world" for the July-2026 retention experiment. CUSTODY ONLY.

Why this exists: in production the outcomes of an experiment arrive from reality a month
later. In the benchmark world they come from custody truth, which also knows each customer's
true potential outcomes (p0 without contact, p1 with contact). That lets the evaluator grade a
value claim against the *true* effect instead of against another estimate.

Trust boundary: this module reads ``custody/`` and must only run evaluator-side (bench runner,
demo controller). Agent tools and candidate code must never import it. It has two kinds of
accessor with different rules:

  * ``outcomes`` is the experiment channel. It returns only what a real experiment would
    observe (customer_id, churned) and is ONE-SHOT per ``experiment_id``: reality reveals one
    potential outcome per customer, so an oracle that answers a second, different assignment
    for the same experiment would reveal both and turn the experiment into a lookup of the
    individual treatment effect. The first call records (experiment_id, assignment digest,
    timestamp, outcomes) in ``custody/experiment_ledger.jsonl`` (append-only, file-locked);
    repeating the identical assignment returns the recorded outcomes; a different assignment
    for a used id raises ``ExperimentAlreadyRunError`` and the refusal is itself recorded.
    ``experiment_id`` must be issued by the controller (task / experiment registration), never
    chosen by the party whose claim is being graded. A fresh id is not a licence to re-randomise:
    each customer can be in at most ONE released experiment of the month. A new experiment whose
    assignment overlaps an earlier release raises ``CustomerAlreadyAssignedError`` (recorded as a
    refusal), so the same customer's other potential outcome is never revealed under another id.
    Several policies compared in one month therefore run as ONE experiment (one id, one combined
    assignment) on disjoint customer sets.
  * ``true_effect`` and ``segment_truth`` are BENCHMARK-ONLY truth accessors. They read p0/p1
    and the true segment directly, exist so the evaluator can grade a claim, and are never
    exposed to agents or tools, nor their results passed back to an agent.

Outcomes use the world's shared uniform draw ``u``: churned = u < p1 if treated else u < p0,
so the control arm reproduces exactly the churn recorded in custody truth for July.

Production mapping: the ledger is the experiment registry (pre-registered id + frozen
assignment) owned by the experimentation platform; outcome data is joined to it once.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import hashlib
import json
import re

import numpy as np
import pandas as pd

from ..common import append_jsonl, digest, utcnow
from ..workspace import Workspace
from .experiment import ARMS, ValueParams

PRE_CHURNED = "already_churned"
LEDGER = "experiment_ledger.jsonl"   # under custody/
_EXPERIMENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")
_TRUTH_COLUMNS = ["p0", "p1", "u", "price_fen", "segment_truth"]


class OracleError(RuntimeError):
    """Base class for oracle refusals and experiment-ledger integrity failures."""


class ExperimentAlreadyRunError(OracleError):
    """The experiment_id already released outcomes for a different assignment (one-shot rule)."""


class CustomerAlreadyAssignedError(OracleError):
    """Customers of a new experiment were already assigned in an earlier released experiment of the month."""


class ExperimentLedgerError(OracleError):
    """custody/experiment_ledger.jsonl is corrupt, edited, or contradicts the custody truth."""


def _check_experiment_id(experiment_id) -> str:
    if not isinstance(experiment_id, str) or not _EXPERIMENT_ID.fullmatch(experiment_id):
        raise ValueError(f"experiment_id must be 1-200 characters of [A-Za-z0-9._:/@+-] starting with a "
                         f"letter or digit, got {experiment_id!r}")
    return experiment_id


class ExperimentOracle:
    def __init__(self, ws: Workspace):
        custody = ws.custody
        meta = json.loads((custody / "world_meta.json").read_text())
        self.month: str = meta["config"]["experiment_month"]
        exp = pd.read_parquet(custody / "truth_experiment.parquet")
        cm = pd.read_parquet(custody / "truth_customer_month.parquet",
                             columns=["customer_id", "month", "churned", "price_fen", "is_test"])
        # Customers who churned before the experiment month but whose cancellation was not yet
        # visible at the scoring cutoff are legitimately in the targeted population. A contact
        # cannot bring them back: both potential outcomes are "churned".
        before = cm[cm["month"] < self.month]
        gone = before[before["churned"] & ~before["is_test"]]
        last_price = before.sort_values("month").groupby("customer_id")["price_fen"].last()
        pre = pd.DataFrame({"customer_id": gone["customer_id"].unique()})
        pre["price_fen"] = pre["customer_id"].map(last_price)
        pre = pre.assign(p0=1.0, p1=1.0, u=0.0, tau_logit=0.0, segment_truth=PRE_CHURNED)
        self._truth = (pd.concat([exp, pre[exp.columns]], ignore_index=True)
                       .astype({"customer_id": str}).set_index("customer_id"))
        if self._truth.index.duplicated().any():
            raise RuntimeError("custody truth lists a customer both as at-risk and as already churned")
        # Binds ledger records to the world they were drawn from: a rebuilt world must not
        # silently serve outcomes recorded against the old one.
        hashed = pd.util.hash_pandas_object(self._truth.sort_index()[_TRUTH_COLUMNS], index=True)
        self.truth_digest: str = hashlib.sha256(
            self.month.encode() + hashed.to_numpy().tobytes()).hexdigest()
        self._ledger = custody / LEDGER

    def _rows(self, customer_ids: Iterable[str]) -> pd.DataFrame:
        ids = [str(c) for c in customer_ids]
        if not ids:
            raise ValueError("no customer ids")
        if len(set(ids)) != len(ids):
            raise ValueError("customer ids contain duplicates")
        unknown = [c for c in ids if c not in self._truth.index]
        if unknown:
            raise ValueError(f"{len(unknown)} ids are not in the {self.month} experiment population "
                             f"(test accounts, new joiners or unknown ids), e.g. {unknown[:3]}")
        return self._truth.loc[ids]

    # ------------------------------------------------------------------ experiment channel
    @staticmethod
    def _arms(assignments: pd.DataFrame) -> tuple[list[str], np.ndarray]:
        if not {"customer_id", "arm"} <= set(assignments.columns):
            raise ValueError("assignments need customer_id and arm columns")
        bad = set(assignments["arm"]) - set(ARMS)
        if bad:
            raise ValueError(f"unknown arms {sorted(bad, key=str)}")
        ids = [str(c) for c in assignments["customer_id"]]
        if len(set(ids)) != len(ids):
            raise ValueError("customer ids contain duplicates")
        return ids, (assignments["arm"] == "treatment").to_numpy()

    def _assignment_body(self, ids: list[str], treated: np.ndarray) -> dict:
        """Order-independent canonical form of an assignment (row order is not part of the design)."""
        return {"month": self.month,
                "treatment": sorted(c for c, t in zip(ids, treated) if t),
                "control": sorted(c for c, t in zip(ids, treated) if not t)}

    def assignment_digest(self, assignments: pd.DataFrame) -> str:
        """Digest identifying an assignment for this oracle's month; independent of row order."""
        return digest(self._assignment_body(*self._arms(assignments)))

    @contextmanager
    def _locked(self) -> Iterator[list[dict]]:
        """Exclusive lock over read-check-append, so two concurrent first calls cannot both release."""
        self._ledger.parent.mkdir(parents=True, exist_ok=True)
        with open(self._ledger.with_suffix(".lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield self._read_ledger()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read_ledger(self) -> list[dict]:
        if not self._ledger.exists():
            return []
        out = []
        with open(self._ledger, encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ExperimentLedgerError(f"{self._ledger}:{lineno}: not valid JSON ({exc.msg})") from exc
                if not isinstance(e, dict) or e.get("status") not in ("released", "refused") \
                        or not isinstance(e.get("experiment_id"), str):
                    raise ExperimentLedgerError(f"{self._ledger}:{lineno}: not an experiment ledger entry")
                out.append(e)
        return out

    def _verify_release(self, rec: dict, churned_now: set[str]) -> set[str]:
        """Integrity of a recorded release before it is served again; returns its churned ids."""
        eid = rec["experiment_id"]
        try:
            body = {"month": rec["month"], "treatment": rec["assignment"]["treatment"],
                    "control": rec["assignment"]["control"]}
            churned = rec["churned"]
            ok = (digest(body) == rec["assignment_digest"]
                  and digest({"assignment_digest": rec["assignment_digest"], "churned": churned})
                  == rec["outcomes_digest"])
        except (KeyError, TypeError) as exc:
            raise ExperimentLedgerError(f"experiment {eid!r}: malformed release record ({exc!r})") from exc
        if not ok:
            raise ExperimentLedgerError(f"experiment {eid!r}: release record does not match its digests "
                                        "(edited ledger)")
        if rec.get("truth_digest") != self.truth_digest:
            raise ExperimentLedgerError(f"experiment {eid!r}: custody truth changed since its outcomes were "
                                        "released; the recorded outcomes describe another world")
        if set(churned) != churned_now:
            raise ExperimentLedgerError(f"experiment {eid!r}: recorded outcomes disagree with custody truth")
        return set(churned)

    def _already_assigned(self, entries: list[dict], eid: str, ids: set[str]) -> dict[str, set[str]]:
        """experiment_id -> customers of ``ids`` already assigned in another released experiment of the month."""
        out: dict[str, set[str]] = {}
        for e in entries:
            if e["status"] != "released" or e["experiment_id"] == eid or e.get("month") != self.month:
                continue
            try:
                used = set(e["assignment"]["treatment"]) | set(e["assignment"]["control"])
            except (KeyError, TypeError) as exc:
                raise ExperimentLedgerError(f"experiment {e['experiment_id']!r}: malformed release record "
                                            f"({exc!r})") from exc
            hit = ids & used
            if hit:
                out.setdefault(e["experiment_id"], set()).update(hit)
        return out

    def outcomes(self, assignments: pd.DataFrame, experiment_id: str) -> pd.DataFrame:
        """Observed outcome per assigned customer: churned = u < (p1 if treatment else p0).

        One-shot per ``experiment_id``: the first call records the release in the custody
        ledger; the identical assignment (any row order) returns the recorded outcomes; a
        different assignment raises ``ExperimentAlreadyRunError`` (and is logged as refused).
        One experiment per customer and month: a new id whose customers overlap an earlier
        release raises ``CustomerAlreadyAssignedError`` (logged as refused).
        Invalid input raises ``ValueError`` before the ledger is touched, so it never uses up
        an experiment id. Rows come back in the order of ``assignments``.
        """
        eid = _check_experiment_id(experiment_id)
        ids, treated = self._arms(assignments)
        t = self._rows(ids)
        p = np.where(treated, t["p1"].to_numpy(), t["p0"].to_numpy())
        churned = t["u"].to_numpy() < p
        body = self._assignment_body(ids, treated)
        a_digest = digest(body)
        churned_ids = sorted(c for c, y in zip(ids, churned) if y)

        with self._locked() as entries:
            released = [e for e in entries if e["experiment_id"] == eid and e["status"] == "released"]
            if len(released) > 1:
                raise ExperimentLedgerError(f"experiment {eid!r} was released {len(released)} times")
            if released:
                rec = released[0]
                if rec.get("assignment_digest") != a_digest:
                    reason = (f"experiment {eid!r} already released outcomes for assignment "
                              f"{str(rec.get('assignment_digest'))[:12]} (month {rec.get('month')}, "
                              f"{rec.get('ts')}); a different assignment would reveal the other potential "
                              f"outcome. Register a new experiment id with the controller instead.")
                    append_jsonl(self._ledger, {
                        "experiment_id": eid, "status": "refused", "month": self.month,
                        "assignment_digest": a_digest, "released_assignment_digest": rec.get("assignment_digest"),
                        "reason": "different assignment for an already-released experiment_id", "ts": utcnow()})
                    raise ExperimentAlreadyRunError(reason)
                recorded = self._verify_release(rec, set(churned_ids))
                return pd.DataFrame({"customer_id": ids, "churned": np.array([c in recorded for c in ids])})
            clash = self._already_assigned(entries, eid, set(ids))
            if clash:
                n_overlap = sum(len(v) for v in clash.values())
                append_jsonl(self._ledger, {
                    "experiment_id": eid, "status": "refused", "month": self.month,
                    "assignment_digest": a_digest, "overlapping_experiments": sorted(clash),
                    "n_overlapping_customers": n_overlap,
                    "reason": "customers already assigned in another released experiment of the month",
                    "ts": utcnow()})
                raise CustomerAlreadyAssignedError(
                    f"{n_overlap} customers of experiment {eid!r} were already assigned in released experiment(s) "
                    f"{sorted(clash)} for {self.month} (e.g. {sorted(next(iter(clash.values())))[:3]}); a customer "
                    f"reveals one potential outcome per month. Run comparisons as one experiment on disjoint "
                    f"customers.")
            append_jsonl(self._ledger, {
                "experiment_id": eid, "status": "released", "month": self.month, "ts": utcnow(),
                "assignment_digest": a_digest, "truth_digest": self.truth_digest,
                "n": {"treatment": len(body["treatment"]), "control": len(body["control"])},
                "assignment": {"treatment": body["treatment"], "control": body["control"]},
                "churned": churned_ids,
                "outcomes_digest": digest({"assignment_digest": a_digest, "churned": churned_ids})})
        return pd.DataFrame({"customer_id": ids, "churned": churned})

    # ------------------------------------------------------------------ BENCHMARK-ONLY truth
    def true_effect(self, customer_ids: Iterable[str], vp: ValueParams | None = None) -> dict:
        """BENCHMARK-ONLY: truth for contacting *all* of ``customer_ids`` vs contacting none.

        Reads the potential outcomes directly. For grading a claim evaluator-side; never
        exposed to agents or tools, and its result is never handed back to an agent.
        """
        vp = vp or ValueParams()
        t = self._rows(customer_ids)
        eff = (t["p0"] - t["p1"]).to_numpy()
        price = t["price_fen"].astype(float).to_numpy()
        realized = ((t["u"] < t["p0"]).astype(int) - (t["u"] < t["p1"]).astype(int)).to_numpy()
        # same cost model as decision.experiment.readout: contact for everyone, offer if retained when treated
        value = float(vp.expected_value(t["p0"].to_numpy(), t["p1"].to_numpy(), price).sum())
        n = len(t)
        return {"month": self.month, "n": n, "n_pre_churned": int((t["segment_truth"] == PRE_CHURNED).sum()),
                "mean_p0": float(t["p0"].mean()), "mean_p1": float(t["p1"].mean()),
                "mean_effect": float(eff.mean()), "realized_effect": float(realized.mean()),
                "expected_retained": float(eff.sum()), "expected_value_fen": value,
                "value_per_contact_fen": value / n, "channel": vp.channel,
                "segments": {k: int(v) for k, v in t["segment_truth"].value_counts().sort_index().items()},
                "value_params": asdict(vp)}

    def true_power(self, customer_ids: Iterable[str], n_per_arm: int, vp: ValueParams | None = None) -> dict:
        """BENCHMARK-ONLY: power of a value readout on ``customer_ids`` from the TRUE potential
        outcomes (what the design could have detected). Never exposed to agents or tools."""
        from .design import itt_power, value_power
        t = self._rows(customer_ids)
        return {"basis": "oracle (custody potential outcomes)",
                "itt_power": itt_power(t["p0"].to_numpy(), t["p1"].to_numpy(), n_per_arm),
                **value_power(t["p0"].to_numpy(), t["p1"].to_numpy(), t["price_fen"].astype(float).to_numpy(),
                              vp or ValueParams(), n_per_arm)}

    def oracle_targets(self, customer_ids: Iterable[str], k: int, vp: ValueParams | None = None) -> list[str]:
        """BENCHMARK-ONLY: the at most k customers of ``customer_ids`` with the highest TRUE positive
        expected value per contact -- what a perfect uplift model would target. Evaluator use only."""
        vp = vp or ValueParams()
        t = self._rows(customer_ids)
        ev = pd.Series(vp.expected_value(t["p0"].to_numpy(), t["p1"].to_numpy(), t["price_fen"].astype(float)),
                       index=t.index)
        ev = ev[ev > 0]
        return sorted(ev.sort_values(ascending=False, kind="stable").index[:k].tolist())

    def segment_truth(self, customer_ids: Iterable[str]) -> pd.Series:
        """BENCHMARK-ONLY: true response segment per customer; never exposed to agents or tools."""
        t = self._rows(customer_ids)
        return t["segment_truth"].rename("segment_truth")
