# Brief for question authors (E1 blind question set)

You are writing an evaluation set of business questions for testing AI data analysts. The analysts
will later get access to this company's data warehouse and must answer each question with numbers.
You do not see the warehouse; you see the company's official metric definitions and a set of
**ground-truth tables** that only the evaluator has. For every question you write, you also write
the SQL that computes the correct answer from the ground-truth tables.

## The company

A subscription video service ("the company"), billing in CNY. Money is stored in **fen**
(1 CNY = 100 fen). Months are calendar months in UTC, written 'YYYY-MM'.

- Customers sign up, hold one subscription on one plan (basic, plus, pro), and may cancel (churn).
  Plans never change for a customer.
- Each customer has a sales region (north, south, east, west) and an acquisition channel
  (organic, paid_search, referral).
- Revenue comes from monthly plan charges, add-on packs (one-off purchases) and refunds (negative).
- Customers use the product (minutes watched) and open support tickets.
- About 1% of customers are internal QA test accounts. They are never part of any business number.
- From 2026-03 to 2026-06 the company ran a randomised retention program: each month about 20% of
  eligible subscribers were enrolled and split 50/50 into a treatment arm (shown a retention message)
  and a control arm. Not every treatment customer actually saw the message ("exposed").

## Official metric definitions (business language)

- **Net revenue** (month): charges + add-ons − refunds booked in the month, excluding test accounts.
  Refunds are booked in the month they are issued.
- **Active subscribers at the end of a month**: distinct non-test customers whose subscription is
  active at the end of the month (equivalently, active at the start of the next month).
- **Active subscribers at the start of a month**: distinct non-test customers active at the start of
  the month.
- **Churned customers** (month): non-test customers active at the start of the month whose
  subscription ended during the month.
- **Churn rate** (month): churned customers / active subscribers at the start of the month (a share
  between 0 and 1).
- **ARPU** (month): net revenue of the month / active subscribers at the start of the month (fen).
- **Retention program churn rate** (campaign month, by arm): share of enrolled customers who churned
  in the campaign month, by randomised arm, counted whether or not they saw the message
  (intent-to-treat). The program's effect is the control rate minus the treatment rate.

## Ground-truth tables (DuckDB; only the evaluator has them)

`truth_customer_month` — one row per customer per month in which the customer had a live
subscription at any time (2025-01 .. 2026-09).

| column | meaning |
|---|---|
| customer_id | customer key |
| month | 'YYYY-MM' |
| active_at_start | true if the subscription was active at the start of the month (false for customers who signed up during the month) |
| at_risk | active_at_start AND NOT is_test (the churn-rate denominator population) |
| churned | true if the subscription ended during this month (only ever true when at_risk) |
| price_fen | monthly list price the customer was charged that month |
| minutes | minutes watched in the month |
| tickets | number of support tickets opened in the month |
| is_test | internal QA test account |

`truth_revenue` — net revenue by month and region, test accounts already excluded:
columns `month`, `region`, `net_revenue_fen` (2025-01 .. 2026-09).

`truth_program` — one row per retention-program enrolment (campaign months 2026-03 .. 2026-06):
columns `customer_id`, `campaign_month`, `arm` ('treatment' | 'control'), `exposed` (bool: saw the
message; always false in control), `churned` (bool: churned in the campaign month).

`dim_customer` — static customer attributes: `customer_id`, `region`, `channel`, `plan`,
`signup_month` ('YYYY-MM'), `is_test`.

## What to write

Write **20 questions**, as a demanding business stakeholder would ask them in chat or email: natural
wording, no table or column names, no SQL hints. Mix:

- 6 questions about the official metrics for specific months, quarters or slices (region, plan,
  channel);
- 8 questions that need a derived calculation not covered by a single official metric, e.g.
  month-over-month growth, a share or ratio, a comparison between segments, a top-N, an average per
  customer, a cumulative total;
- 3 questions about the retention program;
- 3 of the 20 written in Chinese (simplified), on any of the topics above.

Rules:

1. Periods: questions about minutes watched must concern months up to 2026-04. All other questions
   must concern months between 2025-01 and 2026-05.
2. Each question must have exactly one correct numeric answer (one number, or one number per row of a
   small table). If a reasonable reader could interpret it two ways, add the words that decide it
   (e.g. "including refunds", "as a share, not a percentage", "in CNY").
3. Say the unit when it matters (CNY or fen; share or percent).
4. The answer table: one row per combination of `keys` (dimension columns, e.g. `region` or
   `month`; empty for a single number) and one numeric column named `value`.
5. The truth SQL runs on DuckDB against the four tables above and returns exactly the key columns
   and `value`. It must implement the official definitions when the question uses an official
   metric (exclude test accounts everywhere).

## Output format

Reply with ONLY a YAML list, no prose before or after:

```yaml
- id: q01
  lang: en            # en | zh
  question: "..."
  keys: []            # e.g. [region] or [month, plan]
  unit: count         # count | fen | cny | share | percent | minutes | other
  months: ["2026-02", "2026-02"]   # first and last month the question covers
  truth_sql: |
    SELECT ... AS value FROM ...
  notes: "how you resolved any ambiguity"
```
