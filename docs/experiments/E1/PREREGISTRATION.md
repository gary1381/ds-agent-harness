# E1 pre-registration: real-agent error census and harness A/B

Written 2026-11-06, before any agent attempted an evaluation question. Smoke runs before this
point used only sandbox-mechanics prompts. Any change made after the first evaluation attempt is
logged in [DEVIATIONS.md](DEVIATIONS.md) with date and reason.

## Questions

- **Q1 (descriptive).** What errors do current coding agents make on business-analytics questions
  over a warehouse with realistic defects, and how often do they claim success when wrong?
- **Q2 (pilot A/B).** Does the DS Harness lower the rate of wrong answers delivered as trustworthy,
  and at what false-hold rate and cost?

## Agents

| id | CLI | model |
|---|---|---|
| `claude` | Claude Code 2.1.296, print mode | `--model sonnet` (Sonnet 5.5) |
| `codex` | Codex CLI 0.162.0-alpha.17.2, `exec` | `gpt-6-luna`, reasoning effort high |

A local open-weight agent may be added in week 2 as a third agent. It is reported separately and
does not enter the decision rules.

**Isolation.** Each attempt runs in a fresh bubblewrap sandbox (`src/dsharness/bench/sandbox.py`)
with a fresh home and its own working directory. Agents cannot read the repository, the custody
truth, past transcripts, or the user's instructions, hooks, plugins and memories. Web search is
off. Timeout: 1,200 s per attempt. At most 2 attempts run at once.

## Arms

- **N, no harness, good documentation.** The working directory holds `warehouse.duckdb` and
  `DATA_CATALOG.md`.
  - `warehouse.duckdb` is a copy of the certified score build (cutoff 2026-06-30) with the schemas
    `bronze_safe`, `staging` and `silver`. The PII schema is removed.
  - `DATA_CATALOG.md` is rendered from `context/catalog.yaml` and the metric descriptions in
    `context/semantic_model.yaml`: table and column documentation, caveats, data rules and official
    metric definitions.
  - Python with duckdb and pandas is available. Claude's tools are Bash, Read, Write, Edit, Glob
    and Grep; Codex has its shell.
  - Delivered means the agent reports `claimed_success = true`.
- **H, harness.** The agent has only the 7 tools of the analyst MCP server, served from outside
  the sandbox.
  - Delivered means the final answer's rows equal the rows of a `submit_answer` call that returned
    VERIFIED / ALLOW.
  - Also reported: **H-claimed** (delivered means `claimed_success = true`), which separates the
    effect of context and tools from the effect of the gate.

N gets as text the same knowledge that H gets as tools. The comparison is therefore conservative:
the harness against good documentation, not against nothing.

## Question sets

- **S, seen (6).** The designer-written eval questions A1–A6. The designer knew these when the
  checks were built, so they are reported separately and are not used for the decision rules.
- **B, blind (up to 40).** GPT-6-Luna and Haiku 5.5 write 20 questions each, working from
  [author_brief.md](author_brief.md), in the sandbox, without harness code or the defect list.
  - The truth is the author's SQL over the custody tables.
  - Before any agent run, the designer reviews each question and its SQL against the official
    definitions. Every fix is logged in `experiments/E1/questions/REVIEW.md`.
  - A question is dropped only if its truth SQL cannot be fixed to match the question, it is
    ambiguous and no wording fix resolves it, or it breaks the period rule.

k = 3 attempts per (agent, arm, question).

## Grading (fixed now)

The answer rows are read as {key: value}. Keys must equal the truth keys exactly. A value is
correct if

- |got − truth| ≤ max(0.0005·|truth|, half a unit in the last decimal place the agent reported),
  and
- the reported number has at least 3 significant digits, unless it equals the truth exactly.

A share given as a percent (×100) or a percent given as a share is accepted: that is format, not
substance. Fen given as CNY (or the reverse) is not accepted; it is labelled as a unit error.

An attempt with no parseable final JSON, a timeout or a CLI error is wrong and not delivered.

## Metrics (per agent × arm, and pooled over agents)

| | metric | definition |
|---|---|---|
| M1 | silent error rate | #(wrong ∧ delivered) / #attempts |
| M2 | false-hold rate | #(correct ∧ not delivered) / #correct |
| M3 | cost per correct delivered answer | Claude: total_cost_usd (API-equivalent) / #(correct ∧ delivered). Codex: tokens per correct delivered answer (USD unknown on a subscription) |
| M4 | reliability | pass^3 = share of questions with 3/3 correct; also pass@1 |

Also reported: accuracy, abstention rate (`claimed_success = false`) and wall time.

Uncertainty: 95% intervals from a cluster bootstrap over questions (2,000 resamples), paired
between arms because both arms answer the same questions.

## Error taxonomy (fixed list)

One primary label per wrong attempt:

| label | meaning |
|---|---|
| E-def | used a different metric definition than the official one |
| E-test | test accounts not excluded |
| E-refund | refunds handled wrong (gross vs net, wrong month) |
| E-dup | duplicates counted, or legitimate identical rows removed |
| E-unit | unit error (fen vs CNY, ×100) |
| E-time | wrong period, month boundary, as-of date or timezone |
| E-late | incomplete (late-arriving) data treated as complete |
| E-src | wrong source (raw or staging instead of certified), other than the above |
| E-join | join fan-out or grain error |
| E-calc | aggregation or arithmetic error (e.g. average of averages) |
| E-read | misread the question (answered a different slice or quantity) |
| E-fmt | answer shape (missing keys, extra rows, wrong value column) |
| E-proc | process failure (timeout, no final JSON, tool error, gave up) |
| E-other | none of the above |

The designer labels every wrong attempt from its transcript. Haiku 5.5 labels a random 30% blind
(question, truth, answer, transcript excerpt). Agreement is reported as Cohen's κ. A table, fixed
before the runs in `experiments/E1/coverage.md`, records which harness check (if any) is meant to
catch each label.

## Decision rules (fixed now)

| rule | condition | consequence |
|---|---|---|
| D1 | pooled M1(H) ≤ 0.8 × M1(N), with the upper end of the 95% interval of the paired difference below 0, **and** M2(H) ≤ 15% | continue the harness as a product line |
| D2 | M1(N) < 2% | agents with good docs are rarely wrong-and-confident. The answer gate has little value; focus moves to the benchmark and world generator (step 4) |
| D3 | more than 50% of wrong attempts in N fall in labels no current check covers | build checks for those labels before any product work |
| D4 | M2(H) > 15% | the harness is too strict for real use; fix coverage (for example governed metrics for derived questions) before claiming value |

All four are reported whatever the outcome.

**Pilot caveat.** 46 questions × 3 attempts is small. With about 10% silent errors in N, the 95%
interval of the difference is roughly ±5 percentage points, so the pilot can detect only large
effects. If it is inconclusive, the next step is more blind questions, not a reinterpretation.
