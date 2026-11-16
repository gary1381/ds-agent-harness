-- The served monthly metrics: trips and the sum of every money column as reported (DEFINITIONS.md).
SELECT
    strftime(TIMESTAMP '{month_start}', '%Y-%m') AS month,
    count(*)                                     AS trips,
    round(sum(fare_amount), 2)                   AS fare_amount,
    round(sum(extra), 2)                         AS extra,
    round(sum(mta_tax), 2)                       AS mta_tax,
    round(sum(tip_amount), 2)                    AS tip_amount,
    round(sum(tolls_amount), 2)                  AS tolls_amount,
    round(sum(improvement_surcharge), 2)         AS improvement_surcharge,
    round(sum(congestion_surcharge), 2)          AS congestion_surcharge,
    round(sum(airport_fee), 2)                   AS airport_fee,
    round(sum(total_amount), 2)                  AS total_amount
FROM staging_trips
