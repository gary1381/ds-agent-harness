"""Realise E3b v2 typed forecast claims against order-line data."""
from __future__ import annotations

import math
import copy
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


_QUANTITIES = {
    "product_sales", "product_qty", "product_lines", "orders", "buyers",
    "new_buyers", "repeat_buyers", "repeat_sales", "guest_sales",
    "cancellation_value", "cancellation_lines",
}
_OPS = {
    "==": lambda a, b: a == b,
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
}


def _dates(window):
    if not isinstance(window, (list, tuple)) or len(window) != 2:
        raise ValueError("window must be [start, end]")
    start, end = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    if start >= end:
        raise ValueError("window start must precede end")
    return start, end


def _buyer_key(v):
    if pd.isna(v):
        return None
    # Keep IDs canonical when pandas inferred numeric columns from a CSV.
    try:
        f = float(v)
        if f.is_integer():
            return str(int(f))
    except (TypeError, ValueError, OverflowError):
        pass
    return str(v)


def _id_sort(v):
    s = _buyer_key(v)
    try:
        return (0, int(s))
    except (TypeError, ValueError):
        return (1, s or "")


def _read_file(files_dir: Path, name: str) -> pd.DataFrame:
    path = Path(files_dir) / name
    if not path.is_file():
        raise ValueError(f"missing file: {name}")
    try:
        return pd.read_csv(path)
    except Exception as exc:
        raise ValueError(f"cannot read file {name}: {exc}") from exc


def _base_lines(lines: pd.DataFrame) -> pd.DataFrame:
    required = {"order_no", "sku", "qty", "ordered_at", "unit_price", "buyer_id",
                "country", "amount", "market", "line_type"}
    absent = required - set(lines.columns)
    if absent:
        raise ValueError("missing line columns: " + ", ".join(sorted(absent)))
    d = lines.copy()
    d["ordered_at"] = pd.to_datetime(d["ordered_at"])
    d["_buyer"] = d["buyer_id"].map(_buyer_key)
    return d


def _buyer_filter(d, spec, start, files_dir):
    if not isinstance(spec, dict) or not spec:
        raise ValueError("buyer filter must be a nonempty object")
    if "file" in spec:
        form, arg = "file", spec
    elif "top_by" in spec:
        form, arg = "top_by", spec
    elif len(spec) == 1:
        form, arg = next(iter(spec.items()))
    else:
        raise ValueError("buyer filter must specify one form")
    product = d[d.line_type == "product"]
    if form == "ids":
        return {_buyer_key(x) for x in arg if _buyer_key(x) is not None}
    if form == "file":
        f = _read_file(files_dir, arg["file"])
        col = arg["id_column"]
        if col not in f:
            raise ValueError(f"missing column {col} in {arg['file']}")
        return {_buyer_key(x) for x in f[col] if _buyer_key(x) is not None}
    if form in ("active_in", "first_in", "top_by"):
        win = arg["over"] if form == "top_by" else arg
        lo, hi = _dates(win)
        if hi > start:
            raise ValueError(f"{form} must end on or before claim window start")
        hist = product[(product.ordered_at >= lo) & (product.ordered_at < hi) & product._buyer.notna()]
        if form == "active_in":
            return set(hist._buyer)
        if form == "first_in":
            first = product[product._buyer.notna()].groupby("_buyer").ordered_at.min()
            return {b for b, dt in first.items() if lo <= dt < hi}
        q = arg["top_by"]
        # Aggregate a top_by quantity by buyer, using the window's product lines.
        scores = _per_buyer(hist, q)
        ordered = sorted(scores.items(), key=lambda p: (-p[1], _id_sort(p[0])))
        n = int(arg["n"])
        if n < 0:
            raise ValueError("top_by n must be nonnegative")
        return {b for b, _ in ordered[:n]}
    raise ValueError(f"unknown buyer filter: {form}")


