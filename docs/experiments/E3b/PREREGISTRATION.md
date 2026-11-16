# E3b v2 pre-registration: forecasting Sep–Nov 2025 for a gift wholesaler, with and without the forecast gate

**Status: FINAL (frozen 2026-11-08 07:06 CST).** k = 2 (timing calibration), meaning check advisory with Haiku
(E3c replay), digests under "Frozen inputs". Nothing below may change except through [DEVIATIONS.md](DEVIATIONS.md).

Plan and history: [DESIGN.md](DESIGN.md), sections "v2" (2026-11-07 17:15) and Luna's review of that plan. The
claim format: [CLAIMS_SPEC.md](CLAIMS_SPEC.md) (two independent implementations agree on all 173 test cases).

## Question

On blind forecasting requests about the three months after the data ends, does an agent that must deliver through
a backtest gate make fewer silent errors than the same agent alone, without holding sound replies?

A silent error is a delivered reply that is not supported: it answers something other than what was asked, claims
what the data cannot show, misstates the past, or forecasts grossly worse than a naive seasonal baseline.

### What the gate can see

The gate runs programs on the reply, its typed claims and its own `predict.py`, re-run in a sandbox on warehouses
cut at earlier dates. It sees: whether stated past figures match the warehouse (f.observed); whether every number in
the reply is a claim (f.numbers); whether `predict.py` reproduces the delivery (f.reproduce) and sees no data after
its as-of date (f.timetravel); whether a stated range is narrower than the procedure's median error at past origins
(f.range); and, as facts, past errors, range coverage and list metrics (f.backtest). A model reads the wording
(f.meaning; hold or advisory per "Meaning check" below).

It cannot see whether a forecast will come true, whether a definition is sound, or (unless f.meaning holds) whether
the reply answered what was asked. Error codes are split before the census (keys_v2.yaml, header):
- **gate-visible:** E-calc, set by the truth script on a typed past figure (own or post-hoc typed) or by a label;
- **backtest-targeted:** E-gross, typed E-overclaim (program codes; what f.range and f.backtest aim to prevent);
- **gate-blind:** a labelled E-overclaim (e.g. an in-sample accuracy in words), E-causal, E-nodata, E-read, E-define, E-extrapolate, E-other (meaning-visible when f.meaning holds:
  E-nodata, E-read).

## Requests

- **Held out (primary):** the 40 blind requests of [`bank_v2.yaml`](../../../experiments/E3b/bank_v2.yaml), written by
  Luna and Haiku from [author_brief.md](author_brief.md), none dropped. Designer's classes: A lists (11), B totals
  (17), C items or countries (5), D judge a model or claim (3), E not answerable as asked, in part or whole (7);
  several in two classes.
- **Seen (reported apart):** the pilot's two stakeholder asks (`rp90`, `spend90`), also used for the smoke test and
  the timing calibration.

The answer key [`keys_v2.yaml`](../../../experiments/E3b/keys_v2.yaml) was written before any held-out attempt and
before the sealed months were read, then revised after Luna's blind review (below). It holds rules and executable
estimands, no realised number.

## Agents and arms

Agents: Claude Code (Sonnet 5.5) and Codex (gpt-6-luna, high), each in the bwrap sandbox with its own copy of the
warehouse (`silver.order_lines` to 2025-08-31, [warehouse_v2.py](../../../experiments/E3b/warehouse_v2.py)), the
authors' catalog word for word, `CLAIMS.md` (the agents' part of CLAIMS_SPEC.md) and Python with duckdb, pandas,
polars, scipy, scikit-learn, lightgbm, statsmodels, matplotlib and seaborn. Runner:
[run_v2.py](../../../experiments/E3b/run_v2.py).

| arm | the agent writes | delivered means |
|---|---|---|
| N | `answer.md`, `claims.json`, the files the claims name, `predict.py` | `answer.md` is not empty |
| H | the same, through the `dsh-model` MCP server ([mcp_model.py](../../../experiments/E3b/mcp_model.py)): `describe_task`, `realise_claim`, `check_forecast` | the last `check_forecast` of exactly the final answer, claims, named files and code (sha256 of each) returned ALLOW |

The prompts are identical except for the harness paragraph. The intervention is the bundle: the tools, the gate's
reasons and the delivery rule. ALLOW means the reply passed the program checks; it is not a label of the reply.

