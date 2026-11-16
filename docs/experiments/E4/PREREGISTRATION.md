# E4 pre-registration: fresh questions on the shapes E3a held, harness v0.5

**Final**, 2026-11-07 09:02 (CST), before the first evaluation attempt. A blind review (GPT-6-Luna, plan and
code) found three points, all addressed before the freeze: an attempt without a second opinion now counts as not
delivered (it had been left out of R4's denominators); agreement on raw rows is stated as a limit, with the
alias-normalised reading reported beside it; the frozen inputs below are filled. Any change from here goes to
[DEVIATIONS.md](DEVIATIONS.md).

## Question

E3a held 26.5% of arm H's correct answers on real data (M2), almost all of them on four question shapes the
harness could not certify: ratios of two metrics, growth between periods, rankings and shares by item code or
customer type, and wordings the semantic model's terms misread. Harness v0.5 adds those shapes
([H_v0.5_PLAN.md](../H_v0.5_PLAN.md)). On **fresh** questions of the same shapes, written by models that saw
neither the check code nor the v0.5 list:

1. does the gate now hold few enough right answers (M2 ≤ 15%) while still delivering fewer wrong ones than
   an agent alone (M1)?
2. does the gate plus a second opinion from another agent (T2, H v0.4) deliver as safely as two agents
   without the harness, for fewer agent runs?

## Agents and arms

Same agents and sandbox as E1–E3a: Claude Code (Sonnet 5.5) and Codex (gpt-6-luna, high), k = 3.

| arm | what the agent has | delivered means |
|---|---|---|
| N | DuckDB warehouse (`bronze_safe`, `staging`, `silver.order_lines`) + data catalog with the official definitions + Python with duckdb | the agent claims success |
| H | harness **v0.5** analyst tools over the same warehouse, with the E4 retail semantic model | the final answer equals a VERIFIED / ALLOW submission, byte for byte or as numbers (S2) |

Two delivery rules are computed from the same attempts, with no extra runs (H v0.4 T2f,
`census.second_opinions`):

| rule | delivered means |
|---|---|
| N + pair (two agents, no harness) | the agent claims success and the other agent's arm-N answer to the same question agrees |
| H + t2n (gate, then a second opinion) | VERIFIED / ALLOW, or else the final answer's certificate allows a second opinion (static check and re-execution PASS, reconcile not FAIL or ERROR, not FLAGGED) and the other agent's arm-N answer agrees |

"Agrees" is `compare_answers` (H v0.4 T2a) on the raw rows: same columns ignoring case, rows paired by key,
values within 0.05% or half a unit in the coarser last place, whole numbers equal. Each attempt's delivery is
the share of the other agent's three arm-N attempts that agree (the expected value over one random second
opinion). An attempt without a second opinion (the other agent has no arm-N attempt at the question) is not
delivered by it, and such attempts are counted. In production the second opinion is a fresh blind run; here the
other agent's arm-N attempt stands in for it, which is the same model on the same warehouse without the harness.

Agreement is measured as the harness would check it: raw rows, no grader aliases. Two right answers under
different key spellings (`sku` and `item_code`, `UK` and `United Kingdom`) do not agree, for both rules alike;
B1 names the key columns in both arms, which makes this rarer than in E3a. The grader's alias-normalised reading
of agreement is reported beside it (exploratory), so the size of this effect is visible.

## Benchmark changes decided before E4 (H_v0.5_PLAN.md B1, B2)

- **B1** (both arms): the question ends with "(Answer table: one row per sku, with the key column "sku" and
  the number in a column named "value". Give numbers unrounded, at full precision.)", naming the bank's key
  columns (`census.answer_table_note`). In E3a 24 of arm N's 35 "errors" were right values under another key
  name, rounded below 3 significant digits, or keyed by quarter.
- **B2** (grader): other spellings of a key column from the E4 model's dimension names (item code, stock code,
  product code, 商品编码, … → `sku`; customer type → `customer_type`), and a month written `2025-Q1` read as the
  quarter's months on a question not keyed by month. Key values use the model's reviewed aliases (market,
  country, customer type). On the E3a records, B2 turns exactly the 22 attempts the E3a report labelled
  "right value, wrong format" (15 N, 7 H) from wrong to right and no other attempt either way.

## Data and truth

The E3a warehouse: the disguised copy of UCI Online Retail II (CC BY 4.0; +14 years, renamed columns), same
source export, pipeline and governed build (`2025-12-09_436febc0`) in a workspace of its own
(`experiments/E4/world.py`). Truth = the adjudicated independent build B, as in E3a. Only the semantic model
differs ([`experiments/E4/context/semantic_model.yaml`](../../../experiments/E4/context/semantic_model.yaml)):
item code and customer type as dimensions, the round-2 term lists (O1–O3), frozen before the bank was written.
Its term coverage on 160 fresh paraphrases: 69% right, 7% another metric, 24% none
([TERM_COVERAGE.md](TERM_COVERAGE.md)).

## Questions

Luna and Haiku, two rounds each, with [author_brief.md](author_brief.md): the E3a brief with the mix moved to
the shapes E3a held (4 official metrics, 4 ratios, 4 period comparisons, 3 rankings, 2 shares, 3 on
cancellations or new / repeat customers; 4 of 20 in Chinese), the customer-type and item definitions, and
fixed key names. The authors see the definitions and the truth table's columns, never the warehouse, the check
code or the v0.5 list. `build_bank.py` drops mechanically a question whose truth equals an earlier E4 truth
**or any E3a truth** (the E3a questions are the ones v0.5 was fixed on). The designer reviews every truth SQL,
an independent second pass re-checks them ([REVIEW.md](../../../experiments/E4/questions/REVIEW.md)), and
`freeze_bank.py` removes every ROUND() from the truths. DIRECT / DERIVED classified blind by Haiku with the E1
classifier and the E4 model before any attempt.

