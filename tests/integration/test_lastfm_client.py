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

def test_fetch_similar_artists_real_api(network):
    similar = fetch_similar_artists(network, "Tame Impala")

    # Last.fm getSimilar is unreliable and may return empty results.
    # We only verify the return type and structure — not that results exist.
    # This endpoint is not used in the main pipeline (ListenBrainz handles expansion).
    assert isinstance(similar, list)

    if len(similar) > 0:
        assert all("name" in s for s in similar)
        assert all("similarity" in s for s in similar)
        assert all(0.0 <= s["similarity"] <= 1.0 for s in similar)


def test_fetch_artist_tags_real_api_known_tag_present(network):
    tags = fetch_artist_tags(network, "Tame Impala")

    if not tags:
        pytest.skip("Last.fm returned no tags — API may be rate limiting")

    tag_names = [t["tag_name"] for t in tags]
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

    if canonical is None:
        pytest.skip("Last.fm returned None for artist resolution — API may be rate limiting")

    assert isinstance(canonical, str)
    assert len(canonical) > 0


def test_unknown_artist_returns_empty(network):
    tags = fetch_artist_tags(network, "xyznonexistentartist99999")
    assert tags == []


# ─────────────────────────────────────────────────────
# Database write — requires Docker running
# ─────────────────────────────────────────────────────

@pytest.fixture
def test_artist_id():
    """
    Inserts a temporary test artist before the test and
    deletes it afterward — even if the test is skipped.
    """
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

    yield artist_id

    with get_db() as (conn, cur):
        cur.execute(
            "DELETE FROM artists WHERE artist_id = %s",
            (artist_id,),
        )


def test_save_artist_tags_writes_to_database(network, test_artist_id):
    tags = fetch_artist_tags(network, "Tame Impala")

    if not tags:
        pytest.skip("Last.fm returned no tags — API may be rate limiting")

    rows_inserted = save_artist_tags(artist_id=test_artist_id, tags=tags)
    assert rows_inserted > 0

    with get_db() as (conn, cur):
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM raw_lastfm_tags WHERE artist_id = %s",
            (test_artist_id,),
        )
        count = cur.fetchone()["cnt"]

    assert count == rows_inserted
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

    tags = fetch_artist_tags(network, "Tame Impala")

    if not tags:
        with get_db() as (conn, cur):
            cur.execute(
                "DELETE FROM artists WHERE artist_id = %s",
                (artist_id,),
            )
        pytest.skip("Last.fm returned no tags — API may be rate limiting")

    rows_inserted = save_artist_tags(artist_id=artist_id, tags=tags)
    assert rows_inserted > 0

    with get_db() as (conn, cur):
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM raw_lastfm_tags WHERE artist_id = %s",
            (artist_id,),
        )
        count = cur.fetchone()["cnt"]

    assert count == rows_inserted

    with get_db() as (conn, cur):
        cur.execute(
            "DELETE FROM artists WHERE artist_id = %s",
            (artist_id,),
        )