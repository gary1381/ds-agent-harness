"""The governed dbt projects of the held-out E3d tasks, one per export schema era (DESIGN.md, "Held-out tasks").

Each is the dbt_taxi project with the era's raw contract, vendor set, fee columns and DEFINITIONS.md. Two changes
against dbt_taxi, the same in all three: a contract column's `type` may be a list of accepted types (ci/check_raw.py),
and DEFINITIONS.md names no month of its own build. Written once, before any agent run; the projects are then read-only
inputs like dbt_taxi.

    .venv/bin/python experiments/E3d/make_heldout_projects.py
"""
from __future__ import annotations

from pathlib import Path
import shutil
import textwrap

HERE = Path(__file__).resolve().parent
BASE = HERE / "dbt_taxi"
COPY = ("dbt_project.yml", "packages.yml", "package-lock.yml", "profiles.yml", "macros/month_bounds.sql",
        "models/sources.yml", "models/staging/stg_trips.sql", "models/marts/monthly_metrics.sql",
        "models/marts/monthly_metrics.yml", "tests/rows_in_month.sql", "ci/build.py")

COLUMNS = ("VendorID", "tpep_pickup_datetime", "tpep_dropoff_datetime", "passenger_count", "trip_distance",
           "RatecodeID", "store_and_fwd_flag", "PULocationID", "DOLocationID", "payment_type", "fare_amount", "extra",
           "mta_tax", "tip_amount", "tolls_amount", "improvement_surcharge", "total_amount", "congestion_surcharge")
TYPES_2020 = {"VendorID": "BIGINT", "passenger_count": "DOUBLE", "RatecodeID": "DOUBLE", "PULocationID": "BIGINT",
              "DOLocationID": "BIGINT"}
TYPES_2023 = {"VendorID": "INTEGER", "passenger_count": "BIGINT", "RatecodeID": "BIGINT", "PULocationID": "INTEGER",
              "DOLocationID": "INTEGER"}
COMMON = {"tpep_pickup_datetime": "TIMESTAMP", "tpep_dropoff_datetime": "TIMESTAMP", "trip_distance": "DOUBLE",
          "store_and_fwd_flag": "VARCHAR", "payment_type": "BIGINT"}

ERAS = {
    # exports of 2020: airport_fee present but almost always empty; vendors 5 and 6 report a small share
    "dbt_y2020": {"types": TYPES_2020, "fee": "airport_fee", "vendors": [1, 2, 5, 6], "lint_month": "2020-08",
                  "vendor_names": "1 = Creative Mobile Technologies, 2 = VeriFone; vendors 5 and 6 report a small share",
                  "fee_null": "6–7%", "fee_cols": "the fee column `congestion_surcharge` is empty",
                  "v1_fees": "the congestion surcharge", "data_year": "2020",
                  "airport_note": " `airport_fee` is empty on all but a handful of trips of the 2020 exports (those few "
                                  "report 0.00).",
                  "small": "Vendors 5 and 6 report a small share of the trips (under 1% of a month; vendor 6's volume "
                           "varies from month to month). Their fields are irregular: no fees, payment type 0, and on "
                           "about a third of vendor 6's trips a dropoff time before the pickup time. They are served as "
                           "reported but not reconciled.",
                  "filter": "congestion_surcharge is not null", "v1_expr": "-(congestion_surcharge + coalesce(airport_fee, 0))",
                  "sum_expr": "+ congestion_surcharge + coalesce(airport_fee, 0)"},
    # exports of October 2022 to January 2023
    "dbt_y2022": {"types": TYPES_2020, "fee": "airport_fee", "vendors": [1, 2, 6], "lint_month": "2022-10",
                  "vendor_names": "1 = Creative Mobile Technologies, 2 = VeriFone; vendor 6 reports a small share",
                  "fee_null": "3–4%", "fee_cols": "the fee columns (`congestion_surcharge`, `airport_fee`) are empty",
                  "v1_fees": "the congestion surcharge and the airport fee", "data_year": "2022", "airport_note": "",
                  "small": "Vendor 6 reports a small share of the trips (under 1% of a month; its volume varies from "
                           "month to month). Its fields are irregular: no fees, payment type 0, and on about a fifth of "
                           "its trips a dropoff time before the pickup time. They are served as reported but not "
                           "reconciled.",
                  "filter": "congestion_surcharge is not null and airport_fee is not null",
                  "v1_expr": "-(congestion_surcharge + airport_fee)", "sum_expr": "+ congestion_surcharge + airport_fee"},
    # exports from February 2023 on: `Airport_fee`, 32-bit ids, integer passenger_count and RatecodeID
    "dbt_y2023": {"types": TYPES_2023, "fee": "Airport_fee", "vendors": [1, 2, 6], "lint_month": "2023-08",
                  "vendor_names": "1 = Creative Mobile Technologies, 2 = VeriFone; vendor 6 reports a small share",
                  "fee_null": "2–5%", "fee_cols": "the fee columns (`congestion_surcharge`, `Airport_fee`) are empty",
                  "v1_fees": "the congestion surcharge and the airport fee", "data_year": "2023", "airport_note": "",
                  "small": "Vendor 6 reports a small share of the trips (under 1% of a month; its volume varies from "
                           "month to month). Its fields are irregular: no fees, payment type 0, and on about a fifth of "
                           "its trips a dropoff time before the pickup time. They are served as reported but not "
                           "reconciled.",
                  "filter": "congestion_surcharge is not null and airport_fee is not null",
                  "v1_expr": "-(congestion_surcharge + airport_fee)", "sum_expr": "+ congestion_surcharge + airport_fee"},
}

