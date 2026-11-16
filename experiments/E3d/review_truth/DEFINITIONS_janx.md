# Yellow-taxi monthly metrics: definitions (data owner, as of the December 2024 build)

Source: the NYC TLC yellow-taxi trip export, one parquet file per month, as published. The trips are
reported by technology vendors (1 = Creative Mobile Technologies, 2 = Curb Mobility, 6 = Myle
Technologies, 7 = Helix). TLC does not create or correct the data.

## Served metrics (one row per month)

- **month**: the month of the pickup time. A trip belongs to the month its pickup falls in; the export
  carries a few trips from other months, which are left out.
- **trips**: number of trips in the month, every vendor, every payment type, refunds and disputes
  included.
- **fare_amount, extra, mta_tax, tip_amount, tolls_amount, improvement_surcharge,
  congestion_surcharge, airport_fee, total_amount**: the sum over the month's trips of each column **as
  reported**, in dollars. Nothing is corrected, imputed or reallocated. Tips are card tips only (cash
  tips are not recorded).

Once a month is served, its numbers do not change.

## How the vendors report the money columns

`total_amount` is what the passenger was charged (cash tips excluded). The other money columns are its
components.

- On about 8–9% of trips the fee columns (`congestion_surcharge`, `airport_fee`) are empty (these are
  also the trips without `passenger_count`). Their surcharges are inside `total_amount` but in no column,
  so they cannot be reconciled. They are served like every other trip.
- Vendors 6 and 7 report a few hundred trips a month with irregular components; they are served but
  not reconciled.

## Pipeline checks (`python ci/build.py <month>`)

- the export's columns and types match `contracts/yellow_trips.yml` (ci/check_raw.py, with a Great Expectations
  suite on a sample of the export);
- dbt data tests: at least 99.9% of the exported trips fall in the month (`rows_in_month`); vendor ids are 1, 2, 6
  or 7; pickup time and vendor are never empty; one row per month, with at least one trip;
- on vendors 1 and 2, trips with filled fee columns: the components reconcile with the total, to the cent,
  on at least 98% of each vendor's trips (`reconcile_vendor_components`);
- the served table's columns and types are fixed by the model contract of `monthly_metrics`;
- SQL style: sqlfluff (`.sqlfluff`).
