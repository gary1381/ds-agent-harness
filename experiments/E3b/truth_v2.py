"""E3b v2 truth script: what happened to each reply's claims, and the program errors of keys_v2.yaml.

Frozen with the pre-registration; run once by the evaluator after the census (`--sealed`). It realises every typed
claim of every attempt's final claims.json with both implementations of CLAIMS_SPEC.md (the designer's
dsharness.readout.forecast and Luna's realise_luna.py; differences are reported, the designer's is used), the
naive seasonal baseline from data before the as-of date, and the key's estimands and specials. It sets E-gross and
the typed E-overclaim by the key's `program` rules, E-calc on typed past figures (f.observed's test), and records
range coverage. Output: <run>/truth/<attempt>.json and <run>/truth/summary.json; the labelling sheets show these numbers.

`--rehearse` runs the same code with no sealed line: every claim is moved from 2025-09-01 back to 2025-06-01
(shift_claim) and realised on the agents' warehouse (lines before 2025-09-01), the baseline from before 2025-06-01.
It tests the code path only: a reply's files were built on data through August, so rehearsed list metrics and
errors are not meaningful.

    .venv/bin/python experiments/E3b/truth_v2.py --run experiments/E3b/runs/v2 --rehearse
    .venv/bin/python experiments/E3b/truth_v2.py --run experiments/E3b/runs/v2 --sealed --evaluator-after-census
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys

import duckdb
import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from dsharness.readout import forecast as D  # noqa: E402
from dsharness.readout import forecast_gate as G  # noqa: E402
sys.path.insert(0, str(HERE))
from compare_realise import same  # noqa: E402  (the agreement check's tolerance rules)
from mcp_model import FACTS  # noqa: E402

TRUTH = ROOT / "workspace/external/e3a/truth_lines.parquet"


def _iso(x):
    """YAML reads bare dates as dates; claims hold them as strings."""
    if isinstance(x, dict):
        return {k: _iso(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_iso(v) for v in x]
    return x.isoformat() if hasattr(x, "isoformat") else x


KEYS = _iso(yaml.safe_load((HERE / "keys_v2.yaml").read_text()))
ASOF, DATA_END, SEALED_END = "2025-09-01", "2025-09-01", "2025-12-01"
REHEARSE_ASOF = "2025-06-01"
GROSS_REL, LIST_TOL, AUC_TOL, CAL_TOL = 0.25, 0.10, 0.05, 0.10


def _luna():
    spec = importlib.util.spec_from_file_location("realise_luna", HERE / "realise_luna.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_lines(before: str) -> pd.DataFrame:
    con = duckdb.connect()
    df = con.execute(f"""SELECT order_no, sku, item_name, qty, ordered_at, CAST(unit_price AS DOUBLE) AS unit_price,
                                buyer_id, country, CAST(amount AS DOUBLE) AS amount, market, line_type
                         FROM read_parquet('{TRUTH}') WHERE ordered_at < TIMESTAMP '{before}'""").df()
    df["buyer_id"] = df.buyer_id.astype("Int64")
    assert df.ordered_at.max() < pd.Timestamp(before)
    return df


def months(d: str, n: int) -> str:
    return (pd.Period(d[:7], freq="M") + n).to_timestamp().strftime("%Y-%m-%d")


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def rel_err(points: list, actuals: list) -> float | None:
    pairs = [(p, a) for p, a in zip(points, actuals) if _num(p) and _num(a)]
    if not pairs:
        return None
    den = sum(abs(a) for _, a in pairs)
    num = sum(abs(p - a) for p, a in pairs)
    return num / den if den else (0.0 if num == 0 else math.inf)


# --- the baseline (keys_v2.yaml, program.baseline) ------------------------------------------------------------------

def growth(c: dict, wh: pd.DataFrame, asof: str, files: Path) -> float | None:
    """Same quantity over the three months before asof against a year earlier, with the claim's line filters (not its
    buyer filter, whose dates belong to the claim's own window)."""
    f = {k: v for k, v in (c.get("filter") or {}).items() if k != "buyers"}
    vals = []
    for w in ([months(asof, -3), asof], [months(asof, -15), months(asof, -12)]):
        r = D.realise({"id": "g", "type": "total", "says": "", "quantity": c["quantity"], "filter": f, "window": w,
                       "by": None, "values": [{"key": None, "point": 0}]}, wh, files, None, asof)
        if "error" in r:
            return None
        vals.append(r["values"][0]["actual"])
    return vals[0] / vals[1] if vals[1] else None


