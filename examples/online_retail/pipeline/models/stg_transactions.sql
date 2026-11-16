SELECT invoice, stock_code, description, quantity,
       CAST(invoice_ts AS TIMESTAMP) AS invoice_ts, price,
       CAST(customer_id AS BIGINT) AS customer_id, country,
       CAST(available_at AS TIMESTAMP) AS available_at
FROM bronze.retail_transactions
