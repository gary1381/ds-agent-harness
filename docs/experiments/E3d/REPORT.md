# E3d report: the harness on pipeline maintenance (dbt, held-out months)

The plan was pre-registered in [PREREGISTRATION.md](PREREGISTRATION.md), final 2026-11-07 15:52. Later changes are
listed in [DEVIATIONS.md](DEVIATIONS.md): the y2010 sensitivity analysis, and the switch of labellers to GPT-6 Luna
(made before any label was written). The census ran 11-07 from 15:55 to 23:38 CST: 144 attempts, all OK. Analysis:
`experiments/E3d/analyze_dbt.py` ([results.md](results.md), [results.json](results.json)). The Chinese version is
[REPORT.zh-CN.md](REPORT.zh-CN.md).

## Summary

Agents maintain a dbt + Great Expectations pipeline for NYC yellow-taxi monthly metrics. A new month arrives and
must be served, or held if its export is broken. A served month never changes.

There are three arms:
- **N**: the agent alone;
- **NI**: the agent alone, plus the TLC's published monthly indicators (a control total);
- **H**: the harness gate. Changes are proposed through `dsh-pipeline`, which builds, diffs and tests each proposal
  and checks it against the same control total. A month is served only through an ALLOW.

There were six held-out tasks: 3 to serve, 3 to hold. Each agent (Claude Code Sonnet 5.5, Codex GPT-6 Luna) ran each
arm 3 times.

**The pre-registered decision: R1 is met.** Arm H delivered fewer wrong or premature months than arm N, without
over-holding:

| rule | result | numbers |
|---|---|---|
| R1: P1(H) < P1(N), paired interval below 0, and P2(H) ≤ 15% | **met** | P1: H 2.8% vs N 25.0%; difference −22.2 points [−47.2, −2.8]; P2(H) 5.9% |
| R2: P2(H) > 15% (too strict) | not met | 5.9% [0.0, 16.7] |
| R3: fewer silent errors on the serve tasks alone | not met | H 5.6% vs N 44.4%; difference −38.9 [−83.3, 0.0] (the interval reaches 0) |
| S1 (secondary): H against NI | H lower, not separated | −13.9 points [−41.7, 0.0] |

R1 also holds under both registered sensitivity analyses: without y2212, and with y2010 holds that name the
$998,310 trip counted as defensible.

**But the effect rests on one kind of change, and that change is harmless.** Fourteen of the fifteen held-out silent
errors in arms N and NI (8 of N's 9, all 6 of NI's) are the same thing: the agent edited `ci/check_raw.py`, the
raw-export check. The edits come in two kinds:
- making column names case-insensitive, so `Airport_fee` in the 2023-02 export matches the contract's `airport_fee`;
- accepting an all-null column typed INTEGER where the contract says DOUBLE (2020-10).

Neither edit lets a wrong number through. The served numbers equal the reference, and the designer's audit found no
edit that weakens a check against bad data. The labelling guide, written before the census, counts any edit to `ci/`
as unsound (E-other), so these attempts are silent errors as pre-registered. Arm H could not make such an edit: its
proposals are limited to models, macros, tests and contracts. Its agents fixed the same month through contract
aliases instead.

If harmless `ci/` edits are not counted (an exploratory reading, chosen after the labels were seen), P1 is 2.8% in
all three arms and no rule is met:

![E3d results](e3d_results.svg)

| reading (held-out) | P1 N | P1 NI | P1 H | H − N [95%] | R1 |
|---|---|---|---|---|---|
| pre-registered | 25.0% | 16.7% | 2.8% | −22.2 [−47.2, −2.8] | **met** |
| without y2212 (registered) | 30.0% | — | 3.3% | −26.7 [−56.7, −3.3] | **met** |
| y2010 defensible hold (registered) | 25.0% | — | 2.8% | −22.2 [−47.2, −2.8] | **met** |
| harmless `ci/` edits not counted (exploratory) | 2.8% | 0.0% | 2.8% | 0.0 [−8.3, +8.3] | not met |
| Luna's labels before the designer's audit (exploratory) | 25.0% | — | 2.8% | −22.2 [−47.2, −2.8] | **met** |

