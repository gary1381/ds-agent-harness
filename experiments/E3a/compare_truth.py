"""E3a: compare the governed build A with the independent build B, line by line and metric by metric.

A = silver.order_lines of the E3a workspace build and the E3a semantic model (query_metric).
B = truth_lines.parquet and truth_metrics.csv written by run_truth_b.py.
Every disagreement is listed for the designer, who reads it against DEFINITIONS.md and fixes the
wrong side (ADJUDICATION.md). Writes truth_compare.json next to B's files and prints a summary.

    .venv/bin/python experiments/E3a/compare_truth.py --b workspace/external/e3a/truth_b/work
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dsharness.common import open_readonly  # noqa: E402
from dsharness.context.semantic import load_semantic_model, query_metric  # noqa: E402
import world as W  # noqa: E402

RAW_COLS = ("order_no", "sku", "item_name", "qty", "ordered_at", "unit_price", "buyer_id", "country")
REL_TOL = 1e-9


def lines(con, b_path: Path) -> dict:
    con.execute(f"CREATE VIEW b AS SELECT * FROM read_parquet('{b_path}')")
    on = " AND ".join(f"a.{c} IS NOT DISTINCT FROM b.{c}" for c in RAW_COLS)
    n_a, n_b = (con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("wh.silver.order_lines", "b"))
    dup_b = con.execute(f"SELECT count(*) - count(DISTINCT ({', '.join(RAW_COLS)})) FROM b").fetchone()[0]
    con.execute(f"""CREATE TEMP TABLE j AS SELECT a.line_type AS a_type, b.line_type AS b_type,
                    CAST(a.amount AS DECIMAL(18, 4)) AS a_amount, CAST(b.amount AS DECIMAL(18, 4)) AS b_amount,
                    a.month AS a_month, b.month AS b_month, a.market AS a_market, b.market AS b_market,
                    {', '.join(f'coalesce(a.{c}, b.{c}) AS {c}' for c in RAW_COLS)}
                    FROM wh.silver.order_lines a FULL OUTER JOIN b ON {on}""")
    only = con.execute("SELECT sum(CASE WHEN b_type IS NULL THEN 1 ELSE 0 END), "
                       "sum(CASE WHEN a_type IS NULL THEN 1 ELSE 0 END) FROM j").fetchone()
    types = con.execute("""SELECT a_type, b_type, count(*) AS n FROM j WHERE a_type IS NOT NULL AND b_type IS NOT NULL
                           AND a_type <> b_type GROUP BY 1, 2 ORDER BY n DESC""").df()
    examples = con.execute("""SELECT a_type, b_type, order_no, sku, item_name, qty, unit_price FROM j
                              WHERE a_type <> b_type QUALIFY row_number() OVER (PARTITION BY a_type, b_type) <= 3""").df()
    other = {f: con.execute(f"SELECT count(*) FROM j WHERE a_type IS NOT NULL AND b_type IS NOT NULL AND {cond}").fetchone()[0]
             for f, cond in (("amount", "a_amount <> b_amount"), ("month", "a_month <> b_month"),
                             ("market", "a_market <> b_market"))}
    return {"rows_a": n_a, "rows_b": n_b, "duplicate_rows_b": dup_b, "only_a": only[0], "only_b": only[1],
            "type_disagreements": types.to_dict("records"), "type_examples": examples.astype(str).to_dict("records"),
            "other_disagreements": other}


def metrics(con, model, b_csv: Path) -> dict:
    first, last = "2023-12", "2025-12"
    rows = []
    for m in model.metrics:
        for dims in ([], ["market"]):
            df = query_metric(con, model, m, dims, (first, last))
            for r in df.to_dict("records"):
                rows.append({"month": r["month"], "market": r.get("market", "ALL"), "metric": m, "a": r["value"]})
    a = pd.DataFrame(rows)
    b = pd.read_csv(b_csv).rename(columns={"value": "b"})
    b["market"] = b["market"].astype(str)
    j = a.merge(b, on=["month", "market", "metric"], how="outer")

    def differs(r) -> bool:
        x, y = r["a"], r["b"]
        if pd.isna(x) or pd.isna(y):
            return not (pd.isna(x) and pd.isna(y)) and not (pd.isna(y) and x == 0)
        return not math.isclose(float(x), float(y), rel_tol=REL_TOL, abs_tol=1e-9)
    j["differs"] = j.apply(differs, axis=1)
    bad = j[j["differs"]]
    by_metric = bad.groupby("metric").size().to_dict()
    return {"compared": int(len(j)), "differ": int(len(bad)), "differ_by_metric": by_metric,
            "examples": bad.head(30).astype(str).to_dict("records")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--b", type=Path, required=True, help="directory with truth_lines.parquet and truth_metrics.csv")
    a = ap.parse_args()
    ws = W.WS
    wh = ws.lake / W.certified(ws) / "warehouse.duckdb"
    con = open_readonly(wh)
    model = load_semantic_model(ws.context / "semantic_model.yaml")
    out = {"metrics": metrics(con, model, a.b / "truth_metrics.csv")}
    con.close()
    con = duckdb.connect()
    con.execute("SET threads = 2")
    con.execute(f"ATTACH '{wh}' AS wh (READ_ONLY)")
    out["lines"] = lines(con, a.b / "truth_lines.parquet")
    (a.b.parent / "truth_compare.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({"lines": {k: v for k, v in out["lines"].items() if k != "type_examples"},
                      "metrics": {k: v for k, v in out["metrics"].items() if k != "examples"}}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
