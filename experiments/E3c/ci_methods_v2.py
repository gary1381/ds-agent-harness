"""E3c v2: compare the gate's Wald / log-delta intervals with Welch t, OLS HC1 and bootstrap (Luna's review, finding 8).

    ../agent_env_dt/bin/python experiments/E3c/ci_methods_v2.py experiments/E3c/runs/v2_calib/design.yaml
"""
import os, sys, math
from pathlib import Path
os.environ["OMP_NUM_THREADS"] = "1"; os.environ["OPENBLAS_NUM_THREADS"] = "1"
import duckdb, numpy as np
from scipy import stats as st
import statsmodels.formula.api as smf
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from dsharness.readout import experiment as X
D = X.Design.load(sys.argv[1])
con = duckdb.connect(str(D.db), read_only=True)
df = con.execute(f"SELECT * FROM {D.table}").df()
rng = np.random.default_rng(20261107)
G = D.groups
cases = [("spend", "mens", "none", None), ("spend", "womens", "none", None), ("spend", "mens", "womens", None),
         ("conversion", "mens", "none", None), ("visit", "womens", "none", None),
         ("spend", "mens", "none", "womens_only"), ("spend", "womens", "none", "mens_only"),
         ("spend", "mens", "none", "rural_newbie"), ("conversion", "womens", "none", "rural_newbie")]
pops = {"womens_only": (df.mens == 0) & (df.womens == 1), "mens_only": (df.mens == 1) & (df.womens == 0),
        "rural_newbie": (df.zip_code == "Rural") & (df.newbie == 1)}
popc = {"womens_only": [{"column": "mens", "op": "=", "value": 0}, {"column": "womens", "op": "=", "value": 1}],
        "mens_only": [{"column": "mens", "op": "=", "value": 1}, {"column": "womens", "op": "=", "value": 0}],
        "rural_newbie": [{"column": "zip_code", "op": "=", "value": "Rural"}, {"column": "newbie", "op": "=", "value": 1}]}
c2 = X.connect(D)
print("| estimand | outcome | a-b | pop | gate [lo, hi] | worst method: end shift / gate width |")
for est in ("mean_difference", "relative_lift"):
    for o, a, b, p in cases:
        m = pops[p] if p else np.ones(len(df), bool)
        xa = df.loc[m & (df.segment == G[a]), o].to_numpy(float); xb = df.loc[m & (df.segment == G[b]), o].to_numpy(float)
        r = X.recompute(c2, D, {"estimand": est, "outcome": o, "groups": [a, b], "population": popc.get(p)})
        w = r["high"] - r["low"]
        alt = {}
        B = 2000
        ia = rng.integers(0, len(xa), (B, len(xa))); ib = rng.integers(0, len(xb), (B, len(xb)))
        ma, mb = xa[ia].mean(1), xb[ib].mean(1)
        bs = (ma - mb) if est == "mean_difference" else (ma / mb - 1)
        alt["boot pct"] = np.percentile(bs, [2.5, 97.5])
        if est == "mean_difference":
            t = st.ttest_ind(xa, xb, equal_var=False); ci = t.confidence_interval(0.95); alt["welch t"] = (ci.low, ci.high)
            sub = df.loc[m & df.segment.isin([G[a], G[b]])].assign(T=lambda d: (d.segment == G[a]).astype(int))
            f = smf.ols(f"{o} ~ T", sub).fit(cov_type="HC1"); alt["ols hc1"] = tuple(f.conf_int().loc["T"])
            f2 = smf.ols(f"{o} ~ T + recency + history + mens + womens + newbie", sub).fit(cov_type="HC1")
            alt["ols hc1 adj"] = tuple(f2.conf_int().loc["T"])
        else:
            d = xa.mean() - xb.mean(); se = math.sqrt(xa.var(ddof=1)/len(xa) + xb.var(ddof=1)/len(xb))
            # plain delta on the ratio (not the log)
            sr = (xa.mean()/xb.mean()) * math.sqrt(xa.var(ddof=1)/(len(xa)*xa.mean()**2) + xb.var(ddof=1)/(len(xb)*xb.mean()**2))
            rr = xa.mean()/xb.mean()-1; alt["delta linear"] = (rr - 1.96*sr, rr + 1.96*sr)
        worst = max(((k, max(abs(v[0]-r["low"]), abs(v[1]-r["high"])) / w, v) for k, v in alt.items()), key=lambda z: z[1])
        within = sum(max(abs(v[0]-r["low"]), abs(v[1]-r["high"])) <= 0.10 * w for v in alt.values())
        print(f"| {est} | {o} | {a}-{b} | {p or 'all'} | [{r['low']:.4g}, {r['high']:.4g}] | {worst[0]} {worst[1]:.3f} "
              f"({within}/{len(alt)} methods within 10%) |", flush=True)
