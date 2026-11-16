"""E1b: apply the designer's review (questions/REVIEW.md) to bank_v0.yaml and freeze bank.yaml.

Dropped entries stay in the file with a `dropped` reason (the census loader skips them). Every live
truth SQL is re-run; a fixed truth is checked again against the E1 truths and the earlier E1b truths.

    .venv/bin/python experiments/E1b/freeze_bank.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import duckdb
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_bank as B  # noqa: E402

QDIR = B.ROOT / "experiments/E1b/questions"

DROP = {
    "C-H36": "months 2026-03..2026-06 break the brief's period rule (last month 2026-05)",
    "C-H37": "months 2026-03..2026-06 break the brief's period rule (last month 2026-05)",
}
TRUTH_FIX = {
    # "Count each enrolment": the author divided the churned enrolments by distinct customers
    # (1,856) instead of enrolments (2,256); 400 customers were enrolled in more than one month.
    "C-L37": """SELECT
  p.arm,
  SUM(CASE WHEN p.churned THEN 1 ELSE 0 END) * 1.0
    / NULLIF(COUNT(*), 0) AS value
FROM truth_program p
JOIN dim_customer d ON d.customer_id = p.customer_id
WHERE p.campaign_month BETWEEN '2026-03' AND '2026-05'
  AND NOT d.is_test
GROUP BY p.arm""",
}


def main() -> int:
    bank = yaml.safe_load((QDIR / "bank_v0.yaml").read_text(encoding="utf-8"))
    con = duckdb.connect(str(B.EVALDB), read_only=True)
    e1_truth = {k: {kk.lower(): vv for kk, vv in v.items()} for k, v in json.loads(B.E1_TRUTH.read_text()).items()}
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
                dup = next((k for k, v in {**e1_truth, **seen}.items() if B.same_truth(tv, v)), None)
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
