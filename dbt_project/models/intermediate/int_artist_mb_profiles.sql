with tags as (
    select * from {{ ref('stg_mb_tags') }}
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
        vote_count_raw,
        max(vote_count_raw) over (
            partition by artist_id
        ) as max_votes_for_artist,
        round(
            (vote_count_raw / nullif(
                max(vote_count_raw) over (partition by artist_id),
                0
            ))::numeric,
            4
        ) as normalised_weight
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
    where normalised_weight > 0
)

select
    artist_id,
    tag_name,
    normalised_weight
from ranked
where tag_rank <= 20
