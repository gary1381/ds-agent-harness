# E1: which harness check is meant to catch each error label

Fixed 2026-11-06, before any evaluation run. It records what the harness was designed to catch,
so the census can tell "caught by design" from "caught by luck" and find labels nothing covers
(decision rule D3).

The answer gate (`submit_answer` → stage `answer_publish`) delivers an answer only as
VERIFIED / ALLOW. That needs a `metric_call` claim whose governed-metric result equals the
submitted rows (`answer.reconcile`), and no contradiction between the claim and the question
(`claim_contradictions`: metric, months, filter values, group-by). A question without a governed
metric cannot reach VERIFIED: the best it gets is T3 / HOLD, sent to a human.

| label | covered? | by which check | notes |
|---|---|---|---|
| E-def | yes, if a governed metric exists | `answer.reconcile` | the governed metric *is* the official definition |
| E-test | yes, if a governed metric exists | `answer.reconcile`; SQL lint for missing `NOT is_test_account` | |
| E-refund | yes, if a governed metric exists | `answer.reconcile` | |
| E-dup | yes, if a governed metric exists | `answer.reconcile` | |
| E-unit | yes, if a governed metric exists | `answer.reconcile` | a ×100 answer cannot equal the metric |
| E-time | partly | `claim_contradictions` (months in the question vs the claim); time-spine coverage refuses months outside the build | relative dates ("last quarter") and Chinese dates are not parsed (`months_from_text` is English-only) |
| E-late | partly | time-spine coverage only | no completeness check for late-arriving usage rows |
| E-src | yes, if a governed metric exists | `answer.reconcile`; uncertified-source flag on `run_sql` | |
| E-join | yes, if a governed metric exists | `answer.reconcile` | |
| E-calc | yes, if a governed metric exists | `answer.reconcile` | derived quantities (growth, shares, top-N) have no governed metric, so HOLD |
| E-read | partly | `claim_contradictions` (metric, month, filter, group-by) | other misreadings (wrong quantity with a consistent claim) are not caught |
| E-fmt | partly | rows must equal the submitted rows | the gate does not know the expected keys |
| E-proc | yes | nothing delivered without a VERIFIED submission | counted as not delivered, never as silent |
| E-other | no | | |

Expected consequence for the blind set: derived questions (about 8 of every 20) cannot be
VERIFIED, so in arm H they are either held (correct answers count toward false holds, M2) or
delivered only if the agent finds an equivalent governed metric.
