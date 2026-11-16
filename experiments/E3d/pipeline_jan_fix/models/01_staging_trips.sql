-- One row per trip whose pickup falls in the month; the export's columns renamed, nothing dropped or changed.
SELECT
    VendorID              AS vendor_id,
    tpep_pickup_datetime  AS pickup_at,
    tpep_dropoff_datetime AS dropoff_at,
    passenger_count,
    trip_distance,
    RatecodeID            AS ratecode_id,
    store_and_fwd_flag,
    PULocationID          AS pickup_zone,
    DOLocationID          AS dropoff_zone,
    payment_type,
    fare_amount,
    extra,
    mta_tax,
    tip_amount,
    tolls_amount,
    improvement_surcharge,
    congestion_surcharge,
    Airport_fee           AS airport_fee,
    cbd_congestion_fee,
    total_amount
FROM raw_trips
WHERE tpep_pickup_datetime >= TIMESTAMP '{month_start}' AND tpep_pickup_datetime < TIMESTAMP '{month_end}'
