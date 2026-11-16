-- E3a governed order lines (build A); rules in experiments/E3a/DEFINITIONS.md.
--  * Exact duplicate rows (all eight columns equal) are collapsed. OWNER DECISION, not provable from the data.
--  * line_type: the first matching rule wins. Nothing is dropped.
WITH dedup AS (
    SELECT DISTINCT order_no, sku, item_name, qty, ordered_at, unit_price, buyer_id, country, available_at
    FROM staging.stg_order_lines
)
SELECT *,
    -- exact DECIMAL money: float sums depend on summation order
    CAST(qty AS DECIMAL(18, 0)) * CAST(unit_price AS DECIMAL(18, 4)) AS amount,
    strftime(ordered_at, '%Y-%m') AS month,
    CASE WHEN country = 'United Kingdom' THEN 'UK' ELSE 'non-UK' END AS market,
    CASE
        WHEN order_no LIKE 'A%' OR upper(sku) IN ('M', 'ADJUST', 'ADJUST2', 'D', 'S', 'B') THEN 'adjustment'
        WHEN upper(sku) IN ('POST', 'DOT', 'C2', 'AMAZONFEE', 'BANK CHARGES', 'CRUK', 'PADS') THEN 'fee'
        WHEN lower(sku) LIKE 'gift%' THEN 'voucher'
        WHEN NOT regexp_matches(sku, '^[0-9]{5}') THEN 'other_nonproduct'
        WHEN order_no LIKE 'C%' THEN 'cancellation'
        WHEN unit_price <= 0 OR qty <= 0 THEN 'zero_or_negative'
        ELSE 'product'
    END AS line_type
FROM dedup
