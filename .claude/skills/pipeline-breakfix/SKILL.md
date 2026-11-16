---
name: pipeline-breakfix
description: Diagnose and fix a failing governed data pipeline build in DS Harness (e.g. a source schema change, an off-price-book charge, a new unit incident) through the dsh-de MCP tools, so that CI passes the build, the regression diff on already-served data and the oracle-independence check. Use when a build is FAILED, a source contract changed or a reconciliation or price-book test breaks.
---

# Pipeline break-fix (dsh-de)

The pipeline is reviewed SQL (`models/*.sql`) plus data tests and reconciliations
(`schema.yml`). You can only propose new file contents; CI decides.

## Procedure

1. `pipeline_status` for the failing cutoff (e.g. `drift`): read the failing model's error,
   the skipped dependants, the failing tests and the source contract versions
   (`source_contracts` lists the columns of every schema version).
2. `pipeline_status` for a READY cutoff (e.g. `score`) to see what "working" looks like.
3. `read_pipeline_file` for the failing model and its consumers. `describe_table` /
   `search_catalog` / `run_sql` on the READY build for column semantics and units.
4. Write the smallest fix that handles EVERY source schema version in use (old exports are
   rebuilt too). In DuckDB, `COLUMNS('^amount_(cents|minor)$') AS 'amount_fen_raw'` selects
   whichever amount column exists.
5. `propose_pipeline_change` with full new file contents and a rationale. Read all three CI
   results:
   - `ci.build`: the newest cutoff must be READY, including the Finance-close reconciliation and
     the price-book / incident-scope tests; `ci.reference_data_changed` must be empty;
   - `diff`: rebuilding the already-served cutoff must reproduce every silver table exactly;
   - `ci.oracle_independence`: nothing upstream of a reconciled model may read the record it is
     reconciled against.
6. Report the gate verdict for `pipeline_merge`. Merging is a human step.

## Decision rules

- A reconciliation failure means the numbers are wrong. Find the unit, sign or duplication error;
  never edit, weaken or delete a test or reconciliation (CI fails that as "tests removed").
- Never compute pipeline data from the Finance close (balancing entries, scaling factors):
  a reconciliation against its own input proves nothing and fails oracle independence.
- A fix that works only for the new schema version fails the regression diff by design.
- Silver tables are the certified surface. Keep their columns and types stable.
- Personal-data columns stay in the reviewed staging model. Changes that read them are refused.
- Unit corrections are REGISTERED, never inferred. The invoices model corrects only rows inside a
  registered incident's (region, month, invoice type) scope from `models/ref_known_incidents.sql`.
  Never broaden the scope: no rule like "any charge below N whose x100 is a list price", no extra
  region or month, no flag left false on a row you changed. Tests check both directions (every
  in-scope charge was really affected; nothing outside a scope differs from the export).
- A charge below the cheapest plan outside every registered scope (`no_unregistered_unit_incident`
  fails) is a NEW incident. Stop and escalate with the failing-test sample (region, month, rows):
  registration is a human-approved Finance/Billing change. CI fails any agent change to
  `models/ref_known_incidents.sql` or `models/ref_plan_prices.sql` (reference data the tests use),
  and CI reads the raw export itself: "fixing" the incident in a staging model fails too.
- Price-book validity: a charge must equal the price of the customer's plan valid on the day it
  was issued (`valid_from`/`valid_to`; plus is 5900 until 2026-07-31 and 6900 from 2026-08-01).
  An off-book charge is a data problem to explain, not a price row to add.

## Pitfalls (known defect classes in sources)

- Schema drift: a renamed amount column plus a new currency column; check the currency contract
  before assuming units.
- Amounts are minor units (fen). Dividing by 100 "to get yuan" breaks the price-book test and the
  reconciliation.
- Export resends duplicate invoice lines (deduplicate by invoice id only). Look-alike add-ons are
  real purchases.
- A historic unit incident (INC-2026-03-SOUTH: south charges of 2026-03 exported in yuan) is
  registered and corrected in the invoices model. Keep that correction exactly as scoped.
- A price change is dated: plus charges after 2026-08-01 are 6900 (and 5900 before). A fix that
  "normalises" them to one price breaks the price-book test.
- Refunds are negative and booked in their own month.

## Checklist

- [ ] Root cause identified from `pipeline_status` (model error, not just the symptom)
- [ ] Fix handles every billing schema version present in the exports
- [ ] No test or reconciliation removed or edited; no read of the Finance close upstream of invoices
- [ ] No change to the incident registry or the price book; correction scope not broadened
- [ ] A new unit incident or off-book charge was escalated with its sample, not patched
- [ ] `propose_pipeline_change`: build READY, regression diff PASS, oracle independence PASS
- [ ] Gate verdict reported verbatim
