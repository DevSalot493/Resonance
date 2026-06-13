with source as (
    select * from public.artists
)

select
    artist_id,
    name,
    lastfm_name,
    mb_id,
    catalog_tier,
    catalog_status,
    created_at,
    updated_at
from source
where catalog_status = 'active'
