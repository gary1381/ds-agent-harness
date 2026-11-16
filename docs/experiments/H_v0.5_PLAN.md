# H v0.5: covering the business questions E3a held (applied 2026-11-07)

Drafted 2026-11-07 06:49 (CST) from the E3a main run ([report](E3a/REPORT.md),
[root causes](E3a/false_holds.md)), while the E3a control run keeps `src/` frozen.
**Status 2026-11-07 08:05: F18–F24 and F26–F28 are built** in a scratch copy on top of v0.4, as
[`experiments/H_v05/harness_v05.patch`](../../experiments/H_v05/harness_v05.patch) (6 files; apply after
`harness_v04.patch` with `patch -p0`). There are 15 regression tests in `tests/test_answer_v05.py`, each naming the
E3a attempt or review finding it comes from. The full suite passes (612 tests). A blind code review (GPT-6-Luna,
code only, [review](H_v0.5/review_luna_1.md)) found three issues: two real (fixed, F28 and the `change` base), and one
false positive (`total` with `across: month` is already refused). A replay of every recorded arm-H submission
(1,738) through the v0.4 and v0.5 claim and shape checks shows no new contradiction on E1 or E1b. In E3a the new
ones are only on submissions v0.4 already held ([replay](H_v0.5/contradiction_replay.md)). F25 is deferred;
O1–O3 (the retail model) and B1–B2 come with E4.

**Applied 2026-11-07 08:47** to `src/` (on v0.4) with `patch -p0 < experiments/H_v05/harness_v05.patch`
(sha256 `ff9f5c2ed058a72b…`); the five source files are byte-identical to the tested scratch tree. Same day the
census gained the E4 benchmark fixes B1–B2 and the T2f second-opinion rules (`bench/census.py`, `bench/live.py`,
off for the frozen E1–E3a runs). Full suite on the live tree: **615 pass** (612 + 3 census tests). Frozen state
for E4:

| file | sha256 |
|---|---|
| `src/dsharness/tools/analytics.py` | `ceb7121232f6c74e4c80f79c26433e286380bf3307c6ff31c06a0313b1048e74` |
| `src/dsharness/tools/derive.py` | `afca40321835fc4fc92f93d1f9bec6d02ae98662766f8d7ef5b7f2fe27717989` |
| `src/dsharness/tools/shape.py` | `2c019e8e3cee8f09339ddc183a84c7d6e044c5a2058f0bb51806a6155eaf98fa` |
| `src/dsharness/context/semantic.py` | `07560c9ab46aeb6d07c3edaa70a8db2955c7044cdc99fc944d916ef700bfa27b` |
| `src/dsharness/mcp_server.py` | `5f80b7fed43887faf2e27159e7200500f781d387aa4eab3c1455a541e7f16c71` |
| `src/dsharness/policies/gates.yaml` | `a52251bff6b0a32738958781ce8ea9ca023ff6a854a837c3780ff67154b19b5e` |
| `docs/answer_certificate.schema.json` | `8fd84bb82ef91011e96c9dab6368226827ee2514d4110c3ba1c8443ba4751181` |
| `src/dsharness/bench/census.py` | `836b3f97918cce6e9134560388ba9832925b6d474435cb6c2c4a200600eae617` |
| `src/dsharness/bench/live.py` | `ffae3aadaca2b6869c70a107e50acec511bfdd16d5c43c29d547728684e84ec7` |
| `tests/test_answer_v05.py` | `30d2a3c0f67460ca0025abc4a5f8277b5de7d12f42db2fe9b04b0155e4430cfe` |
| `tests/test_census.py` | `15c34a71c2bfdd5c28ef1d469c0f4167aef745d374c621fc892895d4112a1bfe` |

**Overfitting guard.** Every fix below was found on the E3a bank, so E3a cannot show that it works.
The test is E4: a fresh bank written by models that see neither the check code nor this list,
weighted towards the shapes that held answers here (ratios, periods, rankings) but not reusing these
questions. A fix that only helps the cited questions is a failure of the fix, not a reason to edit E4.

## Why

On E3a every correct answer arm H held (114) sits on 22 of 74 questions; on the other 52 the gate
delivered 312 of 312 attempts, all correct. The gate's logic is not the problem; its reach is. The
held shapes are ordinary business questions, and three of the harness's own steps pushed agents off
right answers (all three held, none delivered).

## Harness fixes

