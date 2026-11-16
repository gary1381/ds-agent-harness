"""E1 error labelling: one compact sheet per wrong attempt, built from the attempt record and its transcript.

    .venv/bin/python experiments/E1/label_sheet.py --run main            # sheets for the designer
    .venv/bin/python experiments/E1/label_sheet.py --run main --blind     # 30% sample for the blind labeller

A sheet holds the question, unit, truth rows, the answer rows, the grade, and a digest of the
transcript (tool calls and short outputs, final message). The designer's sheets also show agent, arm,
tier and verdict; blind sheets omit them and are written to their own folder, in shuffled order,
so the blind labeller sees neither the arm nor the designer's labels (pre-registration, taxonomy).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.bench import census as C  # noqa: E402
from dsharness.common import read_jsonl  # noqa: E402

CALL_CHARS, OUT_CHARS, FINAL_CHARS, MAX_STEPS = 900, 400, 1200, 30
BLIND_SHARE, BLIND_SEED = 0.30, 20261107


def _clip(s, n: int) -> str:
    s = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)
    return s if len(s) <= n else s[: n // 2] + f" …[{len(s) - n} chars]… " + s[-n // 2:]


def claude_steps(home: Path) -> list[tuple[str, str]]:
    steps = []
    for p in sorted((home / ".claude" / "projects").glob("*/*.jsonl")):
        for r in read_jsonl(p):
            content = (r.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for x in content:
                if x.get("type") == "tool_use":
                    steps.append(("call " + x.get("name", "?"), _clip(x.get("input"), CALL_CHARS)))
                elif x.get("type") == "tool_result":
                    out = x.get("content")
                    if isinstance(out, list):
                        out = " ".join(y.get("text", "") for y in out if isinstance(y, dict))
                    steps.append(("result", _clip(out or "", OUT_CHARS)))
                elif x.get("type") == "text" and r.get("type") == "assistant":
                    steps.append(("say", _clip(x.get("text", ""), FINAL_CHARS)))
    return steps


def codex_steps(home: Path) -> list[tuple[str, str]]:
    steps = []
    for p in sorted((home / ".codex" / "sessions").rglob("*.jsonl")):
        for r in read_jsonl(p):
            pl = r.get("payload") or {}
            t = pl.get("type")
            if r.get("type") != "response_item":
                continue
            if t in ("custom_tool_call", "function_call"):
                steps.append(("call " + pl.get("name", "?"), _clip(pl.get("input") or pl.get("arguments"), CALL_CHARS)))
            elif t in ("custom_tool_call_output", "function_call_output"):
                out = pl.get("output")
                if isinstance(out, list):
                    out = " ".join(y.get("text", "") for y in out if isinstance(y, dict))
                steps.append(("result", _clip(out or "", OUT_CHARS)))
            elif t == "message" and pl.get("role") == "assistant":
                txt = " ".join(c.get("text", "") for c in pl.get("content", []) if isinstance(c, dict))
                steps.append(("say", _clip(txt, FINAL_CHARS)))
    return steps


def sheet(rec: dict, q: C.Question, truth: dict, *, blind: bool, sheet_id: str) -> str:
    home = Path(rec["attempt_dir"]) / "home"
    steps = (claude_steps if rec["agent"] == "claude" else codex_steps)(home)
    if len(steps) > MAX_STEPS:
        steps = steps[:6] + [("…", f"{len(steps) - MAX_STEPS} steps omitted")] + steps[-(MAX_STEPS - 6):]
    grade = rec.get("grade") or {}
    L = [f"## {sheet_id}", ""]
    if not blind:
        h = rec.get("harness") or {}
        L += [f"- agent {rec['agent']}, arm {rec['arm']}, rep {rec['rep']}, status {rec['status']}, "
              f"claimed_success {rec.get('claimed_success')}, delivered {rec.get('delivered')}, "
              f"tier {h.get('tier')}, verdict {h.get('verdict')}"]
    L += [f"- question ({q.lang}): {q.question}", f"- keys {list(q.keys)}, unit {q.unit}",
          f"- truth: {_clip(truth, 600)}",
          f"- answer rows: {_clip((rec.get('final_answer') or {}).get('answer_rows') if isinstance(rec.get('final_answer'), dict) else rec.get('final_answer'), 600)}",
          f"- grade: {grade.get('reason')}; mismatches {_clip(grade.get('mismatches'), 500)}",
          f"- notes: {_clip((rec.get('final_answer') or {}).get('notes', '') if isinstance(rec.get('final_answer'), dict) else '', 400)}",
          "", "transcript digest:", ""]
    L += [f"    [{k}] {v}".replace("\n", "\n      ") for k, v in steps]
    return "\n".join(L) + "\n"


def latest(records: list[dict]) -> list[dict]:
    keep = {}
    for r in records:
        if r.get("status") in ("RETRY", "SKIPPED"):
            continue
        keep[(r["agent"], r["arm"], r["qid"], r["rep"])] = r
    return list(keep.values())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--questions", type=Path, default=ROOT / "experiments/E1/questions/bank.yaml")
    ap.add_argument("--blind", action="store_true")
    ap.add_argument("--runs-root", type=Path, default=ROOT / "experiments/E1/runs")
    a = ap.parse_args()
    run = a.runs_root / a.run
    qs = {q.qid: q for q in C.load_questions(a.questions)}
    truth = json.loads((run / "truth.json").read_text(encoding="utf-8"))
    wrong = sorted((r for r in latest(read_jsonl(run / "attempts.jsonl")) if not r.get("correct")),
                   key=lambda r: (r["agent"], r["arm"], r["qid"], r["rep"]))
    out = run / "labels" / ("blind" if a.blind else "designer")
    out.mkdir(parents=True, exist_ok=True)
    index = []
    if a.blind:
        rng = random.Random(BLIND_SEED)
        wrong = rng.sample(wrong, round(BLIND_SHARE * len(wrong)))
    for i, r in enumerate(wrong):
        sid = f"W{i + 1:03d}"
        (out / f"{sid}.md").write_text(sheet(r, qs[r["qid"]], truth[r["qid"]], blind=a.blind, sheet_id=sid),
                                       encoding="utf-8")
        index.append({"sheet": sid, "agent": r["agent"], "arm": r["arm"], "qid": r["qid"], "rep": r["rep"]})
    # the key from blind sheet ids to attempts stays out of the blind folder
    (run / "labels" / f"index_{'blind' if a.blind else 'designer'}.json").write_text(json.dumps(index, indent=1))
    print(f"{len(index)} sheets -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
