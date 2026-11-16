"""E3a: apply the designer's review (questions/REVIEW.md) to bank_v0.yaml and freeze bank.yaml.

As in E1b: dropped entries stay in the file with a `dropped` reason (the census loader skips them);
every live truth SQL is re-run on the evaluator database; a fixed truth is checked again against the
earlier truths so a fix cannot turn a question into a repeat.

    .venv/bin/python experiments/E3a/freeze_bank.py
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys

import duckdb
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_bank as B  # noqa: E402

QDIR = B.ROOT / "experiments/E3a/questions"
EVALDB = QDIR / "evaluator.duckdb"

DROP: dict[str, str] = {
    "R-L32": "two readings: the running total's start is not stated (January 2025 gives 0.67M, the start of the data 10.8M)",
    "R-H35": "two readings: one total per market over April-June, or one row per market and month",
}
TRUTH_FIX: dict[str, str] = {
    # ROUND removed: a right unrounded answer could fail against the rounded truth (REVIEW.md)
    "R-H03": """SELECT -SUM(CASE WHEN line_type = 'cancellation' THEN amount END)::DOUBLE
       / SUM(CASE WHEN line_type = 'product' THEN amount END)::DOUBLE AS value
FROM truth_lines
WHERE month = '2025-06'""",
    "R-H07": """WITH m AS (
  SELECT month, SUM(amount)::DOUBLE AS g
  FROM truth_lines
  WHERE line_type = 'product'
  GROUP BY month
),
w AS (
  SELECT month, g, LAG(g) OVER (ORDER BY month) AS prev
  FROM m
)
SELECT month, 100 * (g - prev) / prev AS value
FROM w
WHERE month BETWEEN '2025-01' AND '2025-11'
ORDER BY month""",
    "R-H11": """WITH a AS (
  SELECT market,
         SUM(amount)::DOUBLE / COUNT(DISTINCT order_no) AS aov
  FROM truth_lines
  WHERE line_type = 'product' AND month BETWEEN '2025-01' AND '2025-11'
  GROUP BY market
)
SELECT (SELECT aov FROM a WHERE market = 'UK') / (SELECT aov FROM a WHERE market = 'non-UK') AS value""",
    "R-H14": """WITH y AS (
  SELECT substr(month, 1, 4) AS yr, SUM(amount)::DOUBLE AS g
  FROM truth_lines
  WHERE line_type = 'product'
    AND ((month BETWEEN '2024-01' AND '2024-11') OR (month BETWEEN '2025-01' AND '2025-11'))
  GROUP BY yr
)
SELECT 100 * ((SELECT g FROM y WHERE yr = '2025') - (SELECT g FROM y WHERE yr = '2024'))
       / (SELECT g FROM y WHERE yr = '2024') AS value""",
    "R-H15": """SELECT market,
       -SUM(CASE WHEN line_type = 'cancellation' THEN amount END)::DOUBLE
       / SUM(CASE WHEN line_type = 'product' THEN amount END)::DOUBLE AS value
FROM truth_lines
WHERE month BETWEEN '2024-10' AND '2024-12'
GROUP BY market
ORDER BY market""",
}


def main() -> int:
    bank = yaml.safe_load((QDIR / "bank_v0.yaml").read_text(encoding="utf-8"))
    con = duckdb.connect(str(EVALDB), read_only=True)
    seen = {}
    for q in bank:
        if q["qid"] in DROP and not q.get("dropped"):
            q["dropped"] = DROP[q["qid"]]
        if q["qid"] in TRUTH_FIX:
            q["truth_sql"] = TRUTH_FIX[q["qid"]]
            q["truth_fixed"] = "designer review, see REVIEW.md"
        tv = B.truth_values(q, con)
        if not q.get("dropped"):
            status, detail = B.truth_check(q, con)
            if status != "OK" or B.problems(q):
                raise SystemExit(f"{q['qid']}: {status} {detail} {B.problems(q)}")
            if q["qid"] in TRUTH_FIX:
                dup = next((k for k, v in seen.items() if B.same_truth(tv, v)), None)
                if dup:
                    raise SystemExit(f"{q['qid']}: fixed truth equals {dup}")
                print(f"{q['qid']} fixed: {detail}")
            seen[q["qid"]] = tv
    con.close()
    out = QDIR / "bank.yaml"
    out.write_text(yaml.safe_dump(bank, allow_unicode=True, sort_keys=False), encoding="utf-8")
    live = [q for q in bank if not q.get("dropped")]
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"{len(live)} live of {len(bank)} -> {out}\nsha256 {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
