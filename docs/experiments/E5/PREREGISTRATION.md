# E5 phase 1 pre-registration: does a request-coverage check find the replies that leave out an asked part?

**Status: FINAL (frozen 2026-11-10 07:46 CST), before any model call of this experiment.** Plan and reasons:
[DESIGN.md](DESIGN.md). Nothing below may change except through a DEVIATIONS.md row with its time and reason.

## Question

Can a model-plus-program check, shown only a request and a finished reply, flag the replies whose blind final label
is `E-read` (an asked part missing, or a different quantity than asked), without flagging replies labelled supported?
Phase 1 replays the check offline on the 888 finished replies of E3b v2 (336) and E3c v2 (552). No agent runs.

## Check (as in `experiments/E5/coverage.py`)

1. **extract**: a model sees only the request and returns 1 to 8 numbered parts (a short phrase and a type each).
2. **cover**: a model sees the reply and the numbered parts and answers, per part, `covered` yes or no, with a quote
   copied from the reply for yes. Neither call sees the agent, arm, gate verdict, label or the other model's output.
3. **verify** (program): a `yes` whose quote is not found in the reply (whitespace, case and markdown marks removed)
   counts as no. A reply is **flagged** when any part is not covered.

Two deciders, each with its own part lists (extract and cover by the same model): **Luna** (`gpt-6-luna`, reasoning
high) and **Haiku** (`claude-haiku-5-5`). Cross-runs (one model covering with the other's parts) are exploratory.
The call text is the prompt in `coverage.py`; each call is tried twice, then the run stops.

## Ground truth

The final blind labels of each experiment (`runs/v2/labels.json`): a reply is *supported* when its final `supported`
is true, and an *E-read reply* when it is unsupported with `E-read` among its codes. Also scored against each
labeller's own labels (labeller 1 = Luna, labeller 2 = Haiku). Replies unsupported without E-read count in neither.

## Decision rule (fixed before any result; same as E3c's meaning-check rule)

A decider **qualifies to hold** only if, on E3b and on E3c separately, both on the final labels and on the other
family's labels (Haiku's for Luna, Luna's for Haiku): it flags at most **5%** of supported replies and at least
**half** of the E-read replies. If neither qualifies, the check runs advisory in any later experiment: its reasons are
shown to the agent, never used to hold. If one qualifies, phase 2 (a live census, DESIGN.md) may use it to hold.

## Also reported, not decided

Flag and catch rates by agent and arm; the share of flags that come only from a quote that was not found; part counts
per request for each decider; the cross-runs; the 5% / 50% rule on the pooled replies.

## Cost and budget

About 1,800 model calls on the API (replies are 1,000 to 4,000 tokens): a few US dollars.
Calls are cached on disk by request or reply; a stopped run resumes.

## Frozen inputs (sha256)

| file | sha256 |
|---|---|
| `experiments/E5/coverage.py` | `d5111ba6b0d5286357d5b0acb428d077482d702940bebf51db58995375a4524a` |
| `src/dsharness/bench/llm.py` | `d9935fd3c848da4577ecb6532450fbf50393c317485d00cc50d0414cefeaca9d` |
| `experiments/E3b/runs/v2/labels.json` | `862928060e39543fa38d8dcc8c60f815ef7596ceec65e8cdae744a126040924e` |
| `experiments/E3c/runs/v2/labels.json` | `bce0d8acb28d8c1becea17e04da5932a5d4cb77f213d82343047d3bff7136c5b` |
| `experiments/E3b/runs/v2/attempts.jsonl` | `4d59c89ff6e404cc055882eeb7705db9416f972a90757ba3e0c6cc3c9d7c3757` |
| `experiments/E3c/runs/v2/attempts.jsonl` | `492ac6aa3fdba8f064e05a7ac6814bdeee9249dd6f335dbb13e4383ddf0010b5` |

## Changes from DESIGN.md

DESIGN.md had Terra adjudicate disagreements between the two models' part lists. Phase 1 drops that: each decider
uses its own parts, and the rule above is stated per decider, so a disagreement between lists needs no adjudication.
(Decided 2026-11-10 before any call; the extraction has no ground truth to adjudicate against.)
