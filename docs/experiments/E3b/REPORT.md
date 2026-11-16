# E3b v2 report: forecasting a gift wholesaler's next quarter, with and without the forecast gate

Pre-registered in [PREREGISTRATION.md](PREREGISTRATION.md) (final 2026-11-08 07:06 CST); changes made after that,
all before any label existed, are in [DEVIATIONS.md](DEVIATIONS.md). Census 2026-11-08, 336 attempts (42 requests ×
2 agents × 2 arms × k = 2), all OK; 335 delivered, and one arm-H attempt (Codex, lu06, rep 1) never called the gate
and delivered nothing. One Codex attempt was killed when the census was paused and resumed; it was re-run, and its
ERROR row stays in the log and is not counted. Analysis by the frozen
[analyze_v2.py](../../../experiments/E3b/analyze_v2.py); raw tables in `experiments/E3b/runs/v2/results.md`
(gitignored run directory). Chinese version: [REPORT.zh-CN.md](REPORT.zh-CN.md).

## Summary

Agents (Claude Code, Codex) answered 40 held-out forecasting requests on the UCI Online Retail II order data (a UK
gift wholesaler; the data end before September 2025, the answer is the next three months). Arm N worked alone; arm H
delivered only after the forecast gate (`check_forecast`: backtests the claimed accuracy at past origins, checks for
future data, reproduces the files, requires every number in the reply to be a typed claim) allowed it.

**R1 is inconclusive.** Arm H had slightly fewer silent errors, but the paired interval includes 0:

| held-out, 160 attempts per arm | N | H | H − N, paired over requests |
|---|---|---|---|
| R1 silent error (delivered, not supported) | 48.1% [36.9, 58.8] | 45.0% [33.1, 57.5] | **−3.1 points [−12.5, +6.9]** |
| gate-visible errors (secondary) | 4.4% | 0.6% | |
| backtest-targeted errors (secondary) | 13.8% | 10.0% | −3.8 points [−8.1, +0.6] |
| gate-blind errors (secondary) | 30.0% | 34.4% | +4.4 points [−2.5, +12.5] |
| R2 false hold (supported replies held at the end) | 0.0% | 0.0% | |
| supported delivery | 51.9% | 54.4% | |
| pass^2 (both repetitions supported) | 36.2% | 45.0% | |
| cost per supported delivery | $0.23 / 495k tokens | $0.20 / 842k tokens | |

What the result says:
1. **Nearly half of all replies carry an error that neither arm's tools catch.** About 30–34% of attempts are errors
   the gate cannot see (mostly E-read, an asked part left out: 40 replies in N, 49 in H). That is the main finding:
   the gate is not aimed at where most of the errors are.
2. **On what it can see, the gate works but is small.** Errors with a program check fell from 4.4% to 0.6%
   (gate-visible) and 13.8% to 10.0% (backtest-targeted). The gate stopped one arm-H attempt (caught 0.6%);
   all 72 arm-H silent errors were allowed through.
3. **The gate changed behaviour more than outcomes.** 91 of 168 arm-H attempts were held or rejected at least once
   (63 twice, 21 three times, 7 four times); all but the one above were allowed after a revision. Untyped numbers per reply fell from 2.86 (N) to 0 (H), by design.
4. **Claude and Codex went in different directions** (held-out silent errors, N → H): Claude 47.5% → 36.2%, Codex
   48.8% → 53.8%. This split was not pre-registered, so it is a lead, not a result.

## Setup

| | |
|---|---|
| data | UCI Online Retail II (2009–2011 invoices), warehouse built per request; the answer window is sealed |
| requests | 40 held-out (blind, written by Haiku and Luna), 2 seen (pilot), reported apart |
| agents | Claude Code (`sonnet`, through the CLI subscription) and Codex (GPT-6 Luna, high), in a sandbox |
| arm N | `answer.md` and `predict.py` |
| arm H | the same plus `claims.json`; delivered only after `check_forecast` ALLOW |
| truth | the sealed months, scored by the evaluator code after the census (`truth_v2.py`, mode sealed) |
| labels | blind sheets: Luna (labeller 1) and Haiku (labeller 2) on all 336; 81 disagreements adjudicated blind by GPT-6.1 Terra |

## Pre-registered results

| rule | result |
|---|---|
| R1: H fewer silent errors (paired interval below 0) and R2 ≤ 15% | **inconclusive**: −3.1 points [−12.5, +6.9] |
| R2 too strict (R2 > 15%) | no: 0.0% |
| R1 with ungraded replies counted as errors (sensitivity) | inconclusive |

The seen set (8 attempts per arm) is too small to read: N 12.5%, H 0.0%.

### Each agent (held-out)

