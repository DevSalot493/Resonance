with tags as (
    select * from {{ ref('stg_lastfm_tags') }}
),

artists as (
    select artist_id from {{ ref('stg_artists') }}
),

filtered as (
    select t.*
    from tags t
    inner join artists a on t.artist_id = a.artist_id
),

normalised as (
    select
        artist_id,
        tag_name,
        round((tag_weight_raw / 100.0)::numeric, 4) as normalised_weight
    from filtered
),

ranked as (
    select
        artist_id,
        tag_name,
        normalised_weight,
        row_number() over (
            partition by artist_id
            order by normalised_weight desc
        ) as tag_rank
    from normalised
)

select
    artist_id,
    tag_name,
    normalised_weight
from ranked
where tag_rank <= 30
