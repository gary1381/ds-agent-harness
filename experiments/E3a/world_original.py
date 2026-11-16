"""E3a control world: the same data under its original column names and dates (arm N only).

The main runs disguise UCI Online Retail II (+14 years, renamed columns) because the agents may have
seen it. The control asks the same questions on the undisguised copy, so a gap between the two arm-N
runs is what remembered conventions ("drop cancellations and null customers") are worth. Everything
comes from the main world (world.py) and is changed only where an agent or the grader would notice:

- arm N's warehouse copy: raw columns renamed back (order_no -> Invoice, sku -> StockCode, ...,
  buyer_id -> "Customer ID"), timestamps and `month` 14 years earlier; derived columns (amount,
  market, line_type) keep their names;
- the data catalog: the same text with the original names and years;
- the evaluator: `truth_lines` 14 years earlier; control questions are the main bank with the years
  changed in the question and the truth SQL (make_control_bank.py), reviewed by the designer.

    .venv/bin/python experiments/E1/run_census.py --runs-root experiments/E3a/runs --run control \\
        --questions experiments/E3a/questions/bank_control.yaml --world experiments/E3a/world_original.py --arms N
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
import re
import sys

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import world as W  # noqa: E402

SHIFT = 14
YEARS = {"2023": "2009", "2024": "2010", "2025": "2011"}
ORIGINAL = {"order_no": "Invoice", "sku": "StockCode", "item_name": "Description", "qty": "Quantity",
            "ordered_at": "InvoiceDate", "unit_price": "Price", "buyer_id": "Customer ID", "country": "Country"}
TIME_COLUMNS = ("ordered_at", "available_at")
CUTOFF_TEXT = "2011-12-09"
WS = W.WS


def shift_years(text: str) -> str:
    """2023 -> 2009, 2024 -> 2010, 2025 -> 2011 wherever a year stands alone (dates, 'Q1 2025', 2025年)."""
    return re.sub(r"(?<!\d)(2023|2024|2025)(?!\d)", lambda m: YEARS[m.group(1)], text)


def _select(con, table: str) -> str:
    out = []
    for (col,) in con.execute(f"SELECT column_name FROM (DESCRIBE {table})").fetchall():
        expr = f'"{col}"'
        if col in TIME_COLUMNS:
            expr = f'"{col}" - INTERVAL {SHIFT} YEAR'
        elif col == "month":
            expr = f"strftime(strptime(\"month\", '%Y-%m') - INTERVAL {SHIFT} YEAR, '%Y-%m')"
        out.append(f'{expr} AS "{ORIGINAL.get(col, col)}"')
    return ", ".join(out)


def transform_warehouse(path: Path) -> None:
    """Rename and re-date every table of arm N's warehouse copy in place."""
    con = duckdb.connect(str(path))
    try:
        con.execute("SET threads = 2")
        for schema, table in con.execute("SELECT schema_name, table_name FROM duckdb_tables()").fetchall():
            name = f'"{schema}"."{table}"'
            con.execute(f'CREATE TABLE "{schema}"."_orig" AS SELECT {_select(con, name)} FROM {name}')
            con.execute(f"DROP TABLE {name}")
            con.execute(f'ALTER TABLE "{schema}"."_orig" RENAME TO "{table}"')
        views = con.execute("SELECT schema_name, view_name FROM duckdb_views() WHERE NOT internal").fetchall()
        if views:
            raise W.CensusError(f"arm N warehouse has views the control cannot rewrite: {views}")
    finally:
        con.close()


def catalog_md(ws) -> str:
    text = W.catalog_md(ws)
    for new, old in ORIGINAL.items():
        text = re.sub(rf"(?<![\w.]){re.escape(new)}(?!\w)", old, text)
    return shift_years(text)


def evaluator_db(ws, path: Path) -> Path:
    """The main evaluator's truth_lines, 14 years earlier (column names stay: the grader's SQL is ours)."""
    path = W.evaluator_db(ws, path)
    con = duckdb.connect(str(path))
    try:
        con.execute(f"""CREATE OR REPLACE TABLE truth_lines AS SELECT * REPLACE (
                            ordered_at - INTERVAL {SHIFT} YEAR AS ordered_at,
                            strftime(strptime(month, '%Y-%m') - INTERVAL {SHIFT} YEAR, '%Y-%m') AS month)
                        FROM truth_lines""")
    finally:
        con.close()
    return path


WORLD = dataclasses.replace(W.WORLD, name="e3a-retail-original", catalog_md=catalog_md, evaluator_db=evaluator_db,
                            transform_warehouse=transform_warehouse)