def _filter_lines(d, filt, start, files_dir, all_data=None):
    if filt is None:
        filt = {}
    if not isinstance(filt, dict):
        raise ValueError("filter must be an object")
    out = d
    for key, val in filt.items():
        if key == "market":
            if val == "non-UK":
                out = out[out.market != "UK"]
            elif val == "UK":
                out = out[out.market == "UK"]
            else:
                raise ValueError(f"unknown market filter: {val}")
        elif key == "country":
            out = out[out.country.isin(val)]
        elif key == "sku":
            out = out[out.sku.isin(val)]
        elif key == "catalogue_only":
            if val is not True:
                raise ValueError("catalogue_only only supports true")
            out = out[out.sku.astype(str).str.match(r"^\d{5}", na=False)]
        elif key == "buyers":
            ids = _buyer_filter(d if all_data is None else all_data, val, start, files_dir)
            out = out[out._buyer.isin(ids)]
        else:
            raise ValueError(f"unknown filter: {key}")
    return out


def _window_lines(d, window, filt, files_dir):
    start, end = _dates(window)
    current = d[(d.ordered_at >= start) & (d.ordered_at < end)]
    return start, end, _filter_lines(current, filt, start, files_dir)


def _per_buyer(d, quantity):
    p = d[(d.line_type == "product") & d._buyer.notna()]
    if quantity == "product_sales":
        return p.groupby("_buyer").amount.sum().to_dict()
    if quantity == "product_qty":
        return p.groupby("_buyer").qty.sum().to_dict()
    if quantity == "product_lines":
        return p.groupby("_buyer").size().to_dict()
    if quantity == "orders":
        return p.groupby("_buyer").order_no.nunique().to_dict()
    if quantity == "buyers":
        return {b: 1 for b in p._buyer.unique()}
    if quantity == "cancellation_value":
        cancels = d[(d.line_type == "cancellation") & d._buyer.notna()]
        return (-cancels.groupby("_buyer").amount.sum()).to_dict()
    if quantity == "cancellation_lines":
        cancels = d[(d.line_type == "cancellation") & d._buyer.notna()]
        return cancels.groupby("_buyer").size().to_dict()
    if quantity == "guest_sales":
        return {}
    if quantity in ("new_buyers", "repeat_buyers", "repeat_sales"):
        raise ValueError(f"{quantity} cannot be used as top_by quantity")
    raise ValueError(f"unknown quantity: {quantity}")


def _aggregate(d, quantity, filt, start, end, all_data, files_dir, by=None, requested_key=None):
    if quantity not in _QUANTITIES:
        raise ValueError(f"unknown quantity: {quantity}")
    if start is not None:
        d = _filter_lines(d, filt, start, files_dir, all_data)
    p = d[d.line_type == "product"]
    if quantity == "product_sales": return float(p.amount.sum())
    if quantity == "product_qty": return float(p.qty.sum())
    if quantity == "product_lines": return int(len(p))
    if quantity == "orders": return int(p.order_no.nunique())
    if quantity == "buyers": return int(p._buyer.dropna().nunique())
    if quantity == "guest_sales": return float(p[p._buyer.isna()].amount.sum())
    if quantity == "cancellation_value": return float(-d[d.line_type == "cancellation"].amount.sum())
    if quantity == "cancellation_lines": return int((d.line_type == "cancellation").sum())
    # History quantities define first/repeat from product lines across all passed data.
    if start is None or end is None:
        raise ValueError(f"{quantity} requires a window")
    all_product = all_data[(all_data.line_type == "product") & all_data._buyer.notna()]
    prior_ids = set(all_product.loc[all_product.ordered_at < start, "_buyer"])
    first_rows = all_product.sort_values(["ordered_at", "order_no", "sku"], kind="stable").drop_duplicates("_buyer")
    first = first_rows.set_index("_buyer").ordered_at
    in_window = p[p._buyer.notna()].copy()
    in_window_ids = set(in_window._buyer)
    if quantity == "new_buyers":
        new_ids = {b for b in in_window_ids if b in first and start <= first[b] < end}
        if by is None: return int(len(new_ids))
        # Count each new buyer in the key of their first filtered line in-window.
        first_filtered = in_window.sort_values(["ordered_at", "order_no", "sku"], kind="stable").drop_duplicates("_buyer")
        first_filtered = first_filtered[first_filtered._buyer.isin(new_ids)]
        return int(((_line_key_series(first_filtered, by) == requested_key)).sum())
    repeat_ids = in_window_ids & prior_ids
    if quantity == "repeat_buyers":
        if by is None: return int(len(repeat_ids))
        repeat_lines = in_window[in_window._buyer.isin(repeat_ids)]
        return int(repeat_lines.loc[_line_key_series(repeat_lines, by) == requested_key, "_buyer"].nunique())
    if quantity == "repeat_sales": return float(p[p._buyer.isin(repeat_ids)].amount.sum())
    raise ValueError(f"unknown quantity: {quantity}")


