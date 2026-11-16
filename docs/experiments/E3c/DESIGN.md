# E3c design: reading out a randomised e-mail test (Hillstrom), research use only

Draft, 2026-11-07 05:00 (CST), written while the E3a census runs; built on the facts in
[PROBE.md](PROBE.md). Nothing here is pre-registered. **The file has no licence from its author:
research use only, never redistributed, never in a commercial benchmark** (Gary, 2026-11-06). The
data stays in the local dataset folder; questions, truth and transcripts stay under gitignored run
folders, and reports carry aggregates only.

## Question

Asked what a randomised campaign did, how often does an agent deliver a conclusion the data does not
support (a non-causal comparison sold as an effect, a subgroup "win" found by searching, a pooled
effect that hides two different treatments, an interval that ignores how sparse spend is), and does
a harness that re-checks the conclusion against the per-customer data lower that rate without
refusing sound conclusions?

Analytics questions have one number; a readout has a sentence: "the men's e-mail raised spend per
customer by $0.77 [0.49, 1.05]". The error measured is the **conclusion**: its estimand, its
direction, its interval and whether the design supports it.

## Data

Kevin Hillstrom's MineThatData file (2008): 64,000 customers who bought in the last 12 months,
randomly split into thirds (men's e-mail, women's e-mail, no e-mail), with pre-treatment columns
(recency, history, men's / women's buyer, newbie, zip type, channel) and two-week outcomes (visit,
conversion, spend). Truth is the randomisation: intent-to-treat effects with intervals, computed from
the file by a fixed designer script, plus owner rules written before any question (below).

## Tasks

A small bank of readout requests in a stakeholder's words, each with a pre-written owner answer
key: which estimand is valid, what the effect and interval are, and which claims are unsupported.
Examples of the shapes (wording to be written by a model that has not seen the checks, as for E1):

- the effect of each e-mail on visits, conversion and spend per customer (ITT, two contrasts);
- "how much more did buyers spend" (the per-buyer comparison is not an effect: the honest answer
  says so, or reports spend per customer);
- "which customers should get the men's e-mail" (subgroups: pre-specified effect modifiers only,
  with a multiplicity note; a searched-for winner is unsupported);
- "did e-mail work" (pooled vs per-treatment: the men's e-mail is about 1.7 times stronger on visits);
- "what is the typical customer's gain" (99% of customers spend $0: a mean effect is not a typical
  one).

## Arms

| arm | what the agent has | delivered means |
|---|---|---|
| N | the file in a read-only database, Python with duckdb, pandas, scikit-learn, scipy | the agent states a conclusion and claims it is supported |
| H | harness tools for experiments: estimand-typed effect queries (ITT only; per-protocol and post-treatment conditioning refused, as `ml/uplift.py` already refuses `exposed`), a readout gate that re-reads the per-customer data behind the stated sentence | the readout gate returns ALLOW |

## Truth and grading

A conclusion is **supported** when its estimand is valid for the design, its point estimate and
interval match the designer's within the E3a tolerance (intervals: the same method family), and it
makes no unsupported claim from the answer key. Silent readout error: delivered and not supported.
Labels: E-select (conditioning on a post-treatment outcome), E-fish (an unadjusted searched subgroup),
E-pool (pooled treatments), E-scale (a typical or per-buyer claim from a sparse mean), E-calc,
E-read, E-other.

Grading needs judgement on the sentence. Plan: the agent also returns a structured claim
(estimand, contrast, outcome, estimate, interval, population), graded mechanically; the free text is
labelled by the designer with a blind second labeller (30%), as E1's error labels.

## Metrics (to pre-register)

Same shape as E1: R1 silent readout errors / attempts, R2 false holds / supported, R3 cost per
supported delivered readout, R4 pass^3.

## Harness work needed

The readout gate in `src/dsharness/readout/` checks conclusions about model evaluations (same
judge, paired items, repeat runs); the uplift tools are written for the synthetic retention program.
E3c needs an experiment world (the assignment column, outcomes, pre-treatment columns, pre-specified
modifiers) and readout checks for experiments: estimand valid for the design, effect and interval
re-computed from the per-customer rows, contrasts not pooled, subgroup claims pre-specified and
adjusted.

## Open questions

1. Whether one 64,000-customer file supports enough distinct questions for clusters (E1 had 45);
   the second randomised source with an explicit licence (Criteo uplift, CC BY-NC-SA) is
   non-commercial too.
2. Two-week outcomes only: questions about longer-term value have no truth and should be refused.
3. Whether the structured claim makes the task too easy for arm N (it names the estimand); a
   free-text-only variant tests whether agents pick the estimand themselves.

## Pilot: real agents without the harness (exploratory, done 09:25)

As for E3b, before any experiment tools are built: Claude Code (Sonnet) and Codex (GPT-6 Luna, high), arm N
only, each of the six stakeholder requests once (12 attempts), with the file as `campaign.duckdb`, a catalog
that states the design (random thirds, two-week outcomes) and Python with duckdb, pandas, numpy, scipy and
scikit-learn. The agent writes its reply to marketing (`answer.md`, ≤ 200 words) and every number it states
(`numbers.json`). The owner's answer key ([`experiments/E3c/tasks.py`](../../../experiments/E3c/tasks.py),
sha256 `c188c7bc8d914f73…`, runner `pilot.py` `cc25bad7883c3bf6…`) was fixed before the first attempt
(2026-11-07 09:13 CST):

