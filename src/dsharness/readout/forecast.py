"""Typed forecast claims (E3b v2): what actually happened to each claim in `claims.json`.

docs/experiments/E3b/CLAIMS_SPEC.md is the contract. `realise()` is the one function both the backtest gate (on
past origins) and the sealed truth script (on September-November 2025) call; GPT-6 Luna wrote a second
implementation from the spec alone (experiments/E3b/runs/luna_realise/realise_luna.py) and the two are compared on
the development data before the pre-registration is frozen.

    from dsharness.readout.forecast import realise, shift_claim
    realise(claim, lines, files_dir)        # lines: a DataFrame with silver.order_lines' columns
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import re

import numpy as np
import pandas as pd

QUANTITIES = ("product_sales", "product_qty", "product_lines", "orders", "buyers", "new_buyers", "repeat_buyers",
              "repeat_sales", "guest_sales", "cancellation_value", "cancellation_lines")
BY = (None, "week", "month", "country", "sku", "buyer_id")
METRICS = ("precision_at_k", "recall_at_k", "auc", "base_rate", "hit_at_k")
OPS = {"==": np.equal, ">=": np.greater_equal, ">": np.greater, "<=": np.less_equal, "<": np.less}
REASONS = ("causal", "absent_field", "absent_population")


class ClaimError(ValueError):
    pass


def _ts(d: str) -> pd.Timestamp:
    return pd.Timestamp(date.fromisoformat(str(d)[:10]))


def _window(w) -> tuple[pd.Timestamp, pd.Timestamp]:
    if not isinstance(w, (list, tuple)) or len(w) != 2:
        raise ClaimError(f"window must be [start, end): {w!r}")
    a, b = _ts(w[0]), _ts(w[1])
    if b <= a:
        raise ClaimError(f"empty window {w!r}")
    return a, b


def _ids(values) -> set[int]:
    out = set()
    for v in values:
        s = str(v).strip()
        if s and s.lower() not in ("nan", "none", "<na>"):
            out.add(int(float(s)))
    return out


def buyer_set(spec: dict, lines: pd.DataFrame, files_dir: Path, start: pd.Timestamp) -> set[int]:
    """A buyer filter (CLAIMS_SPEC.md, Filters), from data before `start` only."""
    if not isinstance(spec, dict) or len(spec) == 0:
        raise ClaimError(f"bad buyer filter {spec!r}")
    if "ids" in spec:
        return _ids(spec["ids"])
    if "file" in spec:
        f = Path(files_dir) / spec["file"]
        if not f.exists():
            raise ClaimError(f"missing file {spec['file']}")
        return _ids(pd.read_csv(f, dtype=str)[spec.get("id_column", "buyer_id")])
    key = next(k for k in ("active_in", "top_by", "first_in") if k in spec) if any(
        k in spec for k in ("active_in", "top_by", "first_in")) else None
    if key is None:
        raise ClaimError(f"bad buyer filter {spec!r}")
    span = spec["over"] if key == "top_by" else spec[key]
    a, b = _window(span)
    if b > start:
        raise ClaimError(f"buyer filter {key} {span} ends after the window start {start.date()}")
    prod = lines[(lines.line_type == "product") & lines.buyer_id.notna() & (lines.ordered_at < b)]
    if key == "first_in":
        first = prod.groupby("buyer_id").ordered_at.min()
        return _ids(first[(first >= a) & (first < b)].index)
    inside = prod[prod.ordered_at >= a]
    if key == "active_in":
        return _ids(inside.buyer_id.unique())
    q = spec["top_by"]
    per = _per_buyer(inside, q, prod)
    per = per.reset_index().sort_values([q, "buyer_id"], ascending=[False, True])
    return _ids(per.buyer_id.head(int(spec["n"])))


def _per_buyer(inside: pd.DataFrame, q: str, history: pd.DataFrame) -> pd.Series:
    if q in ("product_sales", "repeat_sales"):
        s = inside.groupby("buyer_id").amount.sum()
    elif q == "product_qty":
        s = inside.groupby("buyer_id").qty.sum()
    elif q == "product_lines":
        s = inside.groupby("buyer_id").size()
    elif q == "orders":
        s = inside.groupby("buyer_id").order_no.nunique()
    else:
        raise ClaimError(f"top_by {q} not supported")
    return s.rename(q)


def apply_filter(f: dict, lines: pd.DataFrame, files_dir: Path, start: pd.Timestamp) -> pd.DataFrame:
    f = f or {}
    unknown = set(f) - {"market", "country", "sku", "catalogue_only", "buyers"}
    if unknown:
        raise ClaimError(f"unknown filter keys {sorted(unknown)}")
    keep = pd.Series(True, index=lines.index)
    if "market" in f:
        if f["market"] not in ("UK", "non-UK"):
            raise ClaimError(f"market {f['market']!r}")
        keep &= lines.market == f["market"]
    if "country" in f:
        keep &= lines.country.isin(list(f["country"]))
    if "sku" in f:
        keep &= lines.sku.isin([str(s) for s in f["sku"]])
    if f.get("catalogue_only"):
        keep &= lines.sku.fillna("").str.match(r"^\d{5}")
    if "buyers" in f:
        ids = buyer_set(f["buyers"], lines, files_dir, start)
        keep &= lines.buyer_id.notna() & lines.buyer_id.isin(list(ids))
    return lines[keep]


def _keys(df: pd.DataFrame, by: str | None, a: pd.Timestamp) -> pd.Series:
    if by is None:
        return pd.Series("", index=df.index)
    if by == "week":
        monday = (df.ordered_at.dt.normalize() - pd.to_timedelta(df.ordered_at.dt.weekday, unit="D"))
        return monday.dt.strftime("%Y-%m-%d")
    if by == "month":
        return df.ordered_at.dt.strftime("%Y-%m")
    if by == "buyer_id":
        return df.buyer_id.astype("Int64").astype(str)
    return df[by].astype(str)


def quantity(q: str, lines: pd.DataFrame, win: tuple, by: str | None = None,
             status: pd.DataFrame | None = None) -> pd.Series:
    """`q` over the window, per key ('' when by is None). `lines` is already filtered. New or repeat status is
    decided from `status` (all product lines, unfiltered, before the window end; CLAIMS_SPEC.md edge case 4)."""
    if q not in QUANTITIES:
        raise ClaimError(f"unknown quantity {q!r}")
    a, b = win
    w = lines[(lines.ordered_at >= a) & (lines.ordered_at < b)]
    prod = w[w.line_type == "product"]
    if q in ("cancellation_value", "cancellation_lines"):
        c = w[w.line_type == "cancellation"]
        g = c.groupby(_keys(c, by, a))
        return (-g.amount.sum()) if q == "cancellation_value" else g.size().astype(float)
    if q == "guest_sales":
        gp = prod[prod.buyer_id.isna()]
        return gp.groupby(_keys(gp, by, a)).amount.sum()
    if q in ("product_sales", "product_qty", "product_lines", "orders"):
        g = prod.groupby(_keys(prod, by, a))
        return {"product_sales": lambda: g.amount.sum(), "product_qty": lambda: g.qty.sum().astype(float),
                "product_lines": lambda: g.size().astype(float),
                "orders": lambda: g.order_no.nunique().astype(float)}[q]()
    named = prod[prod.buyer_id.notna()]
    if q == "buyers":
        return named.groupby(_keys(named, by, a)).buyer_id.nunique().astype(float)
    st = lines if status is None else status
    seen = set(st[(st.line_type == "product") & st.buyer_id.notna() & (st.ordered_at < a)].buyer_id.unique())
    if q == "new_buyers":
        # never bought before the window; counted in the key of their first filtered product line in it
        first = named[~named.buyer_id.isin(seen)].sort_values(["ordered_at", "order_no", "sku"]).drop_duplicates(
            "buyer_id")
        return first.groupby(_keys(first, by, a)).buyer_id.nunique().astype(float)
    rep = named[named.buyer_id.isin(seen)]
    if q == "repeat_buyers":
        return rep.groupby(_keys(rep, by, a)).buyer_id.nunique().astype(float)
    return rep.groupby(_keys(rep, by, a)).amount.sum()


def _all_keys(by: str | None, a: pd.Timestamp, b: pd.Timestamp) -> list[str] | None:
    if by is None:
        return [""]
    if by == "week":
        m = a - pd.Timedelta(days=a.weekday())
        return [d.strftime("%Y-%m-%d") for d in pd.date_range(m, b - pd.Timedelta(days=1), freq="7D")]
    if by == "month":
        return [d.strftime("%Y-%m") for d in pd.period_range(a, b - pd.Timedelta(days=1), freq="M").to_timestamp()]
    return None


def _value_keys(claim: dict) -> list:
    vals = claim.get("values")
    if not isinstance(vals, list) or not vals:
        raise ClaimError("no values")
    return [v.get("key") for v in vals]


def _norm_key(k, by) -> str:
    if k is None:
        return ""
    if by == "buyer_id":
        return str(int(float(k)))
    if by == "week":
        return str(k)[:10]
    return str(k)


def _round(x: float, share: bool = False) -> float:
    return round(float(x), 6 if share else 2)


def _coverage(lines: pd.DataFrame, win: tuple, data_end) -> pd.DataFrame:
    """Edge case 13: the window must lie inside the data; returns the lines before the window end."""
    if len(lines) == 0:
        raise ClaimError("no data")
    end = _ts(data_end) if data_end is not None else lines.ordered_at.max().normalize() + pd.Timedelta(days=1)
    if win[1] > end:
        raise ClaimError(f"the data ends {end.date()} (exclusive), before the window end {win[1].date()}")
    if win[0] < lines.ordered_at.min().normalize():
        raise ClaimError(f"the window starts {win[0].date()}, before the data")
    return lines[lines.ordered_at < win[1]]


def _total(claim: dict, lines: pd.DataFrame, files_dir: Path, data_end=None) -> dict:
    win = _window(claim["window"])
    hist = _coverage(lines, win, data_end)
    by = claim.get("by")
    if by not in BY:
        raise ClaimError(f"by {by!r}")
    keys = [_norm_key(k, by) for k in _value_keys(claim)]

    def side(part):
        df = apply_filter(part.get("filter"), hist, files_dir, win[0])
        return quantity(part["quantity"], df, win, by, status=hist)
    notes = []
    if claim["type"] == "share":
        num, den = side(claim["numerator"]), side(claim["denominator"])
        out = []
        for k in keys:
            d = float(den.get(k, 0.0))
            if d == 0:
                notes.append(f"zero denominator for key {k or None}")
            out.append({"key": None if by is None else k,
                        "actual": None if d == 0 else _round(float(num.get(k, 0.0)) / d, share=True)})
        return {"id": claim["id"], "values": out, "notes": notes}
    q = side(claim)
    return {"id": claim["id"], "values": [{"key": None if by is None else k, "actual": _round(q.get(k, 0.0))}
                                          for k in keys], "notes": claim.get("_notes", [])}


def _per_buyer_claim(claim: dict, lines: pd.DataFrame, files_dir: Path, data_end=None) -> dict:
    f = Path(files_dir) / claim["file"]
    if not f.exists():
        raise ClaimError(f"missing file {claim['file']}")
    df = pd.read_csv(f, dtype=str)
    need = [claim.get("id_column", "buyer_id"), claim.get("point_column")] + [
        claim[c] for c in ("low_column", "high_column") if claim.get(c)]
    missing = [c for c in need if not c or c not in df.columns]
    if missing:
        raise ClaimError(f"missing column(s) {missing} in {claim['file']}")
    raw = [str(int(float(v))) for v in df[claim.get("id_column", "buyer_id")].dropna()]
    ids = list(dict.fromkeys(raw))                    # file order, first row of a repeated id
    notes = [f"{len(raw) - len(ids)} repeated buyer ids, first row kept"] if len(raw) > len(ids) else []
    total = {**claim, "type": "total", "by": "buyer_id", "values": [{"key": i} for i in ids], "_notes": notes}
    return _total(total, lines, files_dir, data_end)


def _auc(score: np.ndarray, y: np.ndarray) -> float | None:
    pos, neg = int(y.sum()), int(len(y) - y.sum())
    if pos == 0 or neg == 0:
        return None
    r = pd.Series(score).rank(method="average").to_numpy()
    return float((r[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def _list(claim: dict, lines: pd.DataFrame, files_dir: Path, data_end=None) -> dict:
    ev = claim["event"]
    win = _window(ev["window"])
    hist = _coverage(lines, win, data_end)
    f = Path(files_dir) / claim["file"]
    if not f.exists():
        raise ClaimError(f"missing file {claim['file']}")
    df = pd.read_csv(f, dtype=str)
    idc = claim.get("id_column", "buyer_id")
    df = df[df[idc].notna()].copy()
    df["_id"] = df[idc].map(lambda v: int(float(v)))
    pop = buyer_set(claim["population"]["buyers"], hist, files_dir, win[0])
    k = int(claim["k"])
    n_file = int(df._id.nunique())
    n_dropped = n_file - int(df[df._id.isin(pop)]._id.nunique())
    base = {"n_population": len(pop), "n_file": n_file, "n_dropped": n_dropped}
    notes = []
    if any(c.get("metric") == "hit_at_k" for c in claim.get("claimed") or []):   # per-buyer item lists (case 12)
        listed = df[df._id.isin(pop)]
        top = listed[pd.to_numeric(listed["rank"], errors="coerce") <= k] if "rank" in listed.columns else listed
        w = hist[(hist.ordered_at >= win[0]) & (hist.line_type == "product") & hist.buyer_id.notna()]
        bought = set(zip(w.buyer_id.astype(int), w.sku.astype(str)))
        hits = {i: False for i in listed._id.unique()}
        for i, sku in zip(top._id, top.sku.astype(str)):
            hits[i] |= (i, sku) in bought
        m = {"hit_at_k": _round(np.mean(list(hits.values())), share=True) if hits else None}
        return {"id": claim["id"], "metrics": m, **base, "notes": notes}
    if ev.get("op") not in OPS:
        raise ClaimError(f"op {ev.get('op')!r}")
    df["_s"] = pd.to_numeric(df[claim["score_column"]], errors="coerce")
    if claim.get("higher_is", "event") == "no_event":
        df["_s"] = -df["_s"]
    if df._id.duplicated().any():
        notes.append(f"{int(df._id.duplicated().sum())} repeated buyer ids, first row kept")
    inside = df.drop_duplicates("_id")
    inside = inside[inside._id.isin(pop)]
    scored = inside[inside._s.notna()].sort_values(["_s", "_id"], ascending=[False, True])
    unscored = sorted((pop - set(scored._id)))        # missing rows and unreadable scores (case 10)
    if unscored:
        notes.append(f"{len(unscored)} population buyers missing from the file or without a score, ranked last")
    order = pd.concat([scored[["_id", "_s"]], pd.DataFrame({"_id": unscored, "_s": np.nan})], ignore_index=True)
    order["_id"] = order._id.astype(int)
    efilter = apply_filter(ev.get("filter"), hist, files_dir, win[0])
    per = quantity(ev["quantity"], efilter, win, "buyer_id", status=hist)
    vals = order._id.map(lambda i: float(per.get(str(i), 0.0))).to_numpy()
    y = OPS[ev["op"]](vals, float(ev["value"])).astype(int)
    lo = np.nanmin(order._s) - 1 if order._s.notna().any() else 0.0
    score = order._s.fillna(lo).to_numpy()
    top = y[:k]
    auc = _auc(score, y)
    m = {"precision_at_k": _round(top.mean(), share=True) if k > 0 and len(top) else None,
         "recall_at_k": _round(top.sum() / y.sum(), share=True) if y.sum() else None,
         "auc": None if auc is None else _round(auc, share=True),
         "base_rate": _round(y.mean(), share=True) if len(y) else None}
    return {"id": claim["id"], "metrics": m, **base, "notes": notes}


def realise(claim: dict, lines: pd.DataFrame, files_dir: Path, claims_by_id: dict | None = None,
            data_end: str | None = None) -> dict:
    """What happened to one claim (CLAIMS_SPEC.md, Output). Never raises for a bad claim: returns {id, error}."""
    cid = claim.get("id") if isinstance(claim, dict) else None
    try:
        t = claim.get("type")
        if t in ("total", "share"):
            return _total(claim, lines, files_dir, data_end)
        if t == "per_buyer":
            return _per_buyer_claim(claim, lines, files_dir, data_end)
        if t == "list":
            return _list(claim, lines, files_dir, data_end)
        if t == "not_identifiable":
            if claim.get("reason") not in REASONS:
                raise ClaimError(f"reason {claim.get('reason')!r}")
            return {"id": cid, "values": []}
        if t == "verdict":
            base = (claims_by_id or {}).get(claim.get("rests_on"))
            if base is None:
                raise ClaimError(f"rests_on {claim.get('rests_on')!r} is not a claim in the file")
            r = realise(base, lines, files_dir, data_end=data_end)
            if "error" in r or len(r.get("values", [])) != 1 or r["values"][0]["actual"] is None:
                raise ClaimError(f"rests_on {claim['rests_on']} has no single realised value")
            if claim.get("op") not in OPS:
                raise ClaimError(f"op {claim.get('op')!r}")
            return {"id": cid, "came_true": bool(OPS[claim["op"]](r["values"][0]["actual"], float(claim["value"])))}
        raise ClaimError(f"unknown type {t!r}")
    except (ClaimError, KeyError, TypeError, ValueError) as exc:
        return {"id": cid, "error": f"{type(exc).__name__}: {exc}"}


def realise_file(claims: dict, lines: pd.DataFrame, files_dir: Path, data_end: str | None = None) -> list[dict]:
    by_id = {c.get("id"): c for c in claims.get("claims", []) if isinstance(c, dict)}
    return [realise(c, lines, files_dir, by_id, data_end) for c in claims.get("claims", [])]


DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def shift_claim(claim: dict, asof: str, origin: str) -> dict:
    """The claim replayed at a past origin (CLAIMS_SPEC.md, Backtest origins). asof and origin are firsts of a
    month. A date on the first of a month moves by whole months (so [2025-09-01, 2025-12-01) becomes
    [2024-09-01, 2024-12-01)); any other date moves by days. Month keys move by months. A weekly claim (by "week") instead moves every date and key by the whole number of weeks nearest to
    origin - asof, so its weeks stay Monday to Sunday (2025-09-01 at origin 2024-09-01: 52 weeks, 2024-09-02)."""
    a, o = date.fromisoformat(asof), date.fromisoformat(origin)
    if a.day != 1 or o.day != 1:
        raise ClaimError("asof and origin must be firsts of a month")
    months, days = (o.year - a.year) * 12 + o.month - a.month, (o - a).days
    weekly = claim.get("by") == "week"
    if weekly:
        days = int(round(days / 7)) * 7

    def mv_date(x: str) -> str:
        d = date.fromisoformat(x)
        if d.day == 1 and not weekly:
            return (pd.Period(x, freq="M") + months).to_timestamp().strftime("%Y-%m-%d")
        return (pd.Timestamp(d) + pd.Timedelta(days=days)).strftime("%Y-%m-%d")

    def mv(x, field=None):
        if isinstance(x, dict):
            return {k: mv(v, k) for k, v in x.items()}
        if isinstance(x, list):
            return [mv(v, field) for v in x]
        if isinstance(x, str) and DATE.match(x) and (field != "key" or weekly):
            return mv_date(x)
        if isinstance(x, str) and field == "key" and re.match(r"^\d{4}-\d{2}$", x):
            return (pd.Period(x, freq="M") + months).strftime("%Y-%m")
        return x
    return mv(claim)
