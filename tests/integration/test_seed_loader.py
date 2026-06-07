import pytest
import os
from ingestion.seed_loader import (
    read_seed_file,
    artist_exists,
    insert_artist,
    mark_artist_sparse,
    insert_seed_artist,
    process_artist,
    load_seeds,
)
from ingestion.lastfm_client import get_lastfm_network
from ingestion.mb_client import init_musicbrainz
from ingestion.utils import get_db


@pytest.fixture(scope="module", autouse=True)
def setup_clients():
    init_musicbrainz()


@pytest.fixture(scope="module")
def network():
    return get_lastfm_network()


# ─────────────────────────────────────────────────────
# Database helpers
# ─────────────────────────────────────────────────────

def test_insert_and_find_artist():
    """
    Inserts an artist and confirms artist_exists finds it.
    """
    artist_id = insert_artist("__test_seed_artist__", catalog_tier=0)

    assert isinstance(artist_id, int)
    assert artist_id > 0

    found_id = artist_exists("__test_seed_artist__")
    assert found_id == artist_id

    # Cleanup
    with get_db() as (conn, cur):
        cur.execute(
            "DELETE FROM artists WHERE artist_id = %s",
            (artist_id,),
        )


def test_mark_artist_sparse():
    artist_id = insert_artist("__test_sparse_artist__", catalog_tier=0)

    mark_artist_sparse(artist_id)

    with get_db() as (conn, cur):
        cur.execute(
            "SELECT catalog_status FROM artists WHERE artist_id = %s",
            (artist_id,),
        )
        row = cur.fetchone()

    assert row["catalog_status"] == "sparse"

    # Cleanup
    with get_db() as (conn, cur):
        cur.execute(
            "DELETE FROM artists WHERE artist_id = %s",
            (artist_id,),
        )


def test_insert_seed_artist():
    artist_id = insert_artist("__test_seed_entry__", catalog_tier=0)

    insert_seed_artist(artist_id)

    with get_db() as (conn, cur):
        cur.execute(
            "SELECT artist_id FROM seed_artists WHERE artist_id = %s",
            (artist_id,),
        )
        row = cur.fetchone()

    assert row is not None
    assert row["artist_id"] == artist_id

    # Cleanup
    with get_db() as (conn, cur):
        cur.execute(
            "DELETE FROM artists WHERE artist_id = %s",
            (artist_id,),
        )


# ─────────────────────────────────────────────────────
# process_artist — real API calls
# ─────────────────────────────────────────────────────

def test_process_artist_full_pipeline(network):
    """
    Processes a single well-known artist end-to-end.
    Verifies data is stored correctly across all tables.
    """
    result = process_artist("Radiohead", network)

    assert result["status"]    in ("ok", "sparse", "already_exists")
    assert result["artist_id"] is not None

    artist_id = result["artist_id"]

    # Verify artist exists
    with get_db() as (conn, cur):
        cur.execute(
            "SELECT name, catalog_tier FROM artists WHERE artist_id = %s",
            (artist_id,),
        )
        artist_row = cur.fetchone()

    assert artist_row is not None
    assert artist_row["catalog_tier"] == 0

    # Verify in seed_artists
    with get_db() as (conn, cur):
        cur.execute(
            "SELECT artist_id FROM seed_artists WHERE artist_id = %s",
            (artist_id,),
        )
        seed_row = cur.fetchone()

    assert seed_row is not None

    # Cleanup only if we inserted (not already_exists)
    if result["status"] != "already_exists":
        with get_db() as (conn, cur):
            cur.execute(
                "DELETE FROM artists WHERE artist_id = %s",
                (artist_id,),
            )


# ─────────────────────────────────────────────────────
# load_seeds — dry run
# ─────────────────────────────────────────────────────

def test_load_seeds_dry_run_reads_real_seed_file():
    """
    Confirms load_seeds dry run reads the real seed file
    without making API calls or writing to the database.
    """
    result = load_seeds(
        filepath="seeds/my_artists.txt",
        dry_run=True,
    )
    assert result == []