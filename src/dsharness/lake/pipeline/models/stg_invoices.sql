-- Billing export schema v1 (amount_cents). Raw: may contain resend duplicates and unit errors.
SELECT
    invoice_id,
    customer_id,
    invoice_type,
    amount_cents                                     AS amount_fen_raw,
    CAST(issued_at AS TIMESTAMP)                     AS issued_at,
    CAST(available_at AS TIMESTAMP)                  AS available_at,
    original_invoice_id
FROM bronze.billing_invoices
