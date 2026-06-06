import pytest
from ingestion.listenbrainz_client import (
    fetch_similar_artists,
    save_similar_artists,
    get_candidate_mbids_for_expansion,
)
from ingestion.mb_client import init_musicbrainz, search_artist
from ingestion.utils import get_db

# Radiohead's MusicBrainz ID — stable, well-known, always has similar artists
RADIOHEAD_MBID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"


@pytest.fixture(scope="module", autouse=True)
def setup_mb():
    init_musicbrainz()


# ─────────────────────────────────────────────────────
# Real API calls
# ─────────────────────────────────────────────────────

def test_fetch_similar_artists_real_api():
    result = fetch_similar_artists(RADIOHEAD_MBID)

    assert isinstance(result, list)
    assert len(result) > 0
    assert all("artist_mbid" in r for r in result)
    assert all("artist_name" in r for r in result)
    assert all("score"       in r for r in result)
    assert all(isinstance(r["score"], float) for r in result)


def test_fetch_similar_artists_scores_in_valid_range():
    result = fetch_similar_artists(RADIOHEAD_MBID)
    assert all(r["score"] >= 0.0 for r in result)


def test_fetch_similar_artists_unknown_mbid_returns_empty():
    result = fetch_similar_artists("00000000-0000-0000-0000-000000000000")
    assert result == []


# ─────────────────────────────────────────────────────
# Database write + retrieval
# ─────────────────────────────────────────────────────

def test_save_and_retrieve_similar_artists():
    """
    Full end-to-end test:
    1. Insert a test artist
    2. Fetch real similar artists from ListenBrainz
    3. Save them to the database
    4. Retrieve candidates for expansion
    5. Verify the candidates are the saved artists
    6. Clean up
    """
    # Insert temporary test artist
    with get_db() as (conn, cur):
        cur.execute(
            """
            INSERT INTO artists (name, mb_id, catalog_tier)
            VALUES (%s, %s::uuid, %s)
            ON CONFLICT (mb_id) DO UPDATE SET name = EXCLUDED.name
            RETURNING artist_id
            """,
            ("__test_radiohead__", RADIOHEAD_MBID, 0),
        )
        artist_id = cur.fetchone()["artist_id"]

    # Fetch real similar artists
    similar = fetch_similar_artists(RADIOHEAD_MBID)
    assert len(similar) > 0

    # Save to database
    rows_inserted = save_similar_artists(
        source_artist_id=artist_id,
        similar_artists=similar,
    )
    assert rows_inserted > 0

    # Retrieve candidates for expansion
    candidates = get_candidate_mbids_for_expansion(artist_id)
    assert isinstance(candidates, list)
    assert len(candidates) > 0
    assert all("artist_mbid" in c for c in candidates)
    assert all("artist_name" in c for c in candidates)

    # Cleanup
    with get_db() as (conn, cur):
        cur.execute(
            "DELETE FROM artists WHERE artist_id = %s",
            (artist_id,),
        )