"""H v0.4, exploratory: would a second independent answer be a usable check where no governed metric
reaches (the T2 trust label the analytics module reserves but does not issue)?

Replays finished census runs; nothing is re-run. For each attempt the "verifier" is another attempt at
the same question in the same arm: the other agent (cross-model) or another repetition of the same
agent (same-model). Two answers agree when they have the same keys and, for every key, the two values
are within one grader tolerance of each other, taken symmetrically: 0.05% of the larger value or half a
unit in the last place of the coarser one, both with at least 3 significant digits unless equal; two
whole numbers must be equal (counts, ids); share <-> percent only for share, percent and "other" units
(never for money). The same rule as H v0.4's `values_close` (harness_v04.patch). Each attempt's delivery under a rule is the share of its verifiers that agree (the
expected value over one random verifier), so no random pairing is drawn. An attempt with no verifier
(its cell was not completed) is left out of that rule and counted.

Rules, per arm:
  recorded    the census's own delivery (N: the agent claims success; H, H1: VERIFIED + ALLOW)
  cross       the other agent's answer agrees
  same        another repetition of the same agent agrees
  hybrid      (harness arms) recorded, or else the other agent's answer agrees: VERIFIED where a
              governed metric reaches, cross-checked where it does not; a second agent is needed only
              for the attempts the gate did not deliver (column "2nd agent")
  both        (harness arms) recorded and the other agent's answer agrees
  t2          (harness arms) the v0.4 design: recorded, or else the other agent agrees and the final
              answer's own certificate is eligible: SQL static check and re-execution PASS,
              reconciliation not FAIL or ERROR (an answer the governed oracle contradicts stays held)
  t2x         (harness arms) t2 with v0.4's own functions instead of the grader's reading: the
              agents' raw rows compared by `compare_answers` (column names must match, no key
              aliases) and both certificates checked by `second_opinion_problems`. Needs the v0.4
              source: DSH_SRC=<tree>/src while the patch is not applied; skipped otherwise
  t2n         (harness arms) t2x, but the second opinion is the other agent's arm-N attempt at the same
              question: another model on another path (raw SQL, no harness tools), so an error the
              harness tools induce in both agents is not confirmed. Arm N has no certificate, so only
              the first answer's own checks apply
Beside each rule, "random" is delivery at random with the same delivery rate: M1 = wrong share x rate,
M2 = 1 - rate. A rule that only delivered the easy questions more often would sit near it.

The agent's own claim is not used: in the harness arms an agent reports the harness tier, so a held
answer comes with claimed_success false, and in arm N almost no answer is unclaimed (E1b: 0.4%).

    .venv/bin/python experiments/H_v04/agreement_probe.py --out docs/experiments/H_v0.4/agreement_E1_E1b.md
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import functools
import importlib.util
import json
import os
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, os.environ.get("DSH_SRC") or str(ROOT / "src"))
from dsharness.bench import census as C  # noqa: E402
from dsharness.common import digest, read_jsonl  # noqa: E402
from dsharness.context.semantic import load_semantic_model  # noqa: E402
try:
    from dsharness.tools.analytics import KEY_HINTS, compare_answers, second_opinion_problems  # noqa: E402
except ImportError:                     # harness before v0.4
    compare_answers = None

BOOT, SEED = 2000, 20261107
RUNS = {   # name -> (runs dir, bank, answer form, key aliases)
    "E1": ("experiments/E1/runs/main", "experiments/E1/questions/bank.yaml", "experiments/E1/questions/answer_form.json"),
    "E1b": ("experiments/E1b/runs/main", "experiments/E1b/questions/bank.yaml", "experiments/E1b/questions/answer_form.json"),
    "E3a": ("experiments/E3a/runs/main", "experiments/E3a/questions/bank.yaml", "experiments/E3a/questions/answer_form.json"),
    "E4": ("experiments/E4/runs/main", "experiments/E4/questions/bank.yaml", "experiments/E4/questions/answer_form.json"),
}


def latest(path: Path) -> list[dict]:
    keep = {}
    for r in read_jsonl(path):
        keep[(r["agent"], r["arm"], r["qid"], r["rep"])] = r
    return [r for r in keep.values() if r.get("status") not in ("RETRY", "SKIPPED")]


def close(a: float, b: float, unit: str) -> bool:
    """Within one grader tolerance of each other: the larger value's 0.05%, or the coarser half unit."""
    da, sa = C._decimals_and_sig(a)
    db, sb = C._decimals_and_sig(b)
    for f in ((1.0, 0.01, 100.0) if unit in ("share", "percent", "other") else (1.0,)):
        bf = b * f
        if a == bf:
            return True
        if float(a).is_integer() and float(bf).is_integer():
            continue
        tol = max(C.REL_TOL * max(abs(a), abs(bf)), 0.5 * 10.0 ** (-da), 0.5 * 10.0 ** (-db) * f)
        if sa >= C.MIN_SIG_DIGITS and sb >= C.MIN_SIG_DIGITS and abs(a - bf) <= tol:
            return True
    return False


