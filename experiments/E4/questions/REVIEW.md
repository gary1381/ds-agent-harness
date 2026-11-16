# E4 question review (designer, before any evaluation attempt)

Two author rounds with the identical brief ([author_brief.md](../../../docs/experiments/E4/author_brief.md);
Luna → F-L, Haiku → F-H; round 2 numbered from 21) gave 80 questions on 2026-11-07 08:35 (CST). `build_bank.py`
ran every truth SQL on the evaluator database (truth build B, as E3a) and dropped mechanically a question whose
truth equals an earlier E4 truth or any E3a truth. The designer read every truth SQL against the official
definitions; an independent second pass (a designer-briefed subagent, which re-ran all 69 truths and checked
the data the truths depend on) re-checked them, and each flagged item was decided by the designer.
`freeze_bank.py` applies the decisions below and writes `bank.yaml`.

**Result: 68 questions** (Luna 35, Haiku 33; English 52, Chinese 16; 36 single numbers, 32 tables keyed by
month 14, market 6, customer type 4, item code 4, country 4). Blind classification (Haiku, E1 classifier, E4
model): 17 DIRECT, 50 DERIVED with one governed step, 1 DERIVED with none.

| | count |
|---|---|
| written | 80 (round 1: 40, round 2: 40) |
| dropped: same truth as an E3a question | 6 (F-H13 = R-H29, F-H16 = R-H08, F-H19 = R-H37, F-L21 = R-L01, F-H29 = R-L18, F-H35 = R-H39) |
| dropped: same truth as an earlier E4 question | 4 (F-L32 = F-L11, F-L33 = F-L13, F-H28 = F-L08, F-H40 = F-L20) |
| dropped: two readings with different values (brief rule 2) | 2 (F-L11, F-L25) |
| truth fixed | 2 (logic) + 14 (ROUND removed) |
| **kept** | **68** |

## Changes

| qid | change | reason |
|---|---|---|
| F-L11 | dropped | "cumulative net product sales at the end of each month from January through June 2025" does not say where the running total starts (January 2025, or the start of the data), as E3a R-L32. F-L32 has the same wording problem and the same truth. F-H11 says "year to date" and stays |
| F-L25 | dropped | "net product sales per active customer in the UK": guest sales in the numerator (608.98, the ratio of the two official metrics) or only identified customers' sales. E3a kept such ratios only when the question says which (R-L11, R-L31, R-H13); F-L05 ("each active identified customer account for") and F-H25 ("guest orders left out of both") do |
| F-L07 | truth fixed | the CROSS JOIN counted every cancellation line once per product line: 300,029.79 GBP per order → 7.856 (cancelled value 11,407.26 GBP / 1,452 product orders in July 2025) |
| F-H31 | truth fixed | the 2024 count covered January–December 2024 (19,627 orders) instead of January–June (8,416): −59.03% → −4.444% |
| F-L31, F-H30 | `months` fixed | the truth reads the year-ago months as its base; `months` now starts at 2024-01, as F-L12 and F-H10 (metadata only: the grader's month check does not apply to month-keyed questions) |
| 14 truths | ROUND removed | F-H01, F-H04–F-H12, F-H14, F-H15, F-H17, F-H20. New in E4: every truth is exact, so a more precise right answer can never fail against a rounded truth. A question asking for two decimals is still graded with half a unit in the answer's last place |

## Kept after a second look

- **F-L23** "how many product units were sold to guests and identified customers" could be one combined total.
  B1 names the answer's key column (`customer_type`, one row per customer type), which settles it. The B1 note
  is appended for every question; on the frozen model it changes neither the terms nor the months the harness
  reads from any of the 68 questions (checked).
- **F-L05** divides identified customers' sales by identified customers ("each active identified customer
  account for"). The other reading, all gross sales over active customers, gives 702.01 instead of 625.47; the
  wording assigns the sales to the customers, so it is the first.
- **F-H37** "share of gross product sales from guest orders, separately for the UK and for non-UK markets" is
  each market's guest share of its own sales (UK 0.1306). Guest sales of each market over all sales (0.1087)
  is a weaker reading.
- **F-L35** top 5 item codes by units in Q1 2025: the first, 23166 (74,215 units), is one order cancelled 16
  minutes later. Units sold count product lines and cancellations are booked separately (definitions), so the
  truth stands; an agent that nets it off is wrong by the definitions and will be labelled.
- **"Customers in X"** (F-H02, F-H05, F-H20) is the market or country slice with guest
  orders included, as in E3a. F-H02 has no guest orders anyway.
- **Item codes** are kept as recorded (85123A and 85123a differ), as in E3a.
- **Related items, all kept** (none asks the same quantity for the same period and slice, the E3a rule):
  F-L39 ⊂ F-H39 ⊂ F-H18 (new customers by month over Jan–Mar, Jan–Jun, Jan–Nov 2025); F-H30 ⊂ F-L31 (year-on-
  year difference by month); F-L19 is F-H18's February row; F-L10 is F-L12's March row; F-L18 is F-H15's April
  row; F-L06 is F-L26's June row; F-L30's rows are F-H11's monthly steps; F-H21 is the sum of F-H01's rows.
  The bootstrap treats questions as independent, so intervals are slightly optimistic; the pre-registration
  adds a sensitivity analysis without the later member of each nested pair (F-L39, F-H39, F-H30).
- **Close to E3a, kept** (different truth, the mechanical rule): F-H36 (non-UK share of gross sales,
  January–November 2025) is the complement of E3a R-H08's UK share; F-H37 has the shape of E3a R-H10 (guest
  share by market) over other months. The test is fresh wording and fresh numbers on the shapes E3a held;
  these are the closest cases and are listed so a reader can set them aside.

## Checks behind the review (second pass)

All 69 truths re-run and reproduced; no product order spans two months, countries or buyers; no order mixes
guest and identified lines; no customer is active in both markets in a month; no tie at any ranking's cut-off
(checked to rank N+2); `month`, `market` and `amount` match their definitions; `line_type` matches the rule
order except 6 lines that are non-product either way; every cancellation line is negative.

## Frozen

`bank.yaml` sha256 `1c426f5477d5d59a8d80e08942f6d4727e3900f294a2dc203bb99f4e4d889670` (68 live of 80);
`answer_form.json` sha256 `19858bcdc8abcd72a89596cdc3e77ec2cf62e88e7d56fd3bb80e97afab2b8b25` (F-L25, dropped
after classification, removed).
