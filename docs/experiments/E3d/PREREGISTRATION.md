# E3d pre-registration: maintaining a real dbt pipeline, with and without the harness's pipeline CI

**Final**, 2026-11-07 15:52 (CST), before the first census attempt.
- Gary confirmed the truth of the routine holds (y2211, y2212, y2309) at 15:30.
- A blind review (GPT-6-Luna, plan and code, 15:32–15:34) found three problems. All three were addressed before the
  freeze:
  - **y2212's truth rested on judgement alone.** The held-out projects' definitions now carry the owner's rule: "a
    month whose export looks incomplete or corrupt is not served: report what you found, and the owner decides what
    the month should contain."
  - **R1 could not separate the gate from the information it holds.** R1 is now read as the whole harness package:
    the gate's checks plus an independent control total. A secondary arm NI gives arm N the same control total.
  - **The analysis kept only successful runs.** Every scheduled attempt now stays in the denominator, and a failed
    run is retried once.
- A smoke test (15:36–15:42, not part of any result) found the 2020 definitions contradicting the data on vendor 6.
  All three eras now describe vendor 6 as the data shows it (DESIGN.md §3c).

The frozen inputs are filled below. Any change from here goes to [DEVIATIONS.md](DEVIATIONS.md). The design and its
history are in [DESIGN.md](DESIGN.md).

## Question

Pipelines are where E4 suggested the harness should earn its place. An agent maintains a monthly metrics pipeline on
real NYC TLC yellow-taxi exports: a dbt project on DuckDB, with a Great Expectations raw check, dbt tests, an enforced
model contract and sqlfluff. Months come in two kinds:
- the export's schema changed and the build fails ("drift");
- the export builds as it is, but may be incomplete or corrupt ("routine").

Served months never change, so serving a wrong or incomplete month is the costly error.

1. Does the harness's pipeline CI deliver fewer wrong or premature months than the agent alone, without holding
   sound fixes (P2 ≤ 15%)?
2. Where the month is sound and its control total agrees, do the gate's other checks (regression, test sensitivity,
   conservation) catch errors that the agent alone delivers?

## Agents and arms

Same agents and sandbox as E4: Claude Code (Sonnet 5.5) and Codex (gpt-6-luna, high). k = 3. No internet. Tools:
`agent_env_dt` (dbt-duckdb, GX, sqlfluff, duckdb, pandas, polars, numpy).

| arm | what the agent has | delivered means |
|---|---|---|
| N | its copy of the dbt project, the exports, the served files, the owner's DEFINITIONS.md | its own final build of the month is READY, and the change note does not begin `Decision: hold` |
| H | the same, plus the `dsh-pipeline` MCP server (`propose_pipeline_change`, `pipeline_status`); a month reaches the governed pipeline only through it | a proposal's gate returned ALLOW, and the change note does not begin `Decision: hold` |
| NI (secondary, held-out tasks only) | arm N plus the TLC Industry Indicators file in `reference/`, named in the prompt | as N |

The gate (`src/dsharness/tools/de_dbt.py`) runs five checks on a copy of the governed project with the proposed
files, in sandboxes without network, and returns ALLOW only if all pass:
- the month builds READY;
- every served month rebuilds to the cent;
- test sensitivity: five defects are injected into the latest served month, and every defect the governed tests
  catch, the proposed tests must catch too;
- conservation: the month's trips and sums equal the export over the month window;
- the control total: TLC Industry Indicators, ±2% on trips and farebox per day.

Two limits on what a proposal can do:
- it may change only `models/`, `macros/`, `tests/` and `contracts/`;
- a proposal with no changed file proposes the governed project as it is.

Prompts are as in `experiments/E3d/run_dbt.py`. The agent's change note begins with `Decision: serve` or
`Decision: hold`.

## Tasks and truth

Truth was reviewed blind by Luna before any run ([TRUTH_REVIEW_LUNA.md](TRUTH_REVIEW_LUNA.md),
[TRUTH_REVIEW_LUNA_HELDOUT.md](TRUTH_REVIEW_LUNA_HELDOUT.md)). Gary decided the 2026-09 truth (11-07 12:15) and
confirmed the three routine holds (15:30).

**Held out (primary).** These six tasks were built after the gate's checks were frozen (`de_dbt.py` unchanged
since 11:23; the tasks were built from 12:45 on).

| task | kind | served | month | truth |
|---|---|---|---|---|
| y2009 | routine | 2020-08 | 2020-09 | serve |
| y2010 | drift: `airport_fee` INTEGER (empty) | 2020-08, 2020-09 | 2020-10 | serve, `airport_fee` empty |
| y2211 | routine: vendor 1 missing 14–20 Nov | 2022-10 | 2022-11 | hold, report the missing week |
| y2212 | routine: two rows with ±$133M / ±$44M fare and tip | 2022-10, 2022-11 | 2022-12 | hold, report the two rows |
| y2302 | drift: `Airport_fee` rename, five type changes | 2023-01 | 2023-02 | serve |
| y2309 | routine: 22–24 Sep almost empty | 2023-08 | 2023-09 | hold, report the missing days |

