# H v0.2: fixes for the harness failures seen in E1

Fixed 2026-11-06, **before the E1b questions were written**. The E1b bank is authored after this
list is frozen, so the fixes cannot be tailored to the questions that will test them. A fix added
after the E1b questions exist is logged in the E1b deviations with its reason.

E1 stays frozen: its results are reported under the pre-registered rules with H v0.1. The fixes
below were found by reading E1 arm-H attempts; every fix gets a regression test built from a real
E1 failure (question and agent submission), plus a test that the old correct behaviour still holds.

## What went wrong in arm H (E1, at 470 of 540 attempts)

The blind question bank splits into DIRECT questions (the answer is one governed-metric result,
unchanged; 19 of 45) and DERIVED questions (a total over months, a unit conversion, a share, a
difference, growth or a ranking; 26 of 45). The split was made by Haiku from the questions and the
metric list only (see `docs/experiments/E1/DEVIATIONS.md`).

| question form | arm | attempts | correct and delivered | correct, held or rejected | wrong and delivered (silent) | wrong, held |
|---|---|---|---|---|---|---|
| DIRECT | N | 96 | 73 | 0 | 23 | 0 |
| DIRECT | H | 93 | 86 | 5 | **0** | 2 |
| DERIVED | N | 140 | 112 | 0 | 25 | 3 |
| DERIVED | H | 135 | **0** | 41 | **56** | 39 |

On DIRECT questions the gate did what it was built for: no silent errors against 23 in N. On
DERIVED questions it failed in two opposite ways:

1. **VERIFIED on the wrong shape (56 silent errors).** The agent submitted the governed monthly
   rows (often in fen) where the question asked for one total in CNY, a share or a difference. The
   rows reconciled with the governed metric, the claim did not contradict the question's metric,
   months or filters, so the gate returned VERIFIED. The tool hint made this likely:
   `query_metric` ends with "Report these rows unchanged … and pass this metric_call to
   submit_answer". In many of these attempts the agent's own notes state the right total.
2. **No path to VERIFIED for a correct derived answer.** A correct share, average or difference
   submitted with its metric_call fails reconciliation (the rows are not the metric's rows), so it is
   FLAGGED and rejected; without a metric_call it can reach HOLD at best. Not one DERIVED attempt was
   delivered correct.

Smaller causes: Chinese month and region wording is not parsed (`months_from_text` is English
only; 北区 is not mapped to `north`), so a correct Chinese claim is held; a quarter key such as
`2026-Q1` in the month column is rejected as outside the period.

## Fixes

| id | fix | where | regression tests from E1 |
|---|---|---|---|
| F1 | **Answer-shape check.** Read from the question what shape is asked: one value, one row per month, one row per group, units (CNY vs fen, share vs percent). VERIFIED requires the submitted rows to have that shape. When the shape cannot be read, the answer is held, never verified. | `submit_answer`, new `asked_shape(question)` | monthly fen rows for "Q1 total in CNY" → not VERIFIED; per-month rows for "in each month" → VERIFIED as before |
| F2 | **Declared derivations.** `submit_answer` takes an optional `derive` step from a fixed list: sum or mean over months, fen → CNY, share → percent, share of total, difference or ratio between two groups, growth between two periods, rank / top-N. The harness computes the derived rows itself from the governed result and compares them with the submitted rows. A derivation must also fit the asked shape (F1). | `submit_answer`, new `derive.py` | Q1 total in CNY, regional share of annual revenue, average monthly revenue per region, pooled ITT effect → VERIFIED when right, FLAGGED when the arithmetic is wrong |
| F3 | **Chinese dates and names.** Parse 2026年3月, 2026年1月至3月, 2026年第一季度 / 一季度, 上半年; English "first quarter of 2026". Chinese aliases for dimension values (北区 → north, 基础版 → basic, …) live in the semantic model, not in code. | `months_from_text`, `claim_contradictions`, `semantic_model.yaml` | the E1 Chinese questions held for 北区 or 第一季度 → VERIFIED with the right call, HOLD with the wrong region |
| F4 | **Period keys.** A derived row over several months carries a period key (`2026-01..2026-03` or no key for a single value), never a fake month like `2026-Q1`. | `derive.py` | the quarter-key rejections |
| F5 | **Tool hints.** `query_metric` no longer says "Report these rows unchanged". It says: submit these rows unchanged only if they answer the question as asked; otherwise declare the derivation and submit the derived rows. `list_metrics` lists the derivation step with one worked example. | `query_metric`, `list_metrics` | prompt-level, tested in E1b only |

Not in v0.2: two-metric derivations (for example revenue per active customer computed from two
governed metrics), free-form SQL derivations. These stay HOLD with a reason the agent can read.

## How v0.2 is tested

E1b runs H v0.2 against N and against H v0.1 on the new blind questions, with the E1 metrics and
decision rules. It is pre-registered once the E1b bank is frozen. Success for v0.2 means, on the
new questions: no rise in DIRECT silent errors, a large fall in DERIVED silent errors in H, and the
false-hold rate M2(H) under the 15% line of rule D4.

## Addendum (2026-11-06 22:34, after the E1 labels, still before any E1b question exists)

The designer labels (173 wrong attempts) showed four more harness causes. They are fixed in v0.2 with
regression tests from the E1 attempts (`tests/test_answer_v02.py`):

| id | fix | E1 evidence |
|---|---|---|
| F6 | Values named only in a definition clause ("including add-ons and refunds", "net of refunds", 含退款) are not a filter or a breakdown | correct one-row totals held because the claim "neither filters nor breaks down by invoice_type" |
| F7 | English "first quarter of 2026", "Q1 of 2026", "first half of 2025", "H2 2025" parse to their months (the year rule had read "of 2026" as the whole year) | correct Q1 calls held as contradicting 2026-01..2026-12 |
| F8 | "churn … control rate minus treatment rate" is read as a churn rate, not a churn count | the pooled ITT effect held for naming the wrong metric |
| F9 | A declared `sum` or `pool` for an "average monthly …" question, or `mean` for a "total …" question, is a contradiction (HOLD) | without it a sum would verify for an average question once derivations exist |

Also: the server's `submit_answer` description no longer says "Rows should carry month" (agents
added keys such as `2025 average` or `2026-Q1`); it says rows carry the columns the question asks for
and names the `derive` step.

**Frozen v0.2 code** (sha256, working tree on top of commit 105f86b; 528 tests pass):

    c0f336f453167dae5eda1e4bd19030b8e6e8d07a130721ee4cdcbc924e62aa2f  src/dsharness/tools/analytics.py
    ce5e834cc063b9406f5e77a8c66a452872f9656b05a7b1f15c723bc72bf1a264  src/dsharness/tools/derive.py
    8bb2152da19c47036fd54b0833ddde77cd3315f84fa2fb0486534dc53901fcc1  src/dsharness/tools/shape.py
    08c2ca62c44a5f101cb4269c9c4c934351ab41f9d9ec01fe8a6e28af5ae3516d  src/dsharness/context/semantic.py
    305ab8ab15380d05b468e0d0ea9b18d40b151ff4927cee4059b757e83caba278  src/dsharness/context/semantic_model.yaml
    f8019729c9dd08f4625a3f9bac64a3a68f74103afed954ee719533448f64c672  src/dsharness/mcp_server.py

Any change to these files after the E1b questions are written is an E1b deviation.
