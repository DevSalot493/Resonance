import pytest
from unittest.mock import patch, MagicMock
from ingestion.expand_catalog import (
    get_seed_artist_ids,
    artist_exists_by_mbid,
    insert_expansion_artist,
    mark_artist_sparse,
    process_expansion_artist,
    expand_hop,
)


# ─────────────────────────────────────────────────────
# get_seed_artist_ids
# ─────────────────────────────────────────────────────

def test_get_seed_artist_ids_returns_list_of_ints():
    mock_rows = [{"artist_id": 1}, {"artist_id": 2}, {"artist_id": 3}]

    with patch("ingestion.expand_catalog.get_db") as mock_get_db:
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = mock_rows
        mock_get_db.return_value.__enter__ = MagicMock(
            return_value=(MagicMock(), mock_cur)
        )
        mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

        result = get_seed_artist_ids()

    assert result == [1, 2, 3]


def test_get_seed_artist_ids_returns_empty_when_no_seeds():
    with patch("ingestion.expand_catalog.get_db") as mock_get_db:
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_get_db.return_value.__enter__ = MagicMock(
            return_value=(MagicMock(), mock_cur)
        )
        mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

        result = get_seed_artist_ids()

    assert result == []


# ─────────────────────────────────────────────────────
# artist_exists_by_mbid
# ─────────────────────────────────────────────────────

def test_artist_exists_by_mbid_returns_id_when_found():
    mock_row = {"artist_id": 42}

    with patch("ingestion.expand_catalog.get_db") as mock_get_db:
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = mock_row
        mock_get_db.return_value.__enter__ = MagicMock(
            return_value=(MagicMock(), mock_cur)
        )
        mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

        result = artist_exists_by_mbid("abc-123")

    assert result == 42


def test_artist_exists_by_mbid_returns_none_when_not_found():
    with patch("ingestion.expand_catalog.get_db") as mock_get_db:
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_get_db.return_value.__enter__ = MagicMock(
            return_value=(MagicMock(), mock_cur)
        )
        mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

        result = artist_exists_by_mbid("unknown-id")

    assert result is None


# ─────────────────────────────────────────────────────
# insert_expansion_artist
# ─────────────────────────────────────────────────────

def test_insert_expansion_artist_returns_new_id():
    mock_row = {"artist_id": 77}

    with patch("ingestion.expand_catalog.get_db") as mock_get_db:
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = mock_row
        mock_get_db.return_value.__enter__ = MagicMock(
            return_value=(MagicMock(), mock_cur)
        )
        mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

        result = insert_expansion_artist(
            name="Mild High Club",
            mb_id="abc-123",
            catalog_tier=1,
        )

    assert result == 77


# ─────────────────────────────────────────────────────
# process_expansion_artist
# ─────────────────────────────────────────────────────

def test_process_expansion_artist_skips_existing():
    mock_network = MagicMock()

    with patch("ingestion.expand_catalog.artist_exists_by_mbid",
               return_value=42):
        result = process_expansion_artist(
            mb_id="abc-123",
            name="Mild High Club",
            catalog_tier=1,
            network=mock_network,
        )

    assert result["status"]    == "already_exists"
    assert result["artist_id"] == 42
    assert result["skipped"]   is True


def test_process_expansion_artist_returns_ok_for_new_artist():
    mock_network = MagicMock()

    with patch("ingestion.expand_catalog.artist_exists_by_mbid",  return_value=None), \
         patch("ingestion.expand_catalog.insert_expansion_artist", return_value=10), \
         patch("ingestion.expand_catalog.lastfm_resolve",          return_value="Mild High Club"), \
         patch("ingestion.expand_catalog.update_artist_lastfm_name"), \
         patch("ingestion.expand_catalog.lastfm_fetch_tags",       return_value=[{"tag_name": "indie", "tag_weight": 80}] * 6), \
         patch("ingestion.expand_catalog.lastfm_save_tags",        return_value=6), \
         patch("ingestion.expand_catalog.mb_fetch_tags",           return_value=[{"tag_name": "rock", "vote_count": 3}] * 4), \
         patch("ingestion.expand_catalog.mb_save_tags",            return_value=4), \
         patch("ingestion.expand_catalog.lb_fetch_similar",        return_value=[]), \
         patch("ingestion.expand_catalog.lb_save_similar",         return_value=0), \
         patch("ingestion.expand_catalog.lastfm_sufficient",       return_value=True), \
         patch("ingestion.expand_catalog.mb_sufficient",           return_value=True):

        result = process_expansion_artist(
            mb_id="abc-123",
            name="Mild High Club",
            catalog_tier=1,
            network=mock_network,
        )

    assert result["status"]      == "ok"
    assert result["artist_id"]   == 10
    assert result["lastfm_tags"] == 6
    assert result["mb_tags"]     == 4


