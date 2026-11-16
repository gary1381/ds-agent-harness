# Brief for question authors (E3a blind question set)

You are writing an evaluation set of business questions for testing AI data analysts. The analysts
will later get access to this company's data warehouse and must answer each question with numbers.
You do not see the warehouse; you see the company's official metric definitions and a
**ground-truth table** that only the evaluator has. For every question you write, you also write
the SQL that computes the correct answer from the ground-truth table.

## The company

An online gift wholesaler selling mostly to small shops, in the UK and abroad. Money is in GBP.
Months are calendar months of the order time, written 'YYYY-MM'. The data covers 2023-12-01 to
2025-12-09 (December 2025 is incomplete).

- An order (`order_no`) has one or more order lines: an item (`sku`), a quantity and a unit price.
- Orders whose number starts with `C` are cancellations of earlier items (negative quantities).
- Besides catalogue items, order lines can be postage and other fees, manual adjustments and
  discounts, gift vouchers, test products, or lines with zero or negative value. These are not
  product sales.
- Many orders are guest orders without a customer number; they count in sales and orders but never
  as customers.
- Some lines appear twice in the export, identical in every field; such a line counts once.

## Official definitions (business language)

Line types (the first rule that matches wins): **adjustment** (order number starts with `A`, or item
code M, ADJUST, ADJUST2, D, S, B); **fee** (item code POST, DOT, C2, AMAZONFEE, BANK CHARGES, CRUK,
PADS); **voucher** (item code starting with "gift"); **other non-product** (item code not starting
with five digits); **cancellation** (order number starts with `C`); **zero or negative** (unit price
≤ 0 or quantity ≤ 0); **product** (everything else). A line's value is quantity × unit price.

- **Gross product sales** (GBP): total value of product lines.
- **Cancelled value** (GBP): the value of cancellation lines, as a positive amount; booked in the
  month of the cancellation line.
- **Net product sales** (GBP): gross product sales − cancelled value.
- **Cancellation rate**: cancelled value / gross product sales, same month and slice (share 0-1).
- **Orders**: distinct order numbers with at least one product line.
- **Average order value** (GBP): gross product sales / orders.
- **Units sold**: total quantity of product lines.
- **Active customers**: distinct customer numbers with at least one product line in the month.
- **New customers**: active customers whose first product line in the whole data is in this month
  (meaningful from 2024-01; the data starts in 2023-12).
- **Repeat customers**: active customers who had a product line in an earlier month.
- **Market**: UK (country = United Kingdom) or non-UK.

Over several months, sums add up month by month; distinct counts (orders, customers) are counted
once over the whole period; rates and averages are recomputed from the period's totals, never
averaged over months.

## Ground-truth table (DuckDB; only the evaluator has it)

`truth_lines`: one row per distinct order line (duplicates already removed), 2023-12-01 ..
2025-12-09.

| column | meaning |
|---|---|
| order_no | order number (prefix C = cancellation, A = adjustment) |
| sku | item code |
| item_name | item description |
| qty | quantity (negative on cancellations) |
| ordered_at | order time (TIMESTAMP) |
| unit_price | unit price, GBP |
| buyer_id | customer number (BIGINT), NULL for guest orders |
| country | customer country |
| amount | qty × unit_price, GBP (DECIMAL) |
| month | 'YYYY-MM' of ordered_at |
| market | 'UK' or 'non-UK' |
| line_type | product, cancellation, fee, adjustment, voucher, other_nonproduct, zero_or_negative |

## What to write

Write **20 questions**, as a demanding business stakeholder would ask them in chat or email: natural
wording, no table or column names, no SQL hints. Mix:

- 6 questions about the official metrics for specific months, quarters or slices (market, country);
- 8 questions that need a derived calculation not covered by a single official metric, e.g.
  month-over-month growth, a share or ratio, a comparison between segments, a top-N, an average per
  customer, a cumulative total;
- 3 questions about cancellations, new or repeat customers;
- 3 of the 20 written in Chinese (simplified), on any of the topics above.

Rules:

1. Periods: every question concerns months between 2023-12 and 2025-11 (questions about new or
   repeat customers: from 2024-01).
2. Each question must have exactly one correct numeric answer (one number, or one number per row of a
   small table). If a reasonable reader could interpret it two ways, add the words that decide it
   (e.g. "net of cancellations", "as a share, not a percentage", "UK only").
3. Say the unit when it matters (GBP; share or percent).
4. The answer table: one row per combination of `keys` (dimension columns, e.g. `market` or
   `month`; empty for a single number) and one numeric column named `value`.
5. The truth SQL runs on DuckDB against `truth_lines` and returns exactly the key columns and
   `value`. It must implement the official definitions when the question uses an official metric.

## Output format

Reply with ONLY a YAML list, no prose before or after:

```yaml
- id: q01
  lang: en            # en | zh
  question: "..."
  keys: []            # e.g. [market] or [month, country]
  unit: gbp           # count | gbp | share | percent | other
  months: ["2025-02", "2025-02"]   # first and last month the question covers
  truth_sql: |
    SELECT ... AS value FROM ...
  notes: "how you resolved any ambiguity"
```
