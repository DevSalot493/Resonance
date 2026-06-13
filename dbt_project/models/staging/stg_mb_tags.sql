with source as (
    select * from public.raw_mb_tags
),

cleaned as (
    select
        id,
        artist_id,
        lower(trim(tag_name))   as tag_name,
        vote_count::float       as vote_count_raw,
        fetched_at
    from source
    where vote_count >= 1
)

select * from cleaned