def _keys(claim):
    return [v.get("key") for v in claim.get("values", [])]


def _line_key(row, by):
    if by == "week": return (row.ordered_at - pd.Timedelta(days=row.ordered_at.weekday())).strftime("%Y-%m-%d")
    if by == "month": return row.ordered_at.strftime("%Y-%m")
    if by == "buyer_id": return _buyer_key(row._buyer)
    if by in ("country", "sku"): return row[by]
    return None


def _line_key_series(rows, by):
    if by == "week":
        return (rows.ordered_at - pd.to_timedelta(rows.ordered_at.dt.day_of_week, unit="D")).dt.strftime("%Y-%m-%d")
    if by == "month": return rows.ordered_at.dt.strftime("%Y-%m")
    if by == "buyer_id": return rows._buyer
    if by in ("country", "sku"): return rows[by]
    return pd.Series([None] * len(rows), index=rows.index)


def _realise_total(claim, d, files_dir, data_end):
    start, end = _dates(claim["window"])
    if start < d.ordered_at.min(): raise ValueError("window starts before data")
    if end > data_end: raise ValueError("window ends after data_end")
    by = claim.get("by")
    valid = (None, "week", "month", "country", "sku", "buyer_id")
    if by not in valid: raise ValueError(f"unknown breakdown: {by}")
    vals = []
    selected = d[(d.ordered_at >= start) & (d.ordered_at < end)]
    for key in _keys(claim):
        keyed = selected
        history_by_key = by is not None and claim["quantity"] in ("new_buyers", "repeat_buyers")
        if by is not None and not history_by_key:
            keyed = selected.loc[_line_key_series(selected, by) == key]
        actual = _aggregate(keyed, claim["quantity"], claim.get("filter", {}), start, end, d, files_dir, by, key)
        if claim["quantity"] in ("product_sales", "repeat_sales", "guest_sales", "cancellation_value"):
            actual = round(actual, 2)
        vals.append({"key": key, "actual": actual})
    return {"id": claim["id"], "values": vals, "notes": []}


def _realise_share(claim, d, files_dir, data_end):
    start, end = _dates(claim["window"])
    by = claim.get("by")
    if by not in (None, "week", "month", "country", "sku", "buyer_id"):
        raise ValueError(f"unknown breakdown: {by}")
    out = []
    for key in _keys(claim):
        results = []
        for side in (claim["numerator"], claim["denominator"]):
            sub = dict(side, window=claim["window"], by=by)
            result = _realise_total({**sub, "id": claim["id"], "values": [{"key": key}]}, d, files_dir, data_end)
            results.append(result["values"][0]["actual"])
        num, den = results
        if den == 0:
            actual = None
        else:
            actual = round(num / den, 6)
        out.append({"key": key, "actual": actual})
    notes = ["share denominator is zero for one or more keys"] if any(v["actual"] is None for v in out) else []
    return {"id": claim["id"], "values": out, "notes": notes}


