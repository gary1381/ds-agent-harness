# E3c v2 report: reading out a randomised e-mail test, with and without the experiment harness

Pre-registered in [PREREGISTRATION.md](PREREGISTRATION.md) (final 2026-11-07 17:07); two additions made before any
sheet existed are in [DEVIATIONS.md](DEVIATIONS.md) (Luna as labeller 1; the offline meaning-check replay). Census
2026-11-07 23:32 to 2026-11-08 05:26 CST, 552 attempts (46 requests × 2 agents × 2 arms × 3), all OK, all delivered.
Analysis by the frozen [analyze_v2.py](../../../experiments/E3c/analyze_v2.py). Chinese version:
[REPORT.zh-CN.md](REPORT.zh-CN.md).

The request bank and the owner's answer key are not distributed in the public copy (the Hillstrom file has no
licence; see [DATA.md](../../DATA.md)); this report keeps the aggregate results.

## Summary

Agents (Claude Code, Codex) answered 39 held-out requests about the Hillstrom e-mail test (64,000 customers, random
thirds: men's e-mail, women's e-mail, no e-mail). Arm N worked alone; arm H delivered only after the experiment gate
(`check_readout`: programs that recompute every typed number and test contrasts, pooling, subgroups and selection)
allowed its typed claims.

**R1 is inconclusive.** Arm H delivered fewer silent errors, but the paired interval includes 0:

| held-out, 234 attempts per arm | N | H | H − N, paired over requests |
|---|---|---|---|
| R1 silent error (delivered, not supported) | 27.8% [18.4, 38.5] | 22.2% [13.2, 32.1] | **−5.6 points [−12.8, +1.7]** |
| gate-visible errors (secondary) | 22.6% | 6.4% | **−16.2 points [−24.8, −8.5]** |
| gate-blind errors (secondary) | 5.1% | 15.8% | +10.7 points |
| R2 false hold | 0.0% | 0.0% | |
| pass^3 (all 3 repetitions supported) | 55.1% | 64.1% | |
| cost per supported delivery | $0.07 / 141k tokens | $0.07 / 147k tokens | |

What the result says:
1. **The gate works on what it can see.** Errors with a program check (wrong figures, pooled effects, fished
   subgroups, outcome-selected comparisons, overclaims) fell from 22.6% to 6.4% of attempts; the interval excludes 0.
2. **Errors it cannot see grew.** "Did not answer every part asked" (E-read) doubled, 18 → 38 replies. It grew just
   as much in arm H attempts the gate never held (31 of 194, 16%, against 8% in arm N), so it is not caused by gate
   reasons: working through typed claims seems to narrow what the reply covers.
3. **No false holds.** The gate held 40 of 276 arm-H attempts at least once (Claude 23, Codex 17); every one was
   allowed after a revision. It cost nothing measurable: same dollars per supported delivery, about 4% more tokens.
4. **A model meaning check is not good enough to hold** (secondary, below): Haiku and Luna flag 17–23% of correct
   replies. In E3b v2 it runs advisory only.

## Setup

| | |
|---|---|
| data | Hillstrom MineThatData e-mail test (research use only; runs gitignored, aggregates only) |
| requests | 39 held-out (blind, written by Luna and Haiku), 1 exposed, 6 seen (pilot, calibration); reported apart |
| agents | Claude Code (Claude) and Codex (GPT), in a sandbox with the warehouse and the toolchain |
| arm N | `answer.md` |
| arm H | `answer.md` + `claims.json` (one structured claim per number), delivered only after `check_readout` ALLOW |
| labels | blind sheets (no agent, arm, gate verdict): Luna (labeller 1) and Haiku (labeller 2) on all 552; 130 disagreements adjudicated blind by a fresh Claude subagent that labelled no sheet |

## Pre-registered results

| rule | result |
|---|---|
| R1: H fewer silent errors (paired interval below 0) and R2 ≤ 15% | **inconclusive**: −5.6 points [−12.8, +1.7] |
| R2 too strict (R2 > 15%) | no: 0.0% |

Sensitivity (pre-registered): no answer as a silent error, and R1 over replies only, give the same figures (no
attempt lacked an answer). Exposed (1 request) and seen (6 requests) sets agree in direction: seen R1 36.1% vs
25.0%, paired −11.1 points [−33.3, +5.6]; gate-visible −16.7 [−27.8, −5.6].

