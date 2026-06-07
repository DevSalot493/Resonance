import pytest
import os
from unittest.mock import patch, MagicMock, call
from ingestion.seed_loader import (
    read_seed_file,
    artist_exists,
    insert_artist,
    mark_artist_sparse,
    insert_seed_artist,
    process_artist,
    load_seeds,
)


# ─────────────────────────────────────────────────────
# read_seed_file
# ─────────────────────────────────────────────────────

def test_read_seed_file_returns_list_of_names(tmp_path):
    seed_file = tmp_path / "artists.txt"
    seed_file.write_text("Tame Impala\nRadiohead\nBonobo\n")

    result = read_seed_file(str(seed_file))

    assert result == ["Tame Impala", "Radiohead", "Bonobo"]


def test_read_seed_file_strips_whitespace(tmp_path):
    seed_file = tmp_path / "artists.txt"
    seed_file.write_text("  Tame Impala  \n  Radiohead  \n")

    result = read_seed_file(str(seed_file))

    assert result == ["Tame Impala", "Radiohead"]


def test_read_seed_file_skips_empty_lines(tmp_path):
    seed_file = tmp_path / "artists.txt"
    seed_file.write_text("Tame Impala\n\nRadiohead\n\n")

    result = read_seed_file(str(seed_file))

    assert result == ["Tame Impala", "Radiohead"]


def test_read_seed_file_skips_comment_lines(tmp_path):
    seed_file = tmp_path / "artists.txt"
    seed_file.write_text(
        "# My favourite artists\n"
        "Tame Impala\n"
        "# Another comment\n"
        "Radiohead\n"
    )

    result = read_seed_file(str(seed_file))

    assert result == ["Tame Impala", "Radiohead"]


def test_read_seed_file_raises_if_not_found():
    with pytest.raises(FileNotFoundError, match="Seed file not found"):
        read_seed_file("nonexistent/path/artists.txt")


def test_read_seed_file_handles_utf8_characters(tmp_path):
    seed_file = tmp_path / "artists.txt"
    seed_file.write_text("Sigur Rós\nBjörk\n", encoding="utf-8")

    result = read_seed_file(str(seed_file))

    assert result == ["Sigur Rós", "Björk"]


# ─────────────────────────────────────────────────────
# artist_exists
# ─────────────────────────────────────────────────────

def test_artist_exists_returns_id_when_found():
    mock_row = {"artist_id": 42}

    with patch("ingestion.seed_loader.get_db") as mock_get_db:
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = mock_row
        mock_get_db.return_value.__enter__ = MagicMock(
            return_value=(MagicMock(), mock_cur)
        )
        mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

        result = artist_exists("Tame Impala")

    assert result == 42


def test_artist_exists_returns_none_when_not_found():
    with patch("ingestion.seed_loader.get_db") as mock_get_db:
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_get_db.return_value.__enter__ = MagicMock(
            return_value=(MagicMock(), mock_cur)
        )
        mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

        result = artist_exists("Unknown Artist")

    assert result is None


# ─────────────────────────────────────────────────────
# insert_artist
# ─────────────────────────────────────────────────────

def test_insert_artist_returns_new_artist_id():
    mock_row = {"artist_id": 99}

    with patch("ingestion.seed_loader.get_db") as mock_get_db:
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = mock_row
        mock_get_db.return_value.__enter__ = MagicMock(
            return_value=(MagicMock(), mock_cur)
        )
        mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

        result = insert_artist("Tame Impala", catalog_tier=0)

    assert result == 99


# ─────────────────────────────────────────────────────
# load_seeds dry_run
# ─────────────────────────────────────────────────────

def test_load_seeds_dry_run_returns_empty_list(tmp_path):
    seed_file = tmp_path / "artists.txt"
    seed_file.write_text("Tame Impala\nRadiohead\n")

    result = load_seeds(filepath=str(seed_file), dry_run=True)

    assert result == []


def test_load_seeds_dry_run_makes_no_api_calls(tmp_path):
    seed_file = tmp_path / "artists.txt"
    seed_file.write_text("Tame Impala\n")

    with patch("ingestion.seed_loader.get_lastfm_network") as mock_lfm:
        with patch("ingestion.seed_loader.init_musicbrainz") as mock_mb:
            load_seeds(filepath=str(seed_file), dry_run=True)

    mock_lfm.assert_not_called()
    mock_mb.assert_not_called()