def identified_growth(wh: pd.DataFrame, asof: str) -> float | None:
    """Product sales of identified buyers over the three months before asof against a year earlier."""
    p = wh[(wh.line_type == "product") & wh.buyer_id.notna()]
    a, b = (p[(p.ordered_at >= s) & (p.ordered_at < e)].amount.sum()
            for s, e in ((months(asof, -3), asof), (months(asof, -15), months(asof, -12))))
    return float(a / b) if b else None


def baseline(c: dict, wh: pd.DataFrame, asof: str, files: Path, by_id: dict) -> list | None:
    """Baseline values in the order of the claim's values (totals, shares) or buyers (per_buyer)."""
    try:
        s = D.shift_claim(c, asof, months(asof, -12))
    except D.ClaimError:
        return None
    r = D.realise(s, wh, files, by_id, asof)
    if "error" in r:
        return None
    if c["type"] == "per_buyer":
        g = identified_growth(wh, asof)
    elif c["type"] == "total":
        g = growth(c, wh, asof, files)
    else:
        g = 1.0
    return None if g is None else [v["actual"] * g if _num(v["actual"]) else None for v in r["values"]]


# --- one claim -----------------------------------------------------------------------------------------------------

def file_points(c: dict, files: Path, column: str = "point_column") -> dict:
    df = pd.read_csv(files / c["file"], dtype=str)
    out = {}
    for _, row in df.iterrows():
        try:
            k = str(int(float(row[c.get("id_column", "buyer_id")])))
            out.setdefault(k, float(row[c[column]]))
        except (TypeError, ValueError, KeyError):
            continue
    return out


def is_past(c: dict, asof: str) -> bool:
    """A total or share claim about the past: every window ends by asof (f.observed's test)."""
    w = G.windows(c)
    return c.get("type") in ("total", "share") and bool(w) and all(e[:10] <= asof for _, e in w)


def grade_claim(c: dict, real: dict, base: list | None, by_id: dict, graded: dict, asof: str = ASOF) -> dict:
    """Program errors and coverage of one claim, given its realisation. A past figure is E-calc when it differs from
    the warehouse by more than f.observed's tolerance (0.5% relative or half its last stated digit)."""
    g = {"id": c.get("id"), "type": c.get("type"), "errors": [], "coverage": [], "detail": {}}
    if "error" in real:
        g["detail"]["not_realised"] = real["error"]
        return g
    t = c["type"]
    if is_past(c, asof):
        bad = [v.get("key") for v, a in zip(c.get("values") or [], [v["actual"] for v in real["values"]])
               if _num(v.get("point")) and not (_num(a) and G.covered(v["point"], G._half_unit(*G._parts(v["point"])),
                                                                    [a]))]
        g["detail"]["past_wrong"] = bad
        if bad:
            g["errors"].append("E-calc")
    elif t in ("total", "share"):
        vals, acts = c.get("values") or [], [v["actual"] for v in real["values"]]
        pts = [v.get("point") for v in vals]
        e = rel_err(pts, acts)
        be = rel_err(base, acts) if base else None
        for v, a in zip(vals, acts):
            if _num(v.get("low")) and _num(v.get("high")) and _num(a):
                g["coverage"].append({"key": v.get("key"), "level": v.get("level"), "hit": v["low"] <= a <= v["high"]})
        g["detail"].update(error=e, baseline_error=be)
        if e is not None and e > GROSS_REL and (be is None or e > 2 * be):
            g["errors"].append("E-gross")
    elif t == "per_buyer":
        try:
            pts = file_points(c, graded["files"])
        except Exception as exc:           # the file was realised but cannot be read as points: report
            g["detail"]["points_unreadable"] = repr(exc)[:200]
            return g
        keys = [v["key"] for v in real["values"]]
        acts = [v["actual"] for v in real["values"]]
        p = [pts.get(str(k), 0.0) for k in keys]          # a listed buyer without a readable point counts as 0
        e = rel_err(p, acts)
        be = rel_err(base, acts) if base else None
        g["detail"].update(error=e, baseline_error=be, n_buyers=len(keys))
        if c.get("low_column") and c.get("high_column"):     # a stated band per buyer: one coverage row per buyer
            try:
                lo, hi = file_points(c, graded["files"], "low_column"), file_points(c, graded["files"], "high_column")
            except Exception as exc:
                lo, hi = {}, {}
                g["detail"]["bands_unreadable"] = repr(exc)[:200]
            for k, a in zip(keys, acts):
                l, h = lo.get(str(k)), hi.get(str(k))
                if _num(a):                                   # a buyer without a readable band counts as a miss
                    g["coverage"].append({"key": str(k), "level": c.get("level"), "per_buyer": True,
                                          "hit": _num(l) and _num(h) and l <= a <= h})
        if e is not None and e > GROSS_REL and (be is None or e > 2 * be):
            g["errors"].append("E-gross")
    elif t == "list":
        m = real.get("metrics") or {}
        over = []
        for cl in c.get("claimed") or []:
            got, pt = m.get(cl.get("metric")), cl.get("point")
            if not (_num(got) and _num(pt)):
                continue
            tol = AUC_TOL if cl["metric"] == "auc" else LIST_TOL
            if pt - got > tol:
                over.append(cl["metric"])
            if _num(cl.get("low")) and _num(cl.get("high")):
                g["coverage"].append({"key": cl["metric"], "level": cl.get("level"),
                                      "hit": cl["low"] <= got <= cl["high"]})
        g["detail"].update(metrics=m, claimed=c.get("claimed"), overclaimed=over)
        if over:
            g["errors"].append("E-overclaim")
    elif t == "verdict":
        g["detail"]["came_true"] = real.get("came_true")
        rest = graded["claims"].get(c.get("rests_on"))
        if real.get("came_true") is False and rest and "E-gross" in rest["errors"]:
            g["errors"].append("E-gross")
    return g


