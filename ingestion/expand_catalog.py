import os
import sys
from dotenv import load_dotenv
from ingestion.utils import (
    logger,
    get_db,
)
from ingestion.lastfm_client import (
    get_lastfm_network,
    fetch_artist_tags       as lastfm_fetch_tags,
    resolve_artist_name     as lastfm_resolve,
    save_artist_tags        as lastfm_save_tags,
    update_artist_lastfm_name,
    has_sufficient_tags     as lastfm_sufficient,
)
from ingestion.mb_client import (
    init_musicbrainz,
    fetch_artist_tags       as mb_fetch_tags,
    save_artist_tags        as mb_save_tags,
    has_sufficient_tags     as mb_sufficient,
    get_artist_name_by_mbid,
)
from ingestion.listenbrainz_client import (
    fetch_similar_artists   as lb_fetch_similar,
    save_similar_artists    as lb_save_similar,
    get_candidate_mbids_for_expansion,
)

load_dotenv()


# ─────────────────────────────────────────────────────
# Database Helpers
# ─────────────────────────────────────────────────────

def get_seed_artist_ids() -> list[int]:
    """
    Returns all artist_ids from the seed_artists table.
    These are the Hop 0 artists — your starting point.
    """
    with get_db() as (conn, cur):
        cur.execute("SELECT artist_id FROM seed_artists ORDER BY artist_id")
        rows = cur.fetchall()
    return [row["artist_id"] for row in rows]


def artist_exists_by_mbid(mb_id: str) -> int | None:
    """
    Checks if an artist already exists by their MusicBrainz ID.
    Returns artist_id if found, None otherwise.
    """
    with get_db() as (conn, cur):
        cur.execute(
            """
            SELECT artist_id FROM artists
            WHERE mb_id = %s::uuid
            """,
            (mb_id,),
        )
        row = cur.fetchone()
    return row["artist_id"] if row else None


def insert_expansion_artist(
    name:         str,
    mb_id:        str,
    catalog_tier: int,
) -> int:
    """
    Inserts a new artist discovered during expansion.
    Returns the new artist_id.
    """
    with get_db() as (conn, cur):
        cur.execute(
            """
            INSERT INTO artists (name, mb_id, catalog_tier)
            VALUES (%s, %s::uuid, %s)
            RETURNING artist_id
            """,
            (name, mb_id, catalog_tier),
        )
        artist_id = cur.fetchone()["artist_id"]

    logger.debug(
        f"Inserted expansion artist '{name}' "
        f"(mb_id={mb_id}, tier={catalog_tier})"
    )
    return artist_id


def mark_artist_sparse(artist_id: int) -> None:
    """Marks an artist as sparse — insufficient tags for similarity."""
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


def get_expansion_stats() -> dict:
    """
    Returns current catalog statistics.
    Useful for logging progress during expansion.
    """
    with get_db() as (conn, cur):
        cur.execute(
            """
            SELECT
                catalog_tier,
                catalog_status,
                COUNT(*) AS count
            FROM artists
            GROUP BY catalog_tier, catalog_status
            ORDER BY catalog_tier, catalog_status
            """
        )
        rows = cur.fetchall()
    return [dict(row) for row in rows]


# ─────────────────────────────────────────────────────
# Per-Artist Processing
# ─────────────────────────────────────────────────────

def process_expansion_artist(
    mb_id:        str,
    name:         str,
    catalog_tier: int,
    network:      object,
) -> dict:
    """
    Processes one candidate artist during catalog expansion:
      1. Checks if already in database (by MBID)
      2. Inserts if new
      3. Resolves Last.fm canonical name
      4. Fetches and saves Last.fm tags
      5. Fetches and saves MusicBrainz tags
      6. Fetches and saves ListenBrainz similar artists
      7. Applies quality gate

    Returns a result dict summarising what happened.
    """
    result = {
        "mb_id":        mb_id,
        "name":         name,
        "status":       "unknown",
        "artist_id":    None,
        "lastfm_tags":  0,
        "mb_tags":      0,
        "lb_similar":   0,
        "skipped":      False,
    }

    # ── Step 1: Check if already in database ──────────
    existing_id = artist_exists_by_mbid(mb_id)
    if existing_id:
        logger.debug(f"Already in catalog: '{name}' (mb_id={mb_id})")
        result["artist_id"] = existing_id
        result["status"]    = "already_exists"
        result["skipped"]   = True
        return result

    # ── Step 1b: Resolve name from MusicBrainz if missing ──
    if not name:
        resolved_name = get_artist_name_by_mbid(mb_id)
        if not resolved_name:
            logger.warning(f"Cannot resolve name for mb_id={mb_id}, skipping")
            result["status"] = "skipped_no_name"
            return result
        name = resolved_name
        logger.debug(f"Resolved name from MusicBrainz: '{name}'")

    # ── Step 2: Insert ────────────────────────────────
    artist_id = insert_expansion_artist(name, mb_id, catalog_tier)
    result["artist_id"] = artist_id

    # ── Step 3: Resolve Last.fm name ──────────────────
    lastfm_name = lastfm_resolve(network, name)
    if lastfm_name:
        update_artist_lastfm_name(artist_id, lastfm_name)
    else:
        lastfm_name = name

    # ── Step 4: Last.fm tags ──────────────────────────
    lastfm_tags = lastfm_fetch_tags(network, lastfm_name)
    if lastfm_tags:
        saved = lastfm_save_tags(artist_id, lastfm_tags)
        result["lastfm_tags"] = saved

    # ── Step 5: MusicBrainz tags ──────────────────────
    mb_tags = mb_fetch_tags(mb_id)
    if mb_tags:
        saved = mb_save_tags(artist_id, mb_tags)
        result["mb_tags"] = saved

    # ── Step 6: ListenBrainz similar artists ──────────
    lb_similar = lb_fetch_similar(mb_id)
    if lb_similar:
        saved = lb_save_similar(artist_id, lb_similar)
        result["lb_similar"] = saved

    # ── Step 7: Quality gate ──────────────────────────
    has_lastfm = lastfm_sufficient(lastfm_tags)
    has_mb     = mb_sufficient(mb_tags)

    if not has_lastfm and not has_mb:
        mark_artist_sparse(artist_id)
        result["status"] = "sparse"
    else:
        result["status"] = "ok"

    return result