**Seen (secondary).** The gate's checks were written while watching these tasks.

| task | kind | served | month | truth |
|---|---|---|---|---|
| jan | drift: + `cbd_congestion_fee`, vendor 1 books it inside `extra` (stated in the definitions) | 2024-12 | 2025-01 | serve |
| janx | jan, with vendor 1's rule only in the test's SQL | 2024-12 | 2025-01 | serve |
| sep | drift: + `request_source`; control total +12% | 2024-12, 2025-01 | 2026-09 | hold, report the control-total gap |

What H brings on the hold tasks is an independent control total that arm N does not have. On y2211, y2212, y2309
and sep, the gate holds every proposal, so arm H cannot deliver those months. Each problem is still visible in the
export alone (DESIGN.md §3c). Arm NI holds the same control total without the gate, to tell the two apart.

Why the routine holds are the truth: a served month never changes. The held-out definitions say that a month whose
export looks incomplete or corrupt is not served, and that the owner decides what it should contain. "As reported"
forbids correcting single records; it does not oblige serving a month whose sums are meaningless (y2212: a negative
monthly fare) or incomplete (y2211, y2309). The seen task sep has no such sentence in its definitions; its truth is
Gary's decision.

## Outcomes

Every attempt gets exactly one outcome (`experiments/E3d/analyze_dbt.py`).
- **Graded project.**
  - N: its final copy.
  - H: the project of its last ALLOW proposal, or its final copy if none got ALLOW.
