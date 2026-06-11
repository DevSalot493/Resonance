import os
import musicbrainzngs
from dotenv import load_dotenv
from ingestion.utils import (
    logger,
    get_db,
    execute_many,
    rate_limit,
    mb_retry,
    normalise_artist_name,
)

load_dotenv()


# ─────────────────────────────────────────────────────
# Client Initialisation
# ─────────────────────────────────────────────────────

def init_musicbrainz() -> None:
    """
    Configures the musicbrainzngs client with a User-Agent string.
    Must be called once before any MusicBrainz API calls.

    MusicBrainz requires a meaningful User-Agent identifying your app
    and contact info. Requests without one are blocked.
    """
    user_agent = os.getenv("MB_USER_AGENT", "resonance/1.0")

    parts = user_agent.split("/", 1)
    if len(parts) != 2:
        raise ValueError(
            f"MB_USER_AGENT must be in format 'appname/version', got: {user_agent}"
        )

    app_name = parts[0].strip()
    version  = parts[1].strip().split(" ")[0]
    contact  = ""

    raw = os.getenv("MB_USER_AGENT", "")
    if "(" in raw and ")" in raw:
        contact = raw[raw.index("(") + 1 : raw.index(")")]

    musicbrainzngs.set_useragent(app_name, version, contact=contact)
    musicbrainzngs.set_rate_limit(limit_or_interval=1.0)
    logger.debug(f"MusicBrainz client initialised: {app_name}/{version}")


# ─────────────────────────────────────────────────────
# Artist Search and Resolution
# ─────────────────────────────────────────────────────

@mb_retry
@rate_limit(1.1)
def search_artist(artist_name: str) -> dict | None:
    """
    Searches MusicBrainz for an artist by name.
    Returns the best-matching result as a dict, or None if not found.

    Returned dict contains:
        {
            "mb_id":    "uuid-string",
            "name":     "Canonical Name",
            "type":     "Group" | "Person" | ...,
            "country":  "US" | "GB" | ... (may be absent),
        }
    """
    logger.info(f"Searching MusicBrainz for: {artist_name}")

    try:
        result = musicbrainzngs.search_artists(
            artist=artist_name,
            limit=5,
        )
    except musicbrainzngs.WebServiceError as e:
        logger.warning(f"MusicBrainz WebServiceError for '{artist_name}': {e}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error searching '{artist_name}': {e}")
        return None

    artists = result.get("artist-list", [])
    if not artists:
        logger.debug(f"No MusicBrainz results for '{artist_name}'")
        return None

    best = _pick_best_match(artist_name, artists)
    if not best:
        return None

    return {
        "mb_id":   best.get("id"),
        "name":    best.get("name", "").strip(),
        "type":    best.get("type", ""),
        "country": best.get("country", ""),
    }


def _pick_best_match(query: str, candidates: list[dict]) -> dict | None:
    """
    Picks the best artist match from a list of MusicBrainz search results.

    MusicBrainz returns results ordered by relevance score, but the top
    result is not always correct — especially for common names. This
    function applies a simple heuristic: prefer an exact name match
    (case-insensitive) over the raw relevance order.

    Returns the best candidate dict, or None if candidates is empty.
    """
    if not candidates:
        return None

    query_normalised = normalise_artist_name(query)

    for candidate in candidates:
        candidate_name = normalise_artist_name(candidate.get("name", ""))
        if candidate_name == query_normalised:
            return candidate

    # No exact match — fall back to first result (highest relevance score)
    return candidates[0]


# ─────────────────────────────────────────────────────
# Tag Fetching
# ─────────────────────────────────────────────────────

