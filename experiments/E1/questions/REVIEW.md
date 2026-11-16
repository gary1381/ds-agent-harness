# E1 question review (designer, before any agent run)

Each blind question's truth SQL was run on the evaluator database and read against the official definitions in the author brief.
Questions not listed passed as written.

| qid | change | reason |
|---|---|---|
| B-L04 | truth fixed | the author's SQL cross-joined 4 region revenue rows with every customer-month row, multiplying revenue by the row count (1.93e7 fen); rewritten as March net revenue / non-test subscribers active at the start of March |
| B-H05 | truth unrounded | the truth SQL rounded to the precision the question asks for; a more precise correct answer would fail the 0.05% rule. Truth is now exact; the agent's rounding is covered by the half-unit rule |
| B-H07 | truth unrounded | the truth SQL rounded to the precision the question asks for; a more precise correct answer would fail the 0.05% rule. Truth is now exact; the agent's rounding is covered by the half-unit rule |
| B-H09 | truth unrounded | the truth SQL rounded to the precision the question asks for; a more precise correct answer would fail the 0.05% rule. Truth is now exact; the agent's rounding is covered by the half-unit rule |
| B-H12 | truth unrounded | the truth SQL rounded to the precision the question asks for; a more precise correct answer would fail the 0.05% rule. Truth is now exact; the agent's rounding is covered by the half-unit rule |
| B-H13 | truth unrounded | the truth SQL rounded to the precision the question asks for; a more precise correct answer would fail the 0.05% rule. Truth is now exact; the agent's rounding is covered by the half-unit rule |
| B-H15 | dropped | duplicate of B-L20 (same question, same truth: pooled March-May ITT effect) |
| B-H17 | wording | the answer key column was not named; added 'Label the groups exposed = true and exposed = false.' |

Checks that passed for every other question: correct population (non-test; at_risk = active at start and non-test),
month-end activity = active_at_start of the next month, churn rate = churned / at_risk, net revenue from truth_revenue
(already non-test and net of refunds), ITT rates over all enrolments by arm, period rules of the brief.
