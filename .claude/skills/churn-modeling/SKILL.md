---
name: churn-modeling
description: Build, evaluate and request release of a monthly churn model in DS Harness through the dsh-ds MCP tools, with point-in-time features, leakage evidence, a budgeted sealed evaluation and gate decisions. Use for churn prediction, feature engineering, model comparison or "is this model good enough to ship" requests.
---

# Churn modeling (dsh-ds)

The task: predict `churned_next_month` for customers active at the start of a month. You
choose definitions (features, model family, hyper-parameters, training months); the harness
owns training, evaluation and evidence.

## Procedure

1. `list_features` — start from the trusted point-in-time features and read their SQL to learn
   the patterns (`{as_of}` DATE placeholder, `population` relation, availability guards).
2. Explore if needed with `list_metrics`, `query_metric`, `describe_table` (temporal tags!) and
   `run_sql`.
3. New feature -> `propose_feature`. It must return `(customer_id, value)` and use only rows
   with `available_at < {as_of}` (or entity attributes that cannot change). Only a PASS counts.
   A FAIL lists the customers whose value changed between an early and a late snapshot: that
   is leakage, rewrite the definition. Do not rename and retry the same logic.
4. `train_candidate` with >= 4 first-of-month `train_as_of` dates whose labels are mature
   (as_of + 1 month + 7 days before the build cutoff), e.g. 2025-12-01 .. 2026-04-01.
   Compare candidates on dev CV (AUC, Brier, log loss). Check `model_leakage.outcome == PASS`.
5. Pick ONE final candidate. `request_final_evaluation` spends the sealed holdout budget
   (3 per task); it returns aggregate metrics with bootstrap intervals.
6. `request_release` for `model_shadow` (with `candidate_id`). That is the end of the model's
   own path: a model is never experimented on or scaled by itself. Experiments and scaling gate a
   targeting POLICY (rule + models + channel + k) through `request_release(policy_id=..., stage=
   "model_experiment" | "model_scale")`; follow `.claude/skills/retention-uplift/SKILL.md`.

## Decision rules

- Dev CV that looks too good (AUC far above ~0.7 here) is a leak signal, not a win.
- Never use current-state or post-outcome columns (current price, end date without a known-at
  guard, cancel reason) as predictors. Use the last charged amount for price.
- Prefer the simpler model when the sealed interval overlaps; calibration (Brier/ECE) matters
  for targeting.
- Predicted risk is not value: whether contacting high-risk customers pays off is decided by a
  randomised experiment, never by the model's scores. Risk ranks who leaves; uplift ranks who
  stays *because* of the contact, and channel costs decide whether staying pays for the contact.

## Pitfalls (known defect classes)

- Late-arriving usage rows: last month's usage is often missing at `as_of`. Treat missing as
  missing, never fill it from a later snapshot.
- Mutable price and end-date columns pass a time-travel check while nothing changes, but still
  leak. The static check catches them. Both checks must pass.
- Post-outcome fields (cancellation reason) are direct label leaks.
- Internal QA accounts are excluded from the population. Do not add them back via joins.
- Labels of the newest months are immature; they are unknown, not "no churn".
- The population shifts later in the year (new channel, price rise). A model can degrade
  without any code change.

## Checklist

- [ ] Every feature is trusted or has a PASS from `propose_feature`
- [ ] `train_candidate` used >= 4 mature months; `model_leakage` is PASS
- [ ] Dev CV plausible (no leak-sized jump)
- [ ] Exactly one `request_final_evaluation` for the chosen candidate
- [ ] `request_release` verdict and reasons reported verbatim; nothing claimed beyond the gate
- [ ] No value claim from the churn model; decisions go through the retention-uplift procedure
