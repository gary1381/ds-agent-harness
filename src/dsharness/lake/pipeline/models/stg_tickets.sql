SELECT ticket_id, customer_id, CAST(created_at AS TIMESTAMP) AS created_at, category, csat,
       CAST(available_at AS TIMESTAMP) AS available_at
FROM bronze.support_tickets
