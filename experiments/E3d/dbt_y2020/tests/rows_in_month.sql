-- Almost every exported trip belongs to the month (the export carries a few stray pickups), and no trip is lost.
-- Fails (returns a row) when fewer than 99.9% of the exported trips reach staging.
with counts as (
    select
        (select count(*) from {{ ref('stg_trips') }}) as staged,
        (select count(*) from {{ source('tlc', 'yellow_trips') }}) as exported
)

select
    staged,
    exported
from counts
where staged < 0.999 * exported
