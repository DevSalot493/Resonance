import pytest
from ingestion.lastfm_client import (
    get_lastfm_network,
    fetch_artist_tags,
    fetch_similar_artists,
    resolve_artist_name,
    save_artist_tags,
    has_sufficient_tags,
)
from ingestion.utils import get_db


@pytest.fixture(scope="module")
def network():
    """Real LastFMNetwork — requires LASTFM_API_KEY in .env"""
    return get_lastfm_network()


# ─────────────────────────────────────────────────────
# Real API calls — Tame Impala used as a known stable artist
# ─────────────────────────────────────────────────────

def test_fetch_artist_tags_real_api(network):
    tags = fetch_artist_tags(network, "Tame Impala")

    assert isinstance(tags, list)
    assert len(tags) > 0
    assert all("tag_name" in t for t in tags)
    assert all("tag_weight" in t for t in tags)
    assert all(isinstance(t["tag_weight"], int) for t in tags)
    assert all(t["tag_weight"] >= 1 for t in tags)


def test_fetch_artist_tags_real_api_known_tag_present(network):
    tags = fetch_artist_tags(network, "Tame Impala")
    tag_names = [t["tag_name"] for t in tags]

    # Tame Impala will always have at least one of these
    known_tags = {"psychedelic", "indie rock", "indie pop", "rock"}
    assert any(tag in tag_names for tag in known_tags)


def test_fetch_similar_artists_real_api(network):
    similar = fetch_similar_artists(network, "Tame Impala")

    assert isinstance(similar, list)
    assert len(similar) > 0
    assert all("name" in s for s in similar)
    assert all("similarity" in s for s in similar)
    assert all(0.0 <= s["similarity"] <= 1.0 for s in similar)


def test_resolve_artist_name_real_api(network):
    canonical = resolve_artist_name(network, "tame impala")

    assert canonical is not None
    assert isinstance(canonical, str)
    assert len(canonical) > 0


def test_unknown_artist_returns_empty(network):
    tags = fetch_artist_tags(network, "xyznonexistentartist99999")
    assert tags == []


# ─────────────────────────────────────────────────────
# Database write — requires Docker running
# ─────────────────────────────────────────────────────

def test_save_artist_tags_writes_to_database(network):
    """
    Inserts a test artist, fetches real tags from Last.fm,
    saves them to the database, verifies they exist, then cleans up.
    """
    # Insert a temporary test artist
    with get_db() as (conn, cur):
        cur.execute(
            """
            INSERT INTO artists (name, catalog_tier)
            VALUES (%s, %s)
            RETURNING artist_id
            """,
            ("__test_tame_impala__", 0),
        )
        artist_id = cur.fetchone()["artist_id"]

    # Fetch real tags
    tags = fetch_artist_tags(network, "Tame Impala")
    assert len(tags) > 0

    # Save to database
    rows_inserted = save_artist_tags(artist_id=artist_id, tags=tags)
    assert rows_inserted > 0

    # Verify they exist in the database
    with get_db() as (conn, cur):
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM raw_lastfm_tags WHERE artist_id = %s",
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