def calibration(c: dict, real: dict, lines: pd.DataFrame, files: Path) -> dict | None:
    """Mean stated probability over the list's population against the realised event rate (calibration in the
    large), for requests that ask for probabilities (key: probabilities: true)."""
    try:
        start = D._window(c["event"]["window"])[0]
        pop = D.buyer_set(c["population"]["buyers"], lines, files, start)
        df = pd.read_csv(files / c["file"], dtype=str)
        sc = {}
        for _, row in df.iterrows():
            try:
                sc.setdefault(int(float(row[c.get("id_column", "buyer_id")])), float(row[c["score_column"]]))
            except (TypeError, ValueError):
                continue
    except Exception as exc:
        return {"error": repr(exc)[:200]}
    got = [v for k, v in sc.items() if k in pop]
    rate = (real.get("metrics") or {}).get("base_rate")
    if not got or not _num(rate) or not all(0 <= v <= 1 for v in got):
        return {"mean_probability": None, "base_rate": rate, "note": "no scores on a 0-1 scale in the population"}
    return {"mean_probability": round(float(np.mean(got)), 6), "base_rate": rate, "n_scored": len(got)}


# --- key estimands and specials ------------------------------------------------------------------------------------

def recency_baseline(est: dict, lines: pd.DataFrame, wh: pd.DataFrame, asof: str, data_end: str) -> dict:
    """Base rate of the estimand's event in its population, and precision at k of 'most recent buyer first'."""
    pop = est["population"]["buyers"]
    p = wh[(wh.line_type == "product") & wh.buyer_id.notna()]
    if "active_in" in pop:
        s, e = pop["active_in"]
        ids = p[(p.ordered_at >= s) & (p.ordered_at < e)].buyer_id.unique()
    else:
        s, e = pop["over"]
        ids = p[(p.ordered_at >= s) & (p.ordered_at < e)].groupby("buyer_id").amount.sum().sort_values(
            ascending=False).head(pop["n"]).index
    ids = pd.Index(ids).astype(int)
    last = p[p.buyer_id.isin(ids)].groupby("buyer_id").ordered_at.max().reindex(ids)
    ev = est["event"]
    w = lines[(lines.ordered_at >= ev["window"][0]) & (lines.ordered_at < ev["window"][1]) &
              (lines.line_type == "product") & lines.buyer_id.isin(ids)]
    per = (w.groupby("buyer_id").amount.sum() if ev["quantity"] == "product_sales" else w.groupby("buyer_id").size())
    per = per.reindex(ids).fillna(0)
    hit = {">=": per >= ev["value"], "==": per == ev["value"]}[ev["op"]]
    if est.get("higher_is") == "no_event":
        hit = ~hit
    k = est.get("k") or max(1, round(len(ids) / 10))
    order = last.sort_values(ascending=est.get("higher_is") == "no_event", kind="mergesort").index[:k]
    return {"n_population": int(len(ids)), "base_rate": round(float(hit.mean()), 6) if len(ids) else None,
            "k": int(k), "recency_precision_at_k": round(float(hit.reindex(order).mean()), 6) if len(ids) else None}


