"""E3b v2 warehouses: `silver.order_lines` as author_brief.md describes it, cut before a date.

The agents' warehouse is the cut at 2025-09-01 (data ends 2025-08-31). The gate's runs use cuts at the development
origin (2025-06-01) and at the backtest origins. Every cut reads E3a's independent build (`truth_lines.parquet`)
below the cut date only; nothing here reads a line on or after 2025-09-01.

    python experiments/E3b/warehouse_v2.py --dir experiments/E3b/runs/v2/warehouses
"""
from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
TRUTH = ROOT / "workspace/external/e3a/truth_lines.parquet"
DATA_END = "2025-09-01"          # exclusive; the sealed months start here
COLUMNS = "order_no, sku, item_name, qty, ordered_at, unit_price, buyer_id, country, amount, market, line_type"
DEV_ORIGIN = "2025-06-01"
ORIGINS = ["2024-09-01"] + [f"2025-{m:02d}-01" for m in range(1, 9)]


def path(d: Path, cut: str) -> Path:
    return d / f"wh_{cut}.duckdb"


def build(dest: Path, cut: str) -> Path:
    if cut > DATA_END:
        raise ValueError(f"cut {cut} is after the agents' data end")
    tmp = dest.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    con.execute("PRAGMA threads=1")
    con.execute("CREATE SCHEMA silver")
    con.execute(f"""CREATE TABLE silver.order_lines AS SELECT {COLUMNS} FROM read_parquet('{TRUTH}')
                    WHERE ordered_at < TIMESTAMP '{cut}' ORDER BY ordered_at, order_no, sku""")
    con.close()
    tmp.rename(dest)
    return dest


def ensure(d: Path, cuts: list[str] | None = None) -> dict[str, Path]:
    d.mkdir(parents=True, exist_ok=True)
    out = {}
    for c in sorted(set(cuts or [DATA_END, DEV_ORIGIN, *ORIGINS])):
        p = path(d, c)
        out[c] = p if p.exists() else build(p, c)
    return out


def lines(wh: Path) -> pd.DataFrame:
    """The warehouse's order lines as realise() reads them."""
    con = duckdb.connect(str(wh), read_only=True)
    df = con.execute("""SELECT order_no, sku, item_name, qty, ordered_at, CAST(unit_price AS DOUBLE) AS unit_price,
                               buyer_id, country, CAST(amount AS DOUBLE) AS amount, market, line_type
                        FROM silver.order_lines""").df()
    con.close()
    df["buyer_id"] = df.buyer_id.astype("Int64")
    return df


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", type=Path, required=True)
    for c, p in ensure(ap.parse_args().dir).items():
        print(c, p, p.stat().st_size)
