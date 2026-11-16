# E3a review pass H (R-H01 .. R-H40)

Reviewer: Claude subagent, 2026-11-07. Every truth SQL in this set was re-run read-only on
`evaluator.duckdb`. Alternative readings were tested on the same database, and the grader's
`value_ok` (`src/dsharness/bench/census.py`) was used to check the rounded truths.

Facts checked once and used below:
- No cancellation line has a positive or zero amount, so `-amount` and `ABS(amount)` agree.
- `country` is never NULL.
- No order spans two months or two markets.
- No buyer has product lines in both markets in the same month.

Rule applied to `ROUND()`: FIX when a correctly computed answer reported to 4 or more significant
digits can fail the grader against the rounded truth, or when the rounding uses at least 90% of the
0.05% tolerance. Otherwise the verdict is OK and the rounding is mentioned in the reason.

| qid | verdict | reason (one sentence with the key numbers) |
|---|---|---|
| R-H01 | DROP | Same truth as R-L01: UK gross product sales Feb 2025 = 417,876.17 (R-L21 is the same too). |
| R-H02 | OK | Net = product + cancellation amounts by market, Q3 2024 as one period: UK 1,820,644.81, non-UK 298,383.26; ROUND(2) on GBP sums is harmless. |
| R-H03 | FIX | ROUND(4) gives 0.0186 against the exact 0.0185936 (0.035% off), so a correct answer of 0.01859 fails the grader; the exact value is 0.0185935503. |
| R-H04 | OK | Non-UK Q1 2025 gross / distinct product orders = 937.504 (ROUND(2) gives 937.50, which is harmless). |
| R-H05 | OK | Units = SUM(qty) of product lines, 12 monthly rows for 2024 (Jan 390,408 … Dec 357,523). |
| R-H06 | OK | Distinct identified buyers with a product line in Sep 2025: UK 1,140, non-UK 119; no buyer is in both markets, so the rows add up to 1,259. |
| R-H07 | FIX | ROUND(2) breaks the small months: June 2025 truth −0.32 vs exact −0.3183 (0.52% off), so a correct −0.3183 fails; LAG before the filter (Jan vs Dec 2024) is right. |
| R-H08 | OK | UK share of gross Jan–Nov 2025 = 0.840894 (ROUND(4) is 0.0007% off, harmless). 英国客户 is read as the UK market including guest orders; an identified-customers-only reading would give 0.7088 or 0.8195. |
| R-H09 | OK | Top-5 skus by gross 2024: 22423 184,128.54, 85123A 146,441.97, 85099B 83,727.18, 84879 66,745.29, 22086 51,923.44 (6th: 49,488). Stored codes are kept case-sensitive; merging 85123a/85099b would give 148,732.37 and 85,838.82. |
| R-H10 | OK | Guest share of gross Jun–Aug 2025: UK 0.148029, non-UK 0.022797. ROUND(4) is 0.020% / 0.013% off and every exact report passes; removing it is optional. |
| R-H11 | FIX | The question asks for 3 decimals, but the rounded truth 0.568 is 0.052% from the exact 0.568294, so an agent that reports 0.5683 fails; the unrounded truth still accepts 0.568 through the half-unit rule. |
| R-H12 | OK | Running total of monthly net sales from Jan 2024 (Dec 2023 excluded): Jan 604,805.57 … Jun 3,794,777.36 … Dec 9,135,190.90. |
| R-H13 | OK | Per month, identified-customer gross / distinct identified customers: 761.41, 584.27, 599.33. "each month" points to monthly rows, but a single-number reading is a risk (see least sure). |
| R-H14 | FIX | ROUND(2) gives 3.01 against the exact 3.00795 (0.068% off), so a correct 3.008 fails; the exact value is 3.0079523331. |
| R-H15 | FIX | ROUND(4): UK 0.0203 vs exact 0.0203097 (0.048% off, 95% of the tolerance used); exact values are UK 0.0203097 and non-UK 0.0476945. |
| R-H16 | OK | Identified buyers whose first product month is 2024-11: 326. Each one is active in Nov 2024 by construction, so this equals the official new customers. |
| R-H17 | OK | Active identified buyers in Mar 2025 with a product line in any earlier month = 794. |
| R-H18 | OK | Non-UK gross by month, Q4 2024: 150,491.75, 183,940.75, 70,405.51. "non-UK customers" is read as the market slice including guest orders. |
| R-H19 | OK | UK cancellation lines in Aug 2025 as a positive amount = 17,271.37. |
| R-H20 | OK | Distinct product orders per month × market, H1 2025, 12 rows (Jan UK 967 / non-UK 114 … Jun 1,379 / 146). |
| R-H21 | OK | Gross by market, Q3 2025: UK 2,031,795.37, non-UK 408,859.98. |
| R-H22 | OK | Distinct orders with a product line in Mar 2025 = 1,440. |
| R-H23 | OK | UK AOV Nov 2024 = gross / distinct product orders = 497.25, guest orders included (identified customers only would give 412.58). |
| R-H24 | DROP | Same truth as R-H05 (units per month in 2024). |
| R-H25 | OK | Active identified customers per month, H1 2025: 739, 757, 973, 853, 1,054, 990. |
| R-H26 | OK | Non-UK cancellation rate June 2025 = 0.0137885 (unrounded). |
| R-H27 | OK | Gross Nov 2025 vs Nov 2024 = +1.5781% (unrounded, percent as asked). |
| R-H28 | OK | Non-UK share of gross in 2024 = 0.140759 (unrounded). |
| R-H29 | OK | Top-5 skus by gross Jan–Nov 2025: 22423 140,467.66, 47566 97,319.01, 85123A 91,800.98, 85099B 87,913.76, 23166 81,448.21 (6th: 57,242). Merging 85123a would give 92,795.48. |
| R-H30 | OK | UK identified-customer gross / active UK customers, Nov 2025 = 643.97, matching the wording on both sides. |
| R-H31 | OK | Net Jan–Jun 2024 = 3,794,777.36. This is the 2024-06 row of R-H12 (see possible repeats). |
| R-H32 | OK | UK AOV 473.856 − non-UK AOV 833.822 = −359.966 over Jan–Nov 2025. These are the same two AOVs as R-H11 (see possible repeats). |
| R-H33 | OK | Top-3 countries by gross, Q1 2025: United Kingdom 1,535,039.99, Netherlands 71,634.56, EIRE 52,066.10 (4th: Australia 40,937); no NULL countries. |
| R-H34 | OK | Guest share of gross Jan–Nov 2025 = 0.135147 (unrounded). |
| R-H35 | OK | Cancelled value by market, Apr–Jun 2025 as one total: UK 50,987.26, non-UK 4,990.87. The wording "in April, May and June" could invite a per-month split, which would fail on keys (see least sure). |
| R-H36 | OK | New customers per month in 2024, 12 rows (368, 375, 441, … 326 in Nov, 76 in Dec). |
| R-H37 | OK | 1,469 buyers with a product line in Nov 2025 and an earlier product line (the official repeat customers). Counting any Nov 2025 line would give 1,476 (fee/adjustment-only orders) or 1,519 (also cancellation-only). |
| R-H38 | OK | Distinct product orders per month, Jan–Nov 2025: 1,081 … 1,440 (Mar) … 2,751 (Nov). |
| R-H39 | OK | Top-5 skus by units Jan–Nov 2025: 23166 77,826, 84077 48,389, 22197 47,944, 85099B 45,115, 85123A 33,074 (6th: 84879 32,669). Merging 85123a would give 33,224. |
| R-H40 | OK | Cancellation rate Jan–Nov 2025 by market, from period totals: UK 0.034349, non-UK 0.020186 (unrounded). |

