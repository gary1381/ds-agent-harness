"""E3a: build the disguised and the original copies of UCI Online Retail II (CC BY 4.0).

The main runs use the disguised copy: timestamps shifted by +14 years day by day (2009-12..2011-12
becomes 2023-12..2025-12; the source years have no 29 February), columns renamed. Values, product
names and countries are unchanged. The control copy keeps the original names and dates. Both go to
workspace/external/e3a/ (git-ignored; data is never committed) with a manifest of sha256 sums.

    .venv/bin/python experiments/E3a/build_world.py
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "workspace/external/online_retail_ii/online_retail_ii.parquet"
OUT = ROOT / "workspace/external/e3a"
SHIFT_YEARS = 14
RENAME = {"Invoice": "order_no", "StockCode": "sku", "Description": "item_name", "Quantity": "qty",
          "InvoiceDate": "ordered_at", "Price": "unit_price", "Customer ID": "buyer_id", "Country": "country"}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=SRC)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET threads = 2")
    src = str(a.src).replace("'", "''")
    cols = ", ".join(
        f'"{old}" + INTERVAL {SHIFT_YEARS} YEAR AS {new}' if old == "InvoiceDate"
        else f'CAST("{old}" AS BIGINT) AS {new}' if old == "Customer ID"
        else f'"{old}" AS {new}'
        for old, new in RENAME.items())
    disguised, original = a.out / "raw_lines.parquet", a.out / "raw_lines_original.parquet"
    con.execute(f"COPY (SELECT {cols} FROM read_parquet('{src}')) TO '{disguised}' (FORMAT parquet)")
    con.execute(f"COPY (SELECT * FROM read_parquet('{src}')) TO '{original}' (FORMAT parquet)")
    n, lo, hi = con.execute(f"SELECT count(*), min(ordered_at), max(ordered_at) FROM read_parquet('{disguised}')").fetchone()
    n0, lo0, hi0 = con.execute(f"SELECT count(*), min(InvoiceDate), max(InvoiceDate) FROM read_parquet('{original}')").fetchone()
    # the shift must be exact, row for row
    bad = con.execute(f"""SELECT count(*) FROM
        (SELECT row_number() OVER () AS i, ordered_at FROM read_parquet('{disguised}')) d
        JOIN (SELECT row_number() OVER () AS i, InvoiceDate FROM read_parquet('{original}')) o USING (i)
        WHERE d.ordered_at <> o.InvoiceDate + INTERVAL {SHIFT_YEARS} YEAR""").fetchone()[0]
    if n != n0 or bad:
        raise SystemExit(f"disguise check failed: rows {n} vs {n0}, {bad} timestamps off")
    manifest = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": {"path": str(a.src.relative_to(ROOT)) if a.src.is_relative_to(ROOT) else a.src.name,
                           "sha256": sha256(a.src), "licence": "CC BY 4.0, UCI Machine Learning Repository dataset 502"},
                "shift_years": SHIFT_YEARS, "rename": RENAME,
                "disguised": {"file": disguised.name, "sha256": sha256(disguised), "rows": n,
                              "first": str(lo), "last": str(hi)},
                "original": {"file": original.name, "sha256": sha256(original), "rows": n0,
                             "first": str(lo0), "last": str(hi0)}}
    (a.out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps(manifest, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