def agree(q: C.Question, a: dict, b: dict, aliases: dict) -> bool:
    ga, _ = C.answer_map(q, (a.get("final_answer") or {}).get("answer_rows"), aliases)
    gb, _ = C.answer_map(q, (b.get("final_answer") or {}).get("answer_rows"), aliases)
    if ga is None or gb is None or set(ga) != set(gb):
        return False
    return all(close(ga[k], gb[k], q.unit) for k in ga)


def final_cert(rdir: Path, r: dict) -> dict | None:
    """The certificate whose rows are the final rows, else the last one (as the census chooses)."""
    path = rdir / "attempts" / r["agent"] / r["arm"] / f"{r['qid']}-r{r['rep']}" / "harness_ws/registry/answer_certificates.jsonl"
    certs = read_jsonl(path) if path.exists() else []
    if not certs:
        return None
    rows = (r.get("final_answer") or {}).get("answer_rows")
    want = digest(rows) if rows is not None else None
    match = [c for c in certs if c["answer"]["rows_digest"] == want]
    return match[-1] if match else certs[-1]


@functools.cache
def _dimensions(path: str | None) -> frozenset[str]:
    return frozenset(load_semantic_model(path).dimensions)


def hints_for(rdir: Path, r: dict) -> set[str]:
    """compare_answers' key hints as cross_check_answers builds them: KEY_HINTS and the semantic model's dimensions."""
    ws = rdir / "attempts" / r["agent"] / r["arm"] / f"{r['qid']}-r{r['rep']}" / "harness_ws"
    p = ws / "context/semantic_model.yaml"
    return set(KEY_HINTS) | _dimensions(str(p) if p.exists() else None)


def t2x(rdir: Path, r: dict, others: list[dict], graded: list[bool], why: Counter, certified: bool = True) -> float:
    """Share of the other agent's attempts that cross_check_answers would accept for r (0 when r itself is not
    eligible). `why` counts the pairs where it differs from the grader's reading (`graded`, from agree)."""
    ca = final_cert(rdir, r)
    rows = (r.get("final_answer") or {}).get("answer_rows")
    if ca is None or rows is None or second_opinion_problems(ca):
        return 0.0
    hints, ok = hints_for(rdir, r), 0
    for o, g in zip(others, graded):
        cb, orows = final_cert(rdir, o) if certified else {}, (o.get("final_answer") or {}).get("answer_rows")
        if cb is None or orows is None or (certified and second_opinion_problems(cb)):
            why["grader agrees; second answer not eligible" if g else "both refuse"] += 1
            continue
        cmp = compare_answers(rows, orows, key_hints=hints)
        ok += cmp["outcome"] == "PASS"
        if g and cmp["outcome"] != "PASS":
            why["grader agrees; " + cmp["outcome"] + ": " + cmp.get("reason", "values differ").split(":")[0]] += 1
        elif not g and cmp["outcome"] == "PASS":
            why["grader disagrees; compare_answers PASS"] += 1
        else:
            why["same"] += 1
    return ok / len(others)


