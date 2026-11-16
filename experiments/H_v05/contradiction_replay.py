"""H v0.5, regression check: which recorded submissions would the claim and shape checks now contradict?

Replays finished census runs; nothing is re-run and no agent is called. Each harness-arm attempt's audit log
records every submit_answer call with its question, metric_call and derive (the rows only as a digest). The
contradiction checks read only those, so they can be recomputed exactly under any harness tree. Run once per
tree, then compare:

    DSH_SRC=<v04 tree>/src .venv/bin/python experiments/H_v05/contradiction_replay.py --tag v04 --out a.json
    DSH_SRC=<v05 tree>/src .venv/bin/python experiments/H_v05/contradiction_replay.py --tag v05 --out b.json
    .venv/bin/python experiments/H_v05/contradiction_replay.py --compare a.json b.json --md out.md

The attempt's grade applies to its final submission only. E1 and E1b used the built-in model (no workspace
context); a run's own model is read from the attempt's harness_ws/context when it has one.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ["experiments/E1/runs/main", "experiments/E1b/runs/main", "experiments/E3a/runs/main"]


def latest(path: Path) -> dict[tuple, dict]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        out[(r["agent"], r["arm"], r["qid"], r["rep"])] = r
    return out


def replay(tag: str) -> list[dict]:
    sys.path.insert(0, os.environ.get("DSH_SRC") or str(ROOT / "src"))
    from dsharness.context.semantic import load_semantic_model
    from dsharness.tools import analytics as A
    from dsharness.tools.derive import DeriveError, normalize_derive
    from dsharness.tools.shape import shape_contradictions
    takes_named = "named" in inspect.signature(shape_contradictions).parameters
    models: dict[str, object] = {}
    out = []
    for run in RUNS:
        rdir = ROOT / run
        for (agent, arm, qid, rep), rec in sorted(latest(rdir / "attempts.jsonl").items()):
            if arm == "N":
                continue
            ws = rdir / "attempts" / agent / arm / f"{qid}-r{rep}" / "harness_ws"
            audit = ws / "registry" / "audit.jsonl"
            if not audit.exists():
                continue
            mpath = ws / "context" / "semantic_model.yaml"
            key = str(mpath) if mpath.exists() else ""
            if key not in models:
                models[key] = load_semantic_model(mpath if mpath.exists() else None)
            model = models[key]
            subs = [json.loads(x)["args"] for x in audit.read_text(encoding="utf-8").splitlines()
                    if json.loads(x).get("tool") == "submit_answer"]
            for i, a in enumerate(subs):
                row = {"run": run.split("/")[1], "agent": agent, "arm": arm, "qid": qid, "rep": rep, "i": i,
                       "final": i == len(subs) - 1, "correct": bool(rec.get("correct")) if i == len(subs) - 1
                       else None, "declared": a.get("metric_call") is not None}
                if a.get("metric_call") is None:
                    out.append(row)
                    continue
                q = a.get("question") or ""
                try:
                    declared = A.normalize_metric_call(a["metric_call"])
                    if declared["metric"] not in model.metrics:
                        raise ValueError("unknown metric")
                except (ValueError, TypeError) as exc:
                    out.append({**row, "skipped": f"metric_call: {str(exc)[:80]}"})
                    continue
                try:
                    derived = normalize_derive(a.get("derive"), declared["metric"], model.spec, declared)
                except DeriveError as exc:
                    out.append({**row, "skipped": f"derive refused: {str(exc)[:80]}"})
                    continue
                q_months = A.months_from_text(q)[0]
                kw = {}
                if takes_named and q_months is None:
                    named = sorted(m for m in A._scan_months(q)[0] if m != "!")
                    kw["named"] = (named[0], named[-1]) if named else None
                found = A.claim_contradictions(model, q, declared)
                found += shape_contradictions(q, declared, derived, model.metrics[declared["metric"]].unit,
                                              q_months, **kw)
                out.append({**row, "contradictions": found})
    return out


def _same(msg: str) -> str:
    """v0.5 reworded one message (it lists 'total'); the same finding is not a new one."""
    return msg.replace("(sum, mean, pool or growth)", "(sum, mean, pool, total or growth)")


def compare(a_path: str, b_path: str) -> str:
    a = {(r["run"], r["agent"], r["arm"], r["qid"], r["rep"], r["i"]): r for r in json.loads(Path(a_path).read_text())}
    b = {(r["run"], r["agent"], r["arm"], r["qid"], r["rep"], r["i"]): r for r in json.loads(Path(b_path).read_text())}
    tally: dict[str, Counter] = defaultdict(Counter)
    new, gone = [], []
    for k, rb in b.items():
        ra = a.get(k, {})
        run = rb["run"]
        tally[run]["submissions"] += 1
        tally[run]["declared"] += rb["declared"]
        ca, cb = {_same(x) for x in ra.get("contradictions") or []}, {_same(x) for x in rb.get("contradictions") or []}
        if "skipped" in rb:
            tally[run]["skipped (v0.5)"] += 1
        if cb - ca:
            tally[run]["new contradiction"] += 1
            new.append((k, rb, sorted(cb - ca)))
            if rb["final"] and rb["correct"]:
                tally[run]["new on a right final answer"] += 1
        if ca - cb:
            tally[run]["contradiction removed"] += 1
            gone.append((k, rb, sorted(ca - cb)))
            if rb["final"] and rb["correct"]:
                tally[run]["removed on a right final answer"] += 1
    cols = ["submissions", "declared", "new contradiction", "new on a right final answer", "contradiction removed",
            "removed on a right final answer", "skipped (v0.5)"]
    lines = ["# Contradiction replay: v0.4 vs v0.5", "",
             "Every recorded harness-arm submission with its question, metric_call and derive, checked by "
             "both trees' claim and shape checks (no agent re-run). A right final answer is one the census "
             "graded correct.", "", "| run | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for run in sorted(tally):
        lines.append(f"| {run} | " + " | ".join(str(tally[run][c]) for c in cols) + " |")

    def show(title, items):
        lines.extend(["", f"## {title}", ""])
        if not items:
            lines.append("None.")
        for (run, agent, arm, qid, rep, i), rb, msgs in items:
            grade = "right final" if rb["final"] and rb["correct"] else ("wrong final" if rb["final"] else "earlier")
            lines.append(f"- {run} {agent} {arm} {qid} r{rep} #{i + 1} ({grade}): " + "; ".join(msgs))
    show("New in v0.5", new)
    show("Removed in v0.5", gone)
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tag")
    ap.add_argument("--out")
    ap.add_argument("--compare", nargs=2)
    ap.add_argument("--md")
    args = ap.parse_args()
    if args.compare:
        text = compare(*args.compare)
        if args.md:
            Path(args.md).write_text(text, encoding="utf-8")
        print(text[:4000])
        return 0
    rows = replay(args.tag)
    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"{len(rows)} submissions -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