# ─────────────────────────────────────────────────────
# Hop Expansion
# ─────────────────────────────────────────────────────

def expand_hop(
    source_artist_ids: list[int],
    catalog_tier:      int,
    network:           object,
    max_per_source:    int = 50,
    max_total:         int = 1000,
) -> dict:
    """
    Expands the catalog by one hop.

    For each source artist, retrieves their stored ListenBrainz
    similar artists that haven't been added yet, then processes
    each candidate.

    Args:
        source_artist_ids: artist_ids to expand from
        catalog_tier:      tier to assign new artists (1 for Hop 1)
        network:           authenticated LastFMNetwork
        max_per_source:    max candidates to process per source artist
        max_total:         hard cap on total new artists this hop

    Returns a summary dict.
    """
    logger.info(
        f"Starting Hop {catalog_tier} expansion — "
        f"{len(source_artist_ids)} source artists"
    )

    summary = {
        "hop":           catalog_tier,
        "sources":       len(source_artist_ids),
        "candidates":    0,
        "ok":            0,
        "sparse":        0,
        "skipped":       0,
        "failed":        0,
    }

    processed_mbids = set()
    total_new       = 0

    for source_id in source_artist_ids:
        if total_new >= max_total:
            logger.info(f"Reached max_total={max_total}, stopping expansion")
            break

        candidates = get_candidate_mbids_for_expansion(
            source_artist_id=source_id,
            limit=max_per_source,
        )

        if not candidates:
            logger.debug(f"No candidates for source artist_id={source_id}")
            continue

        for candidate in candidates:
            if total_new >= max_total:
                break

            mb_id = candidate["artist_mbid"]
            name  = candidate["artist_name"] or ""

            if mb_id in processed_mbids:
                continue
            processed_mbids.add(mb_id)

            summary["candidates"] += 1

            try:
                result = process_expansion_artist(
                    mb_id=mb_id,
                    name=name,
                    catalog_tier=catalog_tier,
                    network=network,
                )

                if result["status"] == "ok":
                    summary["ok"] += 1
                    total_new += 1
                elif result["status"] == "sparse":
                    summary["sparse"] += 1
                    total_new += 1
                elif result["status"] == "already_exists":
                    summary["skipped"] += 1

            except Exception as e:
                logger.error(f"Failed to process candidate '{name}': {e}")
                summary["failed"] += 1

    logger.info(
        f"Hop {catalog_tier} complete — "
        f"OK: {summary['ok']}, "
        f"Sparse: {summary['sparse']}, "
        f"Skipped: {summary['skipped']}, "
        f"Failed: {summary['failed']}"
    )

    return summary


# ─────────────────────────────────────────────────────
# Main Entry Point
# ─────────────────────────────────────────────────────

def expand_catalog(
    max_per_source: int = 50,
    max_total:      int = 1000,
) -> list[dict]:
    """
    Main entry point for catalog expansion.

    Expands the catalog by one hop from all seed artists.
    Seed artists' ListenBrainz similar artists were already
    fetched and stored during seed loading (Phase 7).

    Args:
        max_per_source: max candidates to process per seed artist
        max_total:      hard cap on total new artists added

    Returns list of hop summary dicts.
    """
    logger.info("Initialising API clients...")
    network = get_lastfm_network()
    init_musicbrainz()
    logger.info("API clients ready.")

    seed_ids = get_seed_artist_ids()
    if not seed_ids:
        logger.error(
            "No seed artists found. "
            "Run the seed loader first: python -m ingestion.seed_loader"
        )
        return []

    logger.info(f"Found {len(seed_ids)} seed artists to expand from")

    summaries = []

    hop1_summary = expand_hop(
        source_artist_ids=seed_ids,
        catalog_tier=1,
        network=network,
        max_per_source=max_per_source,
        max_total=max_total,
    )
    summaries.append(hop1_summary)

    stats = get_expansion_stats()
    logger.info("Catalog stats after expansion:")
    for row in stats:
        logger.info(
            f"  Tier {row['catalog_tier']} | "
            f"{row['catalog_status']:8s} | "
            f"{row['count']} artists"
        )

    return summaries


# ─────────────────────────────────────────────────────
# Script Entry Point
# ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Expand the Resonance artist catalog"
    )
    parser.add_argument(
        "--max-per-source",
        type=int,
        default=50,
        help="Max candidates to process per seed artist (default: 50)",
    )
    parser.add_argument(
        "--max-total",
        type=int,
        default=1000,
        help="Hard cap on total new artists added (default: 1000)",
    )
    args = parser.parse_args()

    summaries = expand_catalog(
        max_per_source=args.max_per_source,
        max_total=args.max_total,
    )
    sys.exit(0 if summaries else 1)