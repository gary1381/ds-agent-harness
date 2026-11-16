# E1 designer labelling guide

Fixed 2026-11-06, before the designer labels any final sheet. The designer labels every wrong
attempt (`labels/designer/W###.md`, built by `label_sheet.py`) with one primary label from the
pre-registered taxonomy (`docs/experiments/E1/PREREGISTRATION.md`, "Error taxonomy"), plus two
exploratory tags. Haiku labels a random 30% blind with the same taxonomy and no tags.

## Primary label

Label the error in the **submitted answer as graded**, not the agent's intentions. When several
apply, take the first one in the chain that made the answer wrong (the root cause):

1. E-proc: nothing usable was submitted (timeout, no final JSON, tool failure, gave up).
2. E-read: answered a different quantity or slice than asked (wrong metric for the words in the
   question, wrong region or plan, missing a breakdown the question asked for).
3. E-time: wrong months, quarter or boundary.
4. Definition and data errors in the agent's own computation: E-def, E-test, E-refund, E-dup,
   E-late, E-src, E-join, E-calc.
5. E-unit: right quantity in the wrong unit (fen for CNY, ratio for percent, ×100).
6. E-fmt: right numbers in the wrong shape (monthly rows where one total was asked, extra or
   missing keys, wrong value column).
7. E-other: none of these; say what in the note.

A grade of "several rows for the single value" with monthly values in fen is E-fmt if the
agent's own notes state the right total, and E-unit/E-calc only if they do not.

## Tags (exploratory, not in the decision rules)

- `had_right_value`: the transcript or final message states values equal to the truth (the grader's
  tolerance: 0.5% relative or 0.5 of the last unit), even though the submitted rows differ.
- `harness_induced` (arm H only; always false in arm N): the harness caused the wrong submission.
  True when any of these is visible on the sheet:
  - a harness instruction made the agent submit something other than its own analysis (for example
    `submit_hint: "Report these rows unchanged"` when the question needs a total, a conversion or a
    difference);
  - the harness offered no way to submit a derived value the agent had computed;
  - a harness tool returned an error, refused a valid call or misparsed the question (Chinese dates,
    Chinese region names, quarter keys), and the agent's answer went wrong because of it;
  - a harness hint or verdict pushed the agent away from a correct answer.
  False when the agent's own analysis was already wrong (wrong metric, months or filter chosen by the
  agent), even if the harness did not catch it.

## Output

`labels/designer_labels.json`: a JSON array, one object per sheet, in sheet order:

    {"sheet": "W001", "label": "E-fmt", "harness_induced": true, "had_right_value": true,
     "note": "<one sentence: what was wrong and why>"}
