"""Readout gate: is an evaluation conclusion supported by its own per-item results?

An agent (or a person) writes "arm X beats arm Y by +1.0 [−0.2, +2.1], not passed" or "X = Y".
The readout gate re-reads the raw per-item files behind that sentence and runs nine checks.
Each check becomes an EvidenceRecord, and the ordinary promotion gate (stage ``readout_publish``
in policies/gates.yaml) turns them into ALLOW / HOLD / REJECT: an authenticated FAIL rejects the
conclusion; a missing or INCONCLUSIVE check holds it.

| check | FAIL (reject) | INCONCLUSIVE (hold) |
|---|---|---|
| readout.errors_excluded | error rows were scored | |
| readout.same_judge | arms scored by different judges | judge unknown |
| readout.same_answer_model | arms answered by different models | model unknown |
| readout.repeat_runs | | fewer than 2 independent runs per arm |
| readout.paired_items | arms cover different items | |
| readout.interval | recomputed interval contradicts the claim | "equal" claimed but MDE > margin |
| readout.second_judge | a small "passed" delta does not survive a second judge | small delta, no second judge |
| readout.no_wiring_fault | arms gave byte-identical answers everywhere | |
| readout.same_batch | | control answered in other runs than the treatment |

Item files are read through a ``layout``. ``pm``: one folder per run
holding answers.jsonl ({id: "arm|budget|qid", error, [model]}) and a judged file ({id, label,
[judge]}). ``lifegen`` (answers_b<N>.jsonl, graded by a program) and ``beam`` (rubric verdicts keyed
by content hash) keep no per-item scores. For ``beam`` the scores come from a file exported by the
project's own scorer ({id, score, judge} per answer id); ``lifegen`` has none, so its interval is
INCONCLUSIVE and only the bookkeeping checks run. A judge or model that rows do not record can be
taken from the spec (``judge_default`` / ``model_default``, i.e. the pre-registration text); it is
then marked ``doc:`` and reported as such. Answer and question text are only hashed, never printed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import random
import re
import statistics

import yaml

from ..common import utcnow
from ..evidence.gate import evaluate_gate, load_policy
from ..evidence.records import EvidenceRecord

STAGE = "readout_publish"
PRODUCER = "verifier:readout"
CHECK_VERSION = "1"
CHECKS = ("readout.errors_excluded", "readout.same_judge", "readout.same_answer_model", "readout.repeat_runs",
          "readout.paired_items", "readout.interval", "readout.second_judge", "readout.no_wiring_fault",
          "readout.same_batch")
CLAIMS = ("passed", "not_passed", "equal", "worse")
LABEL_SCORE = {"CORRECT": 1.0, "WRONG": 0.0, "INCORRECT": 0.0}
BOOT = 10000


class ReadoutError(ValueError):
    pass


@dataclass
class Row:
    run: str
    arm: str
    qid: str
    score: float | None          # None = not scored
    error: bool
    judge: str | None
    model: str | None
    answer_hash: str | None


@dataclass
class Spec:
    claim_id: str
    claim: str
    claimed_verdict: str               # passed | not_passed | equal | worse
    treatment: str
    control: str
    runs: list[str]
    root: Path
    layout: str = "pm"
    judged_file: str = "judged.jsonl"
    judge_default: str | None = None   # judge implied by the file name when rows lack it
    model_default: str | None = None   # answer model implied by the code when rows lack it
    budget: str | None = None
    run_pattern: str = r"^(.*?)(?:_(?:lc|rt))?$"   # run index = folder name minus the set suffix
    control_runs: list[str] | None = None          # control answered elsewhere (historic control)
    compare_about: str = "system"                  # system | judge | answer_model
    margin_points: float = 1.0                     # smallest effect (accuracy points) worth detecting
    claimed_delta: float | None = None
    claimed_ci: list[float] | None = None
    second_judged_file: str | None = None         # a second judge's labels, for small "passed" deltas
    small_effect_points: float = 2.0               # below this, a "passed" claim needs a second judge
    model_map: dict | None = None                  # beam layout: answer model per arm
    grader: str | None = None                      # lifegen layout: the program that grades answers
    answered_elsewhere: dict | None = None         # arm -> where its answers came from (imported, older run)
    strata_file: str | None = None                 # pm layout: stratified design file (strata.json) per set
    source: str = ""
    notes: str = ""

    @classmethod
    def load(cls, path: Path) -> "Spec":
        d = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        d["root"] = Path(d["root"])
        if d.get("claimed_verdict") not in CLAIMS:
            raise ReadoutError(f"claimed_verdict must be one of {CLAIMS}")
        known = set(cls.__dataclass_fields__)
        bad = set(d) - known
        if bad:
            raise ReadoutError(f"unknown spec keys {sorted(bad)}")
        return cls(**d)


# ============================================================================ loading
def _h(s) -> str | None:
    return None if s is None else hashlib.sha256(str(s).encode()).hexdigest()[:16]


def _jsonl(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
    return out


def _doc(v: str | None) -> str | None:
    return None if v is None else f"doc:{v}"


def load_pm(spec: Spec, run_dirs: list[str], arms: set[str]) -> list[Row]:
    rows: list[Row] = []
    for rd in run_dirs:
        d = spec.root / rd
        ans_p, jud_p = d / "answers.jsonl", d / spec.judged_file
        if not ans_p.exists():
            raise ReadoutError(f"{ans_p} is missing")
        run = re.match(spec.run_pattern, rd).group(1) if re.match(spec.run_pattern, rd) else rd
        answers = {}
        for a in _jsonl(ans_p):
            arm, budget, qid = (a["id"].split("|") + ["", ""])[:3]
            if arm in arms and (spec.budget is None or budget == str(spec.budget)):
                answers[a["id"]] = a
        judged = {}
        if jud_p.exists():
            for j in _jsonl(jud_p):
                if j.get("id") in answers:
                    judged[j["id"]] = j
        for aid, a in answers.items():
            arm, _, qid = aid.split("|")[:3]
            j = judged.get(aid)
            label = (j or {}).get("label")
            rows.append(Row(run=run, arm=arm, qid=f"{rd.split('_')[-1]}:{qid}",
                            score=LABEL_SCORE.get(str(label).upper()) if label is not None else None,
                            error=bool(a.get("error")), judge=(j or {}).get("judge") or (_doc(spec.judge_default) if j else None),
                            model=a.get("model") or _doc(spec.model_default),
                            answer_hash=_h(a.get("answer", a.get("text")))))
    return rows


def load_lifegen(spec: Spec, run_dirs: list[str], arms: set[str]) -> list[Row]:
    """answers_b<N>.jsonl per folder, id = lang|arm|qid; graded by a program, no stored scores."""
    rows = []
    for rd in run_dirs:
        for p in sorted((spec.root / rd).glob("answers_b*.jsonl")):
            for a in _jsonl(p):
                lang, arm, qid = (a["id"].split("|") + ["", ""])[:3]
                if arm in arms:
                    rows.append(Row(run=f"{rd}:{p.stem}", arm=arm, qid=f"{lang}:{qid}", score=None,
                                    error=bool(a.get("error")), judge=f"program:{spec.grader or 'unknown'}",
                                    model=a.get("model") or _doc(spec.model_default), answer_hash=_h(a.get("text"))))
    return rows


def load_beam(spec: Spec, run_dirs: list[str], arms: set[str]) -> list[Row]:
    """answers.jsonl id = bench|arm|batch|qid, appended (a failed answer is retried under the same id).
    Scores come from ``judged_file``: {id, score, judge} per answer id, exported by the project's scorer.
    Like the scorer, only the last non-error row of an id carries the score."""
    rows = []
    for rd in run_dirs:
        sp = Path(spec.judged_file)
        sp = sp if sp.is_absolute() else spec.root / rd / sp
        scores = {r["id"]: r for r in _jsonl(sp)} if sp.exists() else {}
        answers = _jsonl(spec.root / rd / "answers.jsonl")
        last_ok = {a["id"]: i for i, a in enumerate(answers) if not a.get("error")}
        for i, a in enumerate(answers):
            bench, arm, batch, qid = (a["id"].split("|") + ["", "", ""])[:4]
            if arm not in arms:
                continue
            j = scores.get(a["id"]) if last_ok.get(a["id"]) == i else None
            rows.append(Row(run=f"b{batch}", arm=arm, qid=f"{bench}:{qid}", score=None if j is None else float(j["score"]),
                            error=bool(a.get("error")), judge=None if j is None else j.get("judge"),
                            model=(spec.model_map or {}).get(arm) or _doc(spec.model_default),
                            answer_hash=_h(a.get("answer")) if not a.get("error") else None))
    return rows


LOADERS = {"pm": load_pm, "lifegen": load_lifegen, "beam": load_beam}


def load_rows(spec: Spec, judged_file: str | None = None) -> tuple[list[Row], list[Row]]:
    if spec.layout not in LOADERS:
        raise ReadoutError(f"unknown layout {spec.layout!r}")
    if judged_file:
        spec = Spec(**{**spec.__dict__, "judged_file": judged_file})
    load = LOADERS[spec.layout]
    return load(spec, spec.runs, {spec.treatment}), load(spec, spec.control_runs or spec.runs, {spec.control})


# ============================================================================ checks
@dataclass
class CheckResult:
    check_id: str
    outcome: str                       # PASS | FAIL | INCONCLUSIVE
    metrics: dict = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)


def check_errors(t: list[Row], c: list[Row]) -> CheckResult:
    rows = t + c
    errs = [r for r in rows if r.error]
    scored_errs = [r for r in errs if r.score is not None]
    m = {"error_rows": len(errs), "scored_error_rows": len(scored_errs), "rows": len(rows)}
    if scored_errs:
        return CheckResult("readout.errors_excluded", "FAIL", m,
                           [f"{len(scored_errs)} answers that failed with an error were scored (as wrong)"])
    lim = [f"{len(errs)} error rows present but not scored"] if errs else []
    return CheckResult("readout.errors_excluded", "PASS", m, lim)


def _same(check_id: str, attr: str, t: list[Row], c: list[Row], about: bool) -> CheckResult:
    scored = any(r.score is not None for r in t + c)
    tv = {getattr(r, attr) for r in t if r.score is not None or not scored}
    cv = {getattr(r, attr) for r in c if r.score is not None or not scored}
    m = {"treatment": sorted(map(str, tv)), "control": sorted(map(str, cv))}
    if about:
        return CheckResult(check_id, "PASS", m, [f"the comparison is about the {attr}; difference expected"])
    if None in tv or None in cv:
        return CheckResult(check_id, "INCONCLUSIVE", m, [f"{attr} not recorded for some scored rows"])
    if tv != cv or len(tv) != 1:
        return CheckResult(check_id, "FAIL", m, [f"arms scored with different {attr}s: {m}"])
    if any(str(v).startswith("doc:") for v in tv | cv):
        return CheckResult(check_id, "PASS", m, [f"{attr} taken from the pre-registration text, not recorded per row"])
    return CheckResult(check_id, "PASS", m)


def check_runs(t: list[Row], c: list[Row]) -> CheckResult:
    tr, cr = {r.run for r in t}, {r.run for r in c}
    m = {"treatment_runs": len(tr), "control_runs": len(cr)}
    if min(len(tr), len(cr)) < 2:
        return CheckResult("readout.repeat_runs", "INCONCLUSIVE", m,
                           ["fewer than 2 independent runs per arm: run-to-run noise is not measured"])
    return CheckResult("readout.repeat_runs", "PASS", m)


def check_paired(t: list[Row], c: list[Row]) -> CheckResult:
    scored = any(r.score is not None for r in t + c)
    tq = {r.qid for r in t if r.score is not None or not scored}
    cq = {r.qid for r in c if r.score is not None or not scored}
    both = tq & cq
    share = len(both) / max(1, len(tq | cq))
    m = {"treatment_items": len(tq), "control_items": len(cq), "shared": len(both), "shared_share": round(share, 4)}
    if share < 0.95:
        return CheckResult("readout.paired_items", "FAIL", m, ["arms were scored on different item sets"])
    return CheckResult("readout.paired_items", "PASS", m)


def paired_deltas(t: list[Row], c: list[Row]) -> dict[str, float]:
    """Per item: mean score over runs (treatment) minus the same for control, in accuracy points."""
    def per_item(rows):
        d: dict[str, list[float]] = {}
        for r in rows:
            if r.score is not None:
                d.setdefault(r.qid, []).append(r.score)
        return {q: sum(v) / len(v) for q, v in d.items()}
    ti, ci = per_item(t), per_item(c)
    return {q: 100.0 * (ti[q] - ci[q]) for q in sorted(set(ti) & set(ci))}


def _strata(spec: Spec) -> list[dict]:
    """Per set (lc / rt): the D stratum (treatment found more evidence than control), the random sample
    and the D weight |D| / population (the stratified design's own estimator)."""
    out, seen = [], set()
    for rd in spec.runs:
        s = rd.split("_")[-1]
        if s in seen:
            continue
        seen.add(s)
        meta = json.loads((spec.root / rd / spec.strata_file).read_text(encoding="utf-8"))
        more = {q for q, v in meta["q"].items() if v["ev"].get(spec.treatment, 0) > v["ev"].get(spec.control, 0)}
        out.append({"d": {f"{s}:{q}" for q in more},
                    "sample": {f"{s}:{q}" for q, v in meta["q"].items() if v["sample"]},
                    "wd": len(more) / meta["population"]})
    return out


def estimate(spec: Spec, d: dict[str, float], seed: int = 20261106) -> tuple[float, list[float]]:
    """Point estimate of the mean paired delta (points) and its bootstrap replicates. Plain designs
    resample items; stratified designs resample within each stratum and average the sets."""
    rng = random.Random(seed)
    if not spec.strata_file:
        xs = list(d.values())
        return statistics.fmean(xs), [statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(BOOT)]
    cells = []
    for st in _strata(spec):
        cells.append((st["wd"], [x for q, x in d.items() if q in st["d"]],
                      [x for q, x in d.items() if q in st["sample"] and q not in st["d"]]))
    m = lambda xs: statistics.fmean(xs) if xs else 0.0  # noqa: E731

    def est(pick):
        return statistics.fmean(wd * m(pick(dd)) + (1 - wd) * m(pick(rr)) for wd, dd, rr in cells)
    return est(lambda xs: xs), [est(lambda xs: rng.choices(xs, k=len(xs)) if xs else xs) for _ in range(BOOT)]


def check_interval(spec: Spec, t: list[Row], c: list[Row]) -> CheckResult:
    d = paired_deltas(t, c)
    claimed = {"claimed_delta": spec.claimed_delta, "claimed_ci": spec.claimed_ci}
    if not any(r.score is not None for r in t + c):
        return CheckResult("readout.interval", "INCONCLUSIVE", {"items": 0, **claimed},
                           ["no per-item scores in the result files: the interval cannot be recomputed"])
    if len(d) < 10:
        return CheckResult("readout.interval", "INCONCLUSIVE", {"items": len(d), **claimed}, ["too few paired items"])
    mean, boots = estimate(spec, d)
    boots.sort()
    lo, hi = boots[int(0.025 * BOOT)], boots[int(0.975 * BOOT) - 1]
    se = statistics.stdev(boots)
    mde = 2.8 * se                                     # two-sided alpha .05, power .8
    m = {"items": len(d), "estimator": "stratified" if spec.strata_file else "items",
         "delta_points": round(mean, 3), "ci95": [round(lo, 3), round(hi, 3)],
         "se_points": round(se, 3), "mde_points": round(mde, 3), "margin_points": spec.margin_points,
         "claimed_delta": spec.claimed_delta, "claimed_ci": spec.claimed_ci,
         "note": "bootstrap over items; between-run variance enters only through per-item run means"}
    v = spec.claimed_verdict
    if v == "passed":
        ok = lo > 0
        return CheckResult("readout.interval", "PASS" if ok else "FAIL", m,
                           [] if ok else ["claimed passed, but the recomputed 95% interval includes 0"])
    if v == "worse":
        ok = hi < 0
        return CheckResult("readout.interval", "PASS" if ok else "FAIL", m,
                           [] if ok else ["claimed worse, but the recomputed 95% interval includes 0"])
    if v == "not_passed":
        if lo > 0:
            return CheckResult("readout.interval", "FAIL", m, ["claimed not passed, but the interval excludes 0"])
        lim = [] if mde <= spec.margin_points else [
            f"underpowered for the margin: MDE {mde:.2f} > {spec.margin_points} points (non-adoption stands; "
            "'no effect' is not shown)"]
        return CheckResult("readout.interval", "PASS", m, lim)
    # equal: an equivalence claim needs the interval inside ±margin
    inside = -spec.margin_points <= lo and hi <= spec.margin_points
    if inside:
        return CheckResult("readout.interval", "PASS", m)
    return CheckResult("readout.interval", "INCONCLUSIVE", m,
                       [f"'equal' needs the 95% interval inside ±{spec.margin_points} points; it is [{lo:.2f}, {hi:.2f}]"])


def check_second_judge(spec: Spec, t: list[Row], c: list[Row]) -> CheckResult:
    """A small "passed"/"worse" delta must survive a second judge (the project's own rule:
    one-point deltas need two judges)."""
    cid = "readout.second_judge"
    if spec.claimed_verdict not in ("passed", "worse"):
        return CheckResult(cid, "PASS", {}, ["not needed: the claim is not an improvement or a loss"])
    if all(str(r.judge).startswith("program:") for r in t + c):
        return CheckResult(cid, "PASS", {}, ["not needed: graded by a program, not a model judge"])
    d = paired_deltas(t, c)
    delta = estimate(spec, d)[0] if d else 0.0
    if abs(delta) >= spec.small_effect_points:
        return CheckResult(cid, "PASS", {"delta_points": round(delta, 3)}, ["effect large enough for one judge"])
    if not spec.second_judged_file:
        return CheckResult(cid, "INCONCLUSIVE", {"delta_points": round(delta, 3)},
                           [f"delta {delta:+.2f} < {spec.small_effect_points} points and no second judge"])
    t2, c2 = load_rows(spec, spec.second_judged_file)
    r2 = check_interval(spec, t2, c2)
    m = {"first_judge_delta": round(delta, 3), "second_judge": r2.metrics}
    if r2.outcome != "PASS":
        return CheckResult(cid, "FAIL", m, ["the result does not survive the second judge: " + "; ".join(r2.limitations)])
    return CheckResult(cid, "PASS", m)


def check_wiring(t: list[Row], c: list[Row]) -> CheckResult:
    tk = {(r.run, r.qid): r.answer_hash for r in t if r.answer_hash}
    ck = {(r.run, r.qid): r.answer_hash for r in c if r.answer_hash}
    both = set(tk) & set(ck)
    same = sum(tk[k] == ck[k] for k in both)
    share = same / len(both) if both else None
    m = {"paired_answers": len(both), "identical": same, "identical_share": None if share is None else round(share, 4)}
    if share is not None and share >= 0.99:
        return CheckResult("readout.no_wiring_fault", "FAIL", m,
                           ["the two arms returned identical answers on (almost) every item: the arm switch is not wired"])
    return CheckResult("readout.no_wiring_fault", "PASS", m)


def check_batch(spec: Spec) -> CheckResult:
    if spec.control_runs and set(spec.control_runs) != set(spec.runs):
        return CheckResult("readout.same_batch", "INCONCLUSIVE", {"control_runs": spec.control_runs},
                           ["control answered in other runs than the treatment (historic control)"])
    elsewhere = {a: w for a, w in (spec.answered_elsewhere or {}).items() if a in (spec.treatment, spec.control)}
    if elsewhere:
        return CheckResult("readout.same_batch", "INCONCLUSIVE", {"answered_elsewhere": elsewhere},
                           [f"answers imported from another run: {elsewhere}"])
    return CheckResult("readout.same_batch", "PASS", {"runs": spec.runs})


def run_checks(spec: Spec) -> list[CheckResult]:
    t, c = load_rows(spec)
    if not t or not c:
        raise ReadoutError(f"no rows for treatment {spec.treatment!r} ({len(t)}) or control {spec.control!r} ({len(c)})")
    return [check_errors(t, c),
            _same("readout.same_judge", "judge", t, c, spec.compare_about == "judge"),
            _same("readout.same_answer_model", "model", t, c, spec.compare_about == "answer_model"),
            check_runs(t, c), check_paired(t, c), check_interval(spec, t, c), check_second_judge(spec, t, c),
            check_wiring(t, c), check_batch(spec)]


# ============================================================================ gate
def decide(spec: Spec, results: list[CheckResult] | None = None, *, now: str | None = None) -> dict:
    results = results if results is not None else run_checks(spec)
    now = now or utcnow()
    subject = {"kind": "readout", "claim_id": spec.claim_id, "claim": spec.claim,
               "treatment": spec.treatment, "control": spec.control, "runs": spec.runs}
    evidence = [EvidenceRecord.create(check_id=r.check_id, check_version=CHECK_VERSION, stage=STAGE, subject=subject,
                                      outcome=r.outcome, metrics=r.metrics, producer=PRODUCER, issued_at=now,
                                      limitations=r.limitations) for r in results]
    dec = evaluate_gate(load_policy(), STAGE, subject, evidence, [], requested_by="analyst:claim-author", now=now)
    return {"claim_id": spec.claim_id, "claim": spec.claim, "verdict": dec.verdict, "reasons": dec.reasons,
            "checks": [{"check": r.check_id, "outcome": r.outcome, "metrics": r.metrics, "limitations": r.limitations}
                       for r in results]}


def render(d: dict) -> str:
    L = [f"### {d['claim_id']}: **{d['verdict']}**", "", f"> {d['claim']}", "", "| check | outcome | detail |",
         "|---|---|---|"]
    for c in d["checks"]:
        detail = "; ".join(c["limitations"]) or ", ".join(f"{k}={v}" for k, v in c["metrics"].items()
                                                          if k in ("delta_points", "ci95", "mde_points", "identical_share",
                                                                   "treatment_runs", "control_runs", "scored_error_rows"))
        L.append(f"| {c['check']} | {c['outcome']} | {str(detail).replace('|', '/')} |")
    return "\n".join(L) + "\n"
