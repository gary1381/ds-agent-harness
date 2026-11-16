# E3a onboarding log: a new dataset into the harness

How long it takes to bring a dataset nobody designed for the harness to a governed, queryable state,
and what broke on the way. Times are designer wall-clock (CST), 2026-11-06, done while E1b ran;
`src/` changes were made in a scratch copy and saved as `harness_v03.patch`, to be applied after E1b
(arm H starts its server from this checkout).

| step | what | time |
|---|---|---|
| 1 | disguised copy (+14 years, renamed columns), manifest with sha256 (`build_world.py`) | ~10 min |
| 2 | owner definitions: line types, 10 metrics, period rules (`DEFINITIONS.md`) | ~25 min |
| 3 | pipeline: staging + governed lines, data tests, conservation and one-month-per-order reconciliations (`context/pipeline`) | ~15 min (adapted from `examples/online_retail`) |
| 4 | semantic model (2 datasets, 10 metrics, vocabulary, Chinese aliases) and catalog (`context/`) | ~30 min |
| 5 | workspace builder for the census (`world.py`): context, source export, contract, governed build | ~15 min; build itself 16 s |
| 6 | harness changes needed to serve a second world (v0.3, below) | ~60 min incl. tests |

About 2.5 designer hours from raw file to verified answers, with an LLM designer who wrote the
harness. A data team new to the harness would need longer; step 2 is the part no tool can skip.

## What broke (harness assumptions found by onboarding)

| # | assumption | symptom | fix in v0.3 |
|---|---|---|---|
| 1 | one built-in semantic model, catalog and pipeline | the tools could only serve the synthetic world | `<ws>/context/` holds the workspace's own model, catalog and pipeline; the built-in ones are the default |
| 2 | metric vocabulary lives in code (`GOVERNED_TERMS`, `_SUPPRESSES`) | claim checks know only churn and revenue words | `terms` and `suppresses` per metric in the model (required, regex-checked) |
| 3 | built-in worked examples always shown | `list_metrics` failed: an example names `net_revenue_fen` | a workspace with its own model shows only its own `verified_queries.yaml` |
| 4 | **additive sums are integers** (money in fen) | GBP sales lost their pence: £561,778.13 served as 561778 | a measure declares `cast: double`; default stays BIGINT |
| 5 | the derive example names a subscription metric | agents in another world would be shown a metric that does not exist | `derive_example` in the model |
| 6 | the finance-close check is wired to `net_revenue_fen` | (no symptom, latent) | `independent_record` per metric; no record → answers cap at T1 |
| 7 | census prompts, key spellings and evaluator database are subscription-specific | — | census `World` config; the subscription prompts stay byte-identical (sha256 pinned in a test) |
| 8 | **dimension values are lower-case words that never overlap** (`north`, `paid_search`) | first agent smoke run: "net product sales **in the UK**" answered with the right number and the `market = UK` filter, then held: "the question names no market". The value `UK` was matched against the lower-cased question and could never be found. Fixing the case alone would have broken the next questions: "uk" sits inside "non-uk", 英国 inside 非英国 / 英国以外 | values and aliases are matched case-insensitively, left to right, longest spelling first, over all dimensions at once; Latin spellings must stand alone ("Duke" is not "UK"); spaces, hyphens and underscores are interchangeable |
| 9 | one phrase names one dimension | "UK" is market UK and country United Kingdom; an agent that filters either one answered the question, but the other dimension then counted as "named and not used" | a phrase that names values of two dimensions is satisfied when the call honours either |
| 10 | a dimension without a value list accepts any filter | `country = 'Ireland'` (the data says EIRE) would return no rows, silently; every country filter would be held as "not named" | the model lists the 43 countries with English and Chinese aliases (Ireland / 爱尔兰 → EIRE); an unknown value is an error naming the allowed ones. The census key aliases are derived from the same list |
| 11 | arm N's warehouse copy keeps the agent-visible schemas | `bronze_safe.*` are views over `bronze.*`; the copy drops `bronze`, so the raw export the catalog offers arm N could not be read. Found while building the control world. It was the same in E1 and E1b; no arm-N attempt there queried `bronze_safe` (0 of 270 in E1), so no result changed | the views become tables before `bronze` is dropped (test added) |

Findings 8–10 came from the first end-to-end run with a real agent (Claude Code, arm H, one
question), not from the tests written alongside the change: every unit test had used the synthetic
world's lower-case values. They are over-holds (a right answer not delivered), the error the gate
makes when its parser assumes the shape of one dataset.
Finding 11 turned up while building the control world, and had been in the census since E1.

Finding 4 is the kind of silent error the harness exists to catch, sitting in the harness itself:
every check would have passed, because the governed metric and the reconciliation shared the cast.
It surfaced only because the designer compared one served value with a direct SQL sum. The E3a
truth build B (independent, from the definitions only) is the check that would have caught it.

## Checks so far

- Build READY: contract PASS on 1,067,371 rows; 1,033,036 governed lines after collapsing 34,335
  exact duplicates; conservation reconciliation PASS; no order spans two months.
- Smoke: a DIRECT answer (net product sales by market, March 2025) VERIFIED / T1; a declared Q1 total
  VERIFIED; monthly rows for a Q1 total held (shape check); summing a distinct count over months
  refused; summed orders over a quarter VERIFIED (declared `month_rollup: sum`, pinned by the
  pipeline test); a Chinese question naming 英国市场 VERIFIED with the UK filter.
- Test suite with v0.3 applied: 558 passed (528 before; 30 new, incl. the smoke-run fixes).
