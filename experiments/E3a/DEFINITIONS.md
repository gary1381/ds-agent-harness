# E3a official definitions (owner text)

The business rules of the E3a retail world, written before any question exists. They are the only
spec for the independent truth build (B) and the question authors, and they become the metric
definitions in the arm-N catalog and the arm-H semantic model. Column names are those of the
disguised copy (`experiments/E3a/build_world.py`).

The company is an online gift wholesaler; money is in GBP; `ordered_at` is the time recorded on the
order line, with no time zone, and a line's month is the calendar month of `ordered_at`. The data
runs from 2023-12-01 to 2025-12-09: December 2025 is a partial month and no question uses it.

## Lines

`raw_lines` has one row per order line: `order_no`, `sku`, `item_name`, `qty`, `ordered_at`,
`unit_price`, `buyer_id`, `country`. A line's value is `qty * unit_price`.

1. **Exact duplicates.** Rows equal in all eight columns are one line exported twice; count it once.
   (Owner decision: the data cannot tell an export duplicate from two identical scans.)
2. **Line type.** Each line has exactly one type; the first rule that matches wins:

   | order | type | rule |
   |---|---|---|
   | 1 | adjustment | `order_no` starts with `A`, or upper(`sku`) is `M` (manual), `ADJUST`, `ADJUST2`, `D` (discount), `S` (samples) or `B` (bad debt) |
   | 2 | fee | upper(`sku`) is `POST`, `DOT`, `C2` (carriage), `AMAZONFEE`, `BANK CHARGES`, `CRUK` (commission) or `PADS` |
   | 3 | voucher | lower(`sku`) starts with `gift` |
   | 4 | other_nonproduct | `sku` does not start with five digits (e.g. `DCGS…` gift-shop codes, `SP…`, `TEST001` test products) |
   | 5 | cancellation | `order_no` starts with `C` |
   | 6 | zero_or_negative | `unit_price` ≤ 0 or `qty` ≤ 0 |
   | 7 | product | everything else |

   Only catalogue items (`sku` starting with five digits) are products. A cancellation line is a
   cancelled catalogue item; it is booked in the month of the cancellation line, not of the
   original order.
3. **Customers.** `buyer_id` identifies a customer; lines without one (guest orders, ~23% of lines)
   count in money and orders but never as a customer.
4. **Market.** `UK` when `country` is `United Kingdom`, otherwise `non-UK`.

## Metrics (by month; optional breakdown by market or country)

| metric | definition |
|---|---|
| gross product sales (GBP) | sum of value over product lines |
| cancelled value (GBP) | minus the sum of value over cancellation lines (a positive amount) |
| net product sales (GBP) | sum of value over product and cancellation lines (gross product sales − cancelled value) |
| cancellation rate | cancelled value / gross product sales of the same month and slice (0-1) |
| orders | distinct `order_no` with at least one product line |
| average order value (GBP) | gross product sales / orders |
| units sold | sum of `qty` over product lines |
| active customers | distinct `buyer_id` (not null) with at least one product line in the month |
| new customers | active customers whose first product line in the whole data is in this month. The data starts in 2023-12, so every customer of 2023-12 counts as new: use from 2024-01 on |
| repeat customers | active customers with a product line in an earlier month |

A month or slice with no product lines has no cancellation rate or average order value (not 0).
A total over several months is the sum of the monthly values for sums and counts of lines; for
distinct counts (orders, customers) it is the distinct count over the whole period, and rates and
averages are recomputed from the period's totals, never averaged over months.

Fees, vouchers, adjustments, zero-value lines and other non-product lines are kept in the data and
in no metric above.
