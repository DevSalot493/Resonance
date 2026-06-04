import os
import pylast
from dotenv import load_dotenv
from ingestion.utils import (
    logger,
    get_db,
    execute_many,
    rate_limit,
    lastfm_retry,
    normalise_artist_name,
)

load_dotenv()


# ─────────────────────────────────────────────────────
# Client Initialisation
# ─────────────────────────────────────────────────────

def get_lastfm_network() -> pylast.LastFMNetwork:
    """
    Creates and returns an authenticated LastFMNetwork instance.
    Called once per script run — the network object is reused for all calls.
    """
    api_key = os.getenv("LASTFM_API_KEY")
    api_secret = os.getenv("LASTFM_API_SECRET")

    if not api_key or not api_secret:
        raise ValueError(
            "LASTFM_API_KEY or LASTFM_API_SECRET not set. "
            "Check your .env file."
        )

    return pylast.LastFMNetwork(
        api_key=api_key,
        api_secret=api_secret,
    )


# ─────────────────────────────────────────────────────
# Tag Fetching
# ─────────────────────────────────────────────────────

@lastfm_retry
@rate_limit(0.25)
def fetch_artist_tags(
    network: pylast.LastFMNetwork,
    artist_name: str,
    limit: int = 30,
) -> list[dict]:
    """
    Fetches the top tags for an artist from Last.fm.

    Returns a list of dicts:
        [{"tag_name": "indie rock", "tag_weight": 95}, ...]

    Returns an empty list if the artist is not found or has no tags.
    """
    logger.info(f"Fetching Last.fm tags for: {artist_name}")

    try:
        artist = network.get_artist(artist_name)
        raw_tags = artist.get_top_tags(limit=limit)
    except pylast.WSError as e:
        logger.warning(f"Last.fm WSError for '{artist_name}': {e}")
        return []
    except Exception as e:
        logger.warning(f"Unexpected error fetching tags for '{artist_name}': {e}")
        return []

    if not raw_tags:
        logger.debug(f"No tags returned for '{artist_name}'")
        return []

    tags = []
    for item in raw_tags:
        tag_name = item.item.get_name().strip().lower()
        tag_weight = int(item.weight)

        if not tag_name:
            continue
        if tag_weight < 1:
            continue

        tags.append({
            "tag_name": tag_name,
            "tag_weight": tag_weight,
        })

    logger.debug(f"Got {len(tags)} tags for '{artist_name}'")
    return tags


# ─────────────────────────────────────────────────────
# Similar Artist Fetching
# ─────────────────────────────────────────────────────

@lastfm_retry
@rate_limit(0.25)
def fetch_similar_artists(
    network: pylast.LastFMNetwork,
    artist_name: str,
    limit: int = 50,
) -> list[dict]:
    """
    Fetches similar artists for a given artist from Last.fm.
    Used only for catalog expansion — not for scoring.

    Returns a list of dicts:
        [{"name": "Mild High Club", "similarity": 0.87}, ...]

    Returns an empty list if the artist is not found.
    """
    logger.info(f"Fetching Last.fm similar artists for: {artist_name}")

    try:
        artist = network.get_artist(artist_name)
        raw_similar = artist.get_similar(limit=limit)
    except pylast.WSError as e:
        logger.warning(f"Last.fm WSError for '{artist_name}': {e}")
        return []
    except Exception as e:
        logger.warning(f"Unexpected error fetching similar for '{artist_name}': {e}")
        return []

    if not raw_similar:
        logger.debug(f"No similar artists returned for '{artist_name}'")
        return []

    similar = []
    for item in raw_similar:
        try:
            name = item.item.get_name().strip()
            score = float(item.match)
        except Exception:
            continue

        if not name:
            continue

        similar.append({
            "name": name,
            "similarity": round(score, 4),
        })

    logger.debug(f"Got {len(similar)} similar artists for '{artist_name}'")
    return similar


# ─────────────────────────────────────────────────────
# Artist Resolution
# ─────────────────────────────────────────────────────

@lastfm_retry
@rate_limit(0.25)
def resolve_artist_name(
    network: pylast.LastFMNetwork,
    artist_name: str,
) -> str | None:
    """
    Resolves an artist name to Last.fm's canonical version.
    Last.fm may return a different capitalisation or spelling.

    Returns the canonical name string, or None if not found.
    """
    logger.debug(f"Resolving Last.fm canonical name for: {artist_name}")

    try:
        artist = network.get_artist(artist_name)
        canonical = artist.get_name(properly_capitalized=True)
        return canonical.strip() if canonical else None
    except pylast.WSError as e:
        logger.warning(f"Could not resolve '{artist_name}' on Last.fm: {e}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error resolving '{artist_name}': {e}")
        return None


# ─────────────────────────────────────────────────────
# Database Writers
# ─────────────────────────────────────────────────────

def save_artist_tags(artist_id: int, tags: list[dict]) -> int:
    """
    Saves a list of tags for an artist to raw_lastfm_tags.
    Skips duplicates silently using ON CONFLICT DO NOTHING.

    Returns the number of rows inserted.
    """
    if not tags:
        return 0

    values = [
        (artist_id, tag["tag_name"], tag["tag_weight"])
        for tag in tags
    ]

    rows_inserted = execute_many(
        """
        INSERT INTO raw_lastfm_tags (artist_id, tag_name, tag_weight)
        VALUES (%s, %s, %s)
        ON CONFLICT (artist_id, tag_name) DO NOTHING
        """,
        values,
    )

    logger.debug(f"Saved {rows_inserted} tags for artist_id={artist_id}")
    return rows_inserted


def update_artist_lastfm_name(artist_id: int, lastfm_name: str) -> None:
    """
    Updates the lastfm_name column for an artist after resolution.
    """
    with get_db() as (conn, cur):
        cur.execute(
            """
            UPDATE artists
            SET lastfm_name = %s,
                updated_at  = NOW()
            WHERE artist_id = %s
            """,
            (lastfm_name, artist_id),
        )
    logger.debug(f"Updated lastfm_name='{lastfm_name}' for artist_id={artist_id}")


# ─────────────────────────────────────────────────────
# Tag Quality Check
# ─────────────────────────────────────────────────────

def has_sufficient_tags(tags: list[dict], min_tags: int = 5) -> bool:
    """
    Returns True if the artist has enough meaningful tags to be
    included in similarity computation.

    An artist with fewer than min_tags meaningful tags (weight >= 10)
    will be marked 'sparse' and excluded from the similarity graph.
    """
    meaningful = [t for t in tags if t["tag_weight"] >= 10]
    return len(meaningful) >= min_tags