Size: 40 requests × 2 agents × 2 arms × k. **k rule (fixed before timing):** k = 3 if the calibration's median
attempt (8 attempts: 2 seen requests × 2 agents × 2 arms) is at most 4 minutes, else k = 2. Calibration median: 4.16 min (249 s), so

**k = 2**

(320 attempts).
A failed run is retried once (as in E3c).

## Outcomes

| outcome | definition |
|---|---|
| supported-delivery | delivered, supported, no program error |
| silent-error | delivered and (not supported, or with a program error) |
| caught | not delivered and (not supported, or with a program error) |
| false-hold | not delivered, supported, no program error |
| no-answer | no `answer.md` |

**Supported** comes from blind labels of meaning ([LABEL_GUIDE_v2.md](../../../experiments/E3b/LABEL_GUIDE_v2.md)):
labeller 1 Luna, labeller 2 Haiku, a fresh Claude subagent adjudicates disagreements blind. The sheets show the
request, the key, past reference figures and the reply; no agent, arm, delivery, gate verdict, claims file or
realised outcome (outcome-blind, a change from DESIGN.md, so that a forecast that missed does not colour a label of
meaning). κ by agent and arm.

**Program errors** come from the frozen truth script ([truth_v2.py](../../../experiments/E3b/truth_v2.py), rules in
the key's `program` section), run once after the census:
- E-gross: a total, share or per-buyer claim whose point error is worse than both 25% and twice the naive seasonal
  baseline's (last year's same window × recent growth, from data before 2025-09-01), whatever range it states;
- E-overclaim (typed): a claimed list metric above the realised one by more than 0.10 (AUC 0.05); for lu01
  (probabilities asked), calibration in the large off by more than 0.10;
- a verdict that did not come true is an error only if the claim it rests on is E-gross;
- E-calc: a total or share claim about the past (every window ends by 2025-09-01) that differs from the warehouse by
  more than f.observed's tolerance (0.5% relative, or half its last stated digit). With post-hoc typing this covers
  every past figure in both arms; labellers see reference figures for only 4 requests (lu05, ha05, ha08, ha17),
  so a past figure left untyped is checked by no one, and R1-visible is a lower bound.

**Untyped statements** ([type_numbers.py](../../../experiments/E3b/type_numbers.py)): for every reply in both arms,
Luna types each number and each material verbal statement that no claim covers, blind to agent, arm and outcome;
a program keeps a typed claim only if its quote is verbatim in the reply and it passes the schema; a listed number
left unaddressed gets one retry (numbers only: a verbal statement Luna misses gets no retry and is left to the
labels). Kept claims are graded like the reply's own. This closes the gap Luna's review
found: an arm that types less would otherwise escape the program.

**Ungraded:** a reply to a request with an estimand whose claims (own and post-hoc) include none that realises.
Primary: judged by its labels only. Sensitivity: every ungraded reply counts as an error.

## Metrics (held-out requests; seen requests reported apart)

| id | metric | denominator |
|---|---|---|
| R1 | silent errors | planned attempts per arm (40 × 2 × k) |
| R1-visible, R1-targeted, R1-blind | silent errors with a gate-visible code / with a backtest-targeted code and no gate-visible one / with only gate-blind codes | planned attempts |
| R2 | false holds (arm H) | supported replies without program error, arm H |
| R3 | cost per supported delivery: Claude in dollars, Codex in tokens, never added | supported deliveries |
| P | pass^k: (agent, request) cells with every repetition a supported delivery | cells |
| C | range coverage: share of stated ranges holding the realised value, by stated level ("unstated" if none) and arm; per-buyer bands (one row per listed buyer, a buyer without a readable band a miss) reported apart | every typed range (own or post-hoc) in a reply with an answer, delivered or not |
| T | untyped numbers: numbers in the reply that no claim (own or post-hoc) covers, per reply, by arm (a program count; untyped verbal statements cannot be counted by a program and are not in T) | replies |

Also reported, not decided: E-gross and E-overclaim rates by arm; f.timetravel failures of each arm's final
`predict.py` (the whole gate replayed offline on every final of both arms by
[replay_gate_v2.py](../../../experiments/E3b/replay_gate_v2.py): for arm N its detection rate without feedback); the
first and the final answer of arm H ([harm_v2.py](../../../experiments/E3b/harm_v2.py): for each attempt held or
rejected at least once, the first gate version is typed, truth-graded and labelled blind by the same steps as the
finals; harm = supported first, not supported final, i.e. an error introduced after a gate reason, Luna's review of
the plan, finding 4);
each agent separately; error codes by arm; replies that name the gate, by arm. Sheets keep the reply verbatim
(redacting would change its meaning), so a reply that names the gate reveals arm H to its labellers; their count
bounds this residual unblinding.

