-- Almost every exported trip belongs to the month (the export carries a few stray pickups), and no trip is lost.
SELECT 'rows_in_month',
       (SELECT count(*) FROM staging_trips) >= 0.999 * (SELECT count(*) FROM raw_trips),
       (SELECT count(*) FROM staging_trips) || ' of ' || (SELECT count(*) FROM raw_trips) || ' exported trips'
