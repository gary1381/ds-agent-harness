# What is known about the 2026-09 yellow-taxi export (task sep)

Counted over trips whose pickup falls in September 2026, the way DEFINITIONS_sep.md counts them.

## The new column `request_source`

No definition, data dictionary or TLC notice explains it in the files available to the engineer.

| request_source | trips | share | fee columns filled | passenger_count filled | payment_type |
|---|---|---|---|---|---|
| (empty) | 2,685,930 | 69.3% | 2,685,609 | 2,685,609 | 1–5, and 0 on 321 trips |
| HV0003 | 709,010 | 18.3% | 0 | 0 | 0 on all |
| HV0005 | 408,034 | 10.5% | 0 | 0 | 0 on all |
| A | 63,426 | 1.6% | 0 | 0 | 0 on all |
| EH0004 | 8,733 | 0.2% | 0 | 0 | 0 on all |
| CC | 580 | 0.01% | 0 | 0 | 0 on all |
| EH0010 | 309 | 0.01% | 0 | 0 | 0 on all |

- All trips: 3,876,022. Trips with a `request_source`: 1,190,092 (30.7%).
- Trips with a `request_source` are longer on average: 8.72 vs 3.31 miles, total $36.46 vs $30.22.
- In 2024 and 2025-01, about 8–9% of trips had empty fee columns, and there was no `request_source` column. In
  2026-09, 30.7% of trips have empty fee columns, and every one of them carries a `request_source`.
- The reconciliation test only checks trips with filled fee columns, so it now covers about 69% of the month's
  trips instead of about 91%. It still passes.

## Vendors

| vendor | 2025-01 trips (whole file) | 2026-09 trips (in month) | of which with request_source |
|---|---|---|---|
| 1 | 753,671 | 694,912 | 111,747 |
| 2 | 2,719,860 | 3,127,981 | 1,073,109 |
| 6 | 489 | 5,236 | 5,236 |
| 7 | 1,206 | 47,893 | 0 |

## The published control total (TLC Industry Indicators, yellow, per day)

TLC publishes monthly yellow-taxi trips per day and farebox per day, computed by TLC from its own data.

| month | trips/day: TLC / this export | farebox/day: TLC / Σ(total − tip) here |
|---|---|---|
| 2024-12 | 117,058 / 118,333 (+1.09%) | $2,909,740 / $2,941,879 (+1.10%) |
| 2025-01 | 110,848 / 112,103 (+1.13%) | $2,502,393 / $2,539,306 (+1.48%) |
| 2026-09 | 115,307 / 129,201 (+12.05%) | $3,456,554 / $3,792,286 (+9.71%) |

On the older months the export exceeds TLC's own figures by about 1% (TLC leaves out some trips by a rule it does
not publish). On 2026-09 the gap is 10–12%, and no simple subset explains both figures:
- vendors 1 and 2 only: trips +10.5%;
- only trips with an empty `request_source`: trips −22%;
- leaving out the trips with `request_source` = HV0005: trips +0.25%, but farebox −6.8%.
