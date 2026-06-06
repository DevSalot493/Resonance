import pytest
from unittest.mock import patch, MagicMock
from ingestion.listenbrainz_client import (
    fetch_similar_artists,
    _parse_similar_artists,
    save_similar_artists,
    get_candidate_mbids_for_expansion,
)


# ─────────────────────────────────────────────────────
# Helpers — fake API responses
# ─────────────────────────────────────────────────────

def make_fake_similar_artist(
    mb_id:  str   = "abc-123",
    name:   str   = "Mild High Club",
    score:  float = 0.87,
) -> dict:
    """
    Builds a fake ListenBrainz similar artist dict.
    ListenBrainz returns plain dicts — no special objects.
    """
    return {
        "artist_mbid": mb_id,
        "artist_name": name,
        "score":       score,
    }


def make_fake_response(status_code: int = 200, json_data=None) -> MagicMock:
    """
    Builds a fake requests.Response object.
    """
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = json_data or []

    if status_code >= 400:
        import requests
        mock_response.raise_for_status.side_effect = (
            requests.exceptions.HTTPError(
                f"{status_code} Error"
            )
        )
    else:
        mock_response.raise_for_status.return_value = None

    return mock_response


# ─────────────────────────────────────────────────────
# _parse_similar_artists
# ─────────────────────────────────────────────────────

def test_parse_similar_artists_returns_list_of_dicts():
    data = [
        make_fake_similar_artist("abc-123", "Mild High Club", 0.87),
        make_fake_similar_artist("def-456", "Pond",           0.75),
    ]
    result = _parse_similar_artists(data, limit=100)

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["artist_mbid"] == "abc-123"
    assert result[0]["artist_name"] == "Mild High Club"
    assert result[0]["score"]       == 0.87


def test_parse_similar_artists_returns_empty_for_error_dict():
    data   = {"error": "Artist not found"}
    result = _parse_similar_artists(data, limit=100)
    assert result == []


def test_parse_similar_artists_returns_empty_for_unexpected_type():
    result = _parse_similar_artists("not a list or dict", limit=100)
    assert result == []


def test_parse_similar_artists_skips_items_with_no_mbid():
    data = [
        {"artist_mbid": "",        "artist_name": "No ID",        "score": 0.9},
        {"artist_mbid": "abc-123", "artist_name": "Mild High Club","score": 0.87},
    ]
    result = _parse_similar_artists(data, limit=100)

    assert len(result) == 1
    assert result[0]["artist_mbid"] == "abc-123"


def test_parse_similar_artists_rounds_score():
    data   = [make_fake_similar_artist(score=0.876543)]
    result = _parse_similar_artists(data, limit=100)
    assert result[0]["score"] == 0.8765


def test_parse_similar_artists_respects_limit():
    data = [
        make_fake_similar_artist(mb_id=f"id-{i}", name=f"Artist {i}", score=0.9)
        for i in range(20)
    ]
    result = _parse_similar_artists(data, limit=5)
    assert len(result) == 5


def test_parse_similar_artists_strips_whitespace():
    data   = [{"artist_mbid": "  abc-123  ", "artist_name": "  Mild High Club  ", "score": 0.87}]
    result = _parse_similar_artists(data, limit=100)
    assert result[0]["artist_mbid"] == "abc-123"
    assert result[0]["artist_name"] == "Mild High Club"


def test_parse_similar_artists_empty_list():
    result = _parse_similar_artists([], limit=100)
    assert result == []


# ─────────────────────────────────────────────────────
# fetch_similar_artists
# ─────────────────────────────────────────────────────

def test_fetch_similar_artists_returns_list_on_success():
    fake_data = [
        make_fake_similar_artist("abc-123", "Mild High Club", 0.87),
    ]
    mock_resp = make_fake_response(200, fake_data)

    with patch("ingestion.listenbrainz_client.requests.get",
               return_value=mock_resp):
        result = fetch_similar_artists("some-mb-id")

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["artist_mbid"] == "abc-123"


def test_fetch_similar_artists_returns_empty_on_http_error():
    mock_resp = make_fake_response(404)

    with patch("ingestion.listenbrainz_client.requests.get",
               return_value=mock_resp):
        result = fetch_similar_artists("some-mb-id")

    assert result == []


def test_fetch_similar_artists_returns_empty_on_connection_error():
    import requests as req
    with patch("ingestion.listenbrainz_client.requests.get",
               side_effect=req.exceptions.ConnectionError("no connection")):
        result = fetch_similar_artists("some-mb-id")

    assert result == []


def test_fetch_similar_artists_returns_empty_on_timeout():
    import requests as req
    with patch("ingestion.listenbrainz_client.requests.get",
               side_effect=req.exceptions.Timeout()):
        result = fetch_similar_artists("some-mb-id")

    assert result == []


def test_fetch_similar_artists_returns_empty_on_invalid_json():
    mock_resp = make_fake_response(200)
    mock_resp.json.side_effect = ValueError("invalid json")

    with patch("ingestion.listenbrainz_client.requests.get",
               return_value=mock_resp):
        result = fetch_similar_artists("some-mb-id")

    assert result == []


# ─────────────────────────────────────────────────────
# save_similar_artists
# ─────────────────────────────────────────────────────

def test_save_similar_artists_returns_zero_for_empty_list():
    result = save_similar_artists(source_artist_id=1, similar_artists=[])
    assert result == 0


def test_save_similar_artists_calls_execute_many_with_correct_values():
    similar = [
        {"artist_mbid": "abc-123", "artist_name": "Mild High Club", "score": 0.87},
        {"artist_mbid": "def-456", "artist_name": "Pond",           "score": 0.75},
    ]

    with patch("ingestion.listenbrainz_client.execute_many",
               return_value=2) as mock_exec:
        result = save_similar_artists(source_artist_id=42, similar_artists=similar)

    assert result == 2
    mock_exec.assert_called_once()

    values_passed = mock_exec.call_args[0][1]
    assert (42, "abc-123", "Mild High Club", 0.87) in values_passed
    assert (42, "def-456", "Pond",           0.75) in values_passed