@mb_retry
@rate_limit(1.1)
def fetch_artist_tags(mb_id: str) -> list[dict]:
    """
    Fetches tags for an artist by their MusicBrainz ID (MBID).

    MusicBrainz tags are community-voted genre classifications.
    Returns a list of dicts:
        [{"tag_name": "post-rock", "vote_count": 12}, ...]

    Returns an empty list if the artist has no tags or is not found.
    """
    logger.info(f"Fetching MusicBrainz tags for mb_id: {mb_id}")

    try:
        result = musicbrainzngs.get_artist_by_id(
            mb_id,
            includes=["tags"],
        )
    except musicbrainzngs.WebServiceError as e:
        logger.warning(f"MusicBrainz WebServiceError for mb_id '{mb_id}': {e}")
        return []
    except Exception as e:
        logger.warning(f"Unexpected error fetching tags for mb_id '{mb_id}': {e}")
        return []

    artist_data = result.get("artist", {})
    raw_tags    = artist_data.get("tag-list", [])

    if not raw_tags:
        logger.debug(f"No tags found for mb_id: {mb_id}")
        return []

    tags = []
    for raw_tag in raw_tags:
        tag_name   = raw_tag.get("name", "").strip().lower()
        vote_count = int(raw_tag.get("count", 0))

        if not tag_name:
            continue
        if vote_count < 1:
            continue

        tags.append({
            "tag_name":   tag_name,
            "vote_count": vote_count,
        })

    logger.debug(f"Got {len(tags)} tags for mb_id: {mb_id}")
    return tags


@mb_retry
@rate_limit(1.1)
def get_artist_name_by_mbid(mb_id: str) -> str | None:
    """
    Looks up an artist's canonical name using their MusicBrainz ID.
    Used during catalog expansion when ListenBrainz does not provide names.

    Returns the name string, or None if not found.
    """
    logger.debug(f"Looking up MusicBrainz name for mb_id: {mb_id}")

    try:
        result      = musicbrainzngs.get_artist_by_id(mb_id)
        artist_data = result.get("artist", {})
        name        = artist_data.get("name", "").strip()
        return name if name else None
    except musicbrainzngs.WebServiceError as e:
        logger.warning(f"MusicBrainz WebServiceError for mb_id '{mb_id}': {e}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error looking up name for '{mb_id}': {e}")
        return None


# ─────────────────────────────────────────────────────
# Database Writers
# ─────────────────────────────────────────────────────

def save_artist_tags(artist_id: int, tags: list[dict]) -> int:
    """
    Saves MusicBrainz tags for an artist to raw_mb_tags.
    Skips duplicates silently using ON CONFLICT DO NOTHING.

    Returns the number of rows inserted.
    """
    if not tags:
        return 0

    values = [
        (artist_id, tag["tag_name"], tag["vote_count"])
        for tag in tags
    ]

    rows_inserted = execute_many(
        """
        INSERT INTO raw_mb_tags (artist_id, tag_name, vote_count)
        VALUES (%s, %s, %s)
        ON CONFLICT (artist_id, tag_name) DO NOTHING
        """,
        values,
    )

    logger.debug(f"Saved {rows_inserted} MB tags for artist_id={artist_id}")
    return rows_inserted


def update_artist_mb_id(artist_id: int, mb_id: str) -> None:
    """
    Updates the mb_id column for an artist after MusicBrainz resolution.
    """
    with get_db() as (conn, cur):
        cur.execute(
            """
            UPDATE artists
            SET mb_id      = %s,
                updated_at = NOW()
            WHERE artist_id = %s
            """,
            (mb_id, artist_id),
        )
    logger.debug(f"Updated mb_id='{mb_id}' for artist_id={artist_id}")


# ─────────────────────────────────────────────────────
# Tag Quality Check
# ─────────────────────────────────────────────────────

def has_sufficient_tags(tags: list[dict], min_tags: int = 3) -> bool:
    """
    Returns True if the artist has enough meaningful MusicBrainz tags.

    MusicBrainz vote counts are unbounded integers — a vote_count of 1
    means at least one community member confirmed this genre. The
    threshold is lower than Last.fm (3 vs 5) because MusicBrainz
    coverage is sparser — many valid artists have fewer total tags.

    A meaningful tag requires vote_count >= 1.
    """
    meaningful = [t for t in tags if t["vote_count"] >= 1]
    return len(meaningful) >= min_tags