### Each agent (held-out)

| agent | arm | R1 | gate-visible | gate-blind |
|---|---|---|---|---|
| Claude | N | 29.9% | 22.2% | 7.7% |
| Claude | H | 27.4% | 8.5% | 18.8% |
| Codex | N | 25.6% | 23.1% | 2.6% |
| Codex | H | 17.1% | 4.3% | 12.8% |

Both agents show the same shift from visible to blind errors; Codex gains more overall.

### Error codes on silent errors (held-out)

| code | gate | N | H |
|---|---|---|---|
| E-read: an asked part left out | blind | 18 | **38** |
| E-calc: a wrong figure | visible | 14 | 3 |
| E-overclaim: a certainty the test does not support | visible | 14 | 6 |
| E-pool: one pooled effect where each e-mail is needed | visible | 14 | 0 |
| E-fish: a searched subgroup given as established | visible | 9 | 4 |
| E-select: post-e-mail groups compared as an effect | visible | 8 | 1 |
| E-nodata: a claim the data cannot show | blind | 5 | 4 |
| E-extrapolate | visible | 1 | 1 |

A reply can carry several codes. The split into visible and blind takes a reply with any visible code as visible, so
in arm N some E-read errors sit inside replies counted as visible; the code counts above are the direct measure.

## Labels

Agreement on `supported` between Luna and Haiku, before adjudication:

| | all | arm N | arm H |
|---|---|---|---|
| Claude | 72%, κ 0.42 | 65%, κ 0.32 | 78%, κ 0.51 |
| Codex | 81%, κ 0.52 | 78%, κ 0.47 | 85%, κ 0.57 |
| both agents | 76%, κ 0.47 | 71%, κ 0.39 | 82%, κ 0.54 |

κ is moderate; it is lowest on Claude's arm-N replies, which are the longest and most varied. Luna judged 69% of
replies supported, Haiku 65%. The adjudicator decided 108 of the 130 disagreements as supported. It reported one
strict reading (E-pool given whenever only a pooled effect was given by segment, 10 replies) and three key
ambiguities (an adjusted interval in ha02, a joint test in lu09, intervals in ha19); a ±0.01 rounding difference
in ha12 was passed. The sheets show no agent, arm or gate verdict, and no reply named the gate.

## Meaning check, replayed offline (secondary, DEVIATIONS 11-07 23:49)

Haiku and Luna each answered three fixed questions about every final reply (scale, nodata, unanswered), with a
quote checked by a program. Rule fixed before any label: the check may hold in E3b v2's gate only if one model flags
at most 5% of supported replies and catches at least half of the gate-blind errors, against the final labels and
against the other family's labels.

| decider | labels | false flags on supported | catches gate-blind errors |
|---|---|---|---|
| Haiku | final | 16.7% (69/413) | 38% (24/63) |
| Haiku | Luna's | 17.5% (67/382) | 33% (29/87) |
| Luna | final | 22.5% (93/413) | 46% (29/63) |
| Luna | Haiku's | 20.4% (73/358) | 55% (27/49) |

**Neither qualifies.** E3b v2 runs the meaning check advisory (its reason is shown, it never holds), with Haiku, the
model with fewer false flags, since in advisory mode its reasons still reach the agent. The "unanswered" question
catches about half of E-read errors, the code that grew in arm H.

## What changes next

- **E3b v2** (forecasting, frozen 2026-11-08 07:06, census running) keeps the program gate and adds the advisory
  meaning check; its `harm_v2.py` measures whether gate reasons turn right first answers into wrong final ones.
- **The next gate design** should target E-read: the coverage of the request (every part, grain and breakdown it
  names) is a list a program can check once a model has extracted it from the request ("semantic → model, exact →
  program"), and the typed-claims workflow should ask for every asked part, not only the numbers.

## Limits

- One dataset, two agents, 39 held-out requests; the interval for R1 is wide.
- Labels are model labels (Luna, Haiku, a Claude adjudicator) with moderate κ; a different adjudicator could move
  a few points of R1 in either arm.
- The Hillstrom data are public and old; agents may have seen analyses of it. The effect is read within requests.
- The visible/blind split depends on the key's code split, fixed before the census.