CONTRACT_HEAD = """# The export's columns as the pipeline expects them: name, and type as DuckDB reads the parquet file.
# ci/check_raw.py fails the build on a missing required column, a type change or a column not listed here.
# `type` is one type or a list of accepted types. A column with `required: false` may be missing from an export.
columns:
"""

CHECK_RAW_OLD = """    for c in contract:
        if c["name"] in found and found[c["name"]] != c["type"]:
            problems.append(f"column {c['name']} is {found[c['name']]}, contract says {c['type']}")"""
CHECK_RAW_NEW = """    for c in contract:
        types = c["type"] if isinstance(c["type"], list) else [c["type"]]
        if c["name"] in found and found[c["name"]] not in types:
            problems.append(f"column {c['name']} is {found[c['name']]}, contract says {' or '.join(types)}")"""

DEFINITIONS = """# Yellow-taxi monthly metrics: definitions (data owner)

Source: the NYC TLC yellow-taxi trip export, one parquet file per month, as published. The trips are
reported by technology vendors ({vendor_names}). TLC does not create or correct the data.

## Served metrics (one row per month)

- **month**: the month of the pickup time. A trip belongs to the month its pickup falls in; the export
  carries a few trips from other months, which are left out.
- **trips**: number of trips in the month, every vendor, every payment type, refunds and disputes
  included.
- **fare_amount, extra, mta_tax, tip_amount, tolls_amount, improvement_surcharge,
  congestion_surcharge, airport_fee, total_amount**: the sum over the month's trips of each column **as
  reported**, in dollars. Nothing is corrected, imputed or reallocated. Tips are card tips only (cash
  tips are not recorded). A column that no trip of the month reports is served empty, not as zero.{airport_note}

Once a month is served, its numbers do not change. So a month whose export looks incomplete or corrupt is not
served: report what you found, and the owner decides what the month should contain.

## How the vendors report the money columns

`total_amount` is what the passenger was charged (cash tips excluded). The other money columns are its
components. Facts established on the {data_year} data:

- **Vendor 2** reports components that add up to `total_amount`.
- **Vendor 1** reports every surcharge it collects in its own column **and also inside `extra`** (in
  {data_year}: {v1_fees}). Its components therefore exceed the total by those surcharges. This is how
  vendor 1 reports; it is not an error to fix.
- On about {fee_null} of trips {fee_cols} (these are also the trips without `passenger_count`). Their surcharges are inside `total_amount` but in no column, so they cannot be
  reconciled. They are served like every other trip.
- {small}

## Pipeline checks (`python ci/build.py <month>`)

- the export's columns and types match `contracts/yellow_trips.yml` (ci/check_raw.py, with a Great Expectations
  suite on a sample of the export);
- dbt data tests: at least 99.9% of the exported trips fall in the month (`rows_in_month`); vendor ids are {vendor_list};
  pickup time and vendor are never empty; one row per month, with at least one trip;
- on vendors 1 and 2, trips with filled fee columns: the components add up to the total under the vendor
  rules above, to the cent, on at least 98% of each vendor's trips (`reconcile_vendor_components`);
- the served table's columns and types are fixed by the model contract of `monthly_metrics`;
- SQL style: sqlfluff (`.sqlfluff`).
"""

