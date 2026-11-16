# E3a truth review: pass L (R-L01 .. R-L40)

Reviewer: Claude subagent, 2026-11-07. Read-only on `evaluator.duckdb` (2 threads). Every truth SQL
was re-run. Each value matches the draft.

Data checks behind the verdicts:
- `line_type` recomputed from the DEFINITIONS rule order matches `truth_lines` on all 1,033,036 rows.
- `amount = qty * unit_price` on every row.
- All 17,914 cancellation lines are negative (max −0.12), so `ABS(amount)` and `-amount` give the same result.
- `country` is never NULL. `market` = 'UK' exactly when country = 'United Kingdom'.
- No order spans two months, two markets or two buyers.
- Only one buyer (12745) ever appears in both markets, and its only non-UK line is an adjustment.
- No R-L truth SQL uses ROUND.

| qid | verdict | reason (one sentence with the key numbers) |
|---|---|---|
| R-L01 | OK | UK gross product sales for Feb 2025 (product lines only) = 417,876.17 GBP. |
| R-L02 | OK | Non-UK Q1 2025 gross minus cancelled = 327,116.31 GBP. ABS on cancellations is harmless because every cancellation amount is negative. |
| R-L03 | OK | Distinct orders with a product line in Nov 2025 = 2,751. The same number is the 2025-11 row of the later R-H38. |
| R-L04 | OK | UK Jun 2024 AOV = 600,534.35 / 1,497 orders = 401.159 GBP. |
| R-L05 | OK | UK Mar 2025 units = SUM(qty) of product lines = 303,867. "销售总量…件" reads as gross units sold. |
| R-L06 | OK | Non-UK Oct 2025: 133 distinct non-NULL buyers with a product line. |
| R-L07 | OK | UK gross 417,876.17 (Feb) → 571,328.61 (Mar) = +36.722%, in percent as asked. |
| R-L08 | OK | Non-UK share of Jul 2025 gross = 111,070.43 / 688,144.79 = 0.16141, a share as asked. |
| R-L09 | OK | Aug 2025 AOV: UK 578,676.31 / 1,195 = 484.25; non-UK 145,520.45 / 144 = 1,010.56. R-L29 duplicates it. |
| R-L10 | OK | Top 5 for Sep 2025: United Kingdom 876,044.70, EIRE 42,323.70, Netherlands 26,832.26, France 21,898.04, Germany 16,549.47. 6th is Norway (8,398.94), so there are no ties. "Exclude rows without a country" changes nothing: no NULLs, and 'Unspecified' has only 284.30. |
| R-L11 | OK | May 2025: all gross including guests (739,914.35) / 1,054 identified active customers = 702.006 GBP. |
| R-L12 | OK | UK net product sales Jan–Jun 2025 = 3,067,828.12 GBP, one number as "是多少英镑" asks. |
| R-L13 | OK | Apr 2025: 307,647 units / 1,235 orders = 249.107 units per order. |
| R-L14 | OK | Q3 2024 shares of gross from period totals: UK 85.252%, non-UK 14.748%. |
| R-L15 | OK | UK Nov 2025 cancellation rate = 21,954.76 / 1,280,859.35 = 1.7141%. |
| R-L16 | OK | New customers Feb 2025: UK 109, non-UK 16. Their sum (125) equals all new customers that month, and the market split cannot vary because no product buyer spans markets. |
| R-L17 | OK | May 2025: 1,054 active customers, 943 of them with a product line in an earlier month (over the whole data). |
| R-L18 | OK | Gross 1,992,828.11 (Q2 2025) → 2,440,655.35 (Q3 2025) = +22.472%, all markets. |
| R-L19 | OK | Oct 2024: 1,117 repeat / 1,492 active = 74.866%. |
| R-L20 | OK | Non-UK Q4 2024: (404,838.01 − 19,308.55) / 506 orders counted once over the quarter = 761.916 GBP. |
| R-L21 | DROP | Same truth as R-L01 (UK gross Feb 2025 = 417,876.17), a reworded repeat. Dropped mechanically. |
| R-L22 | OK | Net product sales, all markets, Q1 2025 = 1,757,489.03 GBP. |
| R-L23 | OK | Non-UK Apr–Jun 2025: 383 distinct orders with a product line, each counted once. |
| R-L24 | OK | UK Jun 2025 AOV = 605,030.82 / 1,379 = 438.746 GBP. Different year from R-L04. |
| R-L25 | OK | Units sold Sep 2025: UK 477,310, non-UK 91,388. |
| R-L26 | OK | Country 'United Kingdom' (the same rows as market UK), Mar 2025: 879 active customers. |
| R-L27 | OK | UK net: Jan 455,706.93 (545,835.21 − 90,128.28) → Feb 412,887.66 (417,876.17 − 4,988.51) = −9.396%. The gross reading would give −23.44%, but the wording says net. |
| R-L28 | OK | Non-UK share of Q2 2025 gross = 0.15274, from quarter totals. |
| R-L29 | DROP | Same truth as R-L09 (AOV by market, Aug 2025), a reworded repeat. Dropped mechanically. |
| R-L30 | OK | Top 5 SKUs for Q3 2025: 22423 34,076.94; 47566 30,654.50; 85099B 25,756.60; 85123A 21,061.49; 23298 20,268.15. 6th is 84879 (17,978.22). Caveat: '85123a' (same item name, 404.43) is a separately stored code; merging it would give 21,465.92 (+1.9%). |
| R-L31 | OK | UK Q4 2024: net 2,835,856.01 / 2,459 distinct identified customers over the quarter = 1,153.256 GBP. An average of the monthly ratios would give 781.00, but the period rule decides. |
| R-L32 | OK | Running total from Jan 2025: 670,361.99; 1,178,145.41; 1,867,853.90. The start month is not stated; a since-data-start reading gives 10.84M / 11.35M / 12.04M (see note below). |
| R-L33 | OK | Jul 2025 units per order: UK 332,135 / 1,317 = 252.19; non-UK 67,128 / 135 = 497.24. |
| R-L34 | OK | Each month's share of Q1 2025 gross: Jan 35.889%, Feb 27.185%, Mar 36.925% (they sum to 100). |
| R-L35 | OK | Cancelled value Q4 2024, positive, in the cancellation month: Oct 25,451.07; Nov 35,098.98; Dec 17,547.86. |
| R-L36 | OK | Customers whose first product line is in 2025-01 = 72 (of 739 active that month). |
| R-L37 | OK | Apr 2025: 853 active customers, 747 with a product line in an earlier month. |
| R-L38 | OK | Cancelled value Oct 2025 41,613.03 − Sep 2025 16,980.64 = 24,632.39 GBP. |
| R-L39 | OK | Jun 2025: 108 new / 990 active = 10.909%. |
| R-L40 | OK | Q3 2025 shares of gross: UK 83.248%, non-UK 16.752%. |