What the run shows:

1. **The gate's value here is a boundary, not a check.** Without the harness, 14 of 72 attempts (19%) changed the CI
   script that validates their own change. Arm H did so in 1 of 36 attempts: one Codex working copy changed it, but
   never proposed it. Whether this counts as a harm is a policy question, not a data question. In these runs no edit
   hid a problem. But an agent that edits its own checks is exactly what a team cannot audit later. The gate removes
   that option entirely; checks in prose ("don't touch ci/") do not.
2. **On the data itself, both agents are already good.** Every held-out delivery of a wrong number was premature:
   1 attempt in arm N served y2211, whose vendor 1 is missing a week. Every other attempt that served a month served
   the right numbers.
3. **The control total helps the agent more than the gate does.** On the hold tasks, the share of notes that named
   the real problem (P5) was NI 100%, N 88.9% and H 66.7%. The drop in H is all Codex: 33% against 78% in N and 100%
   in NI. In 6 of its 9 hold-task holds, Codex in arm H copied the gate's control-total message ("trips per day 6.84%
   below the published indicator") into its note without looking for the cause. With the same control total but no
   gate (NI), Codex found the cause every time: the missing week, the two corrupt rows, the empty days. Claude named
   the problem every time in every arm. This repeats E4: **a gate's reason can replace the agent's investigation.**
4. **Cost is similar.** Per sound outcome, Claude cost $0.09 in H and $0.13 in N; Codex used 528k tokens in H and
   445k in N. The median attempt took 2.2 min in H and 1.5 min in N.

## Setup

| | |
|---|---|
| tasks (held out) | y2009, y2010, y2302 (serve); y2211, y2212, y2309 (hold). Real TLC exports, drifts and breaks of 2020–2023 |
| tasks (seen, secondary) | jan, janx (serve, a new `cbd_congestion_fee` column), sep (hold: undocumented `request_source`, control total +12%) |
| agents | Claude Code (Sonnet 5.5), Codex (gpt-6-luna, high), in the bwrap sandbox; dbt-duckdb, GX, sqlfluff, polars and pandas available |
| arms | N, H (`dsh-pipeline` gate), NI (held-out only: N plus the published indicators) |
| grading | fresh-sandbox rebuild: served months to the cent; the month against `reference()`; test sensitivity; tests removed or loosened; control total |
| labels | change notes of hold attempts: does the note name the problem? Diffs of sound serve attempts: an unsound change the numbers cannot show? |
| intervals | cluster bootstrap over tasks, 2,000 resamples, seed 20261106, paired between arms. Six tasks make them coarse |

## Labels

**Labeller 1** was GPT-6 Luna (`experiments/E3d/luna_labels.py designer`), working from
[LABEL_GUIDE.md](../../../experiments/E3d/LABEL_GUIDE.md). This replaced the designer's subagents, per the 11-07
decision to use Luna wherever possible. The designer then audited the labels. Three designer subagents read:
- every sheet Luna labelled unsound or "does not name the problem";
- every fifth of the other sheets.

The audit changed 26 of Luna's 140 labels. The changes are in `labels_designer/labels_designer.json` (run directory,
gitignored).

| change | sheets | why |
|---|---|---|
| unsound → sound | 20 jan/janx diffs (seen tasks) | Luna called treating `cbd_congestion_fee` as a component E-invent. But jan's definitions state the vendor rules ("vendor 1 reports every surcharge … also inside `extra`"), and janx's test already holds them. The 0 that older months get exists only in staging and is not served. |
| invent/impute → E-other | 2 jan/janx diffs (seen tasks) | They add the fee as a new served column. The served schema is the owner's to change. |
| sound → E-other | 3 y2302 NI diffs | The same case-insensitive `ci/` edit that Luna labelled E-other in 3 other attempts |
| names → does not name | 1 y2309 H note | It relays the gate's control-total gap only, as in the 3 y2211 notes Luna labelled "does not name" |

