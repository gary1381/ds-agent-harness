"""E3a control bank: the frozen main bank with the years moved back 14 (2023 -> 2009, 2024 -> 2010, 2025 -> 2011).

The question text, the months field and the truth SQL change; nothing else does. Every control truth,
computed on the control evaluator (truth_lines 14 years earlier), must equal the main truth with its
month keys moved back: a mismatch means the rewrite touched something it should not have, and the
script stops. The designer still reads the rewritten questions (REVIEW_control.md).

    .venv/bin/python experiments/E3a/make_control_bank.py
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dsharness.bench import census as C  # noqa: E402
import world as W  # noqa: E402
import world_original as O  # noqa: E402

QDIR = ROOT / "experiments/E3a/questions"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", type=Path, default=QDIR / "bank.yaml")
    ap.add_argument("--out", type=Path, default=QDIR / "bank_control.yaml")
    ap.add_argument("--truth", type=Path, default=W.TRUTH_B, help="adjudicated truth_lines.parquet")
    a = ap.parse_args()
    W.TRUTH_B = a.truth
    bank = yaml.safe_load(a.bank.read_text(encoding="utf-8"))
    control = []
    for q in bank:
        c = dict(q, question=O.shift_years(q["question"]), months=[O.shift_years(m) for m in q["months"]],
                 truth_sql=O.shift_years(q["truth_sql"]))
        control.append(c)
    text = yaml.safe_dump(control, allow_unicode=True, sort_keys=False)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "control.yaml").write_text(text, encoding="utf-8")
        main_db = W.evaluator_db(W.WS, tmp / "main.duckdb")
        ctrl_db = O.evaluator_db(W.WS, tmp / "control.duckdb")
        bad = []
        for q, c in zip(C.load_questions(a.bank), C.load_questions(tmp / "control.yaml")):
            moved = {O.shift_years(k): v for k, v in C.compute_truth(q, W.WS, main_db, "", W.ALIASES).items()}
            if moved != C.compute_truth(c, W.WS, ctrl_db, "", W.ALIASES):
                bad.append(q.qid)
    if bad:
        raise SystemExit(f"control truth differs from the moved main truth for {bad}")
    a.out.write_text(text, encoding="utf-8")
    lines = ["# E3a control bank: rewritten questions (generated; the designer reads every line)", "",
             f"Source {a.bank.name} sha256 {hashlib.sha256(a.bank.read_bytes()).hexdigest()}; "
             f"control sha256 {hashlib.sha256(text.encode()).hexdigest()}. Every control truth equals the main "
             "truth with month keys moved back 14 years.", "", "| qid | main | control |", "|---|---|---|"]
    lines += [f"| {q['qid']} | {q['question']} | {c['question']} |" for q, c in zip(bank, control)]
    (a.out.parent / "REVIEW_control.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(control)} control questions, truths checked -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
