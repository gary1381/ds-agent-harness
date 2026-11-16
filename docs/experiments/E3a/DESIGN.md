# E3a design: analytics Q&A on real data (UCI Online Retail II)

Draft, 2026-11-06 (night), written while E1b runs. The pre-registration follows once E1b has reported,
because the harness version tested here depends on the E1b result (rule D6 would send v0.2 back
first). Nothing in `src/` changes while E1b runs: arm H starts a fresh harness server from this
checkout for every attempt.

## Question

On real data whose defects nobody planted and whose truth nobody wrote down, does the harness
lower the share of wrong answers delivered as trustworthy, without holding too many right ones?

## What is different from the synthetic world

| | synthetic world (E1, E1b) | Online Retail II |
|---|---|---|
| defects | planted by the designer (test accounts, refunds, duplicates, late rows, …) | real: cancellations (invoice prefix C), negative quantities, postage / fee / adjustment / voucher codes, exact duplicate lines, ~23% of lines without a customer id, zero prices, bad-debt invoices (prefix A) |
| truth | the simulator's own tables | none: built twice, independently (below) |
| agents may know it | no | yes: a well-known Kaggle dataset, with notebooks that mostly "drop cancellations and null customers", which differs from the official definitions here |

## Arms and agents

Same agents as E1 (Claude Code with Sonnet 5.5, Codex with gpt-6-luna high), same sandbox, k = 3.

| arm | what the agent has | delivered means |
|---|---|---|
| N | DuckDB warehouse: the raw lines (`bronze_safe`) and the governed lines (`silver.order_lines`, with `line_type`, `amount`, `month`, `market`) + a data catalog with the official metric definitions + Python with duckdb | the agent claims success |
| H | the harness analyst tools over the same warehouse, with a retail semantic model | VERIFIED / ALLOW |

As in E1, arm N gets the governed table and the definitions, so the comparison is "documented
tables" against "governed metrics plus a gate", not "raw data" against "harness".

## Disguise (training-data control)

Main runs use a disguised copy: every timestamp shifted by +14 years (2009-12..2011-12 becomes
2023-12..2025-12, day by day; no 29 February in the source years), and the columns renamed
(Invoice → `order_no`, StockCode → `sku`, Customer ID → `buyer_id`, …). Product descriptions,
countries and prices stay; the data stays real. The catalog does not name the source.

Control: the original names and dates, arm N only, same questions, k = 3. Arm N is where
remembered conventions would show; the gate does not depend on them. Exploratory.
`experiments/E3a/world_original.py` derives it from the main world (arm N's warehouse renamed and
re-dated, the catalog text rewritten, the evaluator's truth 14 years earlier);
`make_control_bank.py` moves the years in the frozen bank and checks that every control truth equals
the main truth with its months moved back. Smoke-tested on a stand-in truth (A's own lines).

## Truth: two independent builds

1. **A (ours):** `silver.order_lines` from `experiments/E3a/context/pipeline` (adapted from `examples/online_retail`; exact duplicates
   collapsed; `line_type` = product, cancellation, fee, adjustment, voucher, other_nonproduct,
   zero_or_negative) and the retail semantic model.
2. **B (independent):** Luna, in a fresh sandbox, gets the written definitions and the raw lines,
   not our pipeline SQL, and builds `truth_lines` with the same columns.
3. A and B are compared line by line and metric by metric for every month. Each disagreement is
   read against the written definition by the designer: the wrong side is fixed and the case is
   logged. Definitions the data cannot settle (are exact duplicates export errors or two scans?)
   are owner decisions, written into the catalog before any question exists.
4. The question authors write their truth SQL against B (after adjudication), never against A, so a
   grader truth never comes from the harness's own pipeline.

## Questions

Same procedure as E1b: Luna and Haiku with a retail version of the brief (business context, the
official definitions, the truth-table schema; no check code), two rounds if needed, mechanical drop
of repeats, designer review of every truth SQL, frozen bank with sha256, DIRECT / DERIVED classified
blind before any attempt. Target: about 45 questions, a quarter in Chinese.

Planned governed metrics (the authors see the definitions, not this list's SQL): net product sales
(GBP), gross product sales, cancelled value, cancellation rate (cancelled / gross product value),
orders (invoices with at least one product line), average order value, units sold, active
identified customers, new customers (first identified purchase in the month), repeat customers.
Dimensions: month, market (UK / non-UK), country.

## Harness work needed (after E1b ends)

Status 2026-11-06 23:50: done in a scratch copy and saved as `experiments/E3a/harness_v03.patch`
(558 tests pass after the smoke-run fixes, see [H_v0.3_PLAN.md](../H_v0.3_PLAN.md)); the retail workspace builds READY and answers verify. What it took and what broke:
[ONBOARDING.md](../../../experiments/E3a/ONBOARDING.md). Truth build B (`run_truth_b.py`) and the A/B
comparison (`compare_truth.py`, checked on A against itself: 0 differences) wait for Luna to be free.

| step | change | why |
|---|---|---|
| 1 | load the semantic model and catalog from the workspace (`<ws>/context/`), default unchanged | today one model is built in |
| 2 | governed-term patterns come from the semantic model (`terms:` per metric); the current subscription patterns move into the default model | `claim_contradictions` knows only churn / revenue words |
| 3 | finance-ledger check optional per model (retail has no ledger, answers cap at T1) | today the check assumes a closed-revenue table |
| 4 | census `World` config: prompts, warehouse copy, evaluator database, key aliases, period rule | `census.py` assumes the subscription world |
| 5 | every v0.2 regression test still passes on the synthetic world | the change must not move E1b's behaviour |

The harness version tested is v0.2 plus these changes (call it v0.3), frozen with digests before
the questions are written, as for E1b.

**Onboarding time is measured**: wall-clock and designer hours from raw file to a frozen
semantic model, catalog and contracts. For a company buying the product this is one of the first
questions, and it has no number yet.

## Rules to pre-register (draft)

The E1 rules D1 and D4 on the disguised main run, plus a rule for the DIRECT questions, where the
gate has done what it was built for so far: on real data, DIRECT M1(H) must stay below M1(N).
Final wording once E1b has reported.

## Licence and data handling

UCI Online Retail II is CC BY 4.0 (attribution: Daqing Chen, UCI Machine Learning Repository, dataset 502).
Data is never committed; the download script records sha256 and the download date. No personal
data: customer numbers only.
