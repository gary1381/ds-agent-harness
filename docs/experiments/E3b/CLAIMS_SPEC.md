# E3b v2: typed forecast claims (`claims.json`) and how a claim is realised

Draft, 2026-11-07, before any v2 attempt or key. Both arms write `claims.json` with every forecast or list their
reply delivers (DESIGN.md, revised plan, row 7). One function, `realise(claim, lines, files_dir, claims_by_id, data_end)`, turns a claim into
what actually happened. The gate calls it on past origins (backtest). After the census, the truth script calls it on
the sealed months. It is implemented twice from this document alone, by the designer and by GPT-6 Luna, and the two
must agree before the pre-registration is frozen (Luna's review, finding 9).

## The data `realise` reads

A table of order lines with the columns of `silver.order_lines` (author_brief.md): `order_no, sku, item_name, qty,
ordered_at, unit_price, buyer_id, country, amount, market, line_type`. The agents' warehouse ends 2025-08-31; the
truth table runs past 2025-11-30. `realise` must use only lines with `ordered_at` inside the claim's window, plus,
for the quantities marked *history*, lines before the window start. It never uses lines on or after the window end.

## The file

```json
{"asof": "2025-09-01",
 "claims": [ {...}, {...} ]}
```

`asof` is the first day the forecast is about; every window starts on or after it. Each claim has an `id` (unique,
at most 40 characters), a `type`, and a `says`: the reply's sentence the claim stands for, at most 200 characters.

### type `total`: a quantity over a window, optionally broken down

```json
{"id": "uk_sales", "type": "total", "says": "UK product sales Sep-Nov: about £2.1m (£1.8m-£2.4m)",
 "quantity": "product_sales", "filter": {"market": "UK"},
 "window": ["2025-09-01", "2025-12-01"], "by": null,
 "values": [{"key": null, "point": 2100000, "low": 1800000, "high": 2400000, "level": 0.9}]}
```

- `window`: [start, end), dates; end exclusive.
- `by`: null, `"week"`, `"month"`, `"country"`, `"sku"` or `"buyer_id"`. Each value has the `key` it is for: null
  for no breakdown, the week's Monday (`"2025-09-01"`), the month (`"2025-09"`), the country name, the sku or the
  buyer_id (as a string).
- Weeks run Monday to Sunday. A week cut by the window is counted only inside the window and keeps its Monday's key
  (the window [2025-09-01, 2025-12-01) has 13 weeks; the last, keyed 2025-11-24, runs to 30 November).
- `point` is required. `low`, `high` and `level` (a probability, e.g. 0.9) are given together or not at all.
- A key for which no line happened realises as 0 (for counts and sums).

### type `share`: one quantity divided by another

```json
{"id": "top20_share", "type": "share", "says": "the top 20 buyers will take about 38% of product sales",
 "numerator": {"quantity": "product_sales", "filter": {"buyers": {"top_by": "product_sales", "n": 20,
               "over": ["2024-09-01", "2025-09-01"]}}},
 "denominator": {"quantity": "product_sales", "filter": {}},
 "window": ["2025-09-01", "2025-12-01"],
 "values": [{"key": null, "point": 0.38, "low": 0.33, "high": 0.43, "level": 0.9}]}
```

Shares are fractions (0.38, not 38). `by` is allowed as in `total`; numerator and denominator are then both broken
down by the same key.

### type `list`: a ranked file of buyers (or of items per buyer)

```json
{"id": "call_list", "type": "list", "says": "the 250 buyers most likely not to order by November",
 "file": "call_list.csv", "id_column": "buyer_id", "score_column": "p_no_order", "higher_is": "event",
 "event": {"quantity": "orders", "filter": {}, "window": ["2025-09-01", "2025-12-01"], "op": "==", "value": 0},
 "population": {"buyers": {"active_in": ["2024-09-01", "2025-09-01"]}},
 "k": 250,
 "claimed": [{"metric": "precision_at_k", "point": 0.42, "low": 0.36, "high": 0.48, "level": 0.9}]}
```

- The file has one row per buyer (`id_column`) and a score (`score_column`). `higher_is: "event"` means a higher
  score means the event is more likely; `"no_event"` the reverse.
- `event`: per buyer, the `quantity` over the `window` (filtered), compared with `value` by `op` (`==`, `>=`, `>`,
  `<=`, `<`). Example: "places no product order" is orders == 0; "spends at least £500" is product_sales >= 500.
- `population`: the buyers the list ranks (a buyer filter, below). Buyers in the population but missing from the
  file count as the lowest scores (ranked last, in buyer_id order). Rows for buyers outside the population are
  dropped before ranking, and their number is reported.
- `k`: the list length that matters (the request's K, or the reply's). Metrics: `precision_at_k` (share of the top
  k with the event), `recall_at_k` (share of all event buyers in the population that are in the top k), `auc` (over
  the whole population, ties in score count half), `base_rate` (share of the population with the event).
- Per-buyer item lists (lu17): `id_column` is `"buyer_id"`, the file also has `sku` and `rank` columns, and the
  metric is `hit_at_k`: the share of listed buyers with at least one product line of a listed sku in the event
  window. `event` then has `quantity: "product_lines"`, `op: ">="`, `value: 1`, and the sku filter is the buyer's
  own listed skus.

### type `per_buyer`: a value per buyer

The same as a `total` with `by: "buyer_id"`, given as a file because it is long:

```json
{"id": "acct_spend", "type": "per_buyer", "says": "...", "file": "accounts.csv", "id_column": "buyer_id",
 "point_column": "spend_sep_oct", "low_column": null, "high_column": null, "level": null,
 "quantity": "product_sales", "filter": {}, "window": ["2025-09-01", "2025-11-01"]}
```

### type `not_identifiable`: the reply says the data cannot give it

```json
{"id": "discount_lift", "type": "not_identifiable", "says": "the extra orders from a £5 discount cannot be
 estimated: no past discount was randomised", "asked": "extra orders from the October discount",
 "reason": "causal"}
```

`reason` is `"causal"` (the effect of an action that never varied at random), `"absent_field"` (a field the table
does not hold: categories, cash, margin, region within a country, guest identity), or `"absent_population"` (a
population the data cannot name: "my twelve accounts", "the customers we are calling"). `realise` returns nothing
for it; the labellers judge it.

### type `verdict`: yes or no on a statement, resting on another claim

```json
{"id": "claim_half", "type": "verdict", "says": "No: the top 20 are unlikely to reach half",
 "statement": "the top 20 buyers will take at least half of product sales Sep-Nov",
 "rests_on": "top20_share", "op": ">=", "value": 0.5, "answer": "no", "probability": 0.05}
```

`realise` returns whether the statement came true: the realised value of `rests_on` (its single value) compared by
`op` with `value`.

## Quantities

All money is `amount` in GBP. "Product line" means `line_type = 'product'`.

| quantity | per window (and per key) | history? |
|---|---|---|
| `product_sales` | sum of `amount` over product lines | no |
| `product_qty` | sum of `qty` over product lines | no |
| `product_lines` | number of product lines | no |
| `orders` | number of distinct `order_no` with at least one product line | no |
| `buyers` | number of distinct non-null `buyer_id` with at least one product line | no |
| `new_buyers` | buyers in the window whose first product line ever (in all the data the caller passes, from its start, over all product lines whatever the claim's filter: edge case 4) falls in the window; with `by`, counted in the key of their first filtered product line in the window | yes |
| `repeat_buyers` | buyers in the window with a product line before the window start (any product line: edge case 4) | yes |
| `repeat_sales` | `product_sales` from lines of `repeat_buyers` | yes |
| `guest_sales` | `product_sales` over product lines with a null `buyer_id` | no |
| `cancellation_value` | minus the sum of `amount` over `line_type = 'cancellation'` lines (a positive number) | no |
| `cancellation_lines` | number of cancellation lines | no |

Guest lines (null `buyer_id`) count in `product_sales`, `product_qty`, `product_lines` and `orders` unless a buyer
filter is given. A buyer filter keeps only lines of the buyers it names, so it drops guest lines.

## Filters

A `filter` is an object; every key given must hold (AND). An empty object keeps all lines.

| key | value | keeps |
|---|---|---|
| `market` | `"UK"` or `"non-UK"` | lines of that market |
| `country` | a list of country names | lines whose `country` is in it |
| `sku` | a list of skus | lines of those skus |
| `catalogue_only` | true | skus starting with five digits |
| `buyers` | a buyer filter (below) | lines of those buyers |

A **buyer filter** names a set of buyers from data before the window start (never from the window):

| form | the set |
|---|---|
| `{"ids": ["12345", ...]}` | these buyers |
| `{"file": "x.csv", "id_column": "buyer_id"}` | the buyers in a delivered file |
| `{"active_in": [start, end]}` | buyers with a product line in [start, end) |
| `{"top_by": quantity, "n": 20, "over": [start, end]}` | the n buyers with the largest `quantity` in [start, end); ties broken by smaller buyer_id |
| `{"first_in": [start, end]}` | buyers whose first product line ever falls in [start, end) |

`active_in`, `top_by` and `first_in` must end on or before the claim window's start. When the window is moved to a
past origin (backtest, below), these dates move with it.

## Backtest origins (used by the gate, not by the truth script)

A claim made at `asof` = 2025-09-01 is replayed at a past origin `o` (a first of a month) by moving every date in
it (window, buyer filters, value keys). A date on the first of a month moves by whole months, so [2025-09-01,
2025-12-01) at `o` = 2024-09-01 becomes [2024-09-01, 2024-12-01); any other date moves by `o − asof` days; month
keys move by months. A weekly claim (`by: "week"`) instead moves every date and key by the whole number of weeks
nearest to `o − asof`, so its weeks stay Monday to Sunday (at 2024-09-01: 52 weeks back, window [2024-09-02,
2024-12-02)). The replay uses only lines before the moved window end. Origins: 2024-09-01 (the same season last
year), then 2025-01-01, 2025-02-01, … up to the last first-of-month whose moved window ends on or before
2025-09-01. The
agent's `predict.py --asof o` is run on the warehouse cut at `o` to give the forecast it would have made then; the
gate compares that forecast's values with `realise` of the shifted claim. `realise` itself is the same function the
truth script uses.

## `predict.py`: the procedure behind the claims (both arms)

    python predict.py --asof 2025-09-01 --warehouse <path to a warehouse.duckdb> --out <empty directory>

It reads only the warehouse it is given (opened read-only), builds every delivered forecast and file from lines
before `--asof`, and writes `claims.json` and the files the claims name into `--out`. Every date in its claims is
set from `--asof` (a forecast at 2024-09-01 covers [2024-09-01, 2024-12-01)), never written in. It must finish in
10 minutes on one core with no network. Random seeds are fixed, so two runs give the same output.

The gate runs it, in the agent's sandbox environment:
- at `--asof 2025-09-01` on the agent's warehouse: the output must equal the delivered `claims.json` and files
  (f.reproduce);
- at `--asof 2025-06-01` on the agent's warehouse and on a copy cut at 2025-06-01: the two outputs must be equal
  (f.timetravel: no feature may see lines after `--asof`);
- at each backtest origin on a copy cut at the origin: its claims are realised on the agent's warehouse (f.range,
  f.backtest).

## Output of `realise`

```json
{"id": "uk_sales", "values": [{"key": null, "actual": 2034567.12}], "notes": []}
```

For a `list`: `{"id", "metrics": {"precision_at_k": ..., "recall_at_k": ..., "auc": ..., "base_rate": ...},
"n_population", "n_file", "n_dropped", "notes"}`. For a `verdict`: `{"id", "came_true": true}`. For
`not_identifiable`: `{"id", "values": []}`. Money to the penny, shares to 6 decimals. A claim that cannot be realised
(a missing file, an unknown quantity, a window before the data) returns `{"id", "error": "<why>"}` and is never
silently skipped.

## Edge cases (settled 2026-11-07 23:59 from Luna's AMBIGUITIES.md, `experiments/E3b/runs/luna_realise/`)

Both implementations follow these. Numbers are Luna's points.

1. *Signature.* `realise(claim, lines, files_dir, claims_by_id=None, data_end=None)`. `claims_by_id` maps every claim
   id in the file to its claim, so a `verdict` can realise its `rests_on`; without it a verdict returns an error.
2. *Keys.* Only the keys listed in `values` are returned, in their order; a listed key with no lines is 0. Observed
   keys the claim does not list are ignored.
3. *per_buyer.* `values` holds one entry per distinct buyer id in the file, in file order (a repeated id keeps its
   first row, with a note), keyed by the id string; 0 for a buyer with no qualifying lines.
4. *History under filters.* Whether a buyer is new or repeat is decided from all their product lines, ignoring the
   claim's filter: new = first product line in the passed data falls in the window; repeat = a product line before
   the window start. The filter then applies to the measured lines: `new_buyers` with `market: "UK"` counts new
   buyers with a UK product line in the window. With `by`, a buyer counts in the key of their first filtered
   product line in the window. (This replaces the earlier "after the claim's filter" wording.)
5. *top_by.* Ranks over all product lines in its span (no other filter); ties go to the smaller buyer id.
6. *Buyer ids* are integers. `123`, `123.0` and `"123"` are the same buyer; the output key is `"123"`.
7. *Breakdowns* compute only the requested keys (as 2).
8. *Shares.* A zero denominator gives `"actual": null` and a note, whatever the numerator.
9. *Undefined metrics* are `null`: AUC when the population has no event or no non-event buyer; recall when no buyer
   has the event; precision when k is 0.
10. *Ranking.* Higher score first (after `higher_is`); ties at the cut go to the smaller buyer id. A population buyer
    missing from the file, or whose score does not parse as a number, ranks after every scored buyer, by buyer id.
    In AUC every such buyer has the same lowest score (ties count half).
11. *List counts.* `n_file` = distinct buyer ids in the file as submitted; `n_dropped` = those outside the population;
    `n_population` = buyers in the population.
12. *hit_at_k.* Per buyer: a listed in-population buyer is a hit when they have a product line, in the event window,
    of any sku they are listed with at `rank` <= k. The metric is the share of listed in-population buyers that are
    hits; population buyers not in the file are not in the denominator.
13. *Coverage.* `data_end` (exclusive) is the end of the data the caller passes (the gate: the cut date; the truth
    script: 2025-12-01 or later). A window ending after `data_end` returns an error. If `data_end` is not given, it is
    the day after the last line's date. A window starting before the first line's date returns an error.
14. *Backtest shift:* the rule in "Backtest origins" above (months for firsts of a month, whole weeks for weekly
    claims), not a plain day count.
15. *catalogue_only:* the sku starts with five digits (anything may follow).
16. *Rounding:* Python `round(x, 2)` for money and counts, `round(x, 6)` for shares and metrics; comparisons between
    implementations allow 0.01 for money and 1e-6 for shares.

### Settled after the first comparison (2026-11-08 00:40; `experiments/E3b/compare_realise.py`, 173 cases, 7 apart)

17. *repeat_buyers with `by`*: a repeat buyer counts in every key where they have a filtered product line in the
    window, as `buyers` does. Only `new_buyers` counts a buyer once, in the key of their first filtered line (4).
18. *per_buyer files* must hold `id_column` and `point_column` (and `low_column` / `high_column` when named);
    otherwise the claim returns an error.
19. *Lists*: a repeated buyer id keeps its first row, as in 3; its later rows are ignored, with a note.
20. *`higher_is`* is required for score lists only. Item lists (`hit_at_k`) rank by `rank` and ignore it.
21. *Ties in "first line"* (4): the earliest `ordered_at`, then the smallest `order_no`, then the smallest `sku`.
22. *Empty denominators* in list metrics give `null` (an item list with no listed in-population buyer: `hit_at_k`
    null).

### Agreement (2026-11-08 00:42)

After rules 17–22, Luna's third version (`experiments/E3b/runs/luna_realise3/realise_luna.py`, sha256
`e0ddadf0…`) and the designer's (`src/dsharness/readout/forecast.py`, sha256 `fdb09bce…`) agree on all 173 cases
(every type, quantity, filter, breakdown and list metric at as-of 2025-06-01, plus each total and share replayed at
three backtest origins), with 0 shift disagreements. Both are hashed into the pre-registration; the truth script
runs both after the census and reports any difference.
