# E1b question review (designer, before any evaluation attempt)

Two author rounds with the E1 brief (Luna → C-L, Haiku → C-H; round 2 numbers from 21) gave 74
questions. `build_bank.py` ran every truth SQL on the evaluator database and dropped repeats
mechanically; the designer then read every remaining truth SQL against the official definitions
in the data catalog. `freeze_bank.py` applies the decisions below and writes `bank.yaml`.

**Result: 47 questions**; 25 dropped as repeats, 2 dropped for the period rule, 1 truth fixed.

| | count |
|---|---|
| written | 74 (round 1: 37, round 2: 37) |
| dropped: same truth as an E1 question | 17 |
| dropped: same truth as an earlier E1b question | 8 |
| dropped: period rule | 2 |
| **kept** | **47** (Haiku 29, Luna 18; English 39, Chinese 8) |

## Changes

| qid | change | reason |
|---|---|---|
| C-L37 | truth fixed | "Count each enrolment": the author divided churned enrolments by distinct customers (1,856) instead of enrolments (2,256); 400 enrolments are repeat enrolments of customers already enrolled in an earlier campaign month. Now churned enrolments / enrolments per arm (control 0.0568, treatment 0.0469) |
| C-H36 | dropped | months 2026-03..2026-06 break the brief's period rule (last month 2026-05) |
| C-H37 | dropped | same |

Mechanical drops (same keys and same values as an earlier item, also after share ↔ percent or fen ↔
CNY):

| qid | same truth as | qid | same truth as | qid | same truth as |
|---|---|---|---|---|---|
| C-L02 | B-H20 | C-H07 | B-H07 | C-L33 | C-L13 |
| C-L03 | B-L03 | C-H16 | B-H19 | C-L35 | C-L15 |
| C-L05 | B-L06 | C-H17 | B-L18 | C-L36 | C-L16 |
| C-L07 | B-L08 | C-L21 | B-L01 | C-H24 | B-H05 |
| C-L09 | B-L10 | C-L22 | B-L02 | C-H26 | C-L06 |
| C-L10 | B-L11 | C-L25 | C-L04 | C-H27 | C-L27 |
| C-L12 | B-L12 | C-L29 | B-L10 | C-H29 | B-L10 |
| C-H02 | B-L12 | C-L30 | C-H11 | C-H35 | C-L15 |
| | | C-L32 | B-L12 | | |

## Checks that passed for every kept question

- Population: non-test customers; `at_risk` = active at the start of the month and non-test; churn
  rate = churned / at_risk; churned customers only among at_risk (0 churned rows outside it).
- Month-end activity = `active_at_start` of the next month (C-L26, C-H23). Recomputed from
  `silver.subscriptions` in the agents' warehouse with `started_at < month start` and
  `ended_at` null or ≥ month start: identical counts by plan.
- Net revenue from `truth_revenue` (already non-test, net of refunds, refunds in the month issued).
- ITT rates over all enrolments by arm (C-L37 after the fix).
- Usage totals (C-H40: every non-test customer row of March 2026, including sign-ups during the
  month): identical to `silver.usage_monthly` joined to the latest subscription plan.
- Sign-up shares (C-L31): identical to `silver.customers.created_at` in January 2026.
- No truth SQL rounds its result (E1 had to unround five).
- Period rule: 2025-01..2026-05, usage questions to 2026-04.

## Related items kept

The repeat rule compares truths, not wording, so some kept items share a template: C-L23 (churned
customers in April 2026) and C-H38 (the same by plan); C-L26 and C-H23 (subscribers at month end by
plan, February 2026 and December 2025); C-H33 and C-H34 (tickets and minutes per subscriber-month by
channel). They are separate items with separate answers; the cluster bootstrap treats them as
independent, which makes the intervals slightly too narrow if agents fail them together.
