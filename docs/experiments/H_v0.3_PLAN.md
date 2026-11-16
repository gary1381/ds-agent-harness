# H v0.3: a second data world (E3a) without moving the synthetic one

Drafted 2026-11-07 (night) while E1b runs. The changes live in `experiments/E3a/harness_v03.patch`
(made and tested in a scratch copy; arm H of E1b starts its server from this checkout, so `src/`
does not change until E1b ends). v0.3 is applied and frozen with digests **before the E3a questions
are written**, like v0.2 before E1b. Fixes found by the E1b labels may join v0.3 up to that point;
each is listed here with its E1b evidence. Anything later is an E3a deviation.

## What changes

v0.3 = v0.2 plus the changes below. The world-config changes (W1–W7) leave the synthetic world as it
was: the census prompts are byte-identical (sha256 pinned in a test) and every v0.2 test still passes.
The E1b fixes (F15, F16, F17) change arm H's behaviour on purpose, on both worlds; S2 changes how the
census counts a delivered answer (E1b found it undercounted).

### Serving a workspace's own definitions (found by onboarding Online Retail II; [log](../../experiments/E3a/ONBOARDING.md))

| id | change | where |
|---|---|---|
| W1 | `<ws>/context/` holds the workspace's semantic model, catalog, pipeline and (optional) verified queries; the built-in ones stay the default | `workspace.py`, `tools/analytics.py`, `tools/context.py`, `bench/run.py` |
| W2 | metric vocabulary (`terms`, `suppresses`) and the independent record (`independent_record`) are declared per metric in the model, not in code | `context/semantic.py`, `semantic_model.yaml`, `tools/analytics.py` |
| W3 | an additive measure can declare `cast: double` (money with pence); the default stays BIGINT | `context/semantic.py` |
| W4 | the derive example shown to agents comes from the model (`derive_example`) | `tools/analytics.py` |
| W5 | no independent record for any metric → the finance-close check is NOT_APPLICABLE and answers cap at T1 | `tools/analytics.py` |
| W6 | a workspace with its own model lists only its own worked examples | `tools/analytics.py` |
| W7 | census `World` config: company wording, answer-key example, builder, evaluator database, catalog, schemas, key aliases, optional rewrite of arm N's warehouse (control worlds) | `bench/census.py`, `experiments/E1/run_census.py --world` |

### Fixes found by the first real-agent run on real data (smoke, 2026-11-07)

