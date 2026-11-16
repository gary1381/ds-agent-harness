-- pricing_page_views is NULL for months before product analytics started tracking it (2026-01)
SELECT customer_id, usage_month, minutes, sessions, pricing_page_views,
       CAST(available_at AS TIMESTAMP) AS available_at
FROM bronze.app_usage_monthly