Counts: OK 33, FIX 5 (R-H03, R-H07, R-H11, R-H14, R-H15), DROP 2 (R-H01, R-H24).

## FIX R-H03

The only change is removing ROUND(4).

```sql
SELECT -SUM(CASE WHEN line_type = 'cancellation' THEN amount END)::DOUBLE
       / SUM(CASE WHEN line_type = 'product' THEN amount END)::DOUBLE AS value
FROM truth_lines
WHERE month = '2025-06';
```

Result: `value = 0.0185935503083428`. The old truth was 0.0186; a correct 0.01859 failed against it.

## FIX R-H07

The only change is removing ROUND(2).

```sql
WITH m AS (
  SELECT month, SUM(amount)::DOUBLE AS g
  FROM truth_lines
  WHERE line_type = 'product'
  GROUP BY month
),
w AS (
  SELECT month, g, LAG(g) OVER (ORDER BY month) AS prev
  FROM m
)
SELECT month, 100 * (g - prev) / prev AS value
FROM w
WHERE month BETWEEN '2025-01' AND '2025-11'
ORDER BY month;
```

| month | value |
|---|---|
| 2025-01 | -13.565498599659763 |
| 2025-02 | -24.252355059689467 |
| 2025-03 | 35.82729621223159 |
| 2025-04 | -25.279333592080324 |
| 2025-05 | 43.57378231749397 |
| 2025-06 | -0.31833008780002653 |
| 2025-07 | -6.699693358760264 |
| 2025-08 | 5.239009366037629 |
| 2025-09 | 41.99370347914841 |
| 2025-10 | 7.293217303900804 |
| 2025-11 | 31.614090917759036 |

