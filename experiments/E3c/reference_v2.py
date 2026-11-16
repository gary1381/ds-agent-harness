"""E3c v2: every reference number the owner's keys for the blind bank refer to (bank_v2.yaml).

RESEARCH USE ONLY (no licence from the file's author): the output goes to experiments/E3c/runs/ (gitignored);
reports carry aggregates only.

Effects are intent-to-treat: the difference in means between two assigned groups, per customer assigned, with a
normal 95% interval and unpooled variances (tasks.diff). Subgroups are defined on pre-campaign columns only. A
heterogeneity test per column is the joint Wald test of the group × subgroup interactions in an OLS with HC1 errors.

    ../agent_env_dt/bin/python experiments/E3c/reference_v2.py > experiments/E3c/runs/reference_v2.json  # statsmodels
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys

import numpy as np
import statsmodels.formula.api as smf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tasks import GROUPS, OUTCOMES, connect, diff  # noqa: E402

CONTRASTS = (("mens", "none"), ("womens", "none"), ("mens", "womens"))
SUBGROUPS = {   # pre-campaign columns: label -> SQL condition
    "zip_code": {z: f"zip_code = '{z}'" for z in ("Urban", "Surburban", "Rural")},
    "channel": {c: f"channel = '{c}'" for c in ("Phone", "Web", "Multichannel")},
    "newbie": {"new": "newbie = 1", "established": "newbie = 0"},
    "bought": {"mens_only": "mens = 1 AND womens = 0", "womens_only": "mens = 0 AND womens = 1",
               "both": "mens = 1 AND womens = 1"},
    "recency": {"1-3": "recency <= 3", "4-6": "recency BETWEEN 4 AND 6", "7-9": "recency BETWEEN 7 AND 9",
                "10-12": "recency >= 10"},
}


def r4(d: dict) -> dict:
    return {k: (round(v, 5) if isinstance(v, float) else v) for k, v in d.items()}


def group_means(con) -> dict:
    rows = con.execute("SELECT segment, count(*), avg(visit), avg(conversion), avg(spend), median(spend), "
                       "sum(conversion), sum(spend) FROM email_test GROUP BY 1").fetchall()
    inv = {v: k for k, v in GROUPS.items()}
    return {inv[s]: {"n": n, "visit": v, "conversion": c, "spend": sp, "median_spend": md, "buyers": b,
                     "total_spend": ts} for s, n, v, c, sp, md, b, ts in rows}


def pooled(con, outcome: str) -> dict:
    """Either e-mail (both e-mail groups together) minus no e-mail."""
    (na, ma, va), = con.execute(f"SELECT count(*), avg({outcome}), var_samp({outcome}) FROM email_test "
                                "WHERE segment <> 'No E-Mail'").fetchall()
    (nb, mb, vb), = con.execute(f"SELECT count(*), avg({outcome}), var_samp({outcome}) FROM email_test "
                                "WHERE segment = 'No E-Mail'").fetchall()
    est, se = ma - mb, math.sqrt(va / na + vb / nb)
    return {"estimate": est, "low": est - 1.96 * se, "high": est + 1.96 * se, "n": [na, nb], "means": [ma, mb]}


def heterogeneity(df, outcome: str, column: str) -> dict:
    """Joint test that the e-mail effects differ across a pre-campaign column (OLS, HC1)."""
    m = smf.ols(f"{outcome} ~ C(segment, Treatment('No E-Mail')) * C({column})", data=df).fit(cov_type="HC1")
    names = [n for n in m.params.index if ":" in n]
    if not names:
        return {}
    w = m.wald_test(" , ".join(f"{n} = 0" for n in names), scalar=True)
    return {"df": len(names), "chi2": float(w.statistic), "p": float(w.pvalue)}


def gap(con, outcome: str, e: str, w1: str, w2: str) -> dict:
    """Effect of e-mail e (vs none) in subgroup w1 minus its effect in subgroup w2 (independent subgroups)."""
    a, b = diff(con, outcome, e, "none", w1), diff(con, outcome, e, "none", w2)
    sa, sb = (a["high"] - a["low"]) / 3.92, (b["high"] - b["low"]) / 3.92
    est, se = a["estimate"] - b["estimate"], math.sqrt(sa ** 2 + sb ** 2)
    return {"estimate": est, "low": est - 1.96 * se, "high": est + 1.96 * se}


def balance(df) -> dict:
    """Standardised mean differences of pre-campaign columns, each e-mail group vs no e-mail, and the sample-ratio
    chi-square against equal thirds."""
    out = {}
    cols = {"recency": df.recency, "history": df.history, "newbie": df.newbie, "mens": df.mens, "womens": df.womens,
            **{f"channel_{c}": (df.channel == c).astype(float) for c in ("Phone", "Web", "Multichannel")},
            **{f"zip_{z}": (df.zip_code == z).astype(float) for z in ("Urban", "Surburban", "Rural")}}
    for name, x in cols.items():
        ref = x[df.segment == GROUPS["none"]]
        for g in ("mens", "womens"):
            y = x[df.segment == GROUPS[g]]
            out[f"{name}:{g}-none"] = float((y.mean() - ref.mean()) / math.sqrt((y.var() + ref.var()) / 2))
    n = df.segment.value_counts()
    exp = len(df) / 3
    chi2 = float(sum((n[g] - exp) ** 2 / exp for g in GROUPS.values()))
    return {"smd": out, "max_abs_smd": max(abs(v) for v in out.values()), "srm_chi2": chi2,
            "srm_p": float(math.exp(-chi2 / 2)), "counts": {k: int(n[v]) for k, v in GROUPS.items()}}


def reference_v2() -> dict:
    con = connect()
    df = con.execute("SELECT * FROM email_test").df()
    ref = {"groups": group_means(con)}
    ref["itt"] = {f"{o}:{a}-{b}": r4(diff(con, o, a, b)) for o in OUTCOMES for a, b in CONTRASTS}
    ref["pooled"] = {f"{o}:any-none": r4(pooled(con, o)) for o in OUTCOMES}
    g = ref["groups"]
    ref["relative_lift"] = {f"{o}:{e}-none": g[e][o] / g["none"][o] - 1 for o in OUTCOMES for e in ("mens", "womens")}
    ref["per_buyer"] = {f"spend:{a}-{b}|buyers": r4(diff(con, "spend", a, b, "conversion = 1"))
                        for a, b in CONTRASTS}
    ref["per_visitor"] = {f"conversion:{a}-none|visitors": r4(diff(con, "conversion", a, "none", "visit = 1"))
                          for a in ("mens", "womens")}
    ref["subgroups"] = {}
    for col, cuts in SUBGROUPS.items():
        for lab, w in cuts.items():
            n = con.execute(f"SELECT count(*) FROM email_test WHERE {w}").fetchone()[0]
            ref["subgroups"][f"{col}={lab}"] = {"n": n, **{f"{o}:{a}-{b}": r4(diff(con, o, a, b, w))
                                                            for o in OUTCOMES for a, b in CONTRASTS}}
    for hs in sorted(df.history_segment.unique()):
        w = f"history_segment = '{hs}'"
        n = con.execute(f"SELECT count(*) FROM email_test WHERE {w}").fetchone()[0]
        ref["subgroups"][f"history_segment={hs}"] = {"n": n, **{f"{o}:{a}-{b}": r4(diff(con, o, a, b, w))
                                                                 for o in OUTCOMES for a, b in CONTRASTS}}
    df["recency_band"] = np.select([df.recency <= 3, df.recency <= 6, df.recency <= 9], ["1-3", "4-6", "7-9"],
                                   "10-12")
    df["bought"] = np.select([(df.mens == 1) & (df.womens == 0), (df.mens == 0) & (df.womens == 1)],
                             ["mens_only", "womens_only"], "both")
    ref["heterogeneity"] = {f"{o}|{c}": heterogeneity(df, o, c) for o in OUTCOMES
                            for c in ("zip_code", "channel", "newbie", "bought", "recency_band", "history_segment")}
    ref["gaps"] = {}
    for col, (l1, l2) in {"newbie": ("new", "established"), "bought": ("mens_only", "womens_only"),
                          "bought2": ("both", "mens_only"), "zip_code": ("Rural", "Urban"),
                          "zip_code2": ("Surburban", "Urban"), "channel": ("Phone", "Web")}.items():
        cuts = SUBGROUPS[col.rstrip("2")]
        ref["gaps"].update({f"{o}:{e}|{l1}-{l2}": r4(gap(con, o, e, cuts[l1], cuts[l2]))
                            for o in OUTCOMES for e in ("mens", "womens")})
    ref["mailed_conversion"] = {
        f"{col}={lab}": dict(con.execute(f"SELECT segment, avg(conversion) FROM email_test WHERE {w} GROUP BY 1")
                             .fetchall())
        for col, cuts in SUBGROUPS.items() for lab, w in cuts.items()}
    ref["balance"] = balance(df)
    # business inputs stated in requests
    d = {e: ref["itt"][f"spend:{e}-none"] for e in ("mens", "womens")}
    ref["inputs"] = {
        "lu05 spend per $ of send cost at $0.04": {e: {k: d[e][k] / 0.04 for k in ("estimate", "low", "high")}
                                                    for e in d},
        "ha03 margin per men's e-mail at 42%, cost $0.09": {k: 0.42 * d["mens"][k] - 0.09
                                                             for k in ("estimate", "low", "high")},
        "lu10 250k audience, men's e-mail": {
            "spend_total": 250_000 * g["mens"]["spend"], "purchases": 250_000 * g["mens"]["conversion"],
            "spend_total_none": 250_000 * g["none"]["spend"], "purchases_none": 250_000 * g["none"]["conversion"],
            "incremental_spend": {k: 250_000 * d["mens"][k] for k in ("estimate", "low", "high")},
            "incremental_purchases": {k: 250_000 * ref["itt"]["conversion:mens-none"][k]
                                      for k in ("estimate", "low", "high")}},
        "ha13 men's e-mail to 64,000": {
            "spend_total": 64_000 * g["mens"]["spend"],
            "incremental_spend": {k: 64_000 * d["mens"][k] for k in ("estimate", "low", "high")}},
        "ha08 share of e-mail-group spend that would have happened anyway": {
            "mens": g["none"]["spend"] / g["mens"]["spend"], "womens": g["none"]["spend"] / g["womens"]["spend"],
            "either": g["none"]["spend"] / ((g["mens"]["total_spend"] + g["womens"]["total_spend"])
                                            / (g["mens"]["n"] + g["womens"]["n"]))},
        "ha14 plan 2.5% conversion for mailed": {e: g[e]["conversion"] for e in ("mens", "womens")},
    }
    return ref


if __name__ == "__main__":
    print(json.dumps(reference_v2(), indent=1, default=float))
