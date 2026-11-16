SELECT
    subscription_id,
    customer_id,
    plan,
    monthly_price_cents                              AS monthly_price_fen,   -- CURRENT price (mutable)
    CAST(started_at AS TIMESTAMP)                    AS started_at,
    CAST(ended_at AS TIMESTAMP)                      AS ended_at,            -- mutable until churn
    cancel_reason,                                                           -- only known after churn
    CAST(updated_at AS TIMESTAMP)                    AS updated_at
FROM bronze.billing_subscriptions