def eligible(rdir: Path, r: dict) -> bool:
    """The final answer's certificate allows a second opinion (T2d): static check and re-execution PASS,
    reconcile not FAIL or ERROR."""
    c = final_cert(rdir, r)
    if c is None:
        return False
    out = {k["check_id"]: k["outcome"] for k in c["checks"]}
    return (c["gate"]["verdict"] != "REJECT" and out.get("answer.sql_static") == "PASS"
            and out.get("answer.reexecution") == "PASS" and out.get("answer.reconcile") not in ("FAIL", "ERROR"))


def deliveries(recs: list[dict], qs: dict, aliases: dict, rdir: Path, why: Counter) -> list[dict]:
    """One row per attempt: correct, form, and the delivery share under each rule."""
    cell = defaultdict(list)
    for r in recs:
        cell[(r["arm"], r["qid"], r["agent"])].append(r)
    out = []
    for r in recs:
        q = qs[r["qid"]]
        other = cell[(r["arm"], r["qid"], "codex" if r["agent"] == "claude" else "claude")]
        same = [x for x in cell[(r["arm"], r["qid"], r["agent"])] if x["rep"] != r["rep"]]
        share = lambda vs: (sum(agree(q, r, v, aliases) for v in vs) / len(vs)) if vs else None  # noqa: E731
        rec = float(bool(r["delivered"]))
        cross = share(other)
        out.append({"arm": r["arm"], "agent": r["agent"], "qid": r["qid"], "correct": bool(r["correct"]),
                    "recorded": rec, "cross": cross, "same": share(same),
                    "hybrid": rec if r["arm"] == "N" else (rec if rec or cross is None else max(rec, cross)),
                    "both": None if cross is None else min(rec, cross),
                    "t2": rec if r["arm"] == "N" else (rec if rec or cross is None
                                                       else (cross if eligible(rdir, r) else 0.0)),
                    "asks_t2": r["arm"] != "N" and not rec and eligible(rdir, r)})
        if compare_answers is not None and r["arm"] != "N":
            out[-1]["t2x"] = rec if rec or cross is None else t2x(rdir, r, other, [agree(q, r, v, aliases) for v in other], why)
            other_n = cell[("N", r["qid"], "codex" if r["agent"] == "claude" else "claude")]
            out[-1]["t2n"] = (None if not other_n else rec if rec else
                              t2x(rdir, r, other_n, [agree(q, r, v, aliases) for v in other_n], Counter(), certified=False))
    return out


def rates(rows: list[dict], rule: str) -> tuple[float, float | None]:
    rows = [r for r in rows if r[rule] is not None]
    n = len(rows)
    c = sum(r["correct"] for r in rows)
    m1 = sum((not r["correct"]) * r[rule] for r in rows) / n
    m2 = sum(r["correct"] * (1 - r[rule]) for r in rows) / c if c else None
    return m1, m2


