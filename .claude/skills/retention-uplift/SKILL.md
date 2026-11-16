---
name: retention-uplift
description: Decide whether and whom to contact with a retention offer in DS Harness - learn uplift from the randomised 2026 program (ITT, never per-protocol), validate targeting policies offline, size the experiment with a power calculation before it runs, read each policy out separately with channel costs, and scale only on that policy's own PASSing readout. Use for "who should we contact", "what is the campaign worth", "should we scale it" or channel (call vs app) questions.
---

# Retention uplift and targeting policies (dsh-ds + controller)

A churn model ranks who is likely to leave. A retention decision needs who stays *because* we
contact them, net of what the contact costs. In DS Harness the evidence chain is:

historical randomised program -> uplift model -> targeting policy -> offline validation ->
`model_experiment` gate -> pre-registered experiment with a power calculation -> per-policy
readout -> `model_scale` gate. Skipping a step is not a shortcut; it is an unsupported claim.

## Who does what

- Agent (dsh-ds tools): explore with `describe_table` (read the caveats of
  `silver.retention_contacts`), `run_sql`, `query_metric`; ask the gates with
  `request_release(policy_id=..., stage=...)`.
- Controller (not agent tools): trains the uplift model (`ml.uplift`), registers policies and
  records their offline validation (`tools.ds.register_policy`, `validate_policy_offline`),
  records human approvals, and runs the ONE pre-registered July experiment
  (`tools.ds.run_policy_experiment`). The custody oracle answers once per experiment id and never
  lets a customer into a second experiment of the month: there is no re-run.

## Procedure

1. Data: `silver.retention_contacts` has one row per enrolment (`arm` randomised 50/50 within
   enrolled at-risk customers; `exposed` is post-assignment compliance). Outcome = churn in the
   campaign month. Use only campaign months whose labels are mature at the build cutoff (month
   start + 1 month + 7 days); on the 2026-06-30 build that is March..May. June is unknown, not
   "no churn".
2. Estimand: intention to treat on `arm`. Never compare exposed vs control (per-protocol): the
   unexposed treated customers are dropped and the "effect" is inflated (and, in real data,
   confounded by who opens messages).
3. Model: small and transparent (T-learner of two regularised logistic regressions, or the
   interaction learner with pre-specified effect modifiers), trusted point-in-time features at
   each campaign start, fixed seed. Effect modifiers are chosen before seeing outcomes.
4. Offline validation (`policy.offline_qini`): leave-one-campaign-month-out refits, Qini of the
   policy's ranking on held-out months with a customer-cluster bootstrap CI, plus the realised
   held-out value of the policy's operating point under the channel's costs. FAIL (worse than
   random, or the operating point already lost money) means: do not spend an experiment on it.
   PASS is only "not shown to be worse": it is a screen, not evidence of value.
5. Power before the run: with the held-out value and SDs of the operating point, compute
   P(value LCB95 > 0) for the planned n per arm, the minimum detectable value and the n that 80%
   power would need. If the expected value is <= 0, say that no sample size helps. Size (k per
   policy) is fixed in code before any outcome; never enlarge an experiment after peeking.
6. One experiment: the month is split between policies by a salted hash, each policy targets at
   most k customers of its part (uplift-value top-k contacts only customers with positive
   expected value), each list is randomised 50/50, and the oracle is called once with the
   combined assignment. Read each policy out separately: ITT churn difference, value per contact
   with CI after contact + offer costs, SRM per stratum.
7. Gates: `model_experiment` needs policy.models + policy.offline_qini + a business owner who is
   not the requester. `model_scale` needs THIS policy's own PASSing readout + business owner +
   risk sign-off by two distinct people. Another policy's PASS never transfers.

## Channel economics (app is the default)

- `app`: 50 fen per contact + 10% of one month's price, charged only to treated customers who
  are retained (the discount is redeemed on the next bill).
- `call`: 1500 fen per contact + 20% offer (same redemption rule). In this world it loses money
  for every targeting rule; "churn went down" is not a reason to scale an expensive channel.
- Value per retained customer = 6 months x 60% margin x price. Break-even uplift on the app
  channel is roughly offer + contact divided by that value (~3 percentage points).

## Decision rules

- No experiment readout for the policy -> no value claim beyond "unknown"; recommend
  collecting evidence, not scaling.
- INCONCLUSIVE is a result: report the interval and the power, recommend no scale.
- A projection at scale is computed only from a PASSing readout's LCB95 x contacts per month,
  labelled as a projection.
- Report every gate verdict and reason verbatim; approvals never override failed evidence.

## Checklist

- [ ] ITT on `arm`; no exposed-vs-control comparison anywhere in the claim
- [ ] Only mature campaign months used; immature months named as excluded
- [ ] Offline validation recorded for each policy; FAIL policies not experimented
- [ ] Power, MDE and n-for-80% printed before the experiment; k fixed in code
- [ ] One experiment id, one oracle call, readout per policy with SRM and value CI after channel costs
- [ ] `request_release` with `policy_id` for `model_scale`; verdict reported as is; projection only from a PASS LCB95
