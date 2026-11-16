"""Exploratory: how many harness-arm attempts count as undelivered only because the census matches the
final answer to a submission by exact JSON digest (611761 vs 611761.0, or a 17th digit dropped).

For every H/H1 attempt whose final rows match no submission (harness.final_rows_submitted false), this
reads the submit_answer calls from the agent's own log (Claude session JSONL, Codex event stream), finds
each call's audit record by rows digest (tier, gate), and asks whether some VERIFIED + ALLOW submission
carries the same rows as the final answer once numbers are compared as numbers (12 significant digits).
The pre-registered census rule is unchanged; this only sizes the artifact.

    python3 experiments/E1b/delivery_match.py --run main [--runs-root experiments/E1b/runs] [--out FILE]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench.live import digest  # noqa: E402


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return v
    return float(f"{float(v):.12g}")


def norm(rows) -> list | None:
    if not isinstance(rows, list):
        return None
    out = [tuple(sorted((str(k), _num(v)) for k, v in r.items())) if isinstance(r, dict) else r for r in rows]
    return sorted(out, key=repr)


def submissions(att: Path) -> list[list]:
    """answer_rows of every submit_answer call in the agent's own log, in call order."""
    subs = []
    for f in sorted((att / "home/.claude/projects").glob("*/*.jsonl")):          # Claude
        for line in f.open():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            for x in (d.get("message") or {}).get("content") or []:
                if isinstance(x, dict) and x.get("type") == "tool_use" and x.get("name", "").endswith("submit_answer"):
                    subs.append((x.get("input") or {}).get("answer_rows"))
    if not subs and (att / "stdout.txt").exists():                                 # Codex
        for line in (att / "stdout.txt").open():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            it = d.get("item") or {}
            if d.get("type") == "item.completed" and it.get("type") == "mcp_tool_call" and it.get("tool") == "submit_answer":
                subs.append((it.get("arguments") or {}).get("answer_rows"))
    return subs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--runs-root", type=Path, default=ROOT / "experiments/E1b/runs")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    run = a.runs_root / a.run
    recs = [json.loads(line) for line in (run / "attempts.jsonl").open()]
    rows_out, tally = [], Counter()
    for r in recs:
        h = r.get("harness") or {}
        if r["arm"] == "N" or h.get("final_rows_submitted", True) or not h.get("n_submissions"):
            continue
        att = ROOT / r["attempt_dir"]
        audit = {}
        for line in (att / "harness_ws/registry/audit.jsonl").open():
            x = json.loads(line)
            if x.get("tool") == "submit_answer" and x.get("outcome") == "ok":
                s = x.get("result_summary") or {}
                audit[((x.get("args") or {}).get("answer_rows") or {}).get("sha256")] = (s.get("tier"), s.get("gate"))
        final = norm((r.get("final_answer") or {}).get("answer_rows"))
        found = None
        for rows in submissions(att):
            tg = audit.get(digest(rows)) if isinstance(rows, list) else None
            if tg and final is not None and norm(rows) == final:
                found = tg if found is None or tg == ("VERIFIED", "ALLOW") else found
        kind = ("numeric match, VERIFIED+ALLOW" if found == ("VERIFIED", "ALLOW")
                else f"numeric match, {found[0]}/{found[1]}" if found else "no submission with these rows")
        tally[(r["arm"], kind, r["correct"])] += 1
        rows_out.append((r["arm"], r["qid"], r["agent"], r["rep"], r["correct"], kind))
    lines = ["# Census delivery match: digest vs numbers (exploratory, delivery_match.py)", "",
             "Attempts in harness arms whose final rows match no submission by digest.", "",
             "| arm | what the agent's log shows | correct | attempts |", "|---|---|---|---|"]
    lines += [f"| {arm} | {kind} | {c} | {n} |" for (arm, kind, c), n in sorted(tally.items())]
    lines += ["", "| arm | qid | agent | rep | correct | finding |", "|---|---|---|---|---|---|"]
    lines += [f"| {' | '.join(map(str, x))} |" for x in sorted(rows_out)]
    text = "\n".join(lines) + "\n"
    if a.out:
        a.out.write_text(text)
        print("->", a.out)
    print("\n".join(lines[:6 + len(tally)]))


if __name__ == "__main__":
    main()
