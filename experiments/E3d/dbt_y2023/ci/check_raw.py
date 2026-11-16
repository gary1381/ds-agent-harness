"""Validate one month's export before dbt runs:  python ci/check_raw.py 2025-01

Columns and types are read from the parquet file and compared with contracts/yellow_trips.yml. The column set and
a few value expectations run as a Great Expectations suite on a sample of the export's rows.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

import duckdb
import yaml

os.environ.setdefault("GX_ANALYTICS_ENABLED", "false")
import great_expectations as gx  # noqa: E402
from great_expectations.data_context.types.base import ProgressBarsConfig  # noqa: E402

HERE = Path(__file__).resolve().parents[1]
SAMPLE_ROWS = 20_000


def check(month: str, raw_dir: Path) -> list[str]:
    src = raw_dir / f"yellow_tripdata_{month}.parquet"
    contract = yaml.safe_load((HERE / "contracts" / "yellow_trips.yml").read_text())["columns"]
    con = duckdb.connect()
    con.execute("PRAGMA threads=2")
    found = {n: t for n, t, *_ in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{src}')").fetchall()}
    problems = []
    for c in contract:
        types = c["type"] if isinstance(c["type"], list) else [c["type"]]
        if c["name"] in found and found[c["name"]] not in types:
            problems.append(f"column {c['name']} is {found[c['name']]}, contract says {' or '.join(types)}")
    expected = [c["name"] for c in contract if c.get("required", True) or c["name"] in found]

    sample = con.execute(f"SELECT * FROM read_parquet('{src}') LIMIT {SAMPLE_ROWS}").df()
    ctx = gx.get_context(mode="ephemeral")
    ctx.variables.progress_bars = ProgressBarsConfig(globally=False)
    batch = (ctx.data_sources.add_pandas("raw").add_dataframe_asset("yellow_trips")
             .add_batch_definition_whole_dataframe("sample").get_batch(batch_parameters={"dataframe": sample}))
    suite = gx.ExpectationSuite(name="yellow_trips_raw")
    suite.add_expectation(gx.expectations.ExpectTableColumnsToMatchSet(column_set=expected, exact_match=True))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column="tpep_pickup_datetime"))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column="VendorID"))
    result = batch.validate(suite)
    for r in result.results:
        if not r.success:
            cfg = r.expectation_config
            detail = {k: v for k, v in (r.result or {}).items() if k in ("details", "unexpected_count")}
            problems.append(f"{cfg.type} {cfg.kwargs.get('column', '')} failed: {detail}".replace("  ", " "))
    return problems


def main() -> int:
    month = sys.argv[1]
    problems = check(month, Path(os.environ.get("TAXI_RAW_DIR", "raw")))
    print(f"raw export check: {'PASS' if not problems else 'FAIL'}")
    for p in problems:
        print(f"  {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
