-- The served monthly metrics: trips and the sum of every money column as reported (DEFINITIONS.md).
select
    '{{ var("month") }}' as month,
    count(*) as trips,
    round(sum(fare_amount), 2) as fare_amount,
    round(sum(extra), 2) as extra,
    round(sum(mta_tax), 2) as mta_tax,
    round(sum(tip_amount), 2) as tip_amount,
    round(sum(tolls_amount), 2) as tolls_amount,
    round(sum(improvement_surcharge), 2) as improvement_surcharge,
    round(sum(congestion_surcharge), 2) as congestion_surcharge,
    round(sum(airport_fee), 2) as airport_fee,
    round(sum(cbd_congestion_fee), 2) as cbd_congestion_fee,
    round(sum(total_amount), 2) as total_amount
from {{ ref('stg_trips') }}