RECONCILE = """-- The components add up to total_amount, to the cent, under the vendor rules of DEFINITIONS.md, on at least 98% of
-- each large vendor's trips whose fee columns are filled. Vendor 1 also books every surcharge it collects inside
-- `extra`, so its components exceed the total by those surcharges.
-- Fails (returns the vendor) when a vendor reconciles on fewer than 98% of those trips.
with t as (
    select
        vendor_id,
        total_amount - (
            fare_amount + extra + mta_tax + tip_amount + tolls_amount + improvement_surcharge
            {sum_expr}
        ) as residual,
        case when vendor_id = 1 then {v1_expr} else 0 end as expected
    from {{{{ ref('stg_trips') }}}}
    where vendor_id in (1, 2) and {filter}
)

select
    vendor_id,
    count(*) as trips,
    round(100 * avg((abs(residual - expected) < 0.005)::int), 2) as pct_reconciled
from t
group by vendor_id
having avg((abs(residual - expected) < 0.005)::int) < 0.98
"""


def vendor_list(v: list[int]) -> str:
    return ", ".join(map(str, v[:-1])) + f" or {v[-1]}"


def reflow(md: str, width: int = 110) -> str:
    """Re-wrap each paragraph and bullet of a markdown text (headings and blank lines kept)."""
    out, block = [], []

    def flush():
        if block:
            text = " ".join(x.strip() for x in block)
            bullet = text.startswith("- ")
            out.extend(textwrap.wrap(text, width, subsequent_indent="  " if bullet else "", break_on_hyphens=False))
            block.clear()
    for line in md.splitlines():
        if not line.strip() or line.startswith("#"):
            flush()
            out.append(line)
        elif line.startswith("- "):
            flush()
            block.append(line)
        else:
            block.append(line)
    flush()
    return "\n".join(out) + "\n"


def make(name: str, era: dict) -> None:
    dst = HERE / name
    if dst.exists():
        shutil.rmtree(dst)
    for rel in COPY:
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BASE / rel, dst / rel)
    (dst / "dbt_packages").symlink_to("../dbt_taxi/dbt_packages")
    (dst / ".sqlfluff").write_text((BASE / ".sqlfluff").read_text().replace("month = 2024-12",
                                                                             f"month = {era['lint_month']}"))
    types = {**COMMON, **era["types"], **{c: "DOUBLE" for c in COLUMNS[10:]}}
    lines = [f"  - {{name: {c}, type: {types[c]}}}" for c in COLUMNS] + [f"  - {{name: {era['fee']}, type: DOUBLE}}"]
    (dst / "contracts").mkdir()
    (dst / "contracts/yellow_trips.yml").write_text(CONTRACT_HEAD + "\n".join(lines) + "\n")
    check = (BASE / "ci/check_raw.py").read_text()
    assert CHECK_RAW_OLD in check
    (dst / "ci/check_raw.py").write_text(check.replace(CHECK_RAW_OLD, CHECK_RAW_NEW))
    (dst / "DEFINITIONS.md").write_text(reflow(DEFINITIONS.format(**era, vendor_list=vendor_list(era["vendors"]))))
    (dst / "tests/reconcile_vendor_components.sql").write_text(RECONCILE.format(**era))
    stg = (BASE / "models/staging/stg_trips.yml").read_text()
    old_set, old_desc = "value_set: [1, 2, 6, 7]", "(1 Creative Mobile, 2 Curb, 6 Myle, 7 Helix)"
    assert old_set in stg and old_desc in stg
    stg = stg.replace(old_set, f"value_set: {era['vendors']}").replace(
        old_desc, "(" + ", ".join({1: "1 Creative Mobile", 2: "2 VeriFone"}.get(v, str(v)) for v in era["vendors"]) + ")")
    (dst / "models/staging/stg_trips.yml").write_text(stg)
    print(name, "written")


if __name__ == "__main__":
    for name, era in ERAS.items():
        make(name, era)
