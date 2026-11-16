# Brief for request authors (E3b v2 blind request set)

You are writing an evaluation set for testing AI data scientists. Each item is a request that someone in a company
sends to a data scientist. The data scientists will get the company's order data, described below. They must reply
in writing, with numbers where numbers fit, and hand over files where the request asks for them. You do not answer
the requests, and you do not see the data. You write only the requests.

## The company and the data

The company is an online gift wholesaler based in the UK. Many of its customers are small shops that buy for resale.
The data scientists receive this description of the data, word for word:

> Warehouse `warehouse.duckdb`, one certified table. Money is GBP. The data ends on 2025-08-31.
>
> **silver.order_lines**: one row per distinct order line, from December 2023 to 31 August 2025.
> - Only line_type = 'product' lines are product sales. Cancellations are separate negative lines (order numbers
>   starting with C) that do not name the order they cancel.
> - About 23% of lines have no buyer_id (guest orders); they are never customers.
>
> | column | description |
> |---|---|
> | order_no | Order number. Prefix C = cancellation, A = adjustment / bad debt. |
> | sku | Item code. Catalogue items start with five digits; postage, fees and similar codes are not products. |
> | item_name | Item description. |
> | qty | Quantity; negative on cancellations and some zero-value corrections. |
> | ordered_at | Order time as recorded. |
> | unit_price | Unit price, GBP. |
> | buyer_id | Customer number; NULL for guest orders. |
> | country | Customer country. |
> | amount | qty x unit_price, GBP. |
> | market | UK or non-UK. |
> | line_type | product, cancellation, fee, adjustment, voucher, other_nonproduct or zero_or_negative. |

## What to write

Write **20 requests**, as different from each other as you can, that need a predictive model or a forecast. Every
request must be about what customers or the business will do **in the weeks or months after the data ends, up to
three months** (September to November 2025). It may also ask how good such predictions are, or what to do with
them.

Write them the way real people in a company write to their data scientist:
- The requesters vary: a sales director, a CRM or retention manager, finance, a marketing manager, a key-account
  manager, operations or stock planning, a board member, an investor-relations lead, a data-science lead.
- The purposes vary: who to call or mail next, a retention budget, a revenue or volume forecast, a stock plan,
  a board slide, a claim someone already made in a meeting, checking a vendor's model, or deciding whether a model
  is good enough to use.
- The wording varies: precise or vague; short, or with background; neutral, or assuming an answer the requester
  hopes for.
- Some ask for a scored list or a file. Others ask for one number, a forecast with a range, a recommendation, a
  comparison or a yes/no.
- Some may ask for something the data cannot fully answer. Write those as the requester would, and do not mark them.
- If a request needs a business input the data does not hold, such as the cost of a call, a discount or a margin,
  the requester states it in the request, as people do.
- Each request is at most 80 words, in English, and makes sense on its own. Do not refer to other requests.

Do not write SQL, answers or hints about the right method. Do not mention that this is a test of data scientists.

## Output

Reply with ONLY a YAML list, nothing before or after it:

```yaml
- id: R01
  requester: "<role>"
  request: "<the request, in the requester's words>"
- id: R02
  ...
```
