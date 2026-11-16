-- Only the licensed technology vendors report trips.
SELECT 'vendor_ids', count(*) FILTER (WHERE vendor_id NOT IN (1, 2, 6, 7)) = 0,
       count(*) FILTER (WHERE vendor_id NOT IN (1, 2, 6, 7)) || ' trips with another vendor id'
FROM staging_trips