def test_process_expansion_artist_marks_sparse():
    mock_network = MagicMock()

    with patch("ingestion.expand_catalog.artist_exists_by_mbid",  return_value=None), \
         patch("ingestion.expand_catalog.insert_expansion_artist", return_value=10), \
         patch("ingestion.expand_catalog.lastfm_resolve",          return_value=None), \
         patch("ingestion.expand_catalog.lastfm_fetch_tags",       return_value=[]), \
         patch("ingestion.expand_catalog.lastfm_save_tags",        return_value=0), \
         patch("ingestion.expand_catalog.mb_fetch_tags",           return_value=[]), \
         patch("ingestion.expand_catalog.mb_save_tags",            return_value=0), \
         patch("ingestion.expand_catalog.lb_fetch_similar",        return_value=[]), \
         patch("ingestion.expand_catalog.lb_save_similar",         return_value=0), \
         patch("ingestion.expand_catalog.lastfm_sufficient",       return_value=False), \
         patch("ingestion.expand_catalog.mb_sufficient",           return_value=False), \
         patch("ingestion.expand_catalog.mark_artist_sparse")      as mock_sparse:

        result = process_expansion_artist(
            mb_id="abc-123",
            name="Obscure Artist",
            catalog_tier=1,
            network=mock_network,
        )

    assert result["status"] == "sparse"
    mock_sparse.assert_called_once_with(10)


# ─────────────────────────────────────────────────────
# expand_hop
# ─────────────────────────────────────────────────────

def test_expand_hop_returns_summary_dict():
    mock_network = MagicMock()

    with patch("ingestion.expand_catalog.get_candidate_mbids_for_expansion",
               return_value=[]), \
         patch("ingestion.expand_catalog.process_expansion_artist"):

        result = expand_hop(
            source_artist_ids=[1, 2, 3],
            catalog_tier=1,
            network=mock_network,
        )

    assert isinstance(result, dict)
    assert result["hop"]  == 1
    assert result["ok"]   == 0


def test_expand_hop_skips_duplicate_mbids():
    mock_network = MagicMock()

    same_candidate = {
        "artist_mbid": "abc-123",
        "artist_name": "Mild High Club",
    }

    with patch("ingestion.expand_catalog.get_candidate_mbids_for_expansion",
               return_value=[same_candidate]), \
         patch("ingestion.expand_catalog.process_expansion_artist",
               return_value={"status": "ok"}) as mock_process:

        expand_hop(
            source_artist_ids=[1, 2, 3],
            catalog_tier=1,
            network=mock_network,
        )

    # Same MBID appeared for 3 source artists but should only be processed once
    mock_process.assert_called_once()


def test_expand_hop_respects_max_total():
    mock_network = MagicMock()

    candidates = [
        {"artist_mbid": f"id-{i}", "artist_name": f"Artist {i}"}
        for i in range(20)
    ]

    with patch("ingestion.expand_catalog.get_candidate_mbids_for_expansion",
               return_value=candidates), \
         patch("ingestion.expand_catalog.process_expansion_artist",
               return_value={"status": "ok"}):

        result = expand_hop(
            source_artist_ids=[1],
            catalog_tier=1,
            network=mock_network,
            max_total=5,
        )

    assert result["ok"] == 5


def test_expand_hop_handles_failed_artist_gracefully():
    mock_network = MagicMock()

    candidate = {"artist_mbid": "abc-123", "artist_name": "Some Artist"}

    with patch("ingestion.expand_catalog.get_candidate_mbids_for_expansion",
               return_value=[candidate]), \
         patch("ingestion.expand_catalog.process_expansion_artist",
               side_effect=Exception("API failure")):

        result = expand_hop(
            source_artist_ids=[1],
            catalog_tier=1,
            network=mock_network,
        )

    assert result["failed"] == 1
    assert result["ok"]     == 0