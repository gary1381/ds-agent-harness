-- Governed invoice facts.
--  * dedup on invoice_id only (at-least-once export resends); identical-looking rows with
--    different invoice_ids are legitimate purchases and are kept.
--  * unit corrections come ONLY from silver.ref_known_incidents: a row inside a registered
--    incident's (region, month, invoice_type) scope is multiplied by the incident's factor and
--    flagged (unit_corrected), never dropped. Nothing outside a registered scope is "fixed": an
--    unregistered unit error fails the build (schema.yml price-book tests) instead of being
--    silently corrected.
--  * refunds stay negative and are booked in the month they were issued.
WITH dedup AS (
    SELECT * FROM staging.stg_invoices
    QUALIFY ROW_NUMBER() OVER (PARTITION BY invoice_id ORDER BY available_at) = 1
)
SELECT
    d.invoice_id,
    d.customer_id,
    d.invoice_type,
    d.amount_fen_raw * COALESCE(k.factor, 1)                             AS amount_fen,
    k.incident_id IS NOT NULL                                            AS unit_corrected,
    d.issued_at,
    d.available_at,
    strftime(d.issued_at, '%Y-%m')                                       AS revenue_month,
    d.original_invoice_id,
    c.region,
    c.is_test_account
FROM dedup d
LEFT JOIN silver.customers c USING (customer_id)
LEFT JOIN silver.ref_known_incidents k
       ON k.region = c.region
      AND k.month = strftime(d.issued_at, '%Y-%m')
      AND k.invoice_type = d.invoice_type