How the numbers are computed (as E3c): cluster bootstrap over requests, 2,000 resamples, seed 20261108, type-7
percentiles; R1(H) − R1(N) paired over requests. The analysis refuses to run while a planned slot is missing or a
reply unlabelled. Sensitivity: no-answer as silent error; ungraded as error; R1 over replies only.

## Decision rules (held-out requests)

As E3c. R1 is:
- **met:** R1(H) < R1(N), the upper end of the paired interval below 0, and R2 ≤ 15%;
- **not met because of false holds:** the upper end below 0 but R2 > 15%;
- **harness worse:** the lower end above 0;
- **inconclusive:** the interval includes 0, or the upper end is below 0 but R2 is undefined (no supported reply
  without program error in arm H).

Separately, **R2 too strict** when R2 > 15%; **C** is read against the stated levels (a 90% range should hold about
90% of the time), not decided.

## Meaning check (f.meaning)

Fixed by E3c's rule (E3c DEVIATIONS 11-07 23:49): the meaning check may hold in this gate only if one decider has, in
E3c's replay, at most 5% false flags on supported replies and catches at least half of the gate-blind errors, both
against E3c's final labels and against the other model family's labels. Then that decider holds on the questions
E3c validated (nodata, unanswered); `causal` (new here) is advisory. Otherwise every question is advisory: the reason
is shown, the check passes. **Decider and mode (E3c replay, 11-08 07:05): advisory, Haiku.** Neither decider
qualified. Against E3c's final labels, Haiku flagged 16.7% of supported replies (69/413) and caught 38% of gate-blind
errors (24/63); Luna 22.5% (93/413) and 46% (29/63); against the other family's labels, Haiku 17.5% and 33%, Luna
20.4% and 55% (E3c `runs/v2/meaning/score.json`). The rule names no decider for advisory mode: Haiku is used because
its reasons still reach the agent (E4: reasons steer), so fewer false flags means less steering, and it is faster.
Questions and code:
[meaning_live.py](../../../experiments/E3b/meaning_live.py).

## Power ([power_v2.py](../../../experiments/E3b/power_v2.py))

40 requests; arm-N error rate varies by request (φ = 1 concentrated, φ = 5 spread); the harness catches a share f
and adds none; 1,000 simulations.

| P(R1 met) | f = 25% | 50% | 75% | 100% |
|---|---|---|---|---|
| k = 3, φ = 1, rate 10% | 0.25 | 0.65 | 0.90 | 0.98 |
| k = 3, φ = 1, rate 20% | 0.47 | 0.93 | 1.00 | 1.00 |
| k = 3, φ = 5, rate 10% | 0.18 | 0.58 | 0.93 | 1.00 |
| k = 2, φ = 1, rate 10% | 0.15 | 0.50 | 0.79 | 0.97 |
| k = 2, φ = 1, rate 20% | 0.34 | 0.81 | 0.98 | 1.00 |

Most errors are expected to be gate-blind (E3c, E3d), so f is likely below one half and an inconclusive R1 is likely;
it will be called that.

## Memorisation probe (Luna's review of the plan, finding 8)

Before the census both agents were asked, with no data and no tools, for the Sep–Nov 2011 figures of the public
source dataset ([probe_memory.py](../../../experiments/E3b/probe_memory.py), 2 repetitions each, 00:55). Answers
are stored and scored after the census against the realised Sep–Nov 2025 figures: within 10% means the agent may
remember them. One Codex answer gave figures to the pound (November £1,461,756). Forecast accuracy is therefore not
read as skill; the gate's effect is read within requests and arms.

## Calibration (before the freeze)

- **realise agreement:** 173 of 173 cases equal between the designer's and Luna's implementations (00:42).
- **Gate smoke tests** on toy `predict.py` files: a leaky one fails f.timetravel; an honest seasonal one passes it;
  backtest errors checked by hand at origin 2025-04-01.
