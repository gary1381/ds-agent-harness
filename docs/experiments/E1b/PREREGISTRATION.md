# E1b pre-registration: harness v0.2 on new blind questions

Written 2026-11-06, after E1 ([report](../E1/REPORT.md)) and before any E1b evaluation attempt. The
harness fixes were frozen before the E1b questions were written ([H_v0.2_PLAN.md](../H_v0.2_PLAN.md),
with file digests). Any change after the first evaluation attempt goes to [DEVIATIONS.md](DEVIATIONS.md).

## Questions

- **Q1.** On questions the harness designer has not seen, does harness v0.2 deliver fewer wrong
  answers as trustworthy than no harness, without holding too many correct ones?
- **Q2.** Did the v0.2 fixes help: v0.2 against v0.1 on the same new questions?

## Agents

Same as E1: Claude Code 2.1.296 with Sonnet 5.5 (`claude`); Codex CLI 0.162.0-alpha.17.2 with
gpt-6-luna, reasoning effort high (`codex`). Same sandbox, isolation, 1,200 s timeout, at most 2
attempts at once.

## Arms

| arm | what the agent has | delivered means |
|---|---|---|
| N | warehouse copy + `DATA_CATALOG.md` (byte-identical to E1's) + Python with duckdb | the agent claims success |
| H1 | the E1 harness, v0.1, served from a frozen source snapshot (`experiments/E1b/harness_v01/`, SHA256SUMS; analytics, semantic model and server files taken from commit 105f86b) | VERIFIED / ALLOW |
| H | harness v0.2 (this checkout; digests in H_v0.2_PLAN.md) | VERIFIED / ALLOW |

A smoke run (one designer question, not an evaluation item) confirmed that H1 records check version
1 and shows the v0.1 hint text, and H records check version 2.

## Questions (bank)

- Same brief as E1 ([author_brief.md](../E1/author_brief.md)), same two authors (Luna, Haiku), new
  runs in fresh sandboxes: round 1, and a second round with the identical brief because round 1 had
  many repeats of E1 items.
- Mechanical drop rule (`experiments/E1b/build_bank.py`): a question whose truth equals the truth of
  an E1 question or of an earlier E1b question (same keys; values equal, or equal after share ↔
  percent or fen ↔ CNY, which the grader treats alike) is the same item reworded and is dropped.
- The designer checks every remaining truth SQL against the official definitions
  ([REVIEW.md](../../../experiments/E1b/questions/REVIEW.md)) and freezes `bank.yaml`; its sha256 is
  recorded below before the first attempt.
- DIRECT / DERIVED: classified blind by Haiku with the E1 classifier (questions and metric list
  only), before any attempt. Unlike E1, the split is **pre-registered here as a secondary analysis**.

k = 3 attempts per (agent, arm, question).

## Grading, metrics, labels

Exactly as E1 ([PREREGISTRATION.md](../E1/PREREGISTRATION.md)): same grader and tolerance, M1 silent
error rate, M2 false-hold rate, M3 cost per correct delivered answer, M4 pass^3 per (agent,
question) cell, accuracy, abstention, H-claimed; 95% intervals from a cluster bootstrap over
questions (2,000 resamples), paired between arms. Infrastructure failures are retried, as in E1.

The grader is not changed even though E1 found 16 right answers it cannot read (0.200 sent as the
JSON number 0.2; period keys like `2026-Q1`). The same sensitivity analysis is reported.

Error labels: the designer labels every wrong attempt with the E1 guide
([LABEL_GUIDE.md](../../../experiments/E1/LABEL_GUIDE.md)), Haiku labels a random 30% blind, κ reported.

## Decision rules (fixed now)

| rule | condition | consequence |
|---|---|---|
| D1 | pooled M1(H) ≤ 0.8 × M1(N), upper end of the 95% interval of the paired difference H − N below 0, **and** M2(H) ≤ 15% | v0.2 earns the harness a product line |
| D4 | M2(H) > 15% | still too strict: more coverage before any value claim |
| D5 | upper end of the 95% interval of M1(H) − M1(H1) below 0, **and** M2(H) < M2(H1) | the v0.2 fixes worked out of sample |
| D6 | on DIRECT questions, M1(H) > M1(H1) + 5 points | the fixes broke what worked: revert before anything else |

All four are reported whatever the outcome. D1 and D4 are the E1 rules unchanged; D2 and D3 are not
repeated (they concern agents without the harness, which E1 answered).

**Power.** About 40 questions × 3: as in E1, only large effects are detectable. An inconclusive
result leads to more blind questions, not to a reinterpretation.

## Frozen inputs

Filled 2026-11-06 about 22:40 (CST), before the first evaluation attempt (the run started 22:46).

| input | value |
|---|---|
| question bank | `experiments/E1b/questions/bank.yaml`, sha256 `b5ebb247a1e5e1285e161c4d80a8cdd60d148e0cf041cfcd6b8b476a927305c9` |
| questions | 47 kept of 74 written (Haiku 29, Luna 18; English 39, Chinese 8); 25 dropped as repeats, 2 for the period rule, 1 truth fixed ([REVIEW.md](../../../experiments/E1b/questions/REVIEW.md)) |
| DIRECT / DERIVED | 15 / 32 (`experiments/E1b/questions/answer_form.json`, sha256 `96899137f53ad514b0024d5b17cab795e2b04df5dc779277eec00cd153a7c1e6`) |
| catalog for arm N | sha256 `f5d68fba9a4701f8d0aa5510199ecf3566a95677e0d5807f695ea47f4c5c9dcf`, identical to E1 |
| harness v0.2 | the six digests in [H_v0.2_PLAN.md](../H_v0.2_PLAN.md), re-checked unchanged |
| harness v0.1 snapshot | `experiments/E1b/harness_v01/SHA256SUMS` (58 files), re-checked |
| attempts | 47 × 3 arms × 2 agents × 3 = 846 |

**Descriptive breakdown, fixed now.** For 8 of the 32 DERIVED questions the classifier found no
governed metric (sign-ups, watch minutes, tickets, message exposure). Neither harness version has a
verified path for them, so a correct answer there can only be held: they add to M2(H) and M2(H1) by
design. The DERIVED results are also reported split into "one step on a governed metric" (24) and
"no governed metric" (8). This split does not enter the decision rules.
