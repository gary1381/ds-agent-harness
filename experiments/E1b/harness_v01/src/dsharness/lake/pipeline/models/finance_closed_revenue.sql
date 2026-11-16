-- Official, already-closed books. An independent record the business already trusts.
SELECT month, region, CAST(net_revenue_fen AS BIGINT) AS net_revenue_fen
FROM bronze.finance_closed_revenue
