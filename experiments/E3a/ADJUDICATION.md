# E3a truth adjudication: governed build A vs independent build B

2026-11-07. Build B was written by Luna (gpt-6-luna, high) in the bwrap sandbox from the raw
disguised export and the definitions text only (`run_truth_b.py`; no repository, no pipeline SQL, no
harness code; about 5 minutes). `compare_truth.py` compared it with build A (the workspace's
`silver.order_lines` and the E3a semantic model).

## Result: no disagreement

| comparison | A | B | disagreements |
|---|---|---|---|
| order lines (after removing exact duplicates) | 1,033,036 | 1,033,036 | 0 only in A, 0 only in B |
| line type | | | 0 |
| amount (qty × unit price, exact decimal) | | | 0 |
| month, market | | | 0 |
| metric values (10 metrics × 25 months × ALL / UK / non-UK) | 750 | 750 | 0 |

Nothing needed adjudicating. B's own notes (`truth_b/work/NOTES.md`, git-ignored with the data) list
the readings it chose where it found the definitions open; each matches A:

- exact duplicate rows (all eight source columns equal) counted once: 34,335 removed;
- `unit_price` cast to `DECIMAL(18,3)` before multiplying, so amounts are exact;
- line-type rules applied in the listed order; `market` = 'UK' only for the exact country value
  `United Kingdom` (`Unspecified` is non-UK);
- customer counts per market slice: a customer counts where they bought in that market that month;
  first month and repeat status are decided over the whole data set; guest orders count in sales and
  orders but never in customer counts; everyone active in 2023-12 is new;
- cancellation lines are valued in their own month.

That two implementations written from the same text agree everywhere says the definitions are
unambiguous on this data. It does not show that both are right about what the business means; the
designer's review of each question's truth SQL (REVIEW.md) is still the check on that.

## Truth used by the census

`workspace/external/e3a/truth_lines.parquet` = build B's `truth_lines.parquet`, sha256
`3103c8cdcbc775f6058fdaac7175036eca19fc86f71eae8361accfd3a88f9b12` (raw export sha256
`3ed6a1118d671e886c412179049c13e2a112b682fc75b47a5f146d16f4f65225`; B's metrics file
`3b58371e345d0b68822389a7c44f98edebf3c00e410b79207f9492f71bb10558`). Question authors write their
truth SQL against it; `build_bank.py` runs every truth SQL on it.