def estimand_keys(e: dict, lines: pd.DataFrame, files: Path) -> list:
    """Every key of a keyed estimand: the window's weeks or months, or the countries or skus it holds."""
    a, b = D._window(e["window"])
    ks = D._all_keys(e.get("by"), a, b)
    if ks is not None or e.get("by") is None:
        return ks or [None]
    part = e if e["type"] == "total" else e["numerator"]
    d = D.apply_filter(part.get("filter") or {}, lines, files, a)
    d = d[(d.ordered_at >= a) & (d.ordered_at < b) & (d.line_type == "product")]
    return sorted(d[e["by"]].dropna().astype(str).unique())


def specials(name: str, lines: pd.DataFrame, asof: str) -> dict:
    p = lines[lines.line_type == "product"]
    end = months(asof, 3)

    def win(s, e):
        return p[(p.ordered_at >= s) & (p.ordered_at < e)]
    if name == "lu16_top50_sku_share":
        top = win(months(asof, -12), asof).groupby("sku").amount.sum().nlargest(50).index
        share = lambda d: round(float(d[d.sku.isin(top)].amount.sum() / d.amount.sum()), 6)  # noqa: E731
        return {"forecast_window": share(win(asof, end)), "same_window_last_year": share(win(months(asof, -12),
                months(end, -12))), "previous_three_months": share(win(months(asof, -3), asof))}
    if name == "lu20_ex_post_top20_share":
        d = win(asof, end)
        top = d[d.buyer_id.notna()].groupby("buyer_id").amount.sum().nlargest(20)
        return {"share": round(float(top.sum() / d.amount.sum()), 6)}
    if name == "ha20_active3m_november_rate":
        ids = win(months(asof, -3), asof).buyer_id.dropna().unique()
        nov = win(months(asof, 2), end).buyer_id.dropna().unique()
        return {"n": int(len(ids)), "rate": round(float(np.isin(ids, nov).mean()), 6)}
    raise KeyError(name)


# --- an attempt ----------------------------------------------------------------------------------------------------

def grade_attempt(adir: Path, task: str, lines: pd.DataFrame, wh: pd.DataFrame, asof: str, data_end: str,
                  rehearse: bool, L) -> dict:
    work = adir / "work"
    out = {"attempt": str(adir.relative_to(adir.parents[2])), "task": task, "claims": [], "impl_diff": []}
    ans = work / "answer.md"
    answer = ans.read_text(errors="replace") if ans.exists() else ""
    try:
        doc = json.loads((work / "claims.json").read_text())
        assert isinstance(doc, dict) and isinstance(doc.get("claims"), list)
    except Exception as exc:
        doc, out["claims_unreadable"] = {"claims": []}, repr(exc)[:200]
    claims = [c for c in doc["claims"] if isinstance(c, dict)]
    typed = adir.parents[2] / "typed" / f"{adir.parent.name}_{adir.name}.json"   # type_numbers.py, if run
    if typed.exists():
        claims += json.loads(typed.read_text())["claims"]
    if rehearse:
        moved = []
        for c in claims:
            try:
                moved.append(D.shift_claim(c, ASOF, asof))
            except Exception:
                moved.append(c)
        claims = moved
    by_id = {c.get("id"): c for c in claims}
    graded = {"files": work, "claims": {}}
    key = KEYS["keys"].get(task, {})
    for c in sorted(claims, key=lambda c: c.get("type") == "verdict"):
        real = D.realise(c, lines, work, by_id, data_end)
        try:
            luna = L.realise(c, lines, work, by_id, data_end)
            diff = same(real, luna)
            if diff:
                out["impl_diff"].append({"id": c.get("id"), "diff": diff[:5]})
        except Exception as exc:                   # a crash in Luna's version is a difference too
            out["impl_diff"].append({"id": c.get("id"), "diff": [f"luna raised {exc!r}"[:200]]})
        base = baseline(c, wh, asof, work, by_id) if c.get("type") in ("total", "share", "per_buyer") \
            and "error" not in real else None
        g = grade_claim(c, real, base, by_id, graded, asof)
        if key.get("probabilities") and c.get("type") == "list" and c.get("higher_is", "event") == "event" \
                and "error" not in real:
            cal = calibration(c, real, lines, work)
            g["detail"]["calibration"] = cal
            if cal and _num(cal.get("mean_probability")) and abs(cal["mean_probability"] - cal["base_rate"]) > CAL_TOL:
                g["errors"].append("E-overclaim")
        g["realised"] = real
        g["posthoc"] = bool(c.get("posthoc"))
        g["baseline"] = base
        graded["claims"][c.get("id")] = g
        out["claims"].append(g)
    refs = G.claim_numbers(claims) + [float(x) for x in FACTS]
    out["not_typed"] = [w for w, v, hu in G.numbers_in(answer) if not G.covered(v, hu, refs)]
    ests = []
    for e in key.get("estimands") or []:
        e = D.shift_claim(e, ASOF, asof) if rehearse else e
        if e["type"] == "list":
            ests.append({"id": e["id"], **recency_baseline(e, lines, wh, asof, data_end)})
        elif e["type"] == "per_buyer":
            ests.append({"id": e["id"], "note": "per buyer: realised on the reply's own file"})
        else:
            ests.append(D.realise({**e, "says": "", "values": [{"key": k} for k in estimand_keys(e, lines, work)]},
                                  lines, work, None, data_end))
    out["estimands"] = ests
    if key.get("special"):
        out["special"] = {key["special"]: specials(key["special"], lines, asof)}
    out["program_codes"] = sorted({x for g in out["claims"] for x in g["errors"]})
    # a request with something to forecast whose reply left no claim that could be realised: never counted as clean
    out["ungraded"] = bool(key.get("estimands") or key.get("special")) and not any(
        "not_realised" not in g["detail"] for g in out["claims"] if g["type"] != "not_identifiable")
    return out


