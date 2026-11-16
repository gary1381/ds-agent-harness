"""The owner's data checks on the e-mail test table:  python checks/check_experiment.py [campaign.duckdb]

Column and value expectations run as a Great Expectations suite on the whole table; the sample-ratio test (equal
thirds) and the balance of pre-campaign columns across groups (standardised mean differences) are computed beside
it. Exit 0 when every check passes.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import sys

import duckdb

os.environ.setdefault("GX_ANALYTICS_ENABLED", "false")
import great_expectations as gx  # noqa: E402
from great_expectations.data_context.types.base import ProgressBarsConfig  # noqa: E402

GROUPS = ["Mens E-Mail", "Womens E-Mail", "No E-Mail"]
COLUMNS = ["recency", "history_segment", "history", "mens", "womens", "zip_code", "newbie", "channel", "segment",
           "visit", "conversion", "spend"]
SRM_P_MIN, SMD_MAX = 0.001, 0.1


def check(db: Path) -> list[str]:
    con = duckdb.connect(str(db), read_only=True)
    con.execute("PRAGMA threads=1")
    df = con.execute("SELECT * FROM email_test").df()
    ctx = gx.get_context(mode="ephemeral")
    ctx.variables.progress_bars = ProgressBarsConfig(globally=False)
    batch = (ctx.data_sources.add_pandas("campaign").add_dataframe_asset("email_test")
             .add_batch_definition_whole_dataframe("all").get_batch(batch_parameters={"dataframe": df}))
    E = gx.expectations
    suite = gx.ExpectationSuite(name="email_test")
    for e in (E.ExpectTableColumnsToMatchSet(column_set=COLUMNS, exact_match=True),
              E.ExpectColumnDistinctValuesToEqualSet(column="segment", value_set=GROUPS),
              *(E.ExpectColumnValuesToBeInSet(column=c, value_set=[0, 1])
                for c in ("visit", "conversion", "mens", "womens", "newbie")),
              *(E.ExpectColumnValuesToNotBeNull(column=c) for c in COLUMNS),
              E.ExpectColumnValuesToBeBetween(column="spend", min_value=0),
              E.ExpectColumnValuesToBeBetween(column="recency", min_value=1, max_value=12),
              E.ExpectColumnPairValuesAToBeGreaterThanB(column_A="visit", column_B="conversion", or_equal=True),
              E.ExpectColumnPairValuesAToBeGreaterThanB(column_A="spend", column_B="conversion", or_equal=True)):
        suite.add_expectation(e)
    problems = []
    for r in batch.validate(suite).results:
        if not r.success:
            cfg = r.expectation_config
            problems.append(f"{cfg.type} {cfg.kwargs.get('column', '')} failed: "
                            f"{(r.result or {}).get('unexpected_count', '')}")
    if ((df.conversion == 1) & (df.spend <= 0)).any():
        problems.append("purchases with no spend")
    n = df.segment.value_counts()
    exp = len(df) / len(GROUPS)
    chi2 = sum((n.get(g, 0) - exp) ** 2 / exp for g in GROUPS)
    p = math.exp(-chi2 / 2)                      # chi-square with 2 degrees of freedom
    print(f"sample ratio: { {g: int(n.get(g, 0)) for g in GROUPS} }, chi2 {chi2:.2f}, p {p:.3f}")
    if p < SRM_P_MIN:
        problems.append(f"sample ratio mismatch: p = {p:.2g}")
    pre = {"recency": df.recency, "history": df.history, "newbie": df.newbie, "mens": df.mens, "womens": df.womens,
           **{f"channel={c}": (df.channel == c).astype(float) for c in df.channel.unique()},
           **{f"zip_code={z}": (df.zip_code == z).astype(float) for z in df.zip_code.unique()}}
    worst = 0.0
    for name, x in pre.items():
        ref = x[df.segment == "No E-Mail"]
        for g in GROUPS[:2]:
            y = x[df.segment == g]
            smd = abs(y.mean() - ref.mean()) / math.sqrt((y.var() + ref.var()) / 2)
            worst = max(worst, smd)
            if smd >= SMD_MAX:
                problems.append(f"imbalance: {name} {g} vs No E-Mail, SMD {smd:.3f}")
    print(f"balance: largest standardised mean difference {worst:.4f}")
    return problems


def main() -> int:
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("campaign.duckdb")
    problems = check(db)
    print(f"data checks: {'PASS' if not problems else 'FAIL'}")
    for p in problems:
        print(f"  {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
