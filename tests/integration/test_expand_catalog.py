import pytest
from ingestion.expand_catalog import (
    get_seed_artist_ids,
    artist_exists_by_mbid,
    insert_expansion_artist,
    process_expansion_artist,
    get_expansion_stats,
)
from ingestion.lastfm_client import get_lastfm_network
from ingestion.mb_client import init_musicbrainz
from ingestion.utils import get_db

KNOWN_MBID      = "cc197bad-dc9c-440d-a5b5-d52ba2e14234"  # Coldplay — for real API test only
TEST_FAKE_MBID  = "aaaaaaaa-1111-4aaa-8aaa-aaaaaaaaaaaa"  # fake UUID — guaranteed not a real artist

@pytest.fixture(scope="module", autouse=True)
def setup_clients():
    init_musicbrainz()


@pytest.fixture(scope="module")
def network():
    return get_lastfm_network()


# ─────────────────────────────────────────────────────
# Database helpers
# ─────────────────────────────────────────────────────

def test_get_seed_artist_ids_returns_list():
    result = get_seed_artist_ids()
    assert isinstance(result, list)
    assert len(result) > 0
    assert all(isinstance(i, int) for i in result)


def test_artist_exists_by_mbid_not_found():
    result = artist_exists_by_mbid("00000000-0000-0000-0000-000000000000")
    assert result is None


def test_insert_expansion_artist_and_find_by_mbid():
    artist_id = insert_expansion_artist(
        name="__test_expansion__",
        mb_id=TEST_FAKE_MBID,
        catalog_tier=1,
    )

    assert isinstance(artist_id, int)

    found = artist_exists_by_mbid(TEST_FAKE_MBID)
    assert found == artist_id

    # Cleanup
    with get_db() as (conn, cur):
        cur.execute(
            "DELETE FROM artists WHERE artist_id = %s",
            (artist_id,),
        )


def test_get_expansion_stats_returns_list():
    result = get_expansion_stats()
    assert isinstance(result, list)
    assert len(result) > 0
    assert all("catalog_tier" in row for row in result)
    assert all("catalog_status" in row for row in result)
    assert all("count" in row for row in result)


# ─────────────────────────────────────────────────────
# process_expansion_artist — real API calls
# ─────────────────────────────────────────────────────

def test_process_expansion_artist_full_pipeline(network):
    result = process_expansion_artist(
        mb_id=KNOWN_MBID,
        name="Coldplay",
        catalog_tier=1,
        network=network,
    )

    assert result["status"]    in ("ok", "sparse", "already_exists")
    assert result["artist_id"] is not None

    # Cleanup if we inserted
    if result["status"] != "already_exists":
        with get_db() as (conn, cur):
            cur.execute(
                "DELETE FROM artists WHERE artist_id = %s",
                (result["artist_id"],),
            )