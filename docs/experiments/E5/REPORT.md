# E5 phase 1 report: a request-coverage check does not find the replies that leave a part out

Pre-registered in [PREREGISTRATION.md](PREREGISTRATION.md) (frozen 2026-11-10 07:46 CST, before any model call).
Run 2026-11-10 07:50 to 08:29 CST on the 888 finished replies of E3b v2 (336) and E3c v2 (552); no agent ran. Code:
[coverage.py](../../../experiments/E5/coverage.py). Chinese version: [REPORT.zh-CN.md](REPORT.zh-CN.md).

## Result

**Neither decider qualifies, and neither comes close.** The rule asked for at most 5% false flags on supported
replies and at least half of the E-read replies caught, on E3b and on E3c, against the final labels and the other
family's labels.

| decider | labels | E3b false flags | E3b catches | E3c false flags | E3c catches |
|---|---|---|---|---|---|
| Luna | final | 22.8% (50/219) | 28% (25/90) | 27.4% (113/413) | 28% (18/64) |
| Luna | Haiku's | 24.4% (59/242) | 14% (3/22) | 28.8% (103/358) | 32% (13/40) |
| Haiku | final | 24.2% (53/219) | 17% (15/90) | 34.4% (142/413) | 47% (30/64) |
| Haiku | Luna's | 23.1% (53/229) | 19% (15/80) | 31.4% (120/382) | 57% (52/91) |

Pooled over both experiments, against the final labels: Luna flags 25.8% of supported replies and catches 28% of
E-read replies; Haiku flags 30.9% and catches 29%. Per-labeller tables and all scopes: `experiments/E5/runs/phase1/results.md`
(gitignored run directory; the file is reproducible with `run_phase1.sh`).

## What it says

1. **A coverage check built this way is worse than the E3c meaning check**, which flagged 17–23% of supported replies
   and caught 38–55% of gate-blind errors. Here about a quarter to a third of supported replies get flagged, and
   under a third of E-read replies are caught (Haiku on E3c is the exception, at 47–57%).
2. **The flags are not the quote check.** Only 41 of Luna's 232 flagged replies (18%) and 3 of Haiku's 270 (1%) were
   flagged only because a quote was not found. Most flags are a model saying a part is not covered.
3. **The parts are plausible but not what the labels punish.** The two models extract 4.5 to 4.6 parts per request on
   average (range 2 to 8). The 111 E-read replies Luna's check did not flag are mostly replies that touch the part but lack the detail the label wants: "intervals for the requested comparisons", "September and October separately", "per-area effects", a single pooled figure where each e-mail was asked for. A yes/no on whether a part is mentioned does not see that. The check covers only the "an asked
   part is missing" half of the E-read definition.
4. **Decision under the rule:** the coverage check runs advisory only, if at all. Phase 2 (a live census to hold on
   it) is not justified by these numbers.

## Limits

- The ground truth is model labels with moderate κ (0.43 on E3b); many of the replies Luna and Haiku disagree on are
  E-read cases, so the catch rates move by labeller (E3b: 28% on final labels, 14% on Haiku's).
- Both deciders were asked one question per part. A finer check (the grain of a figure, the population it covers) might
  do better, but it would be a different check and needs its own pre-registration.
- Part lists have no ground truth; their quality is judged only through this rule.

## What changes next

The E-read errors are real and the program gates do not see them, but a model reading the reply against the request's
parts is not precise enough to act on. Two directions, neither started:
- **Ask for coverage in the work itself:** the agent lists the request's parts at the start and ticks them off in
  `claims.json`; a program checks that every part has a claim. This changes the agent's workflow, not a judge after
  the fact, and E3b's typed claims already narrowed replies in arm H, so it needs a pilot first.
- **Quantity checks:** E-read in the "different quantity than asked" sense (grain, horizon, population) is a
  per-estimand check, which the forecast and read-out gates already have for their own claim types. Extending those is
  more promising than a general coverage judge.
