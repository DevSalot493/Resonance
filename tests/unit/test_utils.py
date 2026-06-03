import pytest
from unittest.mock import patch, MagicMock
from ingestion.utils import normalise_artist_name, chunk_list, get_db


# ─────────────────────────────────────────────────────
# normalise_artist_name
# ─────────────────────────────────────────────────────

def test_normalise_strips_leading_whitespace():
    assert normalise_artist_name("  Tame Impala") == "tame impala"


def test_normalise_strips_trailing_whitespace():
    assert normalise_artist_name("Tame Impala  ") == "tame impala"


def test_normalise_lowercases():
    assert normalise_artist_name("RADIOHEAD") == "radiohead"


def test_normalise_handles_already_clean_input():
    assert normalise_artist_name("radiohead") == "radiohead"


def test_normalise_preserves_special_characters():
    assert normalise_artist_name("Sigur Rós") == "sigur rós"


def test_normalise_handles_empty_string():
    assert normalise_artist_name("") == ""


# ─────────────────────────────────────────────────────
# chunk_list
# ─────────────────────────────────────────────────────

def test_chunk_list_even_split():
    result = chunk_list([1, 2, 3, 4], 2)
    assert result == [[1, 2], [3, 4]]


def test_chunk_list_uneven_split():
    result = chunk_list([1, 2, 3, 4, 5], 2)
    assert result == [[1, 2], [3, 4], [5]]


def test_chunk_list_chunk_larger_than_list():
    result = chunk_list([1, 2], 10)
    assert result == [[1, 2]]


def test_chunk_list_empty_list():
    result = chunk_list([], 5)
    assert result == []


def test_chunk_list_chunk_size_one():
    result = chunk_list([1, 2, 3], 1)
    assert result == [[1], [2], [3]]


def test_chunk_list_preserves_order():
    artists = ["Radiohead", "Bonobo", "Tame Impala"]
    result = chunk_list(artists, 2)
    assert result[0] == ["Radiohead", "Bonobo"]
    assert result[1] == ["Tame Impala"]


# ─────────────────────────────────────────────────────
# get_db
# ─────────────────────────────────────────────────────

def test_get_db_commits_on_success():
    """
    Verifies that get_db() calls commit() when no exception is raised.
    Uses mocking — no real database needed.
    """
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch("ingestion.utils.get_connection", return_value=mock_conn):
        with get_db() as (conn, cur):
            pass

    mock_conn.commit.assert_called_once()
    mock_conn.rollback.assert_not_called()
    mock_conn.close.assert_called_once()


def test_get_db_rolls_back_on_exception():
    """
    Verifies that get_db() calls rollback() when an exception occurs
    inside the with block.
    """
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch("ingestion.utils.get_connection", return_value=mock_conn):
        with pytest.raises(ValueError):
            with get_db() as (conn, cur):
                raise ValueError("something went wrong")

    mock_conn.rollback.assert_called_once()
    mock_conn.commit.assert_not_called()
    mock_conn.close.assert_called_once()