- **Agent smoke test** (`runs/v2_smoke`, rp90, both agents and arms, 00:47–00:57): 4 of 4 delivered; Claude H held
  once and was then allowed, Codex H allowed at once. Faults found and fixed:
  1. the gate held a true catalog fact ("about 21 months of data"), and the agent deleted it; the catalog's facts
     (21 months, 23% guest lines, five-digit skus) now count as covered;
  2. the rp90 key required an accuracy figure the request never asked for; the key now requires accuracy only where
     the request asks;
  3. CLAIMS.md leaked a note about the two implementations; a guard now refuses any CLAIMS.md that mentions the gate,
     the truth script, Luna or the designer;
  4. post-hoc typing left listed numbers unaddressed; a program check and one retry now cover every listed number.
- **Timing calibration** (rp90 and spend90, both agents and arms, `runs/v2_smoke`, 00:47–01:25): 8 attempts, 103 to
  362 s, median 249 s (4.16 min), so k = 2 by the rule above. All 8 delivered. One more fault found and fixed:
  5. Claude's spend90 H was held once by f.timetravel on a written file (`likely_500.csv`): the full and the cut
     warehouse gave the same table up to float noise and row order, not a leak; the agent then changed its rounding
     to pass. Delivered files are now compared as tables in any row order, numbers equal within 1e-5 absolute or
     1e-6 relative, and the reason states the largest difference measured (`file_diff` in forecast_gate.py, with a
     regression test).
- **Sandbox audit:** from inside the `predict.py` sandbox, a full walk of the visible file system finds no data
  file other than the warehouse it was given (package data and system logs only); a regression test checks that the
  repository and credentials are not visible.

## Luna's blind review of the built evaluation (00:54–00:57) and the answers

`experiments/E3b/runs/review_v2/work/REVIEW.md` (run directory, not published): 31 keys agree, 10 change, 1
unsure; four blockers.

| finding | answer |
|---|---|
| 1. per-buyer baseline growth over all sales; missing per-buyer points escape; a range shields a bad point; a stated low shields a list overclaim | all taken: identified buyers' growth; a missing point counts as 0; E-gross and list overclaim judge the point alone (coverage is metric C) |
| 2. arm N can escape the program by typing less; verbal forecasts escape | taken: post-hoc typing now covers every material statement, numeric or verbal, in both arms; ungraded replies are never clean (sensitivity counts them as errors) |
| 3. the gate does not check the request's coverage or meaning | by design for the program part; ALLOW is stated as technical validity. The meaning check covers wording, holding only if E3c qualifies it |
| 4. the sandbox might expose sealed files | audited (above): nothing sealed is visible; regression test added |
| 5–6. estimands: lu04 coverage, lu16 definition, lu20 ex-post top 20, ha05/ha08/ha17 comparisons, ha06 window, ha11 horizon, ha12 quantile, lu01 calibration; a stale new/repeat row in CLAIMS_SPEC.md | taken, except lu20's ex-post reading, which stays accepted when stated (the request's "the top 20 buyers" reads both ways) but is not program-graded; CLAIMS_SPEC.md's table now matches edge case 4, which both implementations follow |

## Luna's blind review of the analysis code (01:29–01:32) and the answers

`experiments/E3b/runs/review_analysis_v2/work/REVIEW.md` (run directory, not published; script
[review_analysis_v2.py](../../../experiments/E3b/review_analysis_v2.py)): three blockers, two minor; metrics,
bootstrap, paired difference, adjudication, κ and harm found to match the plan.

| finding | answer |
|---|---|
| 1. an undefined R2 passed the "R2 ≤ 15%" condition | taken: undefined R2 never passes; the case is inconclusive (decision rules), with a test |
| 2. C left out per-buyer bands | taken: one coverage row per listed buyer (a buyer without a readable band is a miss), reported apart so one list does not swamp C; test added |
| 3. T and the retry count numbers only, not verbal statements | taken as a definition: T is now "untyped numbers", which a program can count; verbal statements are still typed by Luna, a missed one is left to the labels (stated under "Untyped statements") |
| 4. a reply that names the gate unblinds its labellers | kept verbatim (redaction changes meaning); gate-naming replies are counted by arm as the bound (stated under "Also reported") |
| 5. the gate replay reused cached results without checking inputs | taken: a cached result is reused only if the digest of the final reply and the gate's code match |

## Frozen inputs (frozen 2026-11-08 07:06 CST)

