-- PII (email, phone, birth_year) stops here: only a derived test-account flag moves on.
SELECT
    customer_id,
    CAST(created_at AS TIMESTAMP)                    AS created_at,
    region,
    channel,
    COALESCE(email LIKE '%@qa.internal', FALSE)      AS is_test_account
FROM bronze.crm_customers
