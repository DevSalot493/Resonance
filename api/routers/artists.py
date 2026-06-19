from fastapi import APIRouter, HTTPException, Query
from api.db import get_db_connection
from api.cache import cache_get, cache_set
from api.models.schemas import (
    SimilarArtistsResponse,
    DiscoverResponse,
    SearchResponse,
    ExplainResponse,
    ArtistResult,
    SearchResult,
    SharedTag,
)

router = APIRouter(prefix="/artists", tags=["artists"])


# ─────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────

def _get_artist_id(name: str) -> int | None:
    """Looks up an artist_id by name (case-insensitive)."""
    with get_db_connection() as cur:
        cur.execute(
            """
            SELECT artist_id FROM artists
            WHERE LOWER(name) = LOWER(%s)
              AND catalog_status = 'active'
            LIMIT 1
            """,
            (name,),
        )
        row = cur.fetchone()
    return row["artist_id"] if row else None


def _get_similar_for_artist(
    artist_id: int,
    limit: int,
    exclude_ids: set[int] | None = None,
) -> list[dict]:
    """
    Retrieves the top N similar artists for a given artist_id.
    Optionally excludes a set of artist_ids from results.
    """
    exclude_ids = exclude_ids or set()

    with get_db_connection() as cur:
        cur.execute(
            """
            SELECT
                CASE
                    WHEN s.artist_a_id = %s THEN s.artist_b_id
                    ELSE s.artist_a_id
                END                AS similar_artist_id,
                s.similarity_score,
                s.shared_tag_count,
                a.name,
                a.catalog_tier
            FROM artist_similarity s
            JOIN artists a ON (
                CASE
                    WHEN s.artist_a_id = %s THEN s.artist_b_id
                    ELSE s.artist_a_id
                END = a.artist_id
            )
            WHERE (s.artist_a_id = %s OR s.artist_b_id = %s)
              AND a.catalog_status = 'active'
            ORDER BY s.similarity_score DESC
            LIMIT %s
            """,
            (artist_id, artist_id, artist_id, artist_id, limit + len(exclude_ids)),
        )
        rows = cur.fetchall()

    results = []
    for row in rows:
        if row["similar_artist_id"] in exclude_ids:
            continue
        results.append(dict(row))
        if len(results) >= limit:
            break

    return results


# ─────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────

@router.get("/search", response_model=SearchResponse)
def search_artists(
    q:     str = Query(..., min_length=1, description="Artist name prefix to search"),
    limit: int = Query(10, ge=1, le=50),
):
    """
    Search for artists in the catalog by name prefix.
    Returns active artists whose name starts with the query string.
    """
    with get_db_connection() as cur:
        cur.execute(
            """
            SELECT artist_id, name, catalog_tier, catalog_status
            FROM artists
            WHERE name ILIKE %s
              AND catalog_status = 'active'
            ORDER BY name
            LIMIT %s
            """,
            (f"{q}%", limit),
        )
        rows = cur.fetchall()

    return SearchResponse(
        query=q,
        results=[SearchResult(**dict(row)) for row in rows],
    )


@router.get("/similar", response_model=SimilarArtistsResponse)
def get_similar_artists(
    name:  str = Query(..., description="Artist name to find similar artists for"),
    limit: int = Query(10, ge=1, le=50),
):
    """
    Returns the top N artists most similar to the given artist,
    ranked by weighted Jaccard similarity score.
    """
    cache_key = f"similar:{name.lower()}:{limit}"
    cached    = cache_get(cache_key)
    if cached:
        cached["cache_hit"] = True
        return SimilarArtistsResponse(**cached)

    artist_id = _get_artist_id(name)
    if not artist_id:
        raise HTTPException(
            status_code=404,
            detail=f"Artist '{name}' not found in catalog. "
                   f"Try /artists/search to find the correct name.",
        )

    rows = _get_similar_for_artist(artist_id, limit)

    results = [
        ArtistResult(
            artist_id=        row["similar_artist_id"],
            name=             row["name"],
            similarity_score= round(float(row["similarity_score"]), 4),
            shared_tag_count= row["shared_tag_count"],
            catalog_tier=     row["catalog_tier"],
        )
        for row in rows
    ]

    response_data = {
        "seed_artist":  name,
        "artist_id":    artist_id,
        "results":      [r.model_dump() for r in results],
        "total_found":  len(results),
        "cache_hit":    False,
    }
    cache_set(cache_key, response_data)

    return SimilarArtistsResponse(**response_data)


