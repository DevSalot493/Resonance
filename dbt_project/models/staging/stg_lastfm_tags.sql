with source as (
    select * from public.raw_lastfm_tags
),

cleaned as (
    select
        id,
        artist_id,
        lower(trim(tag_name))   as tag_name,
        tag_weight::float       as tag_weight_raw,
        fetched_at
    from source
    where tag_weight >= 5
)

select * from cleaned
