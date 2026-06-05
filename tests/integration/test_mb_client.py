import pytest
import musicbrainzngs
from ingestion.mb_client import (
    init_musicbrainz,
    search_artist,
    fetch_artist_tags,
    save_artist_tags,
    has_sufficient_tags,
)
from ingestion.utils import get_db


@pytest.fixture(scope="module", autouse=True)
def setup_mb():
    """
    Initialises MusicBrainz client once for all tests in this module.
    autouse=True means it runs automatically without being declared
    as a parameter in each test function.
    """
    init_musicbrainz()


# ─────────────────────────────────────────────────────
# Real API calls — Radiohead used as a known stable artist
# ─────────────────────────────────────────────────────

def test_search_artist_real_api():
    result = search_artist("Radiohead")

    assert result is not None
    assert "mb_id" in result
    assert "name"  in result
    assert result["mb_id"] is not None
    assert len(result["mb_id"]) > 0


def test_search_artist_returns_correct_artist():
    result = search_artist("Radiohead")
    assert "radiohead" in result["name"].lower()


def test_fetch_artist_tags_real_api():
    search_result = search_artist("Radiohead")
    assert search_result is not None

    tags = fetch_artist_tags(search_result["mb_id"])

    assert isinstance(tags, list)
    assert len(tags) > 0
    assert all("tag_name"   in t for t in tags)
    assert all("vote_count" in t for t in tags)
    assert all(isinstance(t["vote_count"], int) for t in tags)


def test_unknown_artist_returns_none():
    result = search_artist("xyznonexistentartist99999zzz")
    assert result is None


def test_save_artist_tags_writes_to_database():
    """
    Full end-to-end test: search → fetch tags → save → verify → cleanup.
    """
    # Insert temporary test artist
    with get_db() as (conn, cur):
        cur.execute(
            """
            INSERT INTO artists (name, catalog_tier)
            VALUES (%s, %s)
            RETURNING artist_id
            """,
            ("__test_radiohead__", 0),
        )
        artist_id = cur.fetchone()["artist_id"]

    # Fetch real tags
    search_result = search_artist("Radiohead")
    assert search_result is not None

    tags = fetch_artist_tags(search_result["mb_id"])
    assert len(tags) > 0

    # Save to database
    rows_inserted = save_artist_tags(artist_id=artist_id, tags=tags)
    assert rows_inserted > 0

    # Verify rows exist
    with get_db() as (conn, cur):
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM raw_mb_tags WHERE artist_id = %s",
            (artist_id,),
        )
        count = cur.fetchone()["cnt"]

    assert count == rows_inserted

    # Cleanup
    with get_db() as (conn, cur):
        cur.execute(
            "DELETE FROM artists WHERE artist_id = %s",
            (artist_id,),
        )