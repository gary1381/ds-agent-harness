# E3a question review (designer, before any evaluation attempt)

Two author rounds with the identical brief ([author_brief.md](../../../docs/experiments/E3a/author_brief.md);
Luna → R-L, Haiku → R-H; round 2 numbered from 21) gave 80 questions. `build_bank.py` ran every truth
SQL on the evaluator database (the adjudicated truth build B, [ADJUDICATION.md](../ADJUDICATION.md))
and dropped same-truth repeats mechanically. Every truth SQL was then read against the official
definitions and re-run: two review passes by designer-briefed subagents
([review_pass_L.md](review_pass_L.md), [review_pass_H.md](review_pass_H.md)), each flagged item decided by
the designer. `freeze_bank.py` applies the decisions below and writes `bank.yaml`.

**Result: 74 questions** (Luna 37, Haiku 37; English 62, Chinese 12); 4 dropped as repeats, 2 for
two-way wording, 5 truths fixed (rounding only).

| | count |
|---|---|
| written | 80 (round 1: 40, round 2: 40) |
| dropped: same truth as an earlier question | 4 (R-H01, R-L21 = R-L01; R-L29 = R-L09; R-H24 = R-H05) |
| dropped: two readings with different values (brief rule 2) | 2 |
| truth fixed | 5 |
| **kept** | **74** |

## Changes

| qid | change | reason |
|---|---|---|
| R-L32 | dropped | "cumulative gross product sales by month through Q1 2025" does not say where the running total starts: from January 2025 the rows are 0.67M / 1.18M / 1.87M, from the start of the data 10.8M / 11.4M / 12.0M. No truth fix can choose. (The other two cumulative questions state the start: R-L12 "1月至6月", R-H12 "starting from January 2024".) |
| R-H35 | dropped | "cancelled value … in each market in April, May and June 2025" reads as one total per market or as one row per market and month; the truth has the first |
| R-H03 | truth fixed | ROUND(…, 4) removed: 0.0186 → 0.0185936; a right 0.01859 failed against the rounded truth |
| R-H07 | truth fixed | ROUND(…, 2) removed: the June row −0.32 → −0.31833 (0.5% off); a right −0.3183 failed |
| R-H11 | truth fixed | ROUND(…, 3) removed: 0.568 → 0.5682943; the question asks for three decimals, and 0.568 still passes against the exact value (half a unit in the last place), while a right 0.5683 failed against 0.568 |
| R-H14 | truth fixed | ROUND(…, 2) removed: 3.01 → 3.00795; a right 3.008 failed |
| R-H15 | truth fixed | ROUND(…, 4) removed: UK 0.0203 → 0.0203097 (95% of the tolerance used), non-UK 0.0477 → 0.0476945 |

Rounded truths left as they are: R-H02, R-H04, R-H08, R-H09, R-H10, R-H12, R-H13, R-H18, R-H19 (money
rounded to pence, or shares to 4 decimals where the exact answer still passes).

## Kept after a second look

- **Customers and guests.** R-L11 and R-L31 divide sales including guest orders by identified
  customers; both say so in the question. R-H13 excludes guest sales from the numerator; it says so.
  "UK customers" / "non-UK customers" (R-H08, R-H18, R-H23, R-H26, R-H28) is the market as defined
  (country of the order), guest orders included, as the definitions put guest orders in sales and
  orders; an identified-customers-only reading is possible but not the defined one.
- **R-H37** "customers who ordered in November 2025 who had already bought from us in an earlier
  month" is the official repeat-customer metric (1,469); counting postage- or cancellation-only
  orders would give 1,476 or 1,519.
- **Item codes that differ only in letter case** (R-L30, R-H09, R-H29, R-H39). The export has 170
  such pairs (e.g. 85123A / 85123a, same item name). The truth keeps codes as recorded, as the
  definitions and the warehouse do; top-5 membership is the same either way, but an agent that merges
  the pairs is 0.45–2.5% off on one row. Reported with the labels if it happens.
- **Key column names.** The grader needs the key column named as in the bank (`sku` for R-L30, R-H09,
  R-H29, R-H39; the questions say "item code"). Same rule as E1/E1b; a right answer under another
  column name would be a "grader?" case in the sensitivity analysis.
- **R-L31** "per active customer across October to December 2024" counts each customer once over the
  quarter (period rule): 1,153.26, not the mean of monthly ratios (781.00).
- **Related items, all kept** (none asks the same quantity for the same period and slice): R-H11
  (ratio) and R-H32 (difference) of the same two average order values; R-H31 is the June row of R-H12;
  R-H16 is the November row of R-H36; R-H22 and R-L03 are rows of R-H38; R-L23 is the sum of three
  R-H20 rows; R-L40's shares come from R-H21's two values; R-L32 (dropped) and R-L34 share Q1 2025
  monthly totals. The bootstrap treats questions as independent, so intervals are slightly optimistic.

## Checks behind the review (both passes)

`line_type` recomputed from the DEFINITIONS rule order matches `truth_lines` on all 1,033,036 rows;
`amount = qty × unit_price` everywhere; every cancellation line is negative (so `ABS(amount)` and
`-amount` agree); `country` is never NULL and `market = 'UK'` exactly when country is United
Kingdom; no order spans two months, markets or buyers; one buyer appears in both markets, with only an
adjustment line outside the UK.

## Frozen

`bank.yaml` sha256 `7bf2f08884b8529c2122ead801a1a58a6e91ecede72f557abae568e7d3cefb3e` (74 live of 80).
