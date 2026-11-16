-- The components add up to total_amount, to the cent, under the vendor rules of DEFINITIONS.md, on at least 98% of
-- each large vendor's trips whose fee columns are filled. Vendor 1 also books every surcharge it collects inside
-- `extra`, so its components exceed the total by those surcharges.
WITH t AS (
    SELECT vendor_id,
           total_amount - (fare_amount + extra + mta_tax + tip_amount + tolls_amount + improvement_surcharge
                           + congestion_surcharge + airport_fee) AS residual,
           CASE WHEN vendor_id = 1 THEN -(congestion_surcharge + airport_fee) ELSE 0 END AS expected
    FROM staging_trips
    WHERE vendor_id IN (1, 2) AND congestion_surcharge IS NOT NULL AND airport_fee IS NOT NULL
)
SELECT 'reconcile_vendor_' || vendor_id,
       avg((abs(residual - expected) < 0.005)::INT) >= 0.98,
       round(100 * avg((abs(residual - expected) < 0.005)::INT), 2) || '% of ' || count(*) || ' trips reconcile'
FROM t GROUP BY vendor_id ORDER BY vendor_id