## FIX R-H11

The only change is removing ROUND(3). The question's "保留三位小数" can stay: the grader accepts
0.568 against the exact value through the half-unit rule.

```sql
WITH a AS (
  SELECT market,
         SUM(amount)::DOUBLE / COUNT(DISTINCT order_no) AS aov
  FROM truth_lines
  WHERE line_type = 'product' AND month BETWEEN '2025-01' AND '2025-11'
  GROUP BY market
)
SELECT (SELECT aov FROM a WHERE market = 'UK') / (SELECT aov FROM a WHERE market = 'non-UK') AS value;
```

Result: `value = 0.5682943150690939` (UK AOV 473.8564, non-UK AOV 833.8222).

## FIX R-H14

The only change is removing ROUND(2).

```sql
WITH y AS (
  SELECT substr(month, 1, 4) AS yr, SUM(amount)::DOUBLE AS g
  FROM truth_lines
  WHERE line_type = 'product'
    AND ((month BETWEEN '2024-01' AND '2024-11') OR (month BETWEEN '2025-01' AND '2025-11'))
  GROUP BY yr
)
SELECT 100 * ((SELECT g FROM y WHERE yr = '2025') - (SELECT g FROM y WHERE yr = '2024'))
       / (SELECT g FROM y WHERE yr = '2024') AS value;
```

Result: `value = 3.0079523331323665`. The old truth was 3.01; a correct 3.008 failed against it.

## FIX R-H15

The only change is removing ROUND(4).

```sql
SELECT market,
       -SUM(CASE WHEN line_type = 'cancellation' THEN amount END)::DOUBLE
       / SUM(CASE WHEN line_type = 'product' THEN amount END)::DOUBLE AS value
FROM truth_lines
WHERE month BETWEEN '2024-10' AND '2024-12'
GROUP BY market
ORDER BY market;
```

| market | value |
|---|---|
| UK | 0.020309693411597426 |
| non-UK | 0.04769450872461308 |

## Possible repeats

| pair | what is shared | recommendation |
|---|---|---|
| R-H11 / R-H32 | The same two AOVs (UK 473.856, non-UK 833.822, Jan–Nov 2025); R-H11 asks for the ratio, R-H32 for the difference | Closest to a reworded repeat: one result decides the other. Drop R-H32 (the later one) unless correlated items are acceptable. |
| R-H12 / R-H31 | R-H31 (net Jan–Jun 2024 = 3,794,777.36) is exactly the 2024-06 row of R-H12 | Keep both: one is a running total, the other a single period total. Drop R-H31 if items must be independent. |
| R-H16 / R-H36 | R-H16 (new customers Nov 2024 = 326) is the 2024-11 row of R-H36 | Keep both (one number vs a 12-row table), noting the dependence. |
| R-H22 / R-H38 / R-H20 | Orders Mar 2025 = 1,440 is the 2025-03 row of R-H38 and the sum of R-H20's March rows (1,302 + 138) | Keep, noting the dependence. |
| R-L03 / R-H38 | Orders Nov 2025 = 2,751 is the 2025-11 row of R-H38 | Keep, noting the dependence. |
| R-H20 / R-L23 | R-L23 (non-UK orders Apr–Jun 2025 = 383) equals 87 + 150 + 146 from R-H20, because no order spans months | Keep, noting the dependence. |
| R-L40 / R-H21 | R-L40's Q3 2025 market percentages are R-H21's two GBP values divided by their sum | Keep: the quantities differ. |

## Other notes for the designer

- **Case-variant skus (R-H09, R-H29, R-H39).**
  - The data has 170 pairs of skus that differ only by letter case. The top-5 lists include two: 85123a and 85099b, with the same item names as 85123A and 85099B.
  - The truth keeps the stored codes separate, as the definitions and the warehouse do; top-5 membership is the same either way.
  - The grader lowercases key values, so an agent that merges the pairs still matches the keys, but its value is 0.45–2.5% off.
  - If wording edits are still allowed, add "item codes exactly as recorded".
- **Key column name (R-H09, R-H29).** The grader requires a column named `sku`, but the questions say "item code". An agent that names its column `item_code` fails on format. This is a harness issue, not a truth issue.
- **The grader's 3-significant-digit floor.** In R-H07, an agent that reports −0.32 or −6.7 (2 significant digits) fails against the exact truth. This is the pre-registered rule, mentioned so it is not mistaken for a truth error.
