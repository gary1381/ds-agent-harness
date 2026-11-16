-- One row per trip whose pickup falls in the month; the export's columns renamed, nothing dropped or changed.
select
    vendorid as vendor_id,
    tpep_pickup_datetime as pickup_at,
    tpep_dropoff_datetime as dropoff_at,
    passenger_count,
    trip_distance,
    ratecodeid as ratecode_id,
    store_and_fwd_flag,
    pulocationid as pickup_zone,
    dolocationid as dropoff_zone,
    payment_type,
    fare_amount,
    extra,
    mta_tax,
    tip_amount,
    tolls_amount,
    improvement_surcharge,
    congestion_surcharge,
    airport_fee,
    total_amount
from {{ source('tlc', 'yellow_trips') }}
where tpep_pickup_datetime >= {{ month_start() }} and tpep_pickup_datetime < {{ month_end() }}