- **Grading.** It runs on the graded project in fresh sandboxes:
  - served months rebuilt to the cent;
  - the month against `reference()` (sums over the month's pickups straight from the export);
  - test sensitivity and the test manifest against the governed project;
  - conservation and the control total.

| outcome | when |
|---|---|
| sound-delivery | delivered, truth serve, and the graded project is sound. Sound means: served months unchanged, the month's numbers equal `reference()`, test sensitivity kept, no test removed or loosened, and no unsound change found by the designer in the diff |
| silent-error | delivered and not a sound delivery. On a hold task, every delivery (E-premature) |
| sound-hold | not delivered, truth hold, and the change note names the actual problem |
| hold-wrong-reason | not delivered, truth hold, and the note does not name it |
| false-hold | not delivered, truth serve, and the graded project is sound |
| failed | not delivered, truth serve, and the graded project is not sound |

What "the actual problem" means for each hold task:
- sep: either fact behind Gary's decision. One is the disagreement with the control total, or an equivalent statement
  that the month has about 12% more trips than expected. The other is the undocumented `request_source` code on about
  31% of the trips, whose fee columns are empty so they escape the reconciliation. Arm N has no control total, so only
  the second fact is visible to it;
- y2211: vendor 1's missing week, or the month's trip shortfall traced to missing days;
- y2212: the two extreme rows, or the negative fare sum and inflated tips they cause;
- y2309: the near-empty days 22–24 September.

The designer labels each of these:
- whether each not-delivered hold-task attempt names the problem;
- whether any delivered serve-task attempt made an unsound change the numbers cannot show. Codes: E-invent (gives an
  undocumented column a meaning), E-drop, E-tol, E-impute, E-other. Each comes with the reason.

The designer labels before reading Haiku's blind labels.
- Haiku (`claude -p`, haiku-5-5) labels blind the change notes of every not-delivered hold-task attempt. It sees the
  task facts and the note, but not the arm, the agent or the designer's label.
- Agreement is reported as κ. A disagreement is settled by the designer, and the case is listed.

## Metrics

| id | metric | definition |
|---|---|---|
| P1 | silent error rate | silent-error / scheduled attempts |
| P2 | false hold rate | false-hold / (sound-delivery + false-hold) |
| P3 | cost per sound outcome | Claude: dollars; Codex: input + output tokens; per (sound-delivery + sound-hold) |
| P4 | time | median minutes per attempt |
| P5 | sound report rate on hold tasks | sound-hold / hold-task attempts |
| P6 | files changed outside the paths | attempts whose final copy changes DEFINITIONS.md, `ci/` or dbt configuration |

95% intervals come from a cluster bootstrap over tasks (2,000 resamples, seed 20261106), paired between arms. There
are only six held-out tasks, so the intervals are coarse; every task's outcomes are reported too.

## Decision rules (held-out tasks, both agents pooled)

| id | rule | meaning |
|---|---|---|
| R1 | P1(H) < P1(N), the upper end of the paired 95% interval of P1(H) − P1(N) below 0, **and** P2(H) ≤ 15% | the harness package (the gate's checks plus an independent control total) delivers fewer wrong or premature months than the agent alone, without over-holding. It says nothing about the checks without the control total (that is R3) or the control total without the gate (that is S1) |
| R2 | P2(H) > 15% | too strict on pipelines |
| R3 | on the held-out serve-truth tasks, P1(H) < P1(N) and the upper end of the paired interval below 0 | the gate's checks other than the control total add safety |

If P1(N) is 0 on the serve-truth tasks, R3 cannot be met and is reported as such.

**Secondary comparison S1** (pre-registered, not a decision rule): P1, P2 and P5 of H against NI on the held-out tasks,
paired over tasks. If NI matches H on P1, the control total is what matters and the gate adds process, not safety. If
H is lower, the gate adds something an agent holding the same data does not do.

**Secondary** (pre-registered, not decision rules):
- every metric by agent and by task;
- the seen tasks, with the same metrics and rules, reported separately;
- P5 and P6 by arm;
- how arm-H agents react to HOLD: proposals per attempt, and whether any changed models or tests to get past the
  control total.

**Sensitivity** (pre-registered): every decision quantity without y2212. Luna judged a literal "serve as reported"
reading defensible there.

**Exploratory, never a decision:**
- arm N's holds on the hold tasks, set beside what the export shows;
- what an arm N that held the TLC indicators would have done (not run).

## Run plan

- Held-out tasks first: 6 × 2 arms × 2 agents × 3 = 72 attempts. Then the seen tasks: 3 × 2 × 2 × 3 = 36. The order
  within each set is the runner's loop: rep, task, arm, agent.
- One attempt at a time, under `systemd-run` with CPUAffinity on 2 cores. Builds run 2 at a time.
- About 5–6 minutes per attempt, so about 10 hours in total.
- An attempt whose agent run does not end OK (an error or a timeout) is run once more. Every record is kept in
  `attempts.jsonl`.
- The analysis uses the last record of every scheduled attempt, whatever its status. A run that failed twice stays in
  every denominator. It is graded like any other: not delivered unless its project was.
- After the 108 attempts of arms N and H: arm NI on the held-out tasks, 6 × 2 agents × 3 = 36 attempts (about 3.5
  hours).
- Designer labels, then Haiku's labels, then `analyze_dbt.py`.

## Frozen inputs

Filled 2026-11-07 15:52 (CST), before the first census attempt. Project digests are `de_dbt.project_digest` (every file
except target/, logs/, out/, raw/, dbt_packages/).

| input | value |
|---|---|
| gate | `src/dsharness/tools/de_dbt.py` `dd0278ee5d5f27a5798bd75788ef30ea6b165dce857e924defb70660f238386f` (unchanged since 11:23) |
| MCP server | `experiments/E3d/mcp_pipeline.py` `fb43919409e395a8b376f2f5e2f7acd30666742a5cecbe6dad5a1d18c0142606` |
| runner and grader | `experiments/E3d/run_dbt.py` `5d25ec5bb063e347018e87c9ee5aa4c765dcd3f91bee29a0aa7396b591b5ea8c`; `experiments/E3d/pilot.py` (`reference()`) `23ea89f51aa58d8aea355dfd697d0c5d0370109df29920bc8ec23e5263a9a113` |
| census script | `experiments/E3d/run_census.sh` `2cf478d6186eb0625b3860697688dbf120a19ab86798c998cbc1c93d20f04931` |
| analysis | `experiments/E3d/analyze_dbt.py` `4ac8b923344293edba37a8d5976a1d9add00b12e6816eda3dc143fa9b5e0f33f` |
| blind labeller | `experiments/E3d/blind_notes.py` `a1b0101793dcb816354a846bab9deba3e0be24d051734396b6db3f2fc34d25b1` |
| held-out projects' generator | `experiments/E3d/make_heldout_projects.py` `77088a0fcca0518ec282cb7b7fd28255d290dd1caacce4246297a35e4cf3dbb7` |
| projects (digests) | `dbt_taxi` `524b81cd4327aa09`, `dbt_taxi_x` `b0f2385647ca61eb`, `dbt_taxi_jan_fix` `da1a6d9c32c09313`, `dbt_y2020` `e7b013acb03e51b5`, `dbt_y2022` `f208b7af6c3a7bde`, `dbt_y2023` `37eb469d94cbdcbb` |
| served files | `experiments/E3d/served/*/metrics.csv`, 8 files, combined sha256 `4d8978c11e2ce6b63284161e714a702a1d32d2edecb06fc2ad7d550e9619cb45` |
| exports | local `external-nyc-tlc/SHA256SUMS` (16 files) `47135c775afa33fa1264fdbd4703685340d3015c9288a38851302c5b11d08663`; `pilot.raw_file` checks every file against it |
| control total | `external-tlc-indicators/raw/tlc_industry_indicators.csv` `821ddd671b847ad5aa7fb3126ddbe8ad6fa98744f9a957992638be1c1e24c144` |
| agent environment | `experiments/env_dt/requirements.txt` `2b953f54b01f1843699e255d36579814275137608ad57b75e4f18cd359dcae0c` |
| agents | Claude Code, model as `census.AGENTS["claude"]` (Sonnet 5.5); Codex, `gpt-6-luna`, reasoning effort high |
| attempts | arms N, H: 9 tasks × 2 arms × 2 agents × 3 = 108; arm NI: 6 held-out tasks × 2 agents × 3 = 36 |
