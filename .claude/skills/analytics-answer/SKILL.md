---
name: analytics-answer
description: Answer business questions (net revenue, refunds, add-on units, active subscribers, churn, ARPU, CSAT, sign-ups, retention-program enrolment and ITT churn by arm) from the DS Harness warehouse through the dsh-analyst MCP tools, get every reported number verified with submit_answer, and report its tier, trust label (T0-T3) and certificate. Use whenever a user asks for a business number, a trend or a breakdown.
---

# Analytics answer (dsh-analyst)

You work on one governed data build. Governed metrics are the official definitions; raw SQL
is a fallback whose results are uncertified until `submit_answer` says otherwise.

## Procedure

1. `list_metrics` — read the metric list, the build's month coverage and the worked examples.
2. Map the question to a metric. `describe_metric` for its dimensions, allowed filter values
   and caveats. Write down the claim: `{metric, dimensions, months: [first, last], filters}`.
   The claim must match the question exactly (see "The claim is the question" below).
3. `query_metric` with exactly that claim. Keep the returned rows unchanged (full precision).
4. Only if no metric fits: `search_catalog` / `describe_table`, then `run_sql` over certified
   `silver.*` tables. Read the warnings it returns and fix the query instead of ignoring them.
5. `submit_answer` with the question verbatim, the rows you will report, the SQL (if you used
   any) and `metric_call` whenever the answer is a governed quantity.
6. Report the tier honestly:
   - VERIFIED: the declared claim reconciles with the governed metric (and the Finance close
     for revenue). You may present it as the official number.
   - CONSISTENT: reproducible, but no oracle covers it, or the declared `metric_call`
     contradicts the question (the reasons and `checks.reconcile.contradictions` say which).
     Say "exploratory, needs review"; a contradiction goes to human review (gate HOLD).
   - FLAGGED: wrong, unsafe or unreproducible. Do not present the number; fix it or say so.
7. Report the trust label next to every number, with its `allowed_use`:
   - T0: governed AND reconciled exactly to the Finance close, every month closed. The only label
     fit for board / regulator / external use, and even then a named person signs off.
   - T1: governed but provisional (no independent record for the metric, or a month not closed
     yet). Internal KPIs and dashboards only, marked "provisional"; it is re-scored at close.
   - T3: exploratory (CONSISTENT). Never in a KPI or a report; list the assumptions.
   - no label (FLAGGED): do not use the number.
   Never upgrade a label in prose ("this is basically official"). T2 (cross-checked) is not
   issued by this harness.
8. Attach or link the answer certificate (`certificate` in the result: question, metric_call,
   digests, build, check outcomes with evidence ids, tier, trust label, caveats). Fill in its
   `harness`, `model` and `cost` fields if your runtime knows them; never edit the other fields.

## The claim is the question

`submit_answer` checks that the declared `metric_call` answers the question as asked, not just
that the rows reconcile with it. It reads the question only, never your `claims`. Declare:

- the metric the question names (a churn RATE question is `churn_rate`, not `churned_customers`);
- months that contain every month the question names (a wider range is fine; other months are not);
- for every dimension value the question names ("east region", "pro plan", "billing tickets",
  "refunds"), a filter on exactly those values, or a breakdown by that dimension;
- no filter the question does not ask for;
- a breakdown (`dimensions`) for every "by / per / for each / in each <dimension>".

A mismatch makes the answer CONSISTENT / T3 with gate HOLD (human review), however well the
numbers reconcile. Fix the claim to match the question; never reword the question, drop it to
an undeclared answer, or add claims to get past the check. If you believe the flagged reading is
right (the question is phrased unusually), report HOLD and the contradiction; a human confirms it.

## Decision rules

- A governed metric covers the question -> use `query_metric`; never re-derive it with SQL.
- Different grain than the metric (e.g. one quarterly average) -> certified SQL, expect CONSISTENT.
  Never average monthly averages or monthly rates; compute over the whole period.
- The months must lie inside the build's coverage; the latest month can still change.
- A FLAGGED or HOLD result is information, not an obstacle: do not resubmit the same numbers
  under a different wording or without the `metric_call` to dodge reconciliation.
- Rows must fit their own claim: one row per (month, declared dimensions), a `value` column.
  Rows that do not fit the declared `metric_call` FAIL ("answer does not match its declared claim").
- Retention program (2026-03..06): use `program_enrolled`, `program_churned`, `program_churn_rate`
  by `arm` (intent-to-treat). Never group by `exposed`. A rate gap is not an effect until a
  two-proportion test on the counts says so.

## Pitfalls (known defect classes in raw data)

- Export resends: the same invoice line can appear more than once. Deduplicate by the invoice id
  only.
- Look-alike purchases: two add-ons with the same customer, day and amount are two real units.
  Deduplicating on (customer, day, amount) undercounts.
- Unit incidents: one region's charges in one month were exported in yuan instead of fen.
  Raw sums are silently 100x too small there.
- Refunds are separate negative rows booked in the month of the refund. Summing charges only
  overstates revenue.
- Internal QA accounts look like customers. Every count, sum or average must exclude test accounts
  (`NOT is_test_account`, via `silver.customers`).
- Late-arriving usage and late-known cancellations: the newest month of a build is incomplete.
- Current-state columns (current price, end date, cancel reason) are not history. Use the
  invoices or the price book for past prices.
- Personal data (e-mail, phone, birth year) and raw `bronze.*` tables are off limits. The
  analyzer refuses them.

## Checklist

- [ ] Metric chosen from `list_metrics`, caveats read with `describe_metric`
- [ ] Months inside the coverage window; filters use allowed values
- [ ] Rows reported exactly as returned (month, dimensions, value), nothing rounded
- [ ] Test accounts excluded, refunds included, no deduplication beyond the invoice id
- [ ] `submit_answer` called with the question verbatim and a `metric_call` that matches it
      exactly: metric, months, named values as filters, every "by X" as a dimension (and the SQL if any)
- [ ] Tier stated truthfully in the final reply, with the reasons for anything not VERIFIED
- [ ] Trust label (T0/T1/T3 or none) and its allowed use stated; provisional months named
- [ ] Certificate attached; only `harness`, `model`, `cost` filled in by you
