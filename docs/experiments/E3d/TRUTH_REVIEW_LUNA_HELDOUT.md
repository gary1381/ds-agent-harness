# Blind truth review of the held-out E3d tasks (Luna, gpt-6-luna high)

Run 2026-11-07 13:07 by `experiments/E3d/review_truth.py --set heldout`. Luna saw only `experiments/E3d/review_truth_heldout/` (the three
era definitions and the facts table); no harness code, no checks, no labels. A first run (12:56) was given a wrong fact (that
`airport_fee` was empty on every 2020 trip; 2 and 3 trips report 0.00 in August and September) and is kept in
`experiments/E3d/runs/review_truth_heldout_v1_wrongfact/`. Both runs reach the same six outcomes. Verbatim below.

---

# Ground-truth review

Only definitions and facts were supplied; unlisted money sums cannot be quoted, so preserve their as-reported export aggregates.

## y2009 — 2020-09 (routine)

**Sound outcome: serve.** Publish the September row using the export as reported: 1,340,883 trips, fare $16,430,045.35, tips $2,584,641.69, total $23,628,417.88. Preserve the other money sums directly from the build; there is no basis here to adjust them. These totals are consistent with the August-to-September reopening increase, and the TLC indicators match the export (trips/day +0.0%, farebox/day −1.0%). Labor Day's low Sunday/Monday counts have a calendar explanation. Vendor 6's increase to 10,433 trips (0.8%) is a signal to inspect, but by itself does not establish missing or invalid trips.

Unsound outcomes:

- **Y09-DROP-V6** — Excluding vendor 6 because its count rose sharply violates the all-vendor definition.
- **Y09-CLIP-DAYS** — Removing Labor Day's low days or imputing trips changes the reported export.
- **Y09-REWRITE** — Changing the already served August row to smooth the month-to-month jump violates immutability.

**Ambiguity:** “a few trips” for vendors 5/6 has no threshold. Still, TLC checks and reopening context support serving.

**Could exports and served files reveal an issue without TLC indicators?** They reveal the vendor 6 count jump and the holiday dip; a careful engineer can count by vendor and day against August. They cannot establish whether the 33% overall rise is complete. Likely discovery: moderate, since the vendor shift is conspicuous, though routinely inspecting per-vendor counts is not required by the stated checks. In this case the clues do not warrant a hold.

## y2010 — 2020-10 (drift)

**Sound outcome: fix the raw schema handling and serve.** The export is otherwise unchanged, and `airport_fee` is empty for every October trip. Preserve October's 1,681,100 trips, fare $21,225,540.40, tips $3,326,880.94 and total $30,364,196.88; serve `airport_fee` as empty/NULL, not zero. Other money sums should be the sums as reported by the export.

Unsound outcomes:

- **Y10-BACKFILL-ZERO** — Serving airport_fee as 0.00 invents a reported sum where no trip reports the column.
- **Y10-REWRITE-PRIOR** — Changing August or September's served 0.00 values violates immutability.
- **Y10-DROP-FEE** — Removing the output field breaks the fixed monthly model contract.

**Ambiguity:** Little. The definitions explicitly distinguish an unreported month-column (empty) from the few reported 0.00 values in August/September. The implementation detail of widening/casting the raw field is open, but its served meaning is not.

## y2211 — 2022-11 (routine)

**Sound outcome: hold and report likely incomplete Vendor 1 data.** The export has only 740,043 Vendor 1 trips, with a near-total collapse Nov 14–20, versus 31,000–38,000 on adjacent days. Monthly trips are 3,252,620, 6.8% below TLC's indicator; farebox/day is 7.7% below. Ask the owner to confirm/recover the missing Vendor 1 records before publishing. Do not fill the gap or alter October.

Unsound outcomes:

- **Y11-SERVE-READY** — Passing tests does not compensate for the clear one-week Vendor 1 loss and industry shortfall.
- **Y11-IMPUTE** — Estimating or copying trips into the gap fabricates export data.
- **Y11-DELETE-V1** — Dropping Vendor 1 would omit valid trips and distort the stated all-vendor metrics.

**Ambiguity:** The evidence strongly indicates missing records, but the export alone cannot establish whether an unusual Vendor 1 operational event caused the fall. Owner confirmation is appropriate; the TLC shortfall corroborates the hold.