| agent | arm | R1 | supported delivery | pass^2 |
|---|---|---|---|---|
| Claude | N | 47.5% | 52.5% | 37.5% |
| Claude | H | 36.2% | 63.7% | 55.0% |
| Codex | N | 48.8% | 51.2% | 35.0% |
| Codex | H | 53.8% | 45.0% | 35.0% |

### Error codes on silent errors (held-out, label and program)

| code | gate | N | H |
|---|---|---|---|
| E-read: an asked part left out | blind | 40 | **49** |
| E-gross: a point worse than 25% and twice the naive seasonal error | targeted | 15 | 11 |
| E-overclaim: a certainty the data do not support | targeted | 12 | 10 |
| E-calc: a past total that does not match the warehouse | visible | 7 | **1** |
| E-define: an undefined population or term | blind | 3 | 8 |
| E-nodata, E-causal, E-other | blind | 8 | 6 |

A reply can carry several codes. E-read again grows in arm H, as in E3c (E3c: 18 → 38).

### Range coverage (C, read against the stated level, not decided)

Ranges stated at 90% held the realised value 92.9% of the time in N (11,543 ranges, mostly per-buyer bands) and
91.9% in H (359 ranges); at 80%, 76.5% (315) and 92.4% (170). Per-buyer bands dominate arm N's count.

## The gate replayed on arm N (secondary)

`replay_gate_v2.py` runs the whole gate offline on every final reply. On arm N it shows what the gate would say
without feedback: of 78 silent-error replies 61 (78%) would be held or rejected, and of 90 supported replies 58 (64%)
would too. The second figure is not a false-hold rate for the gate as deployed: arm N never writes `claims.json`, so
the "numbers with no claim" rule holds almost every N reply. On arm H, all 72 silent-error finals were allowed.

## First answer versus final answer (harm)

`harm_v2.py` replays the same steps on version 1 of the 91 arm-H attempts that were held or rejected at least once.
Harm (supported first, not supported final): **8**. Fixed (not supported first, supported final): **12**. The gate's
reasons make more answers right than wrong, but they do harm. The largest single cluster is request ha02 (win-back
emails): all 4 arm-H attempts were labelled E-define ("several months" given with no cutoff for lapsed accounts), and
all 4 arm-N attempts were supported.

## Labels

Agreement on `supported` between Luna and Haiku, before adjudication:

| | all | arm N | arm H |
|---|---|---|---|
| Claude | 73%, κ 0.29 | 74%, κ 0.30 | 73%, κ 0.29 |
| Codex | 79%, κ 0.53 | 77%, κ 0.47 | 80%, κ 0.57 |
| both agents | 76%, κ 0.43 | 76%, κ 0.39 | 76%, κ 0.46 |

κ is moderate and lowest on Claude's replies. 81 of 336 labels were adjudicated blind by Terra, which sees the label
guide, the sheet and the two labels unattributed, and never the agent, arm or gate verdict. Terra's price was taken
from aggregator sites, not an invoice. 20 replies name the gate in their text (N 13, H 7), so a labeller could in
principle tell the arm.

**Sensitivity to request ha02** (post hoc, my own bootstrap, not the frozen analysis): without ha02 the paired
difference is −5.8 points [−14.1, +2.6], still inconclusive.

## Cost

| | |
|---|---|
| census, agents (list-price equivalent, subscription) | Claude ~$24 (sonnet, ~$0.12 per attempt), Codex ~$1.5 |
| post-census, API (billed) | $1.46: Terra adjudication $0.48 (100 calls), Luna typing $0.63, Luna labels $0.17, Haiku labels $0.18 |

## What changes next

- **The next gate should target E-read.** The most common error, in both arms and growing in arm H, is a part of
  the request left out. The request's parts are a list a model can extract once and a program can check
  ("semantic → model, exact → program"). In E3c the advisory meaning check caught about half of such errors; it is
  advisory in this experiment and its effect on E-read is a separate analysis.
- **Revision loops need a check for new errors** (8 harms): a reason that makes the agent rewrite the answer can
  introduce a different error.
- **Agents and transports for the next experiments:** the Claude agent moves to Haiku on the API key after a pilot;
  helper models run on the API. This is a change of conditions, not of this experiment.

## Limits

- One dataset, two agents, 40 held-out requests; the R1 interval is wide.
- Labels are model labels with moderate κ (0.29 on Claude's replies). The adjudicator is a third model of one
  family (OpenAI), the same family as labeller 1. A different adjudicator could move R1 by a few points either way.
- `labels.json` still states that disagreements were "adjudicated by a fresh Claude subagent". That text is stale:
  Terra adjudicated (DEVIATIONS 11-08 13:20). The code that writes the string is in the frozen digest set, so it is
  left as is and noted here.
- The census ran on the CLI transports; the post-census steps ran on the API (DEVIATIONS). Both arms went through
  the same path, so the comparison holds, but numbers are not transferable to an API-run agent.
- The visible/blind split depends on the key's code split, fixed before the census.
