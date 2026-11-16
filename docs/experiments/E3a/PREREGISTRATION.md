# E3a pre-registration: analytics Q&A on real data (UCI Online Retail II)

**Final**, 2026-11-07 03:27 (CST, file time), before the first evaluation attempt (03:28). Drafted in the night;
the decision rules were confirmed after the E1b report (its D6 was not met, so the harness did not go
back first) and the "Frozen inputs" table was filled when the bank was frozen. Any change from here
goes to `DEVIATIONS.md`. Design and reasons: [DESIGN.md](DESIGN.md).

## Question

On real data whose defects nobody planted and whose truth nobody wrote down, does the harness lower
the share of wrong answers delivered as trustworthy, without holding too many right ones?

## Agents and arms

Same agents as E1/E1b: Claude Code (Sonnet 5.5) and Codex (gpt-6-luna, high), same sandbox, k = 3.

| arm | what the agent has | delivered means |
|---|---|---|
| N | DuckDB warehouse (`bronze_safe`, `staging`, `silver.order_lines`) + data catalog with the official definitions + Python with duckdb | the agent claims success |
| H | harness v0.3 analyst tools over the same warehouse, with the retail semantic model | the final answer equals a VERIFIED / ALLOW submission, byte for byte or as numbers (12 significant digits; v0.3 S2, new since E1b) |
| N-orig (control, exploratory) | as N, on the original column names and dates (`world_original.py`) | as N |

## Data and truth

Disguised copy of UCI Online Retail II (CC BY 4.0; +14 years, renamed columns; `build_world.py`,
sha256 in the local manifest). Truth = the independent build B (Luna, from the written definitions
only) after line-by-line and metric-by-metric adjudication against the governed build A
(`compare_truth.py`, `ADJUDICATION.md`). Question authors write their truth SQL against B.

## Questions

Luna and Haiku with [author_brief.md](author_brief.md), no check code; `build_bank.py` drops repeats
(same truth) mechanically; the designer reviews every truth SQL (REVIEW.md); DIRECT / DERIVED
classified blind by Haiku with the E1 classifier (`--semantic experiments/E3a/context/semantic_model.yaml`)
before any attempt. Two author rounds with the identical brief gave 80 questions (4 same-truth repeats).
**Every question that passes the designer's review is kept**; the draft said "about 45", and the larger
bank was chosen on 2026-11-07 before the review verdicts and before any attempt, for power (E1b's
intervals at 47 questions were wide for one agent).

## Grading, metrics, labels

As E1: same grader and tolerance (0.05% relative or half a unit in the last reported place;
share ↔ percent accepted), key spellings from the world's aliases (market, country; English and
Chinese). M1 silent-error rate, M2 false-hold rate, M3 cost per correct delivered answer, M4 pass^3,
accuracy; 95% intervals from a cluster bootstrap over questions (2,000 resamples, seed 20261106),
paired between arms. Designer labels every wrong attempt (E1 guide); Haiku labels 30% blind; κ.

## Decision rules (fixed 2026-11-07 after the E1b report, unchanged from the draft)

| id | rule | meaning |
|---|---|---|
| R1 | pooled M1(H) ≤ 0.8 × M1(N), upper end of the 95% interval of the paired difference below 0, **and** M2(H) ≤ 15% | the gate holds up on real data |
| R2 | M2(H) > 15% | too strict on real data |
| R3 | on DIRECT questions, M1(H) < M1(N) and the upper end of the paired interval below 0 | what the gate is built for survives real data |

Secondary (pre-registered, not a decision rule): every metric by question form, DIRECT / DERIVED with
one governed step / DERIVED with no governed metric (the classifier's `metric` is empty). In E1b
about half of arm H's false holds (38 of 74) came from the last group, which the gate cannot verify
by design, and 18 more from questions the classifier put in the first group but no governed metric
covers ([E1b report](../E1b/REPORT.md)); this split shows how much of M2 is the gate's coverage, not
its strictness. The census rule change (S2) is reported beside the byte-for-byte count, so E3a's M2
can be compared with E1b's.

Exploratory, never a decision: N vs N-orig (what remembered conventions are worth); onboarding time
(designer hours from raw file to a frozen semantic model, [ONBOARDING.md](../../../experiments/E3a/ONBOARDING.md)).

## Frozen inputs

Filled 2026-11-07 03:27 (CST, file time), before the first evaluation attempt (03:28).

| input | value |
|---|---|
| harness v0.3 | applied and frozen about 03:00; file digests and patch sha256 in [H_v0.3_PLAN.md](../H_v0.3_PLAN.md) (573 tests) |
| truth | build B, identical to build A on every line and metric ([ADJUDICATION.md](../../../experiments/E3a/ADJUDICATION.md)); `truth_lines.parquet` sha256 `3103c8cdcbc775f6058fdaac7175036eca19fc86f71eae8361accfd3a88f9b12` |
| question bank | `experiments/E3a/questions/bank.yaml`, sha256 `7bf2f08884b8529c2122ead801a1a58a6e91ecede72f557abae568e7d3cefb3e` |
| questions | 74 kept of 80 written (Luna 37, Haiku 37; English 62, Chinese 12); 4 same-truth repeats and 2 two-way wordings dropped, 5 truths fixed (rounding) ([REVIEW.md](../../../experiments/E3a/questions/REVIEW.md)) |
| DIRECT / DERIVED | 24 / 50, of which 44 one step on a governed metric and 6 with none (`experiments/E3a/questions/answer_form.json`, sha256 `dc01af88826fd42d682559c043758bc31527260bcd95a265fbd7a50fdaa8dc10`) |
| control bank (N-orig) | `experiments/E3a/questions/bank_control.yaml`, sha256 `73813590d657f97a43e1740c8a0de149c1a39f57f517f89aa7bb0d11637e5831` (same questions with the original years; truths checked) |
| attempts | main: 74 × 2 arms (N, H) × 2 agents × 3 = 888; control: 74 × 2 agents × 3 = 444, run after the main run |