def _event_values(d, event, start, end, files_dir, buyer_skus=None):
    lo, hi = _dates(event["window"])
    if lo < start or hi > end:
        # Event's own window is independent, but must be within passed data extent.
        pass
    q = event["quantity"]
    if q not in _QUANTITIES: raise ValueError(f"unknown quantity: {q}")
    x = d[(d.ordered_at >= lo) & (d.ordered_at < hi)]
    if buyer_skus is not None:
        x = x[x.sku.isin(buyer_skus)]
    x = _filter_lines(x, event.get("filter", {}), lo, files_dir, d)
    p = x[x.line_type == "product"]
    if q == "product_sales": m = p.groupby("_buyer").amount.sum()
    elif q == "product_qty": m = p.groupby("_buyer").qty.sum()
    elif q == "product_lines": m = p.groupby("_buyer").size()
    elif q == "orders": m = p.groupby("_buyer").order_no.nunique()
    else: raise ValueError(f"event quantity {q} is not a per-buyer quantity")
    universe = set(d._buyer.dropna())
    event_flag = {b: bool(_OPS[event["op"]](m.get(b, 0), event["value"])) for b in universe}
    return event_flag


def _auc(scores, labels):
    pos = sum(bool(x) for x in labels)
    neg = len(labels) - pos
    if not pos or not neg: return None
    ranks = pd.Series(scores).rank(method="average").to_numpy()
    rank_sum = float(ranks[np.asarray(labels, dtype=bool)].sum())
    return round((rank_sum - pos * (pos + 1) / 2) / (pos * neg), 6)


def _realise_list(claim, d, files_dir, data_end):
    f = _read_file(files_dir, claim["file"])
    idc, scorec = claim["id_column"], claim.get("score_column")
    if idc not in f: raise ValueError(f"missing column {idc} in {claim['file']}")
    start, end = _dates(claim["event"]["window"])
    _check_coverage(d, start, end, data_end)
    popspec = claim.get("population", {}).get("buyers")
    if popspec is None: raise ValueError("list population must specify buyers")
    pop = _buyer_filter(d, popspec, start, files_dir)
    file_ids = [_buyer_key(x) for x in f[idc] if _buyer_key(x) is not None]
    ranked = f.copy()
    ranked["_id"] = ranked[idc].map(_buyer_key)
    item_list = any(m.get("metric") == "hit_at_k" for m in claim.get("claimed", []))
    # Score lists contain one row per buyer; keep the first submitted row.
    # Item lists intentionally allow multiple (buyer, sku, rank) rows.
    if not item_list:
        duplicate_ids = ranked[ranked._id.notna() & ranked._id.duplicated(keep="first")]._id
        ranked = ranked.drop_duplicates("_id", keep="first")
    else:
        duplicate_ids = pd.Series([], dtype=object)
    dropped = len({x for x in ranked._id if x is not None and x not in pop})
    ranked = ranked[ranked._id.isin(pop)].copy()
    if scorec is None and not item_list:
        raise ValueError("list needs score_column")
    higher_event = claim.get("higher_is") == "event"
    if not item_list and claim.get("higher_is") not in ("event", "no_event"):
        raise ValueError("higher_is must be event or no_event")
    k = int(claim["k"])
    if k < 0: raise ValueError("k must be nonnegative")
    event = claim["event"]
    if any(m.get("metric") == "hit_at_k" for m in claim.get("claimed", [])):
        elo, ehi = _dates(event["window"])
        event_lines = d[(d.ordered_at >= elo) & (d.ordered_at < ehi) & (d.line_type == "product")]
        event_lines = _filter_lines(event_lines, event.get("filter", {}), elo, files_dir, d)
        if "rank" not in ranked or "sku" not in ranked:
            raise ValueError("hit_at_k list needs sku and rank columns")
        rank_values = pd.to_numeric(ranked["rank"], errors="coerce")
        top_rows = ranked.loc[(rank_values <= k) & ranked.sku.notna(), ["_id", "sku"]].drop_duplicates()
        listed_buyers = ranked._id.dropna().drop_duplicates()
        event_pairs = event_lines.loc[event_lines._buyer.notna(), ["_buyer", "sku"]].drop_duplicates()
        hit_buyers = set(top_rows.merge(event_pairs, left_on=["_id", "sku"],
                                        right_on=["_buyer", "sku"], how="inner")._id)
        hits = listed_buyers.isin(hit_buyers)
        metrics = {"hit_at_k": round(float(np.mean(hits)), 6) if len(hits) else None}
    else:
        if scorec not in ranked: raise ValueError(f"missing column {scorec} in {claim['file']}")
        if scorec not in ranked: raise ValueError(f"missing column {scorec} in {claim['file']}")
        scores_by_id = dict(zip(ranked._id, pd.to_numeric(ranked[scorec], errors="coerce")))
        events = _event_values(d, event, start, end, files_dir)
        # Population includes all buyers named by its filter, even if no line in event window.
        ordering = sorted(pop, key=lambda b: (
            0 if b in scores_by_id and not pd.isna(scores_by_id[b]) else 1,
            -(scores_by_id[b] if higher_event else -scores_by_id[b]) if b in scores_by_id and not pd.isna(scores_by_id[b]) else 0,
            _id_sort(b)))
        # Missing scores rank below every supplied score; tie ordering is by buyer ID.
        top = ordering[:k]
        labels = [events.get(b, False) for b in ordering]
        top_labels = [events.get(b, False) for b in top]
        positives = sum(labels)
        n_top = len(top)
        score_array = [
            (scores_by_id[b] if higher_event else -scores_by_id[b])
            if b in scores_by_id and not pd.isna(scores_by_id[b]) else -math.inf
            for b in ordering
        ]
        metric_values = {
            "precision_at_k": sum(top_labels) / n_top if n_top else None,
            "recall_at_k": sum(top_labels) / positives if positives else None,
            "auc": _auc(score_array, labels),
            "base_rate": positives / len(pop) if pop else None,
        }
        metrics = {name: (round(float(v), 6) if v is not None else None)
                   for name, v in metric_values.items()}
    notes = [f"duplicate buyer id {b}; kept first row" for b in duplicate_ids]
    return {"id": claim["id"], "metrics": metrics, "n_population": len(pop),
            "n_file": len(set(file_ids)), "n_dropped": int(dropped), "notes": notes}


