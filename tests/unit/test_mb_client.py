import pytest
from unittest.mock import patch, MagicMock
from ingestion.mb_client import (
    init_musicbrainz,
    search_artist,
    fetch_artist_tags,
    save_artist_tags,
    has_sufficient_tags,
    _pick_best_match,
)


# ─────────────────────────────────────────────────────
# Helpers — fake MusicBrainz API responses
# ─────────────────────────────────────────────────────

def make_fake_artist_result(
    mb_id: str = "abc-123",
    name: str = "Radiohead",
    artist_type: str = "Group",
    country: str = "GB",
) -> dict:
    """
    Builds a fake MusicBrainz artist search result dict.
    musicbrainzngs returns plain dicts, not objects.
    """
    return {
        "id":      mb_id,
        "name":    name,
        "type":    artist_type,
        "country": country,
    }


def make_fake_tag(name: str, count: int) -> dict:
    """
    Builds a fake MusicBrainz tag dict.
    MusicBrainz tags are plain dicts with 'name' and 'count' keys.
    """
    return {
        "name":  name,
        "count": str(count),  # MusicBrainz returns count as string
    }


# ─────────────────────────────────────────────────────
# init_musicbrainz
# ─────────────────────────────────────────────────────

def test_init_musicbrainz_calls_set_useragent():
    with patch("ingestion.mb_client.musicbrainzngs") as mock_mb:
        with patch.dict(
            "os.environ",
            {"MB_USER_AGENT": "resonance/1.0 (test@test.com)"}
        ):
            init_musicbrainz()

    mock_mb.set_useragent.assert_called_once()
    mock_mb.set_rate_limit.assert_called_once()


def test_init_musicbrainz_raises_on_invalid_user_agent():
    with patch.dict("os.environ", {"MB_USER_AGENT": "invalid-no-slash"}):
        with pytest.raises(ValueError, match="MB_USER_AGENT must be in format"):
            init_musicbrainz()


# ─────────────────────────────────────────────────────
# _pick_best_match
# ─────────────────────────────────────────────────────

def test_pick_best_match_returns_exact_name_match():
    candidates = [
        make_fake_artist_result(mb_id="wrong", name="Radiohead UK"),
        make_fake_artist_result(mb_id="correct", name="Radiohead"),
    ]
    result = _pick_best_match("Radiohead", candidates)
    assert result["id"] == "correct"


def test_pick_best_match_case_insensitive():
    candidates = [
        make_fake_artist_result(mb_id="correct", name="Radiohead"),
    ]
    result = _pick_best_match("radiohead", candidates)
    assert result["id"] == "correct"


def test_pick_best_match_falls_back_to_first_if_no_exact_match():
    candidates = [
        make_fake_artist_result(mb_id="first", name="Radiohead UK"),
        make_fake_artist_result(mb_id="second", name="Radiohead Cover Band"),
    ]
    result = _pick_best_match("Radiohead", candidates)
    assert result["id"] == "first"


def test_pick_best_match_returns_none_for_empty_list():
    result = _pick_best_match("Radiohead", [])
    assert result is None


# ─────────────────────────────────────────────────────
# search_artist
# ─────────────────────────────────────────────────────

def test_search_artist_returns_dict_on_success():
    fake_response = {
        "artist-list": [
            make_fake_artist_result(mb_id="abc-123", name="Radiohead")
        ]
    }
    with patch("ingestion.mb_client.musicbrainzngs.search_artists",
               return_value=fake_response):
        result = search_artist("Radiohead")

    assert result is not None
    assert result["mb_id"] == "abc-123"
    assert result["name"] == "Radiohead"


def test_search_artist_returns_none_when_no_results():
    fake_response = {"artist-list": []}
    with patch("ingestion.mb_client.musicbrainzngs.search_artists",
               return_value=fake_response):
        result = search_artist("xyznonexistent99999")

    assert result is None


def test_search_artist_returns_none_on_webservice_error():
    import musicbrainzngs
    with patch("ingestion.mb_client.musicbrainzngs.search_artists",
               side_effect=musicbrainzngs.WebServiceError("connection failed")):
        result = search_artist("Radiohead")

    assert result is None


def test_search_artist_returns_correct_fields():
    fake_response = {
        "artist-list": [
            make_fake_artist_result(
                mb_id="abc-123",
                name="Radiohead",
                artist_type="Group",
                country="GB",
            )
        ]
    }
    with patch("ingestion.mb_client.musicbrainzngs.search_artists",
               return_value=fake_response):
        result = search_artist("Radiohead")

    assert set(result.keys()) == {"mb_id", "name", "type", "country"}
    assert result["type"] == "Group"
    assert result["country"] == "GB"


def test_search_artist_strips_whitespace_from_name():
    fake_response = {
        "artist-list": [
            make_fake_artist_result(name="  Radiohead  ")
        ]
    }
    with patch("ingestion.mb_client.musicbrainzngs.search_artists",
               return_value=fake_response):
        result = search_artist("Radiohead")

    assert result["name"] == "Radiohead"


