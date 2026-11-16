"""Build one month of the yellow-taxi metrics:  python pipeline/build.py 2024-12

1. contract: the export's columns must match contract.json (name and type). A column marked
   "required": false may be missing; it is then read as NULL of its type.
2. models/*.sql run in file-name order, each creating the view named after its file. {month_start} and
   {month_end} are the month's bounds; the export is the view `raw_trips`.
3. tests/*.sql: each returns rows (test, passed, detail); every row must pass.
4. If everything passes, monthly_metrics is written to out/<month>/metrics.csv and the month is READY.

Reads raw/yellow_tripdata_<month>.parquet from the working directory.
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sys

import duckdb

HERE = Path(__file__).resolve().parent


def month_bounds(month: str) -> tuple[str, str]:
    y, m = map(int, month.split("-"))
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1)
    return start.isoformat(), end.isoformat()


def check_contract(con, src: Path) -> tuple[list[str], str]:
    """Problems with the export's columns, and the SELECT list that reads it (missing optional columns as NULL)."""
    contract = json.loads((HERE / "contract.json").read_text())["columns"]
    found = {name: typ for name, typ, *_ in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{src}')").fetchall()}
    problems, select = [], []
    for c in contract:
        if c["name"] not in found:
            if c.get("required", True):
                problems.append(f"missing column {c['name']}")
            select.append(f"NULL::{c['type']} AS {c['name']}")
        elif found[c["name"]] != c["type"]:
            problems.append(f"column {c['name']} is {found[c['name']]}, contract says {c['type']}")
        else:
            select.append(c["name"])
    known = {c["name"] for c in contract}
    problems += [f"unexpected column {n} ({t})" for n, t in found.items() if n not in known]
    return problems, ", ".join(select)


def main() -> int:
    month = sys.argv[1]
    start, end = month_bounds(month)
    src = Path("raw") / f"yellow_tripdata_{month}.parquet"
    con = duckdb.connect()
    con.execute("PRAGMA threads=2")
    problems, select = check_contract(con, src)
    print(f"contract: {'PASS' if not problems else 'FAIL'}")
    for p in problems:
        print(f"  {p}")
    if problems:
        print(f"{month}: FAILED")
        return 1
    con.execute(f"CREATE VIEW raw_trips AS SELECT {select} FROM read_parquet('{src}')")
    for sql in sorted((HERE / "models").glob("*.sql")):
        con.execute(f"CREATE VIEW {sql.stem.split('_', 1)[1]} AS "
                    + sql.read_text().format(month_start=start, month_end=end))
    failed = 0
    for sql in sorted((HERE / "tests").glob("*.sql")):
        for test, passed, detail in con.execute(sql.read_text().format(month_start=start, month_end=end)).fetchall():
            print(f"{test}: {'PASS' if passed else 'FAIL'}  {detail}")
            failed += not passed
    if failed:
        print(f"{month}: FAILED")
        return 1
    out = Path("out") / month
    out.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM monthly_metrics) TO '{out / 'metrics.csv'}' (HEADER)")
    print(f"{month}: READY  -> {out / 'metrics.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