**Result:** 80 written, 68 kept (Luna 35, Haiku 33; English 52, Chinese 16). Dropped: 6 with the truth of an E3a
question, 4 repeats within E4, 2 two-way wordings; 2 truths fixed (a cross join, a wrong base period) and 14
with ROUND removed. Classification: 17 DIRECT, 50 DERIVED with one governed step, 1 DERIVED with none.
A smoke run of two dropped questions outside the bank (top 5 item codes, a market share; 8 attempts) was
correct in every attempt, VERIFIED in all four arm-H attempts.

The bank is weighted toward the shapes v0.5 targets, so its M2 is not an estimate for questions at large; E3a's
mix is the reference for that.

## Grading, metrics, labels

As E3a, with B1 and B2: same tolerance (0.05% relative or half a unit in the last reported place; share ↔
percent accepted). M1 silent-error rate, M2 false-hold rate, M3 cost per correct delivered answer, M4 pass^3,
accuracy; 95% intervals from a cluster bootstrap over questions (2,000 resamples, seed 20261106), paired between
arms. Designer labels every wrong attempt (E1 guide); Haiku labels 30% blind; κ. The designer gives every
correct-but-held arm-H attempt a root cause, as E3a's `false_holds.py`.

## Decision rules

| id | rule | meaning |
|---|---|---|
| R1 | pooled M1(H) ≤ 0.8 × M1(N), upper end of the 95% interval of the paired difference below 0, **and** M2(H) ≤ 15% | the gate holds up on the shapes E3a held |
| R2 | M2(H) > 15% | still too strict on those shapes |
| R3 | on DIRECT questions, M1(H) < M1(N) and the upper end of the paired interval below 0 | as E3a |
| R4 | M1(H + t2n) − M1(N + pair): upper end of the paired 95% interval ≤ +1 point, **and** M2(H + t2n) ≤ M2(N + pair), **and** runs per attempt of H + t2n < 2 | gate + second opinion is as safe as two agents and holds no more, for fewer runs |

R1–R3 are E3a's rules on harness v0.5. If M1(N) is 0, R1 cannot be met and is reported as such. R4 uses the
other agent's arm-N attempts for both rules, so the comparison is paired on the same second opinions.

Secondary (pre-registered, not decision rules): every metric by agent and by question form (DIRECT /
DERIVED with one governed step / DERIVED with none); arm H's false holds by root cause, with the share caused by
term misreadings set beside the O3 estimate (7% of fresh questions resolve to another metric); M2(H) beside
E3a's 26.5% (different banks, descriptive only); H + t2n and N + pair by agent and form, with their runs per
correct delivered answer.

Sensitivity (pre-registered): every decision quantity without the later member of each nested pair (F-L39,
F-H39, F-H30; [REVIEW.md](../../../experiments/E4/questions/REVIEW.md)) and without the two questions closest to
E3a (F-H36, F-H37).

Exploratory, never a decision: N + pair with the grader's reading of agreement (key aliases, as the E3a probe's
"cross"); attempts where `compare_answers` and the grader disagree.

## Frozen inputs

Filled 2026-11-07 09:02 (CST), before the first evaluation attempt.

| input | value |
|---|---|
| harness v0.5 | applied to `src/` 2026-11-07 08:47 from `experiments/H_v05/harness_v05.patch` (sha256 `ff9f5c2ed058a72b…`); harness file digests in [H_v0.5_PLAN.md](../H_v0.5_PLAN.md); full suite 615 pass |
| census (B1, B2, T2f) | `src/dsharness/bench/census.py` `836b3f97918cce6e9134560388ba9832925b6d474435cb6c2c4a200600eae617`, `live.py` `ffae3aadaca2b6869c70a107e50acec511bfdd16d5c43c29d547728684e84ec7`, `experiments/E1/run_census.py` `788ff1e1419bc7eb3e7a7f85af3a12ae8de7fff77986bcceec6cb71574d2d981` |
| analysis | `experiments/E1/analyze.py` (`--rules e4`) `21f29991afb6c8acbbc95818cc95929329ea3a0f3cf09e14973a1c5c27247f08` |
| world | `experiments/E4/world.py` `249968c8a60cdc8742d97a090eaf1c0bcf075aec67753ba265faa2578177e0dd`; governed build `2025-12-09_436febc0` (as E3a) |
| E4 semantic model | `experiments/E4/context/semantic_model.yaml` `ade457dfab4d9d132b84286b6c29459eb96364819167176987a234247b63f8a7` |
| truth | build B, as E3a: `truth_lines.parquet` `3103c8cdcbc775f6058fdaac7175036eca19fc86f71eae8361accfd3a88f9b12` |
| author brief | `docs/experiments/E4/author_brief.md` `919ddc20a21be96e2a8027cf2e84c40ab5982422c2bc7b9c4d2c91269dc777a1` |
| question bank | `experiments/E4/questions/bank.yaml` `1c426f5477d5d59a8d80e08942f6d4727e3900f294a2dc203bb99f4e4d889670` (68 live of 80) |
| DIRECT / DERIVED | `experiments/E4/questions/answer_form.json` `19858bcdc8abcd72a89596cdc3e77ec2cf62e88e7d56fd3bb80e97afab2b8b25` (17 / 50 + 1) |
| attempts | 68 questions × 2 arms (N, H) × 2 agents × 3 = 816 |