# ─────────────────────────────────────────────────────
# process_artist
# ─────────────────────────────────────────────────────

def test_process_artist_skips_existing_artist():
    mock_network = MagicMock()

    with patch("ingestion.seed_loader.artist_exists", return_value=42):
        with patch("ingestion.seed_loader.insert_seed_artist") as mock_seed:
            result = process_artist("Tame Impala", mock_network)

    assert result["status"]   == "already_exists"
    assert result["artist_id"] == 42
    assert result["skipped"]   is True
    mock_seed.assert_called_once_with(42)


def test_process_artist_returns_ok_status_for_new_artist():
    mock_network = MagicMock()

    with patch("ingestion.seed_loader.artist_exists",       return_value=None), \
         patch("ingestion.seed_loader.insert_artist",       return_value=1), \
         patch("ingestion.seed_loader.lastfm_resolve",      return_value="Tame Impala"), \
         patch("ingestion.seed_loader.update_artist_lastfm_name"), \
         patch("ingestion.seed_loader.mb_search",           return_value={"mb_id": "abc-123"}), \
         patch("ingestion.seed_loader.update_artist_mb_id"), \
         patch("ingestion.seed_loader.lastfm_fetch_tags",   return_value=[{"tag_name": "indie", "tag_weight": 90}] * 6), \
         patch("ingestion.seed_loader.lastfm_save_tags",    return_value=6), \
         patch("ingestion.seed_loader.mb_fetch_tags",       return_value=[{"tag_name": "rock", "vote_count": 5}] * 4), \
         patch("ingestion.seed_loader.mb_save_tags",        return_value=4), \
         patch("ingestion.seed_loader.lb_fetch_similar",    return_value=[]), \
         patch("ingestion.seed_loader.lb_save_similar",     return_value=0), \
         patch("ingestion.seed_loader.lastfm_sufficient",   return_value=True), \
         patch("ingestion.seed_loader.mb_sufficient",       return_value=True), \
         patch("ingestion.seed_loader.insert_seed_artist"):

        result = process_artist("Tame Impala", mock_network)

    assert result["status"]      == "ok"
    assert result["artist_id"]   == 1
    assert result["lastfm_tags"] == 6
    assert result["mb_tags"]     == 4


def test_process_artist_marks_sparse_when_insufficient_tags():
    mock_network = MagicMock()

    with patch("ingestion.seed_loader.artist_exists",       return_value=None), \
         patch("ingestion.seed_loader.insert_artist",       return_value=1), \
         patch("ingestion.seed_loader.lastfm_resolve",      return_value="Unknown"), \
         patch("ingestion.seed_loader.update_artist_lastfm_name"), \
         patch("ingestion.seed_loader.mb_search",           return_value=None), \
         patch("ingestion.seed_loader.lastfm_fetch_tags",   return_value=[]), \
         patch("ingestion.seed_loader.lastfm_save_tags",    return_value=0), \
         patch("ingestion.seed_loader.lastfm_sufficient",   return_value=False), \
         patch("ingestion.seed_loader.mb_sufficient",       return_value=False), \
         patch("ingestion.seed_loader.mark_artist_sparse")  as mock_sparse, \
         patch("ingestion.seed_loader.insert_seed_artist"):

        result = process_artist("Unknown Artist", mock_network)

    assert result["status"] == "sparse"
    mock_sparse.assert_called_once_with(1)


def test_process_artist_handles_missing_lastfm_resolution():
    mock_network = MagicMock()

    with patch("ingestion.seed_loader.artist_exists",       return_value=None), \
         patch("ingestion.seed_loader.insert_artist",       return_value=1), \
         patch("ingestion.seed_loader.lastfm_resolve",      return_value=None), \
         patch("ingestion.seed_loader.mb_search",           return_value=None), \
         patch("ingestion.seed_loader.lastfm_fetch_tags",   return_value=[]), \
         patch("ingestion.seed_loader.lastfm_save_tags",    return_value=0), \
         patch("ingestion.seed_loader.lastfm_sufficient",   return_value=False), \
         patch("ingestion.seed_loader.mb_sufficient",       return_value=False), \
         patch("ingestion.seed_loader.mark_artist_sparse"), \
         patch("ingestion.seed_loader.insert_seed_artist"):

        result = process_artist("Tame Impala", mock_network)

    assert result["artist_id"] == 1