| task | the request | supported readout | silent error it invites |
|---|---|---|---|
| effects | did the e-mails work (visits, purchases, spend) | each e-mail vs none, per customer sent, with intervals | per-buyer spend as an effect; one pooled effect |
| buyers | how much more did the e-mail make buyers spend | per-buyer spend is not an effect (the e-mail changes who buys; interval ±$23); spend per customer sent instead | E-select |
| target | which customers should get the men's e-mail | pre-campaign segments with intervals; the search across segments named | E-fish, E-select |
| compare | which e-mail was better, by how much | the direct contrast with its interval (spend: men's − women's +$0.35 [+0.03, +0.66], barely) | a winner claimed where the interval spans zero |
| typical | what a typical customer gains | the median customer spends nothing either way; the mean effect is carried by ~1% who buy | E-scale |
| annual | what the men's e-mail adds per customer over a year | two weeks measured; a yearly figure is an assumption | E-extrapolate |

The famous file may be in the agents' training data; this pilot only asks whether the errors occur at all.

**Result (09:13–09:25, 12 of 12 attempts delivered; designer labels, Haiku blind on all 12: 12 of 12 agree):**

| task | Claude Code | Codex |
|---|---|---|
| effects | supported: all six effects and intervals as the reference | supported |
| buyers | supported: "the e-mail changes who buys", per-buyer +$3 with an interval across zero, per customer instead | supported: "not a causal estimate for buyers" |
| target | supported: searched seven segment families, a formal heterogeneity test (p = 0.41), refuses to target | supported: the pre-specified split with intervals, multichannel named exploratory |
| compare | supported: direct contrasts, the spend gap "barely distinguishable from noise" | supported |
| typical | supported: an average, "most spend nothing" (side claim: the two e-mails' spend "ranges overlap", so the gap is unsettled; the direct contrast excludes zero, a conservative slip) | **E-scale**: "a typical customer … spent about $0.60 more"; the caveat says average including non-spenders, never that the typical customer gains nothing; both e-mails pooled |
| annual | supported: refuses to annualise, explains why ×26 is unsupported | supported: ×26 = $20 [$13, $27] labelled "a projection, not a measured annual result" |

Each attempt took 0.5–2.4 minutes, $0.04–0.06 (Claude) or 65k–142k tokens (Codex).

1. **One silent error in 12** (E-scale, Codex), on the question built to invite it. The others avoided
   every trap of the key: no per-buyer effect, no searched subgroup sold as a winner, no yearly figure as
   a result.
2. Both agents state the design's logic unprompted ("customers were randomly assigned, so …"), report
   spend per customer sent, and compute intervals that equal the reference to the cent.
3. Minor slips outside the key: judging two effects by overlapping intervals instead of their direct
   contrast (conservative here), pooled-only figures, an interval on a ×26 projection.

**What this means for E3c.** As in the E3b pilot, on a documented randomised test real agents read out
the effects correctly. A harness gate would have one error in 12 to catch, so E3c as designed could not
show an effect with k = 3 on a bank of this size. Recommendation: do not build the experiment-readout
tools now. Keep the pilot as the E3c finding ("on a documented randomised test, both agents read out
ITT effects correctly; the one slip is the typical-versus-average framing"). The harder variants all
test documentation rather than the harness (a catalog that does not say the assignment was random,
unequal assignment, an outcome window the question exceeds). Harness effort stays on analytics
definitions (E4) and pipeline fixes (E3d), where real agents make silent errors.

## v2: the full run, with the toolchain and a harness arm (plan, 2026-11-07 16:00)

Gary (11-07): the three pilots ran without the harness and without the tools a real team uses, so they are not
complete. The pilot finding stands ("on a documented randomised test both agents read out ITT effects correctly"),
but 12 attempts on six designer-written requests cannot bound the error rate (one error in 12: 0.2% to 38%), and
arm H was never run. v2 runs after the E3d census, one experiment at a time on two cores.

**1. Requests: a blind bank.** Two outside models (Luna, GPT-6-Luna high; Haiku 5.5) each write 20 requests from
[author_brief.md](author_brief.md) (sha256 `c10f7f37…`). The brief gives the catalog the agents see and asks for
varied requesters, purposes and wording, including requests the file cannot fully answer. It names no method, trap
or check. Ran 16:02-16:03 in the bwrap sandbox (`experiments/E1/run_authors.py --brief …`, output
`experiments/E3c/runs/authors`, gitignored). Near-repeats are dropped by a written rule before the designer writes
any key. The pilot's six requests are kept as a separate "seen" set.

**2. Owner's key per request.** The designer writes the key before any v2 attempt, in the form of `tasks.py`:
- what a supported readout does;
- which claims are unsupported;
- the reference numbers from a fixed script (`reference()` extended).

Luna reviews the key blind, as for the E3d truth.

**3. Arms** (same sandbox, same Python with statsmodels, polars, matplotlib and seaborn added, from `agent_env_dt`):

| arm | what the agent has | delivered means |
|---|---|---|
| N | `campaign.duckdb`, the catalog, Python | `answer.md` and `numbers.json` written (as the pilot) |
| H | N + the `dsh-experiment` MCP server: `describe_experiment` (design facts and the owner's analysis plan), `estimate_effect` (ITT difference in means with its interval; refuses filters on post-treatment columns), `check_readout` (the readout gate below) | the last `check_readout` on the delivered `answer.md` returned ALLOW |

**4. The experiment readout gate** (`src/dsharness/readout/experiment.py`, programs only; a model never decides). It
takes `answer.md` and structured claims, one per number: outcome, groups, population, estimand, estimate,
interval, role (result, exploratory, projection or assumption) and direction.

| check | REJECT | HOLD |
|---|---|---|
| exp.data | — | GX on the table: rows, sample-ratio χ² p < 0.001, conversion without visit, spend > 0 without conversion, pre-treatment balance (SMD ≥ 0.1) |
| exp.numbers_covered | — | a number in `answer.md` that no claim (or the request's own inputs) accounts for |
| exp.recompute | a claim's estimate or interval differs from the rows beyond tolerance | the claim cannot be recomputed |
| exp.estimand | a result on a population filtered by visit, conversion or spend, or a per-buyer comparison as an effect | — |
| exp.contrast | "higher/lower/better" where the interval includes zero | — |
| exp.prespecified | — | a subgroup result outside the owner's plan, not marked exploratory with the number of segments searched |
| exp.horizon | an effect beyond the two-week window given as a result | a projection without its assumption |
| exp.pooled | — | one pooled "any e-mail" effect as the result where the two e-mails differ on that outcome |

The E-scale slip (a mean effect called what "a typical customer" gains) is a wording error, and no check here sees
it. It is graded the same way in both arms. If H lowers it, that comes from the structured claims, not from a check.

**5. Size and rules.** 20 to 24 blind requests plus the 6 seen ones, 2 agents, arms N and H, k = 3: about 300
attempts, about 1–2.5 min each, about 6 hours on two cores. Metrics and rules follow E4/E3d:
- R1: silent readout error, H < N;
- R2: false holds in H ≤ 15%;
- R3: cost per supported delivery;
- pass^3.

All four use a cluster bootstrap over requests, labelled by the designer with a 30% Haiku blind sample. Before
the pre-registration is final, it gets a blind review by Luna.

### v2: after Luna's blind reviews of the plan (16:41–17:07; final 17:07)

Luna's review (`runs/review_prereg/work/REVIEW.md`) had four blockers. The answers are in the pre-registration
(section "Luna's blind review"). The main changes from the plan above:
- **Scope of the gate.** It certifies numbers and declared structure, not the meaning of the prose. The codes are
  split into gate-visible and gate-blind before the census, and tests pin the known limits.
- **exp.contrast.** It rejects only against the sign of the estimate. "Higher" over an interval that includes zero
  now holds, with the schema's meaning of each word in the reason.
- **Ratios.** A ratio's interval is accepted against either delta method. Otherwise it is held, not rejected (table
  below).
- **exp.pooled.** It also tests the difference between the e-mails among everyone assigned. Group means of different
  groups among buyers (or visitors), given as results, are rejected like a per-buyer comparison.
- **Delivery.** Arm H's delivery binds both `answer.md` and `claims.json` by sha256.
- **Labels.** Every sheet is blind, two labellers (designer subagents, Haiku) label every reply, and disagreements
  are adjudicated blind.
- **ha17.** Run in the smoke test, so it is moved to an "exposed" set: 39 held-out requests remain.
- **Power.** A simulation (`power_v2.py`): at the pilot's rate the rule is likely met only if the harness catches
  about three quarters of the errors.

Interval methods against the gate (`experiments/E3c/ci_methods_v2.py`, 16:44). The column gives the largest shift of
an interval end, as a share of the gate's interval width, over Welch's t, OLS HC1 (with and without pre-campaign
covariates) and the percentile bootstrap for means, and the linear delta method and the bootstrap for ratios.

| estimand | outcome | comparison | population | gate interval | largest end shift / width |
|---|---|---|---|---|---|
| mean difference | spend | men's − none | all | [0.485, 1.055] | 0.017 |
| mean difference | spend | women's − none | all | [0.169, 0.680] | 0.005 |
| mean difference | spend | men's − women's | all | [0.033, 0.658] | 0.015 |
| mean difference | conversion | men's − none | all | [0.0050, 0.0086] | 0.007 |
| mean difference | visit | women's − none | all | [0.039, 0.052] | 0.017 |
| mean difference | spend | men's − none | women's-only buyers | [0.270, 0.962] | 0.005 |
| mean difference | spend | women's − none | men's-only buyers | [−0.124, 0.680] | 0.026 |
| mean difference | spend | men's − none | rural newbies | [0.121, 2.22] | 0.067 |
| mean difference | conversion | women's − none | rural newbies | [−0.0009, 0.0118] | 0.026 |
| relative lift | spend | men's / none | all | [0.628, 1.916] | 0.079 |
| relative lift | spend | women's / none | all | [0.218, 1.236] | 0.083 |
| relative lift | spend | men's / women's | all | [0.027, 0.699] | 0.068 |
| relative lift | conversion | men's / none | all | [0.768, 1.709] | 0.057 |
| relative lift | visit | women's / none | all | [0.356, 0.500] | 0.013 |
| relative lift | spend | men's / none | women's-only buyers | [0.429, 2.559] | 0.129 |
| relative lift | spend | women's / none | men's-only buyers | [−0.136, 1.270] | 0.137 |
| relative lift | spend | men's / none | rural newbies | [0.425, 8.648] | 0.291 |
| relative lift | conversion | women's / none | rural newbies | [−0.120, 3.335] | 0.441 |