@router.get("/discover", response_model=DiscoverResponse)
def discover_artists(
    seeds:         str = Query(..., description="Comma-separated list of seed artist names"),
    limit:         int = Query(20, ge=1, le=50),
    exclude_seeds: bool = Query(True, description="Exclude seed artists from results"),
):
    """
    Given multiple seed artists, returns artists you might not know
    that are similar to your seeds — ranked by average similarity score.
    """
    seed_names  = [s.strip() for s in seeds.split(",") if s.strip()]
    cache_key   = f"discover:{','.join(sorted(s.lower() for s in seed_names))}:{limit}"
    cached      = cache_get(cache_key)
    if cached:
        cached["cache_hit"] = True
        return DiscoverResponse(**cached)

    # Resolve all seed artist IDs
    seed_ids: dict[str, int] = {}
    for name in seed_names:
        artist_id = _get_artist_id(name)
        if artist_id:
            seed_ids[name] = artist_id

    if not seed_ids:
        raise HTTPException(
            status_code=404,
            detail="None of the provided seed artists were found in the catalog.",
        )

    exclude_ids = set(seed_ids.values()) if exclude_seeds else set()

    # Aggregate similar artists across all seeds
    scores: dict[int, dict] = {}
    for name, artist_id in seed_ids.items():
        rows = _get_similar_for_artist(artist_id, limit=50, exclude_ids=exclude_ids)
        for row in rows:
            sid = row["similar_artist_id"]
            if sid not in scores:
                scores[sid] = {
                    "artist_id":        sid,
                    "name":             row["name"],
                    "catalog_tier":     row["catalog_tier"],
                    "total_score":      0.0,
                    "max_score":        0.0,
                    "shared_tag_count": 0,
                    "seed_count":       0,
                }
            scores[sid]["total_score"]      += float(row["similarity_score"])
            scores[sid]["max_score"]         = max(scores[sid]["max_score"], float(row["similarity_score"]))
            scores[sid]["shared_tag_count"]  = max(scores[sid]["shared_tag_count"], row["shared_tag_count"])
            scores[sid]["seed_count"]       += 1

    # Sort by max similarity score descending
    ranked = sorted(scores.values(), key=lambda x: x["max_score"], reverse=True)[:limit]

    results = [
        ArtistResult(
            artist_id=        r["artist_id"],
            name=             r["name"],
            similarity_score= round(r["max_score"], 4),
            shared_tag_count= r["shared_tag_count"],
            catalog_tier=     r["catalog_tier"],
        )
        for r in ranked
    ]

    response_data = {
        "seeds":       seed_names,
        "results":     [r.model_dump() for r in results],
        "total_found": len(results),
        "cache_hit":   False,
    }
    cache_set(cache_key, response_data)

    return DiscoverResponse(**response_data)


@router.get("/explain", response_model=ExplainResponse)
def explain_similarity(
    artist_a: str = Query(..., description="First artist name"),
    artist_b: str = Query(..., description="Second artist name"),
):
    """
    Explains why two artists are considered similar by showing
    the tags they share, ordered by weight and source confirmation.
    """
    id_a = _get_artist_id(artist_a)
    id_b = _get_artist_id(artist_b)

    if not id_a:
        raise HTTPException(status_code=404, detail=f"Artist '{artist_a}' not found.")
    if not id_b:
        raise HTTPException(status_code=404, detail=f"Artist '{artist_b}' not found.")

    # Ensure correct ordering (lower ID first — matches CHECK constraint)
    lo, hi = (id_a, id_b) if id_a < id_b else (id_b, id_a)

    with get_db_connection() as cur:
        # Get the similarity pair
        cur.execute(
            """
            SELECT similarity_score, shared_tag_count
            FROM artist_similarity
            WHERE artist_a_id = %s AND artist_b_id = %s
            """,
            (lo, hi),
        )
        pair = cur.fetchone()

        # Get shared tags
        cur.execute(
            """
            SELECT
                ta.tag_name,
                ta.unified_weight,
                ta.source_count
            FROM mart_artist_tag_profiles ta
            JOIN mart_artist_tag_profiles tb
                ON ta.tag_name = tb.tag_name
               AND tb.artist_id = %s
            WHERE ta.artist_id = %s
            ORDER BY ta.source_count DESC, ta.unified_weight DESC
            LIMIT 20
            """,
            (id_b, id_a),
        )
        tag_rows = cur.fetchall()

    shared_tags = [
        SharedTag(
            tag_name=       row["tag_name"],
            unified_weight= round(float(row["unified_weight"]), 4),
            source_count=   row["source_count"],
        )
        for row in tag_rows
    ]

    return ExplainResponse(
        artist_a=         artist_a,
        artist_b=         artist_b,
        similarity_score= round(float(pair["similarity_score"]), 4) if pair else None,
        shared_tag_count= pair["shared_tag_count"] if pair else None,
        shared_tags=      shared_tags,
    )