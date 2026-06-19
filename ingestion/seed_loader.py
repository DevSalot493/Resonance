import os
import sys
from dotenv import load_dotenv
from ingestion.utils import (
    logger,
    get_db,
    normalise_artist_name,
)
from ingestion.lastfm_client import (
    get_lastfm_network,
    fetch_artist_tags       as lastfm_fetch_tags,
    fetch_similar_artists   as lastfm_fetch_similar,
    resolve_artist_name     as lastfm_resolve,
    save_artist_tags        as lastfm_save_tags,
    update_artist_lastfm_name,
    has_sufficient_tags     as lastfm_sufficient,
)
from ingestion.mb_client import (
    init_musicbrainz,
    search_artist           as mb_search,
    fetch_artist_tags       as mb_fetch_tags,
    save_artist_tags        as mb_save_tags,
    update_artist_mb_id,
    has_sufficient_tags     as mb_sufficient,
)
from ingestion.listenbrainz_client import (
    fetch_similar_artists   as lb_fetch_similar,
    save_similar_artists    as lb_save_similar,
)

load_dotenv()


# ─────────────────────────────────────────────────────
# Reading the Seed File
# ─────────────────────────────────────────────────────

def read_seed_file(filepath: str = "seeds/my_artists.txt") -> list[str]:
    """
    Reads artist names from the seed file.
    Returns a list of stripped, non-empty lines.
    Raises FileNotFoundError if the file does not exist.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(
            f"Seed file not found: {filepath}\n"
            f"Create it with one artist name per line."
        )

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    artists = []
    for line in lines:
        name = line.strip()
        if name and not name.startswith("#"):
            artists.append(name)

    logger.info(f"Read {len(artists)} artists from {filepath}")
    return artists


# ─────────────────────────────────────────────────────
# Database Helpers
# ─────────────────────────────────────────────────────

def artist_exists(name: str) -> int | None:
    """
    Checks if an artist already exists in the database by name.
    Uses case-insensitive comparison.
    Returns the artist_id if found, None otherwise.
    """
    with get_db() as (conn, cur):
        cur.execute(
            """
            SELECT artist_id
            FROM artists
            WHERE LOWER(name) = LOWER(%s)
            """,
            (name,),
        )
        row = cur.fetchone()

    return row["artist_id"] if row else None


def insert_artist(name: str, catalog_tier: int = 0) -> int:
    """
    Inserts a new artist into the artists table.
    Returns the new artist_id.
    """
    with get_db() as (conn, cur):
        cur.execute(
            """
            INSERT INTO artists (name, catalog_tier)
            VALUES (%s, %s)
            RETURNING artist_id
            """,
            (name, catalog_tier),
        )
        artist_id = cur.fetchone()["artist_id"]

    logger.debug(f"Inserted artist '{name}' with artist_id={artist_id}")
    return artist_id


def mark_artist_sparse(artist_id: int) -> None:
    """
    Marks an artist as 'sparse' — insufficient tags for similarity
    computation. Excluded from the similarity graph but kept in the
    database for potential future re-fetching.
    """
    with get_db() as (conn, cur):
        cur.execute(
            """
            UPDATE artists
            SET catalog_status = 'sparse',
                updated_at     = NOW()
            WHERE artist_id = %s
            """,
            (artist_id,),
        )
    logger.debug(f"Marked artist_id={artist_id} as sparse")


def insert_seed_artist(artist_id: int) -> None:
    """
    Adds an artist to the seed_artists table.
    Silently skips if the artist is already a seed.
    """
    with get_db() as (conn, cur):
        cur.execute(
            """
            INSERT INTO seed_artists (artist_id)
            VALUES (%s)
            ON CONFLICT (artist_id) DO NOTHING
            """,
            (artist_id,),
        )


def get_artist_id_by_mbid(mb_id: str) -> int | None:
    """
    Checks if an artist already exists by MusicBrainz ID.
    Returns artist_id if found, None otherwise.
    Used in process_artist to detect MBID collisions before updating.
    """
    with get_db() as (conn, cur):
        cur.execute(
            "SELECT artist_id FROM artists WHERE mb_id = %s::uuid",
            (mb_id,),
        )
        row = cur.fetchone()
    return row["artist_id"] if row else None

# ─────────────────────────────────────────────────────
# Per-Artist Processing
# ─────────────────────────────────────────────────────

def process_artist(
    name:    str,
    network: object,
) -> dict:
    """
    Fully processes one seed artist:
      1. Checks if already in database
      2. Inserts if new
      3. Resolves Last.fm canonical name
      4. Resolves MusicBrainz ID
      5. Fetches and saves Last.fm tags
      6. Fetches and saves MusicBrainz tags
      7. Fetches and saves ListenBrainz similar artists
      8. Checks tag quality and marks sparse if needed
      9. Adds to seed_artists table

    Returns a result dict summarising what happened.
    """
    result = {
        "name":            name,
        "status":          "unknown",
        "artist_id":       None,
        "lastfm_tags":     0,
        "mb_tags":         0,
        "lb_similar":      0,
        "skipped":         False,
    }

    # ── Step 1: Check if already in database ──────────
    existing_id = artist_exists(name)
    if existing_id:
        logger.info(f"Artist already exists: '{name}' (id={existing_id})")
        result["artist_id"] = existing_id
        result["status"]    = "already_exists"
        result["skipped"]   = True
        insert_seed_artist(existing_id)
        return result

    # ── Step 2: Insert new artist ──────────────────────
    artist_id = insert_artist(name, catalog_tier=0)
    result["artist_id"] = artist_id

    # ── Step 3: Resolve Last.fm canonical name ─────────
    lastfm_name = lastfm_resolve(network, name)
    if lastfm_name:
        update_artist_lastfm_name(artist_id, lastfm_name)
        logger.debug(f"Resolved Last.fm name: '{name}' → '{lastfm_name}'")
    else:
        logger.warning(f"Could not resolve '{name}' on Last.fm")
        lastfm_name = name

    # ── Step 4: Resolve MusicBrainz ID ────────────────
    mb_result = mb_search(name)
    if mb_result and mb_result.get("mb_id"):
        mb_id = mb_result["mb_id"]

        existing_by_mbid = get_artist_id_by_mbid(mb_id)
        if existing_by_mbid and existing_by_mbid != artist_id:
            logger.info(
                f"'{name}' already in catalog under different name "
                f"(artist_id={existing_by_mbid}), using existing record"
            )
            with get_db() as (conn, cur):
                cur.execute(
                    "DELETE FROM artists WHERE artist_id = %s",
                    (artist_id,),
                )
            artist_id           = existing_by_mbid
            result["artist_id"] = artist_id
            result["status"]    = "already_exists"
            result["skipped"]   = True
            insert_seed_artist(artist_id)
            return result

        update_artist_mb_id(artist_id, mb_id)
        logger.debug(f"Resolved MusicBrainz ID for '{name}': {mb_id}")
    else:
        logger.warning(f"Could not resolve '{name}' on MusicBrainz")
        mb_id = None

    # ── Step 5: Fetch and save Last.fm tags ───────────
    lastfm_tags = lastfm_fetch_tags(network, lastfm_name)
    if lastfm_tags:
        saved = lastfm_save_tags(artist_id, lastfm_tags)
        result["lastfm_tags"] = saved
        logger.info(f"Saved {saved} Last.fm tags for '{name}'")

    # ── Step 6: Fetch and save MusicBrainz tags ───────
    if mb_id:
        mb_tags = mb_fetch_tags(mb_id)
        if mb_tags:
            saved = mb_save_tags(artist_id, mb_tags)
            result["mb_tags"] = saved
            logger.info(f"Saved {saved} MusicBrainz tags for '{name}'")

    # ── Step 7: Fetch and save ListenBrainz similar ───
    if mb_id:
        lb_similar = lb_fetch_similar(mb_id)
        if lb_similar:
            saved = lb_save_similar(artist_id, lb_similar)
            result["lb_similar"] = saved
            logger.info(f"Saved {saved} ListenBrainz similar artists for '{name}'")

    # ── Step 8: Quality gate ──────────────────────────
    has_lastfm = lastfm_sufficient(lastfm_tags)
    has_mb     = mb_sufficient(mb_tags if mb_id else [])

    if not has_lastfm and not has_mb:
        mark_artist_sparse(artist_id)
        result["status"] = "sparse"
        logger.warning(
            f"Marked '{name}' as sparse — "
            f"insufficient tags from both sources"
        )
    else:
        result["status"] = "ok"

    # ── Step 9: Add to seed_artists ───────────────────
    insert_seed_artist(artist_id)

    return result


# ─────────────────────────────────────────────────────
# Main Entry Point
# ─────────────────────────────────────────────────────

def load_seeds(
    filepath:  str  = "seeds/my_artists.txt",
    dry_run:   bool = False,
) -> list[dict]:
    """
    Main entry point for seed loading.

    Reads the seed file, initialises API clients, and processes
    each artist in order.

    Args:
        filepath: path to the seed file
        dry_run:  if True, reads the file and logs what would be
                  processed without making any API calls or
                  database writes

    Returns a list of result dicts, one per artist.
    """
    artists = read_seed_file(filepath)

    if dry_run:
        logger.info(f"Dry run — would process {len(artists)} artists:")
        for name in artists:
            logger.info(f"  {name}")
        return []

    # Initialise API clients once
    logger.info("Initialising API clients...")
    network = get_lastfm_network()
    init_musicbrainz()
    logger.info("API clients ready.")

    results  = []
    total    = len(artists)
    ok       = 0
    skipped  = 0
    sparse   = 0
    failed   = 0

    for i, name in enumerate(artists, start=1):
        logger.info(f"Processing artist {i}/{total}: {name}")

        try:
            result = process_artist(name, network)
            results.append(result)

            if result["status"] == "ok":
                ok += 1
            elif result["status"] == "already_exists":
                skipped += 1
            elif result["status"] == "sparse":
                sparse += 1

        except Exception as e:
            logger.error(f"Failed to process '{name}': {e}")
            failed += 1
            results.append({
                "name":    name,
                "status":  "failed",
                "error":   str(e),
            })

    logger.info(
        f"\nSeed loading complete:\n"
        f"  Total:   {total}\n"
        f"  OK:      {ok}\n"
        f"  Skipped: {skipped}\n"
        f"  Sparse:  {sparse}\n"
        f"  Failed:  {failed}"
    )

    return results


# ─────────────────────────────────────────────────────
# Script Entry Point
# ─────────────────────────────────────────────────────

if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    results = load_seeds(dry_run=dry_run)
    sys.exit(0 if all(r.get("status") != "failed" for r in results) else 1)