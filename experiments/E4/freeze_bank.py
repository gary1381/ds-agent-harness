"""E4: apply the designer's review (questions/REVIEW.md) to bank_v0.yaml and freeze bank.yaml.

As in E3a: dropped entries stay in the file with a `dropped` reason (the census loader skips them); every live
truth SQL is re-run on the evaluator database; a fixed truth is checked again against the earlier E4 truths and
the E3a truths so a fix cannot turn a question into a repeat. New in E4: every ROUND() is taken out of the live
truths (the truth is the exact value; a question that asks for two decimals is still graded with half a unit in
the answer's last place), instead of fixing only the truths a right answer would fail against.

    .venv/bin/python experiments/E4/freeze_bank.py
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sys

import duckdb
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_bank as B  # noqa: E402

QDIR = B.ROOT / "experiments/E4/questions"
EVALDB = QDIR / "evaluator.duckdb"

DROP: dict[str, str] = {
    "F-L11": "two readings: the running total's start is not stated (January 2025, or the start of the data); "
             "as E3a R-L32",
}
DROP["F-L25"] = ("two readings: net product sales per active UK customer with guest sales in the numerator "
                 "(608.98) or only identified customers' sales; E3a kept such questions only when they say which")
META_FIX: dict[str, dict] = {           # months must include the year-ago base the truth reads (as F-L12, F-H10)
    "F-L31": {"months": ["2024-01", "2025-11"]},
    "F-H30": {"months": ["2024-01", "2025-06"]},
}
TRUTH_FIX: dict[str, str] = {
    # the CROSS JOIN counted every cancellation line once per product line (300,029.79 GBP per order)
    "F-L07": """SELECT (SELECT -SUM(amount) FROM truth_lines WHERE month = '2025-07' AND line_type = 'cancellation')
       / (SELECT COUNT(DISTINCT order_no) FROM truth_lines WHERE month = '2025-07' AND line_type = 'product')
       AS value""",
    # the 2024 count covered January to December 2024 (19,627 orders), not January to June (8,416)
    "F-H31": """SELECT CAST((c25 - c24) * 100.0 / c24 AS DOUBLE) AS value
FROM (
    SELECT COUNT(DISTINCT CASE WHEN month BETWEEN '2025-01' AND '2025-06' THEN order_no END) AS c25,
           COUNT(DISTINCT CASE WHEN month BETWEEN '2024-01' AND '2024-06' THEN order_no END) AS c24
    FROM truth_lines
    WHERE line_type = 'product'
) t""",
}


def strip_round(sql: str) -> str:
    """ROUND(expr, n) -> expr, for every ROUND call (nested parentheses respected)."""
    while True:
        m = re.search(r"\bROUND\s*\(", sql, re.I)
        if not m:
            return sql
        depth, i, comma = 1, m.end(), None
        while depth:
            c = sql[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            elif c == "," and depth == 1:
                comma = i
            i += 1
        inner = sql[m.end():comma if comma is not None else i - 1]
        sql = sql[:m.start()] + "(" + inner.strip() + ")" + sql[i:]


def main() -> int:
    bank = yaml.safe_load((QDIR / "bank_v0.yaml").read_text(encoding="utf-8"))
    con = duckdb.connect(str(EVALDB), read_only=True)
    old = [q for q in yaml.safe_load(B.ROOT.joinpath("experiments/E3a/questions/bank_v0.yaml").read_text(encoding="utf-8"))
           if q.get("truth_sql")]
    seen = {f"E3a {q['qid']}": B.truth_values(q, con) for q in old}
    for q in bank:
        if q["qid"] in DROP and not q.get("dropped"):
            q["dropped"] = DROP[q["qid"]]
        if q.get("dropped"):
            continue
        fixed = []
        if q["qid"] in META_FIX:
            q.update(META_FIX[q["qid"]])
            fixed.append("months include the year-ago base")
        if q["qid"] in TRUTH_FIX:
            q["truth_sql"] = TRUTH_FIX[q["qid"]]
            fixed.append("designer review, see REVIEW.md")
        if re.search(r"\bROUND\s*\(", q["truth_sql"], re.I):
            q["truth_sql"] = strip_round(q["truth_sql"])
            fixed.append("ROUND removed")
        if fixed:
            q["truth_fixed"] = "; ".join(fixed)
        tv = B.truth_values(q, con)
        status, detail = B.truth_check(q, con)
        if status != "OK" or B.problems(q) or tv is None:
            raise SystemExit(f"{q['qid']}: {status} {detail} {B.problems(q)}")
        dup = next((k for k, v in seen.items() if B.same_truth(tv, v)), None)
        if dup:
            raise SystemExit(f"{q['qid']}: truth equals {dup}")
        if q["qid"] in TRUTH_FIX:
            print(f"{q['qid']} fixed: {detail}")
        seen[q["qid"]] = tv
    con.close()
    out = QDIR / "bank.yaml"
    out.write_text(yaml.safe_dump(bank, allow_unicode=True, sort_keys=False), encoding="utf-8")
    live = [q for q in bank if not q.get("dropped")]
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"{len(live)} live of {len(bank)} ({sum('ROUND removed' in q.get('truth_fixed', '') for q in live)} "
          f"with ROUND removed) -> {out}\nsha256 {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