| id | fix | evidence |
|---|---|---|
| F10 | dimension values and aliases match case-insensitively, left to right, longest spelling first, over all dimensions at once; Latin spellings must stand alone; spaces, hyphens and underscores are interchangeable | Claude, arm H: "net product sales in the UK" answered right with `market = UK`, held as "the question names no market"; "uk" sits inside "non-uk", 英国 inside 非英国 |
| F11 | a phrase that names values of two dimensions (market UK / country United Kingdom) is satisfied when the call honours either | the same attempt's second submission, with `country = United Kingdom`, held as "names no country" |
| F12 | the retail model lists its 43 countries with English and Chinese aliases; an unknown filter value is an error | `country = 'Ireland'` (stored as EIRE) would return no rows, silently |
| F13 | arm N's warehouse copy turns the agent-visible views into tables before dropping `bronze` | `bronze_safe.*` were views over the dropped `bronze.*` and could not be read; true in E1 and E1b as well, where no arm-N attempt queried them |
| S1 | the sandbox also hides `/opt` and `/snap` (the host's installed applications) | an arm-N transcript listed `/opt`; nothing in the repository or the evaluator data was reachable (`/home`, `/tmp`, `/mnt` were already hidden) |

### Fixes found by E1b (all 203 wrong attempts labelled; [report](E1b/REPORT.md))

| id | fix | E1b evidence |
|---|---|---|
| F15 | when the submitted rows are some of the derived rows, or lack the key column, the FAIL reason names `derive.keep` with the values the answer reports; `share` with `a` reports only that member (v0.2 accepted `a` and ignored it) | 4 of the 11 arm-H silent errors: "the west region's share" (C-H15, C-L28) answered with the west row was told `rows [west] != derived rows [east, north, south, west]`; the agents then submitted all four regions, which reconciled (VERIFIED, delivered, wrong). `keep` existed in v0.2 but no agent used it |
| F16 | `derive.across` may run over the months (`dimension: "month"`): `top` (the highest month), `difference` / `ratio` between two months, `share` for metrics that add up; the tool text says so | the other 7 of the 11: C-H20 "which month in 2025 had the highest churn rate" (6): agents declared `across.dimension = "month"`, v0.2 refused it, and they submitted all twelve months (delivered, wrong); C-H31 "February 2026 minus February 2025" (1 delivered wrong, 4 correct answers held as "rows lack a month column and the claim spans more than one month") |
| F17 | `derive.months: "cumsum"`, a running total since the first declared month; unlike the other month steps it keeps one row per month; only for metrics whose monthly values add up | C-H32 "cumulative net revenue at the end of each month": all 6 correct answers held (FLAGGED); one agent declared `months: cumsum` and was refused |
| S2 | the audit stores a second digest of the submitted rows read as numbers (12 significant digits, row order ignored), and the census counts the final answer as delivered when it equals a VERIFIED / ALLOW submission byte for byte or as numbers | 7 of the 74 arm-H false holds were verified and then retyped in the final answer (611761 submitted, 611761.0 reported; a 17th digit dropped), so the census found no submission with those rows (`experiments/E1b/delivery_match.py`). Audits from v0.1 and v0.2 carry only the byte digest, so E1 and E1b numbers do not change |

Each comes with a regression test rebuilt from those attempts (`tests/test_answer_v03.py`,
`tests/test_derive.py`, `tests/test_bench.py`). F15 and F16 cover all 11 arm-H silent errors in E1b;
F16, F17 and S2 cover 17 of its 74 false holds. The other 56 holds answer questions that no governed
metric covers; v0.3 does not change that (E1b report, "Coverage is the open problem").

Blind review of F15/F16 (Haiku, code only): fixed `share` with `a` (above) and a `difference` / `ratio`
whose two sides cover different groups, which dropped the unpaired groups silently and now refuses
with their names. Not changed: a "highest month" answer may leave the month out (the grader's truth
for such questions is the value alone, so requiring the month would hold answers the grader counts
right; a month that is given must match); two crash reports on paths no caller reaches (derive is
always normalised first).

### From the blind code review (Haiku, 2026-11-07, patch and E3a scripts only)

Nine findings; each was checked against the code. Fixed:

| id | finding | fix |
|---|---|---|
| F14 | a model may name any `independent_record`, but the finance-close check can only reconcile `silver.finance_closed_revenue`; another record would be skipped with a misleading reason | the loader refuses a record no check exists for (`CHECKABLE_RECORDS`); test added |
| — | `world.py` reused a source export made from an older raw file | the export manifest records the raw file's sha256; a mismatch stops the build |
| — | `build_bank.py` numbered questions 20 per round, so a round with more than 20 would reuse ids | ids count per author across rounds |
| — | a reused FAILED build was returned without an error | it raises (the census already stopped later, in `latest_ready_build`) |

Not changed: hiding `/opt` and `/snap` cannot hide the agent CLIs (they live under the home folder and are
bound explicitly; the harness server runs outside the sandbox); the example model loads (it has two
metrics, both with terms); a date literal 2024-02-29 in a control question would fail loudly (the
source years have no 29 February, so the data has none either); short canonical values: no alias
`US` exists (it is only an answer-key spelling). Reproducibility: the arm-N warehouse change (F13)
means E1 and E1b can only be re-run from a v0.2 checkout, so E1b's retries run before v0.3 is applied.

After the fixes the same two smoke questions VERIFIED in arm H (Claude, stand-in truth = A's own
lines), and the control world (original names and dates, arm N) answered the DIRECT one correctly.

## Tests

573 tests pass on the scratch copy and on the live tree with the patch applied (528 in v0.2): world context, metric
vocabulary, cast, derive example, census prompts pinned, 18 retail matcher cases
(`tests/test_world_context.py`), unknown country, arm-N warehouse readable, and the E1b regressions
(keep hint, highest month, one month minus another, running total: `tests/test_answer_v03.py`,
`tests/test_derive.py`; census match by value: `tests/test_bench.py`). The patch applies cleanly
to the live tree and reproduces the tested files byte for byte.

## Freeze

Applied to the live tree 2026-11-07 about 03:00 (CST), after E1b ended and needed no retries, and before any
E3a question was written. `experiments/E3a/harness_v03.patch` (21 files, 1,543 lines, sha256
`69fa7bafc9986de0adb959104e4da64d43dcbca33089d5442f974406466204be`) applies cleanly on top of commit 105f86b plus the uncommitted v0.2 tree and
reproduces the tested scratch files byte for byte; 573 tests pass on the live tree. Any change after this
point is an E3a deviation. Re-check with `sha256sum -c` on the block below from the repository root.

Frozen v0.3 code (the files the harness serves and the census runs; `mcp_server.py` unchanged since v0.2)
and the E3a workspace context:

    b33d8eb0e6f928be239470fbebf3a0fc19c20e36a75910a80e12f263b5ef5b74  src/dsharness/bench/census.py
    1154bb55f4ef9c4e878a8a228489ee14b000f8f09e73fa121432dfd14678833e  src/dsharness/bench/live.py
    a14f7fd5d7ffa5a1ac8a66271b656639b95f5c73ad7468c0fd2739d483dd5ce3  src/dsharness/bench/run.py
    94f183df12f3320680ae043e7cde4e6dce2c456695255817eaa9f16c8f1fc00f  src/dsharness/bench/sandbox.py
    a0ca5368f05dfbd2b711122e2c0f50ad8692dfda63fb5c50d64a94dee9147375  src/dsharness/common.py
    0dcb7711d56addbc050902507c0b5d57cbaca2f58a2d3667b7b11cb569966850  src/dsharness/context/semantic.py
    ec6c31aed8c1414b3c6d0e0e5163b7d43aba49cd068e0e1cf5a254393d95d7e4  src/dsharness/context/semantic_model.yaml
    f8019729c9dd08f4625a3f9bac64a3a68f74103afed954ee719533448f64c672  src/dsharness/mcp_server.py
    ffe35a097b45545e929b189dad9f2fee2dfd5f56a5ba11a3effe9cd171fe790a  src/dsharness/tools/analytics.py
    d02aac17d4e77215956853c623840ae9a68e874d354b3849127ff0ca918e9d59  src/dsharness/tools/audit.py
    25cedbb72011fdc04806ce7de2deee182d09deb0b057c9546ff9f6e0e3566a7b  src/dsharness/tools/context.py
    7f8550e09f0abaac8847ef63d9ed35ceb36f41c93ed5a3fa0ebb2c59aae5863c  src/dsharness/tools/derive.py
    b2fb97b83b81585b8613ea34bea4a5d7e0fe66f87e2c52097621f6fcc149fb80  src/dsharness/tools/ds.py
    0f88315340b6d733bc4af6a0a598becead35af3d58a81e4d472ed59fddf69fec  src/dsharness/tools/shape.py
    b5e08f1096db3b8ebf1cb80ac885435f64056f81a979809f6fd4f325bd9f9433  src/dsharness/workspace.py
    e2f7523d4653a68021e94cb88f486c92b51f40b3de1c15f2c95564c70dae0ec5  examples/online_retail/semantic_model.yaml
    3ed32bc0f57e3202a49458997963fe5e88befd737c8cf1b036f9bca6e8cc84e7  experiments/E3a/context/catalog.yaml
    0a12b7f8f41ef2ceb105ee87fc171bd08764f4a2662d66d77c236ff685a49a34  experiments/E3a/context/pipeline/models/order_lines.sql
    cd9cd3ef32188d073b4d0538c10cc878934ebbd9ef3cb3989692bbfbbe511f0e  experiments/E3a/context/pipeline/models/stg_order_lines.sql
    d8e05be6f8687023cd369218be21ad768b1ad91b75a50e24484c35caf393df97  experiments/E3a/context/pipeline/schema.yml
    94e84917ce8a7f57528a38f6e07514c2c883cbd5c3a301871a8bebed716d353f  experiments/E3a/context/semantic_model.yaml