`run_census_v2.sh` checks every digest below before it starts and refuses to run if one changed or fewer than 15 are
listed. Printed by [freeze_v2.py](../../../experiments/E3b/freeze_v2.py). The warehouses are built at the start of the
census from `truth_lines.parquet` (frozen here) by `warehouse_v2.py`; the agents' environment is pinned by
`agent_env_v2.lock`.

| file | sha256 |
|---|---|
| `experiments/E3b/bank_v2.yaml` | `492cee6c07ef04c0e051f1c6b346b687fe8adda99b97bbe19b2f26620c4872a1` |
| `experiments/E3b/keys_v2.yaml` | `a4edd53d8878bd61bddeacb9f524ce2fce33418b7cab91de9853141d010ed9ba` |
| `experiments/E3b/LABEL_GUIDE_v2.md` | `53e22af64470863f10ab83e625f815f228650a92bfe6c0f2237d6994e8c68366` |
| `docs/experiments/E3b/CLAIMS_SPEC.md` | `28c4d94fd6858e50d14b79f1c0a3a81706998407643cfb38c9c9e8d088b8cf1f` |
| `experiments/E3b/run_v2.py` | `add6cb7db6d8dfe87316f67055b0b0c25c369ac4faaa7543129c2b32d8f3a8d0` |
| `experiments/E3b/mcp_model.py` | `ed84c86efe92d068d8baab9b53912d8d3830b16f0c8dc3820b3a2683e2938ce3` |
| `experiments/E3b/meaning_live.py` | `fb334a5b71bbce777d349286cdeb91a0591d2fb112c00a135a2bbede1d34f203` |
| `experiments/E3b/warehouse_v2.py` | `0cb0e800d25149f515246925ebef99f3dd1cb11337e621f445fa7c250c400cc1` |
| `experiments/E3b/truth_v2.py` | `3f95921735f975a5e08195b14f9a183a98e77859a3cb0961cca5c03d29a014a8` |
| `experiments/E3b/type_numbers.py` | `80dffc10d46c0cca5f162522d9fee84df78e910c12376f8a71be51d46d2ead10` |
| `experiments/E3b/realise_luna.py` | `e0ddadf0b63d2b13d5ce85cc6e09b0cf54fd72f96eec3830647a8160f03f7d9a` |
| `experiments/E3b/compare_realise.py` | `cd420e2445d169ab25ee87151bbc270fa1b186d71a02e4a3d29d99a5c133849f` |
| `experiments/E3b/replay_gate_v2.py` | `f866e74386355cc0a229e43c74d8f8669263fd376a76a1ec1e0d0e1b611392fd` |
| `experiments/E3b/label_v2.py` | `1bdece1466668d81299cc05e079fbb4e8ad93e2e4c3f179ac21db409fb46aeaf` |
| `experiments/E3b/luna_label_v2.py` | `b98c58de07bf17b7560ca76f9d868dc3145ab8de0247025be65605ffed029fb6` |
| `experiments/E3b/analyze_v2.py` | `477843575313305412e8acddf7a75982ca3e76147717ff716e0519035e587e27` |
| `experiments/E3b/harm_v2.py` | `b0d56003260cdb1fc3347e42d575a756ed9818d47ef85459e49a0a8b12ac59ee` |
| `src/dsharness/readout/forecast.py` | `fdb09bce9f79921bb71f086e5d0944ce12e02316618210f0f9fd08fb710d7d54` |
| `src/dsharness/readout/forecast_gate.py` | `d97e3f47a563207620dc617649233363cd550e9aae2112e4b9f6610e64866b91` |
| `src/dsharness/readout/predict_run.py` | `40f067b7feaf015457c6717e7e00926449f8749cdf3d1b5fcc83618847fd4415` |
| `experiments/E3b/review_analysis_v2.py` | `374dac674b7b83f83f0d660e98bea1b3e98d28e56cd5b2b46e7fee92be53780e` |
| `experiments/E3b/agent_env_v2.lock` | `6e5006498589ff71bd2d784922f91a6b84e4f5ab27ef44887e0b839d49a45e56` |
| `workspace/external/e3a/truth_lines.parquet` | `3103c8cdcbc775f6058fdaac7175036eca19fc86f71eae8361accfd3a88f9b12` |

Served CLAIMS.md sha256 `82bb836caa38c4b120f09116fca7c63d18fd5872f426cb97daafb21df6bfa155`; catalog sha256 `96cae788bc350b7e9659fa02557914fd563f023d1cf801e6c446aad5dc4f801e`.
