# E3a labelling notes: the E1 taxonomy on the retail world

Written 2026-11-07 03:35 (CST), while the census runs and before any wrong E3a attempt was labelled
(the designer had seen the answer rows of one, codex H R-H07 r2, while testing the analysis script;
nothing below comes from it). The pre-registration labels E3a with the E1 guide ([LABEL_GUIDE.md](../E1/LABEL_GUIDE.md):
one primary label, first cause in the chain, tags `had_right_value` and `harness_induced`). The guide's
labels were named after the synthetic world's defects; this note fixes, in advance, which retail
defect each one stands for, so the mapping cannot drift towards the results. Labels and order of
causes are unchanged.

| label | E1 meaning | on the retail data ([DEFINITIONS.md](DEFINITIONS.md)) |
|---|---|---|
| E-def | another metric definition | a different metric for the words (net for gross, guest orders counted as customers, AOV over all lines), or a non-official reading of "new" / "repeat" customers; also an item code merged with its case variant (`85123a` into `85123A`) |
| E-test | test accounts kept | non-product lines kept in a product metric or excluded wrongly: adjustments, fees, vouchers, `other_nonproduct` (including `TEST001`), zero or negative price or quantity |
| E-refund | refunds wrong | cancellation lines (`C…` orders) handled wrong: left in gross sales, netted out of gross, booked in the original order's month, or the sign of cancelled value flipped |
| E-dup | duplicates | exact duplicate rows counted twice, or rows that differ in any column merged |
| E-late | late data as complete | December 2025 (partial month) treated as complete, or a period that runs into it |
| E-src | wrong source | answer computed from `bronze_safe` or `staging` in a way that changes the value, when not covered by the labels above |
| E-join | fan-out / grain | grain errors: counting lines for orders, orders for customers, a join that repeats lines |
| E-calc | arithmetic | average of monthly ratios for a period ratio, sum of monthly distinct counts for a period distinct count, wrong running-total start, growth base month swapped |
| E-time | months, boundary | wrong months, quarter or year, or the 14-year date shift undone or doubled; a month taken from another timestamp |
| E-unit | unit | ratio for percent or ×100 twice, pence for pounds |
| E-read | different quantity | answered another slice or quantity (one market for both, a country for a market, top 3 for top 5) |
| E-fmt | shape | right numbers in the wrong shape (monthly rows for one total, wrong key column name, extra rows) |
| E-proc, E-other | | as in E1 |

Precedence when two apply is the guide's chain (E-proc, E-read, E-time, then the data labels, E-unit,
E-fmt). Notes keep the E1 prefixes: `grader?:` when the value is right but the pre-registered grader
cannot read it (for example a key column named `item_code` where the bank says `sku`, REVIEW.md);
`question?:` when the truth does not match the question's wording. Both feed the sensitivity analyses
only.

`harness_induced` (arm H) as in the guide. Two retail-specific cases, decided now: a v0.3 refusal of a
valid market or country spelling (the F10–F12 matcher) is harness-induced if the agent's answer went
wrong because of it; a hold or FAIL on a question no governed metric covers is not an error at all
(the answer is not wrong), so it never gets a label: it is a false hold (M2), counted by
`hold_reasons.py`.
