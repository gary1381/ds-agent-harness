# Yellow-taxi monthly metrics: definitions (data owner, as of the January 2025 build)

Source: the NYC TLC yellow-taxi trip export, one parquet file per month, as published. The trips are
reported by technology vendors (1 = Creative Mobile Technologies, 2 = Curb Mobility, 6 = Myle
Technologies, 7 = Helix). TLC does not create or correct the data.

## Served metrics (one row per month)

- **month**: the month of the pickup time. A trip belongs to the month its pickup falls in; the export
  carries a few trips from other months, which are left out.
- **trips**: number of trips in the month, every vendor, every payment type, refunds and disputes
  included.
- **fare_amount, extra, mta_tax, tip_amount, tolls_amount, improvement_surcharge,
  congestion_surcharge, airport_fee, cbd_congestion_fee, total_amount**: the sum over the month's trips of each column **as
  reported**, in dollars. Nothing is corrected, imputed or reallocated. Tips are card tips only (cash
  tips are not recorded). `cbd_congestion_fee` (Manhattan congestion pricing, from 2025-01-05) is empty
  for months before the export carried it.

Once a month is served, its numbers do not change.

## How the vendors report the money columns

`total_amount` is what the passenger was charged (cash tips excluded). The other money columns are its
components. Facts established on the 2024 data:

- **Vendor 2** reports components that add up to `total_amount`.
- **Vendor 1** reports every surcharge it collects in its own column **and also inside `extra`** (in
  2024: the congestion surcharge and the airport fee; from 2025-01 also the CBD congestion fee). Its components therefore exceed the total by those
  surcharges. This is how vendor 1 reports; it is not an error to fix.
- On about 8–17% of trips the fee columns (`congestion_surcharge`, `airport_fee`) are empty (these are
  also the trips without `passenger_count`). Their surcharges are inside `total_amount` but in no column,
  so they cannot be reconciled. They are served like every other trip.
- Vendors 6 and 7 report a few hundred trips a month with irregular components; they are served but
  not reconciled.

- January 2025: about 1.35% of vendor 2's reconcilable trips are off by the CBD fee (first month of
  congestion pricing); within the 98% check.

## Pipeline checks

- the export's columns match `contract.json` (`cbd_congestion_fee` optional: absent before 2025-01);
- at least 99.9% of the exported trips fall in the month;
- vendor ids are 1, 2, 6 or 7;
- on vendors 1 and 2, trips with filled fee columns: the components add up to the total under the vendor
  rules above, to the cent, on at least 98% of each vendor's trips.