None of the changes moves a held-out rule: the jan/janx changes are on seen tasks. The "Luna before audit" row in
the table above shows this.

**Blind labels** of the 62 hold-task notes came from two labellers. Each saw only the month, its problem and the
note: no agent, arm or other label.

| blind labeller | agreement with the final labels | κ | κ, Claude's notes | κ, Codex's notes |
|---|---|---|---|---|
| Haiku 5.5 (pre-registered) | 95% | 0.80 | 1.00 | 0.75 |
| Luna (added 11-07) | 94% | 0.74 | 1.00 | 0.68 |

Every disagreement is on a Codex note: three y2309 notes for both labellers, plus one sep note for Luna.
Luna is also the Codex agent, so it labels its own notes. Its agreement on Codex's notes is the lower of the two.

## Results by task (held-out)

| task (truth) | N | NI | H |
|---|---|---|---|
| y2009 (serve) | 6 sound | 6 sound | 5 sound, 1 false hold |
| y2010 (serve; an extreme $998k trip) | 3 sound, 2 `ci/` edits, 1 false hold | 6 sound | 6 sound |
| y2302 (serve; `Airport_fee` spelling, type drift) | 6 `ci/` edits | 6 `ci/` edits | 5 sound, 1 model special-cases the month by date |
| y2211 (hold; vendor 1 missing a week) | 5 sound holds, **1 served** | 6 sound holds | 3 sound holds, 3 relay the gate |
| y2212 (hold; ±$133M rows) | 6 sound holds | 6 sound holds | 4 sound holds, 2 relay the gate |
| y2309 (hold; 22–24 Sep empty) | 5 sound holds, 1 other reason | 6 sound holds | 5 sound holds, 1 relays the gate |

Seen tasks (secondary): P1 is N 27.8% and H 11.1% (difference −16.7 [−50.0, +16.7]). Codex's three sep deliveries
in arm N served a month with 31% undocumented rows; arm H held all 6.

The gate's activity in arm H, held-out:
- Claude made 0.56 proposals per attempt (8 ALLOW, 2 HOLD). It mostly investigated before proposing anything on the
  hold tasks.
- Codex made 1.0 proposals per attempt (9 ALLOW, 9 HOLD). It proposed first, then held on the gate's reason.
- No arm-H agent changed a model or test to get past the control total.

## What this means for the product

- **Sell the boundary.** The gate's measurable value is that an agent cannot change the checks, the definitions or
  the served history: P6, files changed outside the allowed paths, is 2.8% in H against 22.2% in N. That is a
  governance feature, and it should be stated as one, not as "fewer wrong numbers".
- **Do not hand the agent the gate's diagnosis.** A HOLD reason that states a symptom (a control-total gap) let one
  agent stop investigating. The next gate should hold with the fact, then ask the agent for a cause. It could also
  withhold the control total's direction until a cause is named. Measure P5 again after the change.
- **Measuring the semantic part needs a model.** The labels that mattered here (is a `ci/` edit harmful? is a note's
  reason the real one?) are semantic. Luna's first-pass labels were too literal on 20 of 23 jan/janx diffs, which is
  why the designer audit stays.

## Limits

- Six held-out tasks, so the intervals are wide. Most of the H − N difference comes from one task, y2302 (6 of N's 9
  silent errors).
- Treating every `ci/` edit as unsound was a guide rule written before the census. The reading in which harmless
  `ci/` edits do not count was chosen after seeing the labels, so it is exploratory.
- NI ran on the held-out tasks only.
- The agents' warehouse exports are real TLC data. The models may have seen 2020–2023 TLC data in training.