def boot(rows: list[dict], rule: str, rng: random.Random) -> dict:
    by_q = defaultdict(list)
    for r in rows:
        by_q[r["qid"]].append(r)
    qids = sorted(by_q)
    m1, m2 = rates(rows, rule)
    b1, b2 = [], []
    for _ in range(BOOT):
        s = [r for q in (rng.choice(qids) for _ in qids) for r in by_q[q]]
        x1, x2 = rates(s, rule)
        b1.append(x1)
        if x2 is not None:
            b2.append(x2)
    ci = lambda xs: (sorted(xs)[int(0.025 * len(xs))], sorted(xs)[int(0.975 * len(xs)) - 1])  # noqa: E731
    used = [r for r in rows if r[rule] is not None]
    rate = sum(r[rule] for r in used) / len(used)
    wrong = sum(not r["correct"] for r in used) / len(used)
    return {"n": len(used), "no_verifier": len(rows) - len(used), "questions": len(qids), "M1": m1, "M1_ci": ci(b1),
            "M2": m2, "M2_ci": ci(b2) if b2 else None, "accuracy": 1 - wrong, "delivery_rate": rate,
            "random_M1": wrong * rate, "random_M2": 1 - rate,
            "extra_run": {"cross": 1.0, "same": 1.0, "both": 1.0, "recorded": 0.0,
                          "hybrid": sum(1 - r["recorded"] for r in used) / len(used),
                          "t2": sum(r["asks_t2"] for r in used) / len(used),
                          "t2x": sum(r["asks_t2"] for r in used) / len(used),
                          "t2n": sum(r["asks_t2"] for r in used) / len(used)}[rule]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", default=["E1", "E1b"])
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    lines = ["| run | arm | questions | rule | form | attempts | accuracy | M1 silent errors | M2 false holds "
             "| random M1 / M2 | extra run |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    result, notes = {}, []
    for name in a.runs:
        rdir, bank, af = (ROOT / p for p in RUNS[name])
        qs = {q.qid: q for q in C.load_questions(bank)}
        form = {}
        for x in json.loads(af.read_text()):
            form[x["qid"]] = x["form"] if x["form"] == "DIRECT" else ("DERIVED-none" if x.get("metric") is None
                                                                      else "DERIVED-step")
        aliases = C.KEY_ALIASES
        if name in ("E3a", "E4"):           # the world's key spellings (E4: B2's column spellings are not used here)
            spec = importlib.util.spec_from_file_location(f"{name}_world", ROOT / f"experiments/{name}/world.py")
            W = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(W)
            aliases = W.WORLD.key_aliases
        why = Counter()
        rows = deliveries(latest(rdir / "attempts.jsonl"), qs, aliases, rdir, why)
        if why:
            result[f"{name}/t2x_not_pass"] = dict(why)
            notes.append(f"{name}, t2x: pairs not accepted (first answer eligible, held): "
                         + "; ".join(f"{k} {v}" for k, v in why.most_common()))
        for arm in ("N", "H1", "H"):
            ar = [r for r in rows if r["arm"] == arm]
            if not ar:
                continue
            rules = ("recorded", "cross", "same") + (("hybrid", "t2", "both") if arm != "N" else ()) \
                + (("t2x", "t2n") if arm != "N" and compare_answers is not None else ())
            for rule in rules:
                for f in ("all", "DIRECT", "DERIVED-step", "DERIVED-none"):
                    sub = ar if f == "all" else [r for r in ar if form[r["qid"]] == f]
                    if not sub:
                        continue
                    b = boot(sub, rule, random.Random(f"{SEED}/{name}/{arm}/{rule}/{f}"))   # own stream per cell
                    result[f"{name}/{arm}/{rule}/{f}"] = b
                    m2 = "—" if b["M2"] is None else f"{b['M2']:.1%} [{b['M2_ci'][0]:.1%}, {b['M2_ci'][1]:.1%}]"
                    lines.append(f"| {name} | {arm} | {b['questions']} | {rule} | {f} | {b['n']} | {b['accuracy']:.1%} | "
                                 f"{b['M1']:.1%} [{b['M1_ci'][0]:.1%}, {b['M1_ci'][1]:.1%}] | {m2} | "
                                 f"{b['random_M1']:.1%} / {b['random_M2']:.1%} | {b['extra_run']:.0%} |"
                                 + (f" ({b['no_verifier']} without a verifier)" if b["no_verifier"] else ""))
    text = "\n".join(lines + ([""] + notes if notes else []))
    print(text)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
        a.out.with_suffix(".json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