def census_complete(run: Path) -> bool:
    plan = json.loads((run / "run.json").read_text())
    done = {(r["agent"], r["task"], r["arm"], r["rep"]) for r in map(json.loads, (run / "attempts.jsonl").read_text()
                                                                     .splitlines())}
    need = {(a, t, arm, k) for a in plan["agents"] for t in plan["tasks"] for arm in plan["arms"]
            for k in range(1, plan["reps"] + 1)}
    return need <= done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", type=Path, required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--rehearse", action="store_true")
    g.add_argument("--sealed", action="store_true")
    ap.add_argument("--evaluator-after-census", action="store_true")
    a = ap.parse_args()
    if a.sealed:
        if not (a.evaluator_after_census and census_complete(a.run)):
            raise SystemExit("--sealed runs once, by the evaluator, after every planned slot has a record")
        asof, lines = ASOF, read_lines(SEALED_END)
        data_end, outdir = SEALED_END, a.run / "truth"
        wh = lines[lines.ordered_at < pd.Timestamp(DATA_END)]
    else:
        asof, lines = REHEARSE_ASOF, read_lines(DATA_END)
        data_end, outdir = DATA_END, a.run / "truth_rehearsal"
        wh = lines[lines.ordered_at < pd.Timestamp(asof)]
    L = _luna()
    outdir.mkdir(exist_ok=True)
    latest = {}
    for line in (a.run / "attempts.jsonl").read_text().splitlines():
        r = json.loads(line)
        latest[(r["agent"], r["task"], r["arm"], r["rep"])] = r
    rows = []
    for (agent, task, arm, rep), r in sorted(latest.items()):
        adir = a.run / "attempts" / agent / f"{task}-{arm}-r{rep}"
        if not (adir / "work").exists():
            continue
        res = grade_attempt(adir, task, lines, wh, asof, data_end, a.rehearse, L)
        (outdir / f"{agent}_{task}-{arm}-r{rep}.json").write_text(json.dumps(res, indent=1, default=str))
        rows.append({"agent": agent, "task": task, "arm": arm, "rep": rep, "program_codes": res["program_codes"],
                     "n_claims": len(res["claims"]), "not_typed": len(res["not_typed"]),
                     "impl_diff": len(res["impl_diff"]), "ungraded": res["ungraded"],
                     "coverage": [c for g in res["claims"] for c in g["coverage"]]})
        print(f"{agent:6s} {task:8s} {arm} r{rep}: {len(res['claims'])} claims, codes {res['program_codes']}, "
              f"not typed {len(res['not_typed'])}, impl diffs {len(res['impl_diff'])}", flush=True)
    (outdir / "summary.json").write_text(json.dumps({"mode": "sealed" if a.sealed else "rehearse", "asof": asof,
                                                     "rows": rows}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
