# E3d probe: what the NYC TLC yellow-taxi files look like before any design

2026-11-07 (night), designer's first look at the three downloaded months (`yellow_tripdata_2024-12`,
`2025-01`, `2026-09`; source, terms and sha256 in the local dataset folder's SOURCE.txt and
SHA256SUMS). Not a design: these are the facts the E3d design has to start from. Queries ran on
DuckDB, 2 threads.

## Schema versions

| month | rows | columns | change |
|---|---|---|---|
| 2024-12 | 3,668,371 | 19 | — |
| 2025-01 | 3,475,226 | 20 | `cbd_congestion_fee` added (Manhattan congestion pricing started 2025-01-05) |
| 2026-09 | 3,876,044 | 21 | `request_source` added (VARCHAR; NULL on 69% of rows, otherwise codes such as HV0003, HV0005, A, EH0004, CC) |

Each file also holds a few pickups outside its month (2008-12-31 and 2025-03 in the 2024-12 file,
2009-01-01 and 2026-10 in the 2026-09 file).

## The components do not add up to `total_amount`, and not by accident

Share of trips where `fare + extra + mta_tax + tip + tolls + improvement_surcharge +
congestion_surcharge + airport_fee (+ cbd_congestion_fee)` equals `total_amount` to the cent:

| month | without the CBD fee | with the CBD fee |
|---|---|---|
| 2024-12 | 73.3% | (no column) |
| 2025-01 | 26.2% | 68.0% |
| 2026-09 | 17.0% | 56.5% |

The residuals are a few fixed amounts, and they follow the vendor:

| residual (total − components) | 2024-12 | 2025-01 | reading |
|---|---|---|---|
| 0 | 73.3% | 68.0% | adds up |
| −2.50 | 17.7% | 5.1% | vendor 1: `extra` already contains the congestion surcharge, which is also in its own column |
| −3.25 | — | 11.3% | vendor 1 from 2025: `extra` also contains the 0.75 CBD fee |
| +2.50 | 7.6% | 11.3% | trips with NULL `passenger_count` (mostly vendor 2): the surcharge is in the total but its column is NULL |

By vendor, 2024-12: vendor 1 adds up on 9% of its trips (84% are under), vendor 2 on 92%.

## What this means for E3d

- A "components reconcile to the total" test is the natural pipeline check, and on this data it
  fails for a quarter of trips **before** any schema change. A break-fix task built on it must
  first encode the vendor rules (or the test is noise), and an agent that "fixes" the pipeline by
  loosening the tolerance or dropping vendor 1 passes a naive gate. That is the silent error worth
  measuring.
- The 2025-01 change is not only a new column: the new fee also enters vendor 1's `extra`. A fix
  that adds `cbd_congestion_fee` to the component sum makes vendor 2 reconcile and vendor 1 worse.
- Questions such as "congestion surcharge collected in January 2025" have at least three plausible
  answers (sum of the column; plus the NULL-column trips; minus vendor 1's double count). The
  owner has to decide before any truth exists, as for E3a.
- 2026-09 postdates the agents' training data; `request_source` is a new column with no published
  definition in our copy. An honest agent says so instead of inventing one.
