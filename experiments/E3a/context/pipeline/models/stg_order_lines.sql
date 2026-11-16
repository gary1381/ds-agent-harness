SELECT order_no, sku, item_name, CAST(qty AS BIGINT) AS qty,
       CAST(ordered_at AS TIMESTAMP) AS ordered_at, CAST(unit_price AS DOUBLE) AS unit_price,
       CAST(buyer_id AS BIGINT) AS buyer_id, country,
       CAST(available_at AS TIMESTAMP) AS available_at
FROM bronze.order_lines
