"""E3b v2: the designer's realise() vs Luna's independent one, on the development data only.

Reads truth_lines.parquet up to 2025-08-31 (what the agents' warehouse holds; the sealed months are never read) and
realises a fixed set of claims made at as-of 2025-06-01, covering every type, quantity, filter, breakdown and list
metric of CLAIMS_SPEC.md, plus each claim replayed at the backtest origins by shift_claim. Lists use score files
drawn at random (seed 20261108). Prints every disagreement; the pre-registration is frozen only with none left (or
each explained and settled in the spec).

    taskset -c 4,5 .venv/bin/python experiments/E3b/compare_realise.py experiments/E3b/runs/luna_realise2
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dsharness.readout import forecast as D  # noqa: E402

TRUTH = ROOT / "workspace/external/e3a/truth_lines.parquet"
DEV_END = "2025-09-01"          # exclusive: the agents' warehouse end; nothing on or after it is read
ASOF = "2025-06-01"
JJA, JUN = ["2025-06-01", "2025-09-01"], ["2025-06-01", "2025-07-01"]
SEED = 20261108


def lines() -> pd.DataFrame:
    con = duckdb.connect()
    df = con.execute(f"""SELECT order_no, sku, item_name, qty, ordered_at, CAST(unit_price AS DOUBLE) AS unit_price,
                                buyer_id, country, CAST(amount AS DOUBLE) AS amount, market, line_type
                         FROM read_parquet('{TRUTH}') WHERE ordered_at < TIMESTAMP '{DEV_END}'""").df()
    df["buyer_id"] = df.buyer_id.astype("Int64")
    assert df.ordered_at.max() < pd.Timestamp(DEV_END)
    return df


def claims(df: pd.DataFrame, files: Path) -> list[dict]:
    rng = np.random.default_rng(SEED)
    hist = df[(df.ordered_at < pd.Timestamp(ASOF)) & (df.line_type == "product") & df.buyer_id.notna()]
    act = sorted(hist[hist.ordered_at >= pd.Timestamp("2024-12-01")].buyer_id.unique().astype(int))
    pd.DataFrame({"buyer_id": act, "p": rng.random(len(act))}).to_csv(files / "scores.csv", index=False)
    top = hist.groupby("buyer_id").amount.sum().sort_values(ascending=False).head(60).index.astype(int)
    sc = pd.DataFrame({"buyer_id": list(top[:50]) + [999999, int(top[0])], "s": list(rng.random(50)) + [0.5, "x"]})
    sc.to_csv(files / "scores_dirty.csv", index=False)             # outsider, repeated id, unreadable score
    pd.DataFrame({"buyer_id": list(top[:30]) + [int(top[0])], "p": rng.random(31)}).to_csv(files / "accts.csv", index=False)
    skus = hist.sku.value_counts().head(400).index
    items = [{"buyer_id": int(b), "sku": skus[rng.integers(len(skus))], "rank": r} for b in top[:40] for r in (1, 2, 3)]
    pd.DataFrame(items).to_csv(files / "items.csv", index=False)
    top_skus = list(hist[hist.sku.str.match(r"^\d{5}", na=False)].sku.value_counts().head(5).index)
    countries = list(hist[hist.market == "non-UK"].country.value_counts().head(4).index)
    weeks = D._all_keys("week", pd.Timestamp(JJA[0]), pd.Timestamp(JJA[1]))
    months = ["2025-06", "2025-07", "2025-08"]

    def total(i, q, f=None, by=None, keys=(None,), window=JJA):
        return {"id": i, "type": "total", "says": "", "quantity": q, "filter": f or {}, "window": window, "by": by,
                "values": [{"key": k, "point": 1} for k in keys]}
    active = {"buyers": {"active_in": ["2024-12-01", ASOF]}}
    cs = [total(f"t_{q}", q) for q in D.QUANTITIES]
    cs += [total(f"w_{q}", q, by="week", keys=weeks) for q in ("product_sales", "orders", "new_buyers")]
    cs += [total(f"m_{q}", q, by="month", keys=months) for q in D.QUANTITIES]
    cs += [total("uk", "product_sales", {"market": "UK"}), total("nonuk_new", "new_buyers", {"market": "non-UK"}),
           total("ctry", "product_sales", by="country", keys=countries + ["Atlantis"]),
           total("sku_qty", "product_qty", {"sku": top_skus}, by="sku", keys=top_skus),
           total("cat", "product_sales", {"catalogue_only": True}),
           total("act_sales", "product_sales", active), total("act_orders_m", "orders", active, "month", months),
           total("top20", "product_sales", {"buyers": {"top_by": "product_sales", "n": 20, "over": ["2024-06-01", ASOF]}}),
           total("first", "buyers", {"buyers": {"first_in": ["2025-03-01", ASOF]}}),
           total("file_b", "product_sales", {"buyers": {"file": "accts.csv", "id_column": "buyer_id"}}),
           total("repeat_uk_m", "repeat_sales", {"market": "UK"}, "month", months),
           total("bad_q", "margin"), total("bad_window", "product_sales", window=["2025-08-01", "2025-10-01"]),
           total("peek", "product_sales", {"buyers": {"active_in": ["2025-01-01", "2025-07-01"]}})]
    cs.append({"id": "share_top20", "type": "share", "says": "", "window": JJA, "values": [{"key": None, "point": .3}],
               "numerator": {"quantity": "product_sales",
                             "filter": {"buyers": {"top_by": "product_sales", "n": 20, "over": ["2024-06-01", ASOF]}}},
               "denominator": {"quantity": "product_sales", "filter": {}}})
    cs.append({"id": "share_rep_m", "type": "share", "says": "", "window": JJA, "by": "month",
               "values": [{"key": m, "point": .5} for m in months],
               "numerator": {"quantity": "repeat_sales", "filter": {}},
               "denominator": {"quantity": "product_sales", "filter": {}}})
    cs.append({"id": "verdict", "type": "verdict", "says": "", "statement": "", "rests_on": "share_top20", "op": ">=",
               "value": 0.5, "answer": "no"})
    cs.append({"id": "pb", "type": "per_buyer", "says": "", "file": "accts.csv", "id_column": "buyer_id",
               "point_column": "p", "quantity": "product_sales", "filter": {}, "window": ["2025-06-01", "2025-08-01"]})
    for name, ev in (("l_none", {"quantity": "orders", "op": "==", "value": 0}),
                     ("l_500", {"quantity": "product_sales", "op": ">=", "value": 500})):
        for higher in ("event", "no_event"):
            cs.append({"id": f"{name}_{higher}", "type": "list", "says": "", "file": "scores.csv",
                       "id_column": "buyer_id", "score_column": "p", "higher_is": higher, "k": 250,
                       "event": {**ev, "filter": {}, "window": JJA}, "population": active,
                       "claimed": [{"metric": "precision_at_k", "point": .4}]})
    cs.append({"id": "l_dirty", "type": "list", "says": "", "file": "scores_dirty.csv", "id_column": "buyer_id",
               "score_column": "s", "higher_is": "event", "k": 10,
               "event": {"quantity": "orders", "filter": {}, "window": JUN, "op": ">=", "value": 1},
               "population": {"buyers": {"top_by": "product_sales", "n": 60, "over": ["2023-12-01", ASOF]}},
               "claimed": [{"metric": "auc", "point": .6}]})
    cs.append({"id": "l_items", "type": "list", "says": "", "file": "items.csv", "id_column": "buyer_id", "k": 2,
               "event": {"quantity": "product_lines", "filter": {}, "window": JUN, "op": ">=", "value": 1},
               "population": {"buyers": {"top_by": "product_sales", "n": 60, "over": ["2023-12-01", ASOF]}},
               "claimed": [{"metric": "hit_at_k", "point": .2}]})
    cs.append({"id": "ni", "type": "not_identifiable", "says": "", "asked": "lift", "reason": "causal"})
    return cs


def load_luna(d: Path):
    spec = importlib.util.spec_from_file_location("realise_luna", d / "realise_luna.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def same(a, b, path="") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        keys = (set(a) | set(b)) - {"notes"}
        return [x for k in sorted(keys) for x in same(a.get(k), b.get(k), f"{path}.{k}")]
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} vs {len(b)}"]
        return [x for i, (u, v) in enumerate(zip(a, b)) for x in same(u, v, f"{path}[{i}]")]
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        tol = 1e-6 if abs(a) <= 1.5 and abs(b) <= 1.5 else 0.011
        return [] if abs(a - b) <= tol else [f"{path}: {a} vs {b}"]
    if path.endswith(".error"):
        return [] if (a is None) == (b is None) else [f"{path}: {a!r} vs {b!r}"]
    return [] if a == b else [f"{path}: {a!r} vs {b!r}"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("luna_dir", type=Path)
    a = ap.parse_args()
    L = load_luna(a.luna_dir)
    df = lines()
    files = a.luna_dir / "compare_files"
    files.mkdir(exist_ok=True)
    cs = claims(df, files)
    by_id = {c["id"]: c for c in cs}
    cases = [(c, None) for c in cs]
    for origin in ("2024-06-01", "2025-01-01", "2025-03-01"):
        cases += [(D.shift_claim(c, ASOF, origin), origin) for c in cs if c["type"] in ("total", "share")]
    bad, shift_bad = [], []
    for c, origin in cases:
        if origin:
            lc = L.shift_claim(by_id[c["id"]], ASOF, origin)
            if lc != c:
                shift_bad.append((c["id"], origin, same(c, lc)))
        dv = D.realise(c, df, files, by_id, DEV_END)
        lv = L.realise(c, df, files, by_id, DEV_END)
        diff = same(dv, lv)
        if diff:
            bad.append({"id": c["id"], "origin": origin, "diff": diff[:5], "designer": str(dv)[:300],
                        "luna": str(lv)[:300]})
    out = {"cases": len(cases), "disagree": len(bad), "shift_disagree": len(shift_bad), "rows": bad,
           "shift_rows": [list(x) for x in shift_bad]}
    (a.luna_dir / "compare.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"{len(cases)} cases, {len(bad)} disagree, {len(shift_bad)} shifts disagree")
    for r in bad:
        print(f"- {r['id']} @ {r['origin']}: {r['diff']}")
    for i, o, d in shift_bad:
        print(f"- shift {i} @ {o}: {d[:3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