| id | change | E3a evidence (questions, attempts) | status |
|---|---|---|---|
| F18 | **ratio of two governed metrics**: `submit_answer` takes a second call (`denominator_call`) and `derive.combine: ratio`; the harness recomputes both calls and divides row by row (unit from the question: share, percent or per unit) | R-L11, R-L13, R-L19, R-L20, R-L31, R-L33, R-L39, R-H13, R-H30: 54 held; 24 FAILED because the numerator alone was declared (10.9% compared with 108 new customers) | built as `derive.divide_by: {metric, filters}`; the denominator is a governed call on the same keys, recomputed by the harness (per month, or over the window with `months: total`) |
| F19 | **whole-period grain for distinct counts**: a call may ask for the declared window as one period (`grain: period`); distinct-count metrics are compiled over the window (each customer counted once), never summed from months | R-L31 "per active customer across October to December" (1,153.26, not the mean of monthly ratios); needed by F18 and F20 for customer metrics | built as `derive.months: total`: compiled over the window (`compile_metric_period`); refused for point-in-time metrics (active subscribers) |
| F20 | **growth between two windows**: `derive.months: growth` with `base: [m1, m2]` and `compare: [m3, m4]`; each window rolled up by the metric's rule (sum, pool, or F19's period grain); the current first-vs-last-month growth stays for two single months | R-L18 (Q2 → Q3: harness 99.5% vs right 22.47%), R-H14 (Jan–Nov year on year): 10 held, all FAILED; codex R-L18 r2 fell back to monthly rows after the FAIL (harness-induced) | built as `derive.windows: [[m1, m2], [m3, m4]]` with `months: growth`; each window must lie inside the call's months |
| F21 | **change for each month**: `derive.months: change` keeps one row per month (like `cumsum`); the month before the first reported month is the base and must be in the call | R-H07 (month-over-month for each month of 2025): 5 held | built (`months: change`) |
| F22 | **share reports every member** unless `keep` names some; `across.a` / `b` no longer drop rows | R-L40: Claude r3's right pair (UK 83.25, non-UK 16.75) FAILED as "rows != derived rows [uk]", resubmitted UK only (harness-induced); Codex r1–r3 FAILED the same way | built |
| F23 | **generic terms do not contradict a refinement**: a metric's terms split into specific and `generic` (sales, revenue, 销售额); a question that names a metric only through a generic term is not contradicted by a declared metric that `suppresses` it | R-H08, R-L40, R-H14: 毛产品销售额 matched net sales through 销售额 (the onboarding gap is O3); Codex R-H14 r1 switched to net sales because of it (harness-induced) | built as model fields `generic_terms` and `qualifiers` (below) |
| F24 | **ungoverned key column before shape**: answer rows keyed by a column that is not a governed dimension (item code) read INCONCLUSIVE ("no governed metric has this dimension") before the month-shape check can FAIL them | R-H29 Claude r2, r3: FAILED "rows lack a month column" on a right top-5 list | built |
| F25 | **declared metric of another kind**: when the question asks for a share, percent or per-unit value and the declared metric is a count or money with no derive step that converts it, reconcile says INCONCLUSIVE ("declare the ratio or no metric call") instead of comparing the values | the 24 numerator-only FAILs of F18; with F18 the agent has a right way to declare, so this only stops a mis-declaration from looking like a contradiction. Risk to test: a wrong ratio is no longer FAILED by the numerator's values, so it relies on T2 or a person | deferred |
| F26 | **"through November"**: the question's month range ends at the named month ("each month of 2025 through November", "到11月") | R-H07: "the question names months 2025-01..2025-12" contradicted every right call | built |
| F27 | **re-execution pairs a renamed value column**: same names pair; one column left on each side pairs too (answer `value`, SQL `AS v`); SQL columns left over that hold numbers in every row are helpers the answer may leave out. A text column (a key) may not be dropped, and two names left on each side are still a mismatch | found while building F24: 23 arm-H attempts had a submission FAILED re-execution on the column names; the rule clears 20 (`v`, `sh`, `q` or an expression for the value: 18; helper counts kept in the SQL: 2). The other 3 (Claude R-H30 r1–r3) added a `month` column the SQL does not return and stay FAILED; R-H30 is a ratio, which F18 covers | built |

| F28 | **a step over months covers exactly the asked months** (from the blind review): `sum`, `mean`, `pool`, `total` and `growth` without windows, and `across: month` with `top` or `share`, need metric_call.months equal to the question's months. A running total must start where the question starts, and growth windows must be the question's periods (or the later one). Allowed: one month of growth against the month before or a year before, and a sum when the question says cumulative / year to date. Only a cleanly read scope counts: "January and February 2026" reads as February alone, so equality with it would hold right answers (the replay found 6 such in E1b C-H39 before this rule was narrowed) | a Q1 total declared over December to March VERIFIED in v0.4 (a gap older than v0.5, not seen in any run); the claim check's containment rule is right for per-month answers but not for a step that combines the months | built |
| — | **`change` uses the calendar month before** (from the blind review): a rate or average omits a month with no data, and the row before is then two months back; that month's change now has no base and is left out | none observed (additive metrics are zero-filled) | built |

