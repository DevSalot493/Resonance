with lastfm as (
    select
        artist_id,
        tag_name,
        normalised_weight,
        1 as source_count
    from {{ ref('int_artist_lastfm_profiles') }}
),

mb as (
    select
        artist_id,
        tag_name,
        normalised_weight,
        1 as source_count
    from {{ ref('int_artist_mb_profiles') }}
),

combined as (
    select * from lastfm
    union all
    select * from mb
),

unified as (
    select
        artist_id,
        tag_name,
        round(max(normalised_weight)::numeric, 4) as unified_weight,
        count(*)::smallint                         as source_count
    from combined
    group by artist_id, tag_name
),

ranked as (
    select
        artist_id,
        tag_name,
        unified_weight,
        source_count,
        row_number() over (
            partition by artist_id
            order by
                source_count desc,
                unified_weight desc
        ) as tag_rank
    from unified
)

select
    artist_id,
    tag_name,
    unified_weight,
    source_count,
    now() as computed_at
from ranked
where tag_rank <= 50