# ─────────────────────────────────────────────────────
# fetch_artist_tags
# ─────────────────────────────────────────────────────

def test_fetch_artist_tags_returns_list_of_dicts():
    fake_response = {
        "artist": {
            "tag-list": [
                make_fake_tag("art rock", 15),
                make_fake_tag("alternative rock", 12),
            ]
        }
    }
    with patch("ingestion.mb_client.musicbrainzngs.get_artist_by_id",
               return_value=fake_response):
        result = fetch_artist_tags("abc-123")

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0] == {"tag_name": "art rock", "vote_count": 15}
    assert result[1] == {"tag_name": "alternative rock", "vote_count": 12}


def test_fetch_artist_tags_lowercases_tag_names():
    fake_response = {
        "artist": {
            "tag-list": [make_fake_tag("Art Rock", 15)]
        }
    }
    with patch("ingestion.mb_client.musicbrainzngs.get_artist_by_id",
               return_value=fake_response):
        result = fetch_artist_tags("abc-123")

    assert result[0]["tag_name"] == "art rock"


def test_fetch_artist_tags_filters_zero_vote_count():
    fake_response = {
        "artist": {
            "tag-list": [
                make_fake_tag("art rock", 15),
                make_fake_tag("seen live", 0),
            ]
        }
    }
    with patch("ingestion.mb_client.musicbrainzngs.get_artist_by_id",
               return_value=fake_response):
        result = fetch_artist_tags("abc-123")

    assert len(result) == 1
    assert result[0]["tag_name"] == "art rock"


def test_fetch_artist_tags_returns_empty_when_no_tags():
    fake_response = {"artist": {"tag-list": []}}
    with patch("ingestion.mb_client.musicbrainzngs.get_artist_by_id",
               return_value=fake_response):
        result = fetch_artist_tags("abc-123")

    assert result == []


def test_fetch_artist_tags_returns_empty_when_tag_list_absent():
    fake_response = {"artist": {}}
    with patch("ingestion.mb_client.musicbrainzngs.get_artist_by_id",
               return_value=fake_response):
        result = fetch_artist_tags("abc-123")

    assert result == []


def test_fetch_artist_tags_returns_empty_on_webservice_error():
    import musicbrainzngs
    with patch("ingestion.mb_client.musicbrainzngs.get_artist_by_id",
               side_effect=musicbrainzngs.WebServiceError("not found")):
        result = fetch_artist_tags("bad-id")

    assert result == []


def test_fetch_artist_tags_converts_count_to_int():
    fake_response = {
        "artist": {
            "tag-list": [make_fake_tag("art rock", 15)]
        }
    }
    with patch("ingestion.mb_client.musicbrainzngs.get_artist_by_id",
               return_value=fake_response):
        result = fetch_artist_tags("abc-123")

    assert isinstance(result[0]["vote_count"], int)


# ─────────────────────────────────────────────────────
# has_sufficient_tags
# ─────────────────────────────────────────────────────

def test_has_sufficient_tags_returns_true_when_enough():
    tags = [
        {"tag_name": "art rock",          "vote_count": 15},
        {"tag_name": "alternative rock",  "vote_count": 12},
        {"tag_name": "experimental rock", "vote_count": 8},
    ]
    assert has_sufficient_tags(tags) is True


def test_has_sufficient_tags_returns_false_when_too_few():
    tags = [
        {"tag_name": "art rock", "vote_count": 15},
        {"tag_name": "alternative rock", "vote_count": 12},
    ]
    assert has_sufficient_tags(tags) is False


def test_has_sufficient_tags_empty_list():
    assert has_sufficient_tags([]) is False


def test_has_sufficient_tags_custom_min():
    tags = [
        {"tag_name": "art rock",         "vote_count": 15},
        {"tag_name": "alternative rock", "vote_count": 12},
    ]
    assert has_sufficient_tags(tags, min_tags=2) is True
    assert has_sufficient_tags(tags, min_tags=3) is False


def test_has_sufficient_tags_all_zero_votes():
    tags = [
        {"tag_name": "art rock",         "vote_count": 0},
        {"tag_name": "alternative rock", "vote_count": 0},
        {"tag_name": "experimental",     "vote_count": 0},
    ]
    assert has_sufficient_tags(tags) is False


# ─────────────────────────────────────────────────────
# save_artist_tags
# ─────────────────────────────────────────────────────

def test_save_artist_tags_returns_zero_for_empty_list():
    result = save_artist_tags(artist_id=1, tags=[])
    assert result == 0


def test_save_artist_tags_calls_execute_many_with_correct_values():
    tags = [
        {"tag_name": "art rock",         "vote_count": 15},
        {"tag_name": "alternative rock", "vote_count": 12},
    ]

    with patch("ingestion.mb_client.execute_many", return_value=2) as mock_exec:
        result = save_artist_tags(artist_id=99, tags=tags)

    assert result == 2
    mock_exec.assert_called_once()

    values_passed = mock_exec.call_args[0][1]
    assert (99, "art rock", 15)         in values_passed
    assert (99, "alternative rock", 12) in values_passed