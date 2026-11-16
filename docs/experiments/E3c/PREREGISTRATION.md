# E3c v2 pre-registration: reading out a randomised e-mail test, with and without the experiment harness

**Final**, 2026-11-07 17:07 (CST). Written while the E3d census ran, before any held-out v2 attempt. Luna reviewed
it blind twice (sections below); the frozen inputs are listed at the end.

The census runs after the E3d census, on two cores. Any change after the freeze goes to `DEVIATIONS.md`. Design and
history: [DESIGN.md](DESIGN.md), section "v2".

**Research use only.** The Hillstrom file has no licence from its author (Gary, 2026-11-06). It is never
redistributed and never part of a commercial benchmark. Requests, keys, transcripts and labels stay under
gitignored run folders, and reports carry aggregates only.

## Question

1. Colleagues ask real agents what a randomised e-mail test showed. How often does an agent deliver a readout the
   test does not support?
2. Does the experiment harness deliver fewer such readouts, without holding supported ones? Holds of supported
   readouts (false holds) must stay at or below 15%.

The pilot (12 attempts, arm N only) found 1 error in 12, so the base rate may be low. A null result on R1, with its
interval, is a result. An interval that includes 0 is reported as inconclusive, not as "no effect" (see Power).

### What the gate can see

The gate works on the agent's structured claims. It recomputes each claim from the rows, checks the estimand, checks
the direction against the estimate and interval, checks subgroups against the plan, and checks the window. It does
**not** read what the prose says a number means. Two examples pass the gate (pinned by
`tests/test_readout_experiment.py`, "known limits"):
- a correct mean difference described as what "a typical customer" gains;
- correct spend figures offered as proof of profit.

The key's codes are therefore split before the census:

| gate-visible (a check exists, if the claims are declared honestly) | gate-blind |
|---|---|
| E-calc, E-select, E-fish, E-pool, E-extrapolate, E-overclaim | E-scale, E-nodata, E-read, E-other |

R1 counts every error. The split is a secondary result: R1 for each kind, and R1(H) − R1(N) on gate-visible errors.
If gate-blind errors dominate, the next variant adds a model that reads the meaning, following the rule "semantic →
model, exact → program" (Gary, 2026-11-07). That variant is not part of this experiment.

## Requests

| set | requests | source |
|---|---|---|
| held out | 39 | written blind by Luna (20) and Haiku (20) from `author_brief.md` (sha256 `c10f7f37…`). Neither author saw the checks, the pilot or the key. Kept verbatim, none dropped (`experiments/E3c/bank_v2.yaml`), except ha17 (next row). |
| exposed | 1 | ha17, run twice in the smoke test before the freeze. Reported beside the seen requests, never in a decision. |
| seen | 6 | the pilot's requests (`experiments/E3c/tasks.py`). Used to calibrate the gate, so they are secondary. |

The owner's key (`experiments/E3c/keys_v2.yaml`) was written before any v2 attempt, with numbers from
`reference_v2.py`. Luna reviewed it blind and agreed on 30 of 40. Of the 10 changes it asked for, 9 were taken,
mostly breakdowns a request asks for. In the tenth, Luna's premise was refuted: no customer has neither past
purchase category.

**Public copy:** `bank_v2.yaml` and `keys_v2.yaml` are not distributed, because the Hillstrom file has no licence;
the digests under "Frozen inputs" still identify the files that were frozen. The key was written by hand and cannot
be regenerated. The reference numbers it uses can be recomputed with `reference_v2.py` from the fetched data
([DATA.md](../../DATA.md)). The results are in [REPORT.md](REPORT.md).

## Agents and arms

Same agents and sandbox as E3d: Claude Code (Sonnet 5.5) and Codex (gpt-6-luna, high). k = 3. No internet. Both
arms get the same workspace:
- `campaign.duckdb`;
- the v2 catalog: the design, plus the owner's analysis plan with past purchase category as the only
  pre-specified modifier;
- the owner's data checks (`checks/check_experiment.py`: a Great Expectations suite, the sample ratio, balance);
- Python from `agent_env_dt`: duckdb, pandas, polars, numpy, scipy, scikit-learn, statsmodels, matplotlib, seaborn.

| arm | the agent writes | delivered means |
|---|---|---|
| N | `answer.md` (≤ 200 words) and `numbers.json` (as the pilot) | `answer.md` is not empty |
| H | `answer.md` and `claims.json` (one structured claim per number), through the `dsh-experiment` MCP server | the last `check_readout` of exactly the final `answer.md` and `claims.json` (both matched by sha256) returned ALLOW |