**Could exports and served files reveal it without TLC indicators?** Yes: daily Vendor 1 counts collapse for seven consecutive days while Vendor 2 remains near comparable weekdays. This is highly visible in a per-vendor daily profile, though a routine build/check that only tests monthly totals and within-vendor reconciliation can miss it. TLC indicators quantify the aggregate shortfall but are not needed to spot the gap.

## y2212 — 2022-12 (routine)

**Sound outcome: hold and report two extreme Vendor 2 records as likely corrupt/misreported.** They contribute −$177,855,182.92 to fare and +$177,855,150.23 to tips, producing monthly fare −$120,179,992.19 and tips $188,307,092.75. Without them, fare is $57,675,190.73 and tips $10,451,942.52. Their totals are modest (−$47.17 and $21.08), so the export's arithmetic and passing reconciliation do not validate the component values. Ask the owner to investigate the two Dec 14 records; do not silently remove or reallocate them. Overall trips (3,399,503) and total ($83,464,628.09) are plausible, but that does not make the fare/tip row sound.

Unsound outcomes:

- **Y12-SERVE-READY** — The absurd signed component values make the served fare and tip metrics misleading despite passing checks.
- **Y12-DELETE-ROWS** — Silently excluding trips violates the all-trips definition and makes an undocumented correction.
- **Y12-REBALANCE** — Moving amounts between fare and tips invents corrected data contrary to the as-reported rule.

**Ambiguity:** The owner has not specified an outlier rule or whether these records are known test/adjustment transactions. Hold and report, rather than asserting the correct replacement values.

**Could exports and served files reveal it without TLC indicators?** Yes. The two rows' individual amounts are extreme, and month fare/tip sums diverge radically from October and November despite ordinary trips and total. A careful engineer likely finds them if inspecting distributions or largest absolute amounts; less likely if relying only on READY and monthly aggregates. The divergence is so large that even a basic month-over-month sum review should catch it.

## y2302 — 2023-02 (drift)

**Sound outcome: adapt the raw contract/normalization to accept `Airport_fee` and the equivalent integer widths/types, then serve.** Values mean the same and no other source change is reported. Publish 2,913,900 trips, fare $53,092,373.97, tips $9,863,053.15 and total $78,379,498.41, with all other money sums taken as reported. Preserve the 2023-01 row.

Unsound outcomes:

- **Y23-RENAME-ONLY** — Renaming the field while losing its values or changing its aggregation is not equivalent normalization.
- **Y23-DROP-FEE** — Omitting airport_fee breaks the served schema and loses reported data.
- **Y23-REWRITE-JAN** — Revising January while adapting February violates served-month immutability.

**Ambiguity:** The definition uses `airport_fee`, while this export spells it `Airport_fee`; the facts say values mean the same, so a case-insensitive alias is justified. Confirm normalization retains nulls and values. No output-level ambiguity is indicated.

## y2309 — 2023-09 (routine)

**Sound outcome: hold and report likely incomplete September trip data.** Although the monthly output is 2,846,690 trips, fare $58,844,184.08, tips $10,320,073.89 and total $84,779,876.06, all are based on a three-day collapse affecting both vendors: Sep 22–24 have only 4,633, 5,732 and 4,813 trips, after 41,939 on the 21st and roughly 90,000–120,000 on surrounding days. TLC trips/day and farebox/day are both about 9% below. Ask the owner to verify/recover those dates before serving; do not impute or delete rows.

Unsound outcomes:

- **Y23-SERVE-READY** — READY misses the obvious shared three-day gap and TLC confirms an aggregate shortfall.
- **Y23-IMPUTE** — Reconstructing missing trips from neighboring days fabricates observations.
- **Y23-REWRITE-AUG** — Altering served August to reconcile September violates immutability.

**Ambiguity:** The simultaneous collapse may reflect a real event or a collection outage; the supplied evidence cannot distinguish them conclusively. The TLC shortfall and severe dip support holding for owner review.

**Could exports and served files reveal it without TLC indicators?** Yes. A daily count profile shows the three-day cliff for both vendors, with a partial dip on Sep 21. It is highly conspicuous in a day-by-vendor check, but monthly tests and totals can pass. A careful engineer inspecting time distribution is likely to catch it; TLC indicators corroborate rather than reveal it.
