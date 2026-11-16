-- Governed transaction lines (UCI Online Retail II).
--  * Exact duplicate lines (all columns equal) are collapsed. OWNER DECISION, not provable from the data:
--    the source cannot tell an export duplicate from a customer scanning the same item twice.
--  * line_type separates products from cancellations, fees (postage, carriage, marketplace and bank
--    fees), manual adjustments / bad-debt invoices, vouchers and zero-price lines. Nothing is dropped.
WITH dedup AS (
    SELECT DISTINCT invoice, stock_code, description, quantity, invoice_ts, price, customer_id, country, available_at
    FROM staging.stg_transactions
)
SELECT *,
    -- money in exact DECIMAL: float sums depend on summation order and broke the 1-penny reconciliation
    CAST(quantity AS DECIMAL(18, 0)) * CAST(price AS DECIMAL(18, 4)) AS amount,
    strftime(invoice_ts, '%Y-%m') AS month,
    CASE WHEN country = 'United Kingdom' THEN 'UK' ELSE 'non-UK' END AS market,
    CASE
        WHEN invoice LIKE 'A%' OR upper(stock_code) IN ('M', 'ADJUST', 'ADJUST2', 'D', 'S', 'B') THEN 'adjustment'
        WHEN upper(stock_code) IN ('POST', 'DOT', 'C2', 'AMAZONFEE', 'BANK CHARGES', 'CRUK', 'PADS') THEN 'fee'
        WHEN lower(stock_code) LIKE 'gift%' THEN 'voucher'
        WHEN NOT regexp_matches(stock_code, '^[0-9]{5}') THEN 'other_nonproduct'
        WHEN invoice LIKE 'C%' THEN 'cancellation'
        WHEN price <= 0 OR quantity <= 0 THEN 'zero_or_negative'
        ELSE 'product'
    END AS line_type
FROM dedup