def _realise_per_buyer(claim, d, files_dir, data_end):
    f = _read_file(files_dir, claim["file"])
    columns = [claim["id_column"], claim["point_column"]]
    columns.extend(claim.get(k) for k in ("low_column", "high_column") if claim.get(k) is not None)
    for col in columns:
        if col not in f: raise ValueError(f"missing column {col} in {claim['file']}")
    start, end = _dates(claim["window"])
    _check_coverage(d, start, end, data_end)
    selected = d[(d.ordered_at >= start) & (d.ordered_at < end)]
    selected = _filter_lines(selected, claim.get("filter", {}), start, files_dir, d)
    q = claim["quantity"]
    if q in ("new_buyers", "repeat_buyers", "repeat_sales"):
        all_product = d[d.line_type == "product"]
        first = all_product[all_product._buyer.notna()].groupby("_buyer").ordered_at.min()
        prior = set(all_product[(all_product.ordered_at < start) & all_product._buyer.notna()]._buyer)
        in_window = selected[selected.line_type == "product"]
        actuals = {}
        for b in set(in_window._buyer.dropna()):
            if q == "new_buyers": actuals[b] = int(b in first and start <= first[b] < end)
            elif q == "repeat_buyers": actuals[b] = int(b in prior)
            elif b in prior:
                actuals[b] = float(in_window[in_window._buyer == b].amount.sum())
            else: actuals[b] = 0.0
    else:
        actuals = _per_buyer(selected, q)
    values = []
    seen = set()
    notes = []
    for buyer in f[claim["id_column"]]:
        b = _buyer_key(buyer)
        if b in seen:
            notes.append(f"duplicate buyer id {b}; kept first row")
            continue
        seen.add(b)
        actual = actuals.get(b, 0)
        if claim["quantity"] in ("product_sales", "repeat_sales", "guest_sales", "cancellation_value"):
            actual = round(float(actual), 2)
        values.append({"key": b, "actual": actual})
    return {"id": claim["id"], "values": values, "notes": notes}


def _check_coverage(d, start, end, data_end):
    if len(d) and start < d.ordered_at.min(): raise ValueError("window starts before data")
    if end > data_end: raise ValueError("window ends after data_end")