Counts: OK 38, FIX 0, DROP 2 (both mechanical same-truth repeats).

## FIX sections

None. Every non-dropped truth SQL implements the official definitions for its wording.

## Notes on the closest calls (verdict OK)

- **R-L32**: "cumulative gross product sales by month through Q1 2025" does not name a start month.
  - I read it as a running total within Q1 2025 (year-to-date, the same as quarter-to-date here), which the truth computes.
  - The only other reading is a total since the data starts (2023-12), which gives 10,841,805.50 / 11,349,588.92 / 12,039,297.41.
  - I kept it because a total from an arbitrary export start is not a business quantity, and "by month through Q1 2025" sets Q1 2025 as the scope. R-H12, by contrast, says "starting from January 2024".
  - If the designer treats this as a rule-2 ambiguity, DROP it. No SQL fix is possible.
- **R-L30**: item code '85123a' (lower case, same item name "WHITE HANGING HEART T-LIGHT HOLDER") is stored separately from '85123A'.
  - The truth groups by the stored code. That is the literal reading of "item codes".
  - An agent that normalises case would report 21,465.92 for 85123A, which the grader would mark wrong. The ranking is the same either way.
  - The same variant affects R-H09 and R-H29, which are not in this pass.
- **R-L31**: the truth counts distinct customers once over the quarter (2,459), as the period rule says. The sum of monthly customers (3,674) or an average of monthly ratios (781.00) would differ.
- **R-L27 and R-L32/R-L34**: order 541431 (77,183.60 GBP, UK) and its cancellation C541433 both fall in Jan 2025.
  - This drives the net vs gross gap in R-L27 and inflates Jan 2025 gross in R-L32 and R-L34.
  - That is correct under the definitions, but it makes these questions sensitive to the net/gross distinction.

## Possible repeats

1. **R-L01 / R-H01 / R-L21**: identical quantity, period and slice (UK gross Feb 2025). Already dropped mechanically. Keep R-L01.
2. **R-L09 / R-L29**: identical (AOV by market, Aug 2025). Already dropped mechanically. Keep R-L09.
3. **R-L03 and R-H38**: R-L03 (orders Nov 2025 = 2,751) is the 2025-11 row of R-H38 (orders per month Jan–Nov 2025).
   - Likewise R-H22 (orders Mar 2025 = 1,440) is R-H38's 2025-03 row.
   - The periods differ, so this is not a repeat under the rule. Keep all three, but one monthly order-count mistake will be scored more than once.
4. **R-H16 and R-H36** (outside this pass, same pattern): new customers Nov 2024 (326) is a row of new customers per month in 2024. Keep both.
5. **R-L40 and R-H21**: R-L40's shares are R-H21's two GBP values (2,031,795.37 and 408,859.98) divided by their sum.
   - It is a different quantity that needs an extra derived step. Keep both.
6. **R-L32 and R-L34**: both are built from the same three Q1 2025 monthly gross totals (670,361.99; 507,783.42; 689,708.49), as running totals vs shares. Keep both.
7. **R-L07 and R-H07**: R-L07 is UK gross growth Feb→Mar 2025 (+36.72%); R-H07's 2025-03 row is the all-market figure (+35.83%). Different slice. Keep.
8. **R-L04 and R-L24**: UK AOV Jun 2024 vs Jun 2025. The text similarity is 0.86, but the year differs. Keep.
9. **R-L02 and R-L22**: net product sales Q1 2025, non-UK vs all markets. Different slice. Keep.
10. **R-L26 and R-H25**: active customers Mar 2025, UK (879) vs all markets (973, a row of R-H25). Different slice. Keep.
11. **R-L17, R-L37, R-H17, R-H37**: repeat customers in May, Apr, Mar and Nov 2025. Same metric, different months, so not repeats. Keep, but four questions test the same SQL pattern.
12. **R-L30, R-H29, R-H09**: top-5 SKUs by gross for Q3 2025, Jan–Nov 2025 and 2024. Different periods. Keep. All three include 85123A and so carry the '85123a' caveat.
