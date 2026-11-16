-- The components add up to total_amount, to the cent, under the vendor rules of DEFINITIONS.md, on at least 98% of
-- each large vendor's trips whose fee columns are filled. Vendor 1 also books every surcharge it collects inside
-- `extra`, so its components exceed the total by those surcharges.
-- Fails (returns the vendor) when a vendor reconciles on fewer than 98% of those trips.
with t as (
    select
        vendor_id,
        total_amount - (
            fare_amount + extra + mta_tax + tip_amount + tolls_amount + improvement_surcharge
            + congestion_surcharge + coalesce(airport_fee, 0)
        ) as residual,
        case when vendor_id = 1 then -(congestion_surcharge + coalesce(airport_fee, 0)) else 0 end as expected
    from {{ ref('stg_trips') }}
    where vendor_id in (1, 2) and congestion_surcharge is not null
)

select
    vendor_id,
    count(*) as trips,
    round(100 * avg((abs(residual - expected) < 0.005)::int), 2) as pct_reconciled
from t
group by vendor_id
having avg((abs(residual - expected) < 0.005)::int) < 0.98
