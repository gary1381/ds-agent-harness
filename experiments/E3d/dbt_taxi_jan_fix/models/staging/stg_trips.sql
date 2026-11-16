-- One row per trip whose pickup falls in the month; the export's columns renamed, nothing dropped or changed.
-- cbd_congestion_fee exists from the 2025-01 export on; empty for older months.
{% set cols = source_columns() %}
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
    {% if 'cbd_congestion_fee' in cols %}
        cbd_congestion_fee,
    {% else %}
        null::double as cbd_congestion_fee,
    {% endif %}
    total_amount
from {{ source('tlc', 'yellow_trips') }}
where tpep_pickup_datetime >= {{ month_start() }} and tpep_pickup_datetime < {{ month_end() }}