**The intervention is the harness as a bundle**, not the gate alone (Luna's review, finding 3). Arm H differs from
N in five ways:
1. the MCP tools: `describe_experiment`, `estimate_effect` and `check_readout`;
2. the duty to write structured claims instead of a numbers list;
3. the server's instruction: on HOLD or REJECT, read the reasons and correct the reply or the claims; do not change a
   correct analysis just to pass;
4. the gate's verdicts and reasons;
5. the delivery rule.

A difference in R1 is credited to the bundle. Which part helped is read, not tested, from the check logs: for
example, verdict sequences, and replies changed after a HOLD.

The gate (`src/dsharness/readout/experiment.py`) has nine checks. Programs decide; no model reads the words. A FAIL
rejects the readout, an INCONCLUSIVE holds it, and ALLOW needs no FAIL and no INCONCLUSIVE.

| check | FAIL (reject) | INCONCLUSIVE (hold) |
|---|---|---|
| exp.data | | the owner's data checks fail |
| exp.claims | | a claim names an unknown column, group, estimand or role |
| exp.numbers_covered | | a number in the reply that no claim, the request or a design fact accounts for |
| exp.recompute | an estimate off by more than 0.5% relative (or half the last digit); a mean's interval end off by more than 10% of the width | an estimand the gate cannot recompute, given as a result; a ratio's interval end more than 10% of the width from both delta-method intervals |
| exp.estimand | a comparison among units selected by an outcome, or group means of different groups there given as results, unless marked as a caveat | |
| exp.contrast | "higher"/"lower" against the sign of the estimate | "higher"/"lower" where the interval includes 0, or "none" where it excludes 0 |
| exp.prespecified | | a subgroup result outside the plan, not marked exploratory with the number of segments searched |
| exp.horizon | an effect beyond the 14-day window given as a result | a projection without its assumption |
| exp.pooled | | a pooled-only result where the e-mails differ on that outcome, among everyone assigned or in the claim's population |

**Interval tolerance, checked on the real table before the freeze (16:44,
`experiments/E3c/ci_methods_v2.py`; table in DESIGN.md, v2).**
- Mean differences: Welch's t, OLS with HC1 errors (with and without pre-campaign covariates) and percentile
  bootstrap all fall within 7% of the gate's interval width, overall and in subgroups.
- Ratios: a linear-delta or bootstrap interval falls 6–14% of the width from the gate's log-delta interval in
  planned subgroups, and up to 44% in a small one. So a ratio's interval is accepted against either delta method,
  and otherwise held rather than rejected.
- Intervals stay optional: the key does not require one unless the request asks for certainty.

## Outcomes and labels

Every reply, delivered or not, is labelled as supported or not against the key, with error codes
([`LABEL_GUIDE_v2.md`](../../../experiments/E3c/LABEL_GUIDE_v2.md)). Labelling is blind and double (changed after
Luna's review, finding 4):
- **The sheets are blind.** Each shows the request, its key and the reply, nothing else: no agent, arm, delivery,
  gate verdict, claims or numbers file. The order is shuffled.
- **Two labellers on every reply:** designer subagents, and Haiku. Haiku is not one of the agents under test.
  Luna, the Codex model, does not label.
- **Disagreements are adjudicated blind.** Where the two disagree on `supported`, a fresh designer subagent decides
  from the blind sheet, with both labels shown unattributed and in random order. It labelled no sheet, so it cannot
  recognise its own label.
- **Agreement is reported** as κ, overall and by arm (arms unmasked only for this count).
- **Unmasking risk is counted.** A program counts the replies whose text names the gate, by arm.

| outcome | definition |
|---|---|
| supported-delivery | delivered and supported |
| silent-error | delivered and not supported |
| caught | not delivered (the gate never allowed it) and not supported |
| false-hold | not delivered and supported |
| no-answer | no `answer.md` (the run failed after one retry, or the agent wrote none) |

## Metrics (held-out requests; exposed and seen requests reported separately)

| id | metric | denominator |
|---|---|---|
| R1 | silent errors | planned attempts (39 × 2 agents × 3 = 234 per arm) |
| R1-visible, R1-blind | silent errors with any gate-visible code / with only gate-blind codes | planned attempts |
| R2 | false holds (arm H) | supported replies |
| R3 | cost per supported delivery: Claude in dollars, Codex in tokens, never added together | supported deliveries |
| P | pass^3: (agent, request) cells with all three repetitions supported and delivered | cells; an incomplete cell counts as not passed |

How the numbers are computed:
- **Intervals:** cluster bootstrap over requests (2,000 resamples, seed 20261107), percentiles by linear
  interpolation (type 7). The number of resamples where the metric is defined is reported beside each interval.
- **The difference R1(H) − R1(N)** is paired over requests.
- **The final analysis refuses to run** while any planned slot is missing or any reply is unlabelled. It reports
  planned, recorded, failed, no-answer and unlabelled counts.
- **Sensitivity analyses:** R1 with no-answer counted as a silent error, and R1 over replies only.

## Decision rules (held-out requests)

The R1 result is one of four:
- **met:** R1(H) < R1(N), the upper end of the paired interval is below 0, and R2 ≤ 15%;
- **not met because of false holds:** the upper end is below 0 but R2 > 15%;
- **harness worse:** the lower end of the paired interval is above 0;
- **inconclusive:** the paired interval includes 0.

Separately, **R2 too strict** applies when R2 > 15%.

Reported but not decided:
- each agent separately;
- error codes by arm, and the gate-visible / gate-blind split;
- the gate's verdict sequences;
- the exposed and seen requests;
- whether the H agents changed a correct analysis to pass the gate (from the check logs).

## Power (`experiments/E3c/power_v2.py`, before the freeze)

The model:
- 39 requests and 6 attempts per arm per request;
- the arm-N error rate varies by request: φ = 1 puts the errors on a few requests, φ = 5 spreads them;
- the harness catches a share f of errors and adds none;
- 1,000 simulations per cell.

| P(R1 met), φ = 1 | f = 25% | 50% | 75% | 100% |
|---|---|---|---|---|
| arm-N rate 5% | 0.13 | 0.27 | 0.47 | 0.66 |
| 10% | 0.23 | 0.60 | 0.89 | 0.98 |
| 20% | 0.43 | 0.91 | 0.99 | 1.00 |
| 30% | 0.62 | 0.99 | 1.00 | 1.00 |

With φ = 5 the numbers are similar (5%: 0.10 / 0.30 / 0.66 / 0.96). At the pilot's rate, about 8%, the rule has 80%
power only if the harness catches about three quarters of the errors. If half the errors are gate-blind, power is
about 0.5–0.6. An inconclusive result is therefore likely and will be called that.

## Calibration (before the freeze)

Two runs come before the freeze; faults they find are fixed and listed here.
- **Smoke test** (`runs/v2_smoke`, 2 attempts, arm H). It used ha17, now in the exposed set. Its key was written
  before the test and was not changed.
- **Arm-H calibration** on the six seen requests (`runs/v2_calib`).

Faults found in the calibration:
1. `exp.contrast` judged a direction stated on a group's own mean. Agents wrote "lower" on the women's mean, meaning
   lower than the men's. The check now applies to comparisons only (regression test).
2. "95%", the interval level, counted as an uncovered number. It is now a design fact.
3. The multiplier of a labelled projection ("× 26 two-week periods") counted as an uncovered number. Codex held once
   and then dropped the "26" from its reply. A claim's `scale` and `horizon_days` now count as covering it
   (regression test).

Calibration result (16:31–16:39, arm H, 6 seen requests × 2 agents):
- 12 of 12 delivered: 11 allowed on the first `check_readout`, 1 held once (fault 3), none rejected.
- No reply was changed away from a correct analysis, except for fault 3's wording.
- On the seen traps, both agents declined the per-buyer "uplift".
- Claude named the search across segments and stayed within the planned split.
- Codex labelled its yearly figure a projection.
- Both readouts of "typical" reported spend per customer assigned and said the average includes non-buyers. Neither
  said that the typical customer spends nothing. Whether that is supported is left to the labels.

## Luna's blind review of this plan (16:40–16:41) and the answers

Luna read the draft and the code without the designer's notes (`runs/review_prereg/work/REVIEW.md`).

| # | finding | answer |
|---|---|---|
| 1 | blocker: the gate does not link what the prose says to the claims, so a correct number described wrongly ("a typical customer") passes | taken as a stated limit, not a fix: the gate stays programs-only. "What the gate can see" names it; the codes are split into gate-visible and gate-blind before the census; tests pin the known limits. |
| 2 | blocker: `exp.contrast` rejects "higher" when the interval includes 0, even if the reply says so honestly; reasons can steer | fixed: FAIL only against the sign of the estimate. A significance mismatch holds, and the reason states the facts and the schema's meaning of each word. The schema now defines higher/lower/unclear/none. |
| 3 | arms differ in more than the gate | the intervention is named as the bundle (five parts, above) |
| 4 | blocker: the labeller sees the arm and verdict; Haiku's blind 30% is too weak | fixed: every sheet is blind, two labellers on all, disagreements adjudicated blind, κ by arm |
| 5 | bootstrap drops undefined draws; denominators vague | fixed: complete-slot check, type-7 quantiles, defined-draw counts, stated denominators, two sensitivity analyses |
| 6 | frozen inputs blank; low power | power simulation added (above); frozen inputs filled at the freeze; "inconclusive" defined |
| 7 | ha17 exposed in the smoke test | moved to an "exposed" set, outside every decision |
| 8 | the gate's interval method may differ from the analyst's | checked on the real table (above): means within 7%; ratios accepted against either delta method, otherwise held |
| 9 | pooled check depends on a test in the claim's population; outcome-selected group means not flagged; claims not bound to the final answer | fixed: pooled also tested among everyone assigned; group means of different groups among outcome-selected units given as results are rejected; delivery needs the final `claims.json` to match the allowed check too. "Always require per-treatment results" was not taken: ha08's key accepts one pooled figure ("one number please"). |

After the fixes, the gate was replayed on the 12 calibration deliveries: all 12 are still ALLOW.

**Luna's second review (16:57–16:59, `runs/review_prereg2/work/REVIEW.md`).**
- Findings 3, 5 and 7 are resolved. Findings 1, 2, 4, 8 and 9 are resolved as stated limits. Finding 6 waits for
  the freeze.
- New finding 1, fixed: number coverage ignored the sign, so "−$0.77" in the reply was covered by a +0.77 claim. A
  number written with a sign must now match a claim of that sign; an unsigned one ("fell by $0.30") may match
  either (regression test).
- New finding 3, fixed: the adjudicator is a fresh subagent that labelled no sheet.
- After this fix, the calibration replay is still 12 of 12 ALLOW, and 19 gate tests pass.

**Smoke test after the fixes** (17:05–17:06, `runs/v2_smoke2`, seen request "typical", arm H, both agents): both
delivered, ALLOW on the first `check_readout`. The records bind both the answer's and the claims' sha256. Not part
of any result.

## Frozen inputs (17:07)

Check with `sha256sum` before the census starts and before the analysis. A changed file is a deviation.

| input | sha256 |
|---|---|
| `experiments/E3c/bank_v2.yaml` | `ca92eac8e794b2995d77ff0c19cb0fb09aa2ef929a17cd8faf45fc40f84753bd` |
| `experiments/E3c/keys_v2.yaml` | `9475668a936929b549731a86651586eff2f3e4b7030140670c7e1db2a99fa012` |
| `experiments/E3c/reference_v2.py` | `d322143f75fe90c9cd7b93add6c2e04262314b7827b76011dabaf9fc979e1e43` |
| `experiments/E3c/run_v2.py` | `740762b6f04c52caad3a7df74043e0f047d256b8717798d0cf08f9ee84ca8a23` |
| `experiments/E3c/mcp_experiment.py` | `dbedef2e0fd579a8f458aa7fb99d3692c2f1cd5b3481bd11e90a32af060c31ff` |
| `src/dsharness/readout/experiment.py` | `327aac46e47f63ca4dc332a72049cbacc69000866cff56aceb78b83619b2204f` |
| `experiments/E3c/workspace_v2/checks/check_experiment.py` | `fd52c1b76d77855e8ac9565e0a2f92f29438fba9243fe29ca4293f72113ec9f7` |
| `experiments/E3c/label_v2.py` | `dcb49374a36d4e226eb10e10e48f810a32faa6e79e9f57eb54467870df4a2142` |
| `experiments/E3c/LABEL_GUIDE_v2.md` | `b8914e831ffa5845ff61edbbf55adcd81c53135124357eeb734fff82f6413137` |
| `experiments/E3c/analyze_v2.py` | `6d3ecf505e2ce071a65946891367a9c9f23430f82bae6ff22cc952b84553f102` |
| `experiments/E3c/power_v2.py` | `3469f2f82de2d53350288edf34a135d12deef45fe3374f1737875da0be89eb64` |
| `tests/test_readout_experiment.py` | `ab2149b4b4f235ec762299fa8628d45e72169d4fb9ced959e15318434535e962` |
