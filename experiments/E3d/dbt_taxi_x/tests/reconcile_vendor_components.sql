-- The components reconcile with total_amount, to the cent, on at least 98% of each large vendor's trips whose fee
-- columns are filled. Fails (returns the vendor) when a vendor reconciles on fewer than 98% of those trips.
with t as (
    select
        vendor_id,
        total_amount - (
            fare_amount + extra + mta_tax + tip_amount + tolls_amount + improvement_surcharge
            + congestion_surcharge + airport_fee
        ) as residual,
        case when vendor_id = 1 then -(congestion_surcharge + airport_fee) else 0 end as expected
    from {{ ref('stg_trips') }}
    where vendor_id in (1, 2) and congestion_surcharge is not null and airport_fee is not null
)

select
    vendor_id,
    count(*) as trips,
    round(100 * avg((abs(residual - expected) < 0.005)::int), 2) as pct_reconciled
from t
group by vendor_id
having avg((abs(residual - expected) < 0.005)::int) < 0.98