**The `windows` rule.** Windows must be two periods of the same length, back to back (Q2 then Q3) or a year
apart (Jan–Nov 2024 then Jan–Nov 2025), base first. Any other pair is refused, because the review showed that two
months of each quarter otherwise VERIFIED as "Q4 to Q1 growth".

**F23 in the model.** A metric may list `generic_terms`, words that name it only by default (`sales`,
`revenue`, 销售额, 收入), and `qualifiers`, words that make it the reading of a generic word for a metric it
suppresses (`gross`, 毛 for gross sales; `cancel`, 取消 for cancelled value). 毛产品销售额 then reads as gross
sales, so declaring net sales for it is contradicted (Codex R-H14 r1's harness-induced error would have been
held as a contradiction instead of being caused by one). A bare "sales" still means net sales, so gross-for-net
(a definition error) stays caught. A qualifier with no generic word to refine names nothing, and it counts only
within 16 characters before the generic word ("sales excluding cancelled orders" stays net). A default reading is
weak: it suppresses no other metric, so "cancelled sales" reads as cancelled value even though "sales" also names
net sales by default. The synthetic model
has no generic terms, so its behaviour is unchanged; the retail model gets them in O3.

**Why F24 and F27 matter beyond E3a's numbers.** Under the gate, INCONCLUSIVE and FAIL both hold, so neither
fix delivers anything by itself. What changes is that a right answer is no longer *recorded as contradicted*:
the T2 rules release only answers whose own checks did not FAIL, and the agent is not told to change a right answer.

## Onboarding (the retail semantic model; a new data set gets the same checks)

**Status 08:20:** drafted as [`experiments/E4/context/semantic_model.yaml`](../../experiments/E4/context/semantic_model.yaml)
(the E3a model stays frozen). O1 and O2 reproduce the frozen E3a truths of R-H29, R-H10 and R-H34 as VERIFIED
(`experiments/E4/model_check.py`; a sanity check, not an evaluation). The customer-type value is
`identified_customer`, not `identified`, because R-H29 says "products, identified by item code". O3 is done
([TERM_COVERAGE.md](E4/TERM_COVERAGE.md)). On 160 fresh paraphrases the E3a terms resolve 47% of questions to
their own metric and 15% to another; the E4 terms, after two rounds, 69% and 7%. Hand-written terms plateau
there, and a classifier trained on the paraphrases is a v0.6 candidate.

| id | change | evidence |
|---|---|---|
| O1 | item code (`sku`) as a dimension of the line metrics, so top-N by item code is a governed `across: top` | R-H09, R-H29, R-H39, R-L30: 15 held; arm N's 13 errors there are all key names (B2) |
| O2 | customer type (identified / guest) as a dimension | R-H10, R-H34: 12 held |
| O3 | **term-coverage test**: a model that has not seen the term lists paraphrases each metric's question forms (English and Chinese); every paraphrase must resolve to its metric before the model is frozen. Adds 毛(产品)销售额 to gross sales and "already bought / earlier month" phrasing to repeat customers | R-H08, R-L40, R-H14, R-H37: 17 held, one harness-induced error |

Onboarding E3a took about 2.5 designer hours and its gaps caused 44 of the 114 false holds (O1–O3).
Onboarding is part of the product: the time it takes and the false holds its gaps cause are both
reported for every new data set.

## Benchmark (decided before E4 runs)

| id | change | why |
|---|---|---|
| B1 | the answer-format prompt names the key columns each question needs (e.g. `"sku"`) and asks for full precision | 24 of arm N's 35 E3a "errors" were right values: `item_code` keys (13), rounding below 3 significant digits (9), quarter labels (2) |
| B2 | the grader's key aliases include `item_code` → `sku`; a `YYYY-Qn` key is read as the quarter's months | as B1; the registered E3a grader stays unchanged |

## Order

1. After the E3a control run ends: apply v0.4 (`harness_v04.patch`), full suite, freeze with digests.
2. ~~Build F18–F26 in a scratch copy on top of v0.4, regression test per cited attempt, full suite~~ (done
   07:20, plus F27; F25 deferred); blind code review (Luna, code only); O1–O3 in a copy of the retail model
   for E4 (the E3a model stays frozen).
3. T2f (census reports the t2n second opinion beside VERIFIED); pre-register E4 with B1–B2.
