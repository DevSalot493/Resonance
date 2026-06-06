import os
import requests
from dotenv import load_dotenv
from ingestion.utils import (
    logger,
    get_db,
    execute_many,
    rate_limit,
    lb_retry,
)

load_dotenv()

# ─────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────

LB_BASE_URL   = "https://labs.api.listenbrainz.org"
LB_USER_AGENT = "resonance/1.0"

SIMILARITY_ALGORITHM = (
    "session_based_days_7500_session_300_contribution_5"
    "_threshold_10_limit_100_filter_True_skip_30"
)


# ─────────────────────────────────────────────────────
# Similar Artist Fetching
# ─────────────────────────────────────────────────────

@lb_retry
@rate_limit(1.0)
def fetch_similar_artists(
    mb_id: str,
    limit: int = 100,
) -> list[dict]:
    """
    Fetches similar artists for a given artist from ListenBrainz Labs API.

    Uses session-based collaborative filtering on open listening data.
    Results are used ONLY for catalog expansion — not for similarity scoring.

    Returns a list of dicts:
        [
            {
                "artist_mbid": "uuid-string",
                "artist_name": "Artist Name",
                "score":       0.87,
            },
            ...
        ]

    Returns an empty list if the artist has no similar artists or is not found.
    """
    logger.info(f"Fetching ListenBrainz similar artists for mb_id: {mb_id}")

    url    = f"{LB_BASE_URL}/similar-artists/json"
    params = {
        "artist_mbids": mb_id,
        "algorithm":   SIMILARITY_ALGORITHM,
        "limit":       limit,
    }
    headers = {"User-Agent": LB_USER_AGENT}

    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        logger.warning(f"ListenBrainz HTTP error for mb_id '{mb_id}': {e}")
        return []
    except requests.exceptions.ConnectionError as e:
        logger.warning(f"ListenBrainz connection error for mb_id '{mb_id}': {e}")
        return []
    except requests.exceptions.Timeout:
        logger.warning(f"ListenBrainz request timed out for mb_id '{mb_id}'")
        return []
    except Exception as e:
        logger.warning(f"Unexpected error fetching similar for mb_id '{mb_id}': {e}")
        return []

    try:
        data = response.json()
    except Exception as e:
        logger.warning(f"Failed to parse ListenBrainz response for mb_id '{mb_id}': {e}")
        return []

    return _parse_similar_artists(data, limit)


def _parse_similar_artists(data: list | dict, limit: int) -> list[dict]:
    """
    Parses the raw ListenBrainz API response into a clean list of dicts.

    The API returns either:
        - A list of artist objects directly
        - A dict with an error message

    Each artist object in the list contains:
        "artist_mbid", "artist_name", "score"
    """
    if isinstance(data, dict):
        error = data.get("error", "unknown error")
        logger.debug(f"ListenBrainz returned error response: {error}")
        return []

    if not isinstance(data, list):
        logger.warning(f"Unexpected ListenBrainz response type: {type(data)}")
        return []

    results = []
    for item in data[:limit]:
        try:
            mb_id       = item.get("artist_mbid", "").strip()
            artist_name = item.get("artist_name", "").strip()
            score       = float(item.get("score", 0.0))
        except Exception:
            continue

        if not mb_id:
            continue

        results.append({
            "artist_mbid": mb_id,
            "artist_name": artist_name,
            "score":       round(score, 4),
        })

    logger.debug(f"Parsed {len(results)} similar artists from ListenBrainz")
    return results


# ─────────────────────────────────────────────────────
# Database Writers
# ─────────────────────────────────────────────────────

def save_similar_artists(
    source_artist_id: int,
    similar_artists:  list[dict],
) -> int:
    """
    Saves ListenBrainz similar artists to raw_lb_similar_artists.
    Used later during catalog expansion to find new artists to add.

    Returns the number of rows inserted.
    """
    if not similar_artists:
        return 0

    values = [
        (
            source_artist_id,
            artist["artist_mbid"],
            artist["artist_name"],
            artist["score"],
        )
        for artist in similar_artists
    ]

    rows_inserted = execute_many(
        """
        INSERT INTO raw_lb_similar_artists
            (source_artist_id, similar_artist_mbid,
             similar_artist_name, lb_similarity_score)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (source_artist_id, similar_artist_mbid) DO NOTHING
        """,
        values,
    )

    logger.debug(
        f"Saved {rows_inserted} LB similar artists "
        f"for source_artist_id={source_artist_id}"
    )
    return rows_inserted


# ─────────────────────────────────────────────────────
# Catalog Expansion Helpers
# ─────────────────────────────────────────────────────

def get_candidate_mbids_for_expansion(
    source_artist_id: int,
    limit: int = 100,
) -> list[dict]:
    """
    Retrieves stored ListenBrainz similar artists from the database
    that have not yet been added to the artist catalog.

    Used during catalog expansion to find new MBIDs to process.

    Returns a list of dicts:
        [{"artist_mbid": "uuid", "artist_name": "Name"}, ...]
    """
    with get_db() as (conn, cur):
        cur.execute(
            """
            SELECT
                lb.similar_artist_mbid  AS artist_mbid,
                lb.similar_artist_name  AS artist_name
            FROM raw_lb_similar_artists lb
            LEFT JOIN artists a
                ON a.mb_id = lb.similar_artist_mbid::uuid
            WHERE lb.source_artist_id = %s
              AND a.artist_id IS NULL
            ORDER BY lb.lb_similarity_score DESC
            LIMIT %s
            """,
            (source_artist_id, limit),
        )
        rows = cur.fetchall()

    return [dict(row) for row in rows]