def realise(claim: dict, lines: pd.DataFrame, files_dir: Path, claims_by_id=None, data_end=None) -> dict:
    """Return the realised output for one claim; errors are represented in-band."""
    claim_id = claim.get("id")
    try:
        if claim.get("type") == "not_identifiable": return {"id": claim_id, "values": []}
        d = _base_lines(lines)
        if data_end is None:
            if d.empty: raise ValueError("data_end is required when no lines are passed")
            data_end = d.ordered_at.max().normalize() + pd.Timedelta(days=1)
        else:
            data_end = pd.Timestamp(data_end)
        typ = claim["type"]
        if typ == "total": return _realise_total(claim, d, Path(files_dir), data_end)
        if typ == "share": return _realise_share(claim, d, Path(files_dir), data_end)
        if typ == "list": return _realise_list(claim, d, Path(files_dir), data_end)
        if typ == "per_buyer": return _realise_per_buyer(claim, d, Path(files_dir), data_end)
        if typ == "verdict":
            if claims_by_id is None or claim.get("rests_on") not in claims_by_id:
                raise ValueError("verdict rests_on claim is unavailable")
            dependency = claims_by_id[claim["rests_on"]]
            realised = realise(dependency, d, files_dir, claims_by_id, data_end)
            if "error" in realised: raise ValueError(f"rests_on could not be realised: {realised['error']}")
            if "values" not in realised or len(realised["values"]) != 1:
                raise ValueError("verdict rests_on must have one realised value")
            rests = realised["values"][0].get("actual")
            if rests is None: raise ValueError("verdict rests_on value is undefined")
            op = claim.get("op")
            if op not in _OPS: raise ValueError(f"unknown comparison operator: {op}")
            return {"id": claim_id, "came_true": bool(_OPS[op](rests, claim["value"]))}
        raise ValueError(f"unknown claim type: {typ}")
    except Exception as exc:
        return {"id": claim_id, "error": str(exc)}


def shift_claim(claim: dict, asof: str, origin: str) -> dict:
    """Shift dated fields using the calendar-month or weekly replay rule."""
    a, o = pd.Timestamp(asof), pd.Timestamp(origin)
    days = int((o - a).days)
    months = (o.year - a.year) * 12 + o.month - a.month
    weekly = claim.get("by") == "week"
    nearest_weeks = math.floor(days / 7 + 0.5) if days >= 0 else math.ceil(days / 7 - 0.5)
    week_days = int(nearest_weeks) * 7

    def moved_date(value):
        dt = pd.Timestamp(value)
        if weekly: dt += pd.Timedelta(days=week_days)
        elif dt.day == 1: dt += pd.DateOffset(months=months)
        else: dt += pd.Timedelta(days=days)
        return dt.strftime("%Y-%m-%d")

    def moved_window(value): return [moved_date(x) for x in value]
    out = copy.deepcopy(claim)
    if "window" in out: out["window"] = moved_window(out["window"])
    typ = out.get("type")
    def move_filter(f):
        f = dict(f or {})
        if "buyers" in f: f["buyers"] = move_buyer(f["buyers"])
        return f
    def move_buyer(b):
        b = dict(b)
        for key in ("active_in", "first_in", "over"):
            if key in b: b[key] = moved_window(b[key])
        return b
    if typ in ("total", "per_buyer"): out["filter"] = move_filter(out.get("filter", {}))
    elif typ == "share":
        for side in ("numerator", "denominator"):
            if side in out:
                z = dict(out[side]); z["filter"] = move_filter(z.get("filter", {})); out[side] = z
    elif typ == "list":
        if "event" in out:
            z = dict(out["event"]); z["window"] = moved_window(z["window"]); z["filter"] = move_filter(z.get("filter", {})); out["event"] = z
        if "population" in out and "buyers" in out["population"]:
            out["population"] = dict(out["population"], buyers=move_buyer(out["population"]["buyers"]))
    by = out.get("by")
    for value in out.get("values", []):
        key = value.get("key")
        if by == "week" and key is not None: value["key"] = moved_date(key)
        elif by == "month" and key is not None:
            dt = pd.Timestamp(f"{key}-01") + pd.DateOffset(months=months)
            value["key"] = dt.strftime("%Y-%m")
    return out
