import pytest
from unittest.mock import MagicMock, patch
from ingestion.lastfm_client import (
    fetch_artist_tags,
    fetch_similar_artists,
    resolve_artist_name,
    save_artist_tags,
    has_sufficient_tags,
)


# ─────────────────────────────────────────────────────
# Helpers — build fake pylast objects
# ─────────────────────────────────────────────────────

def make_fake_tag(name: str, weight: int) -> MagicMock:
    """
    Builds a fake pylast TopItem that looks like a tag result.
    pylast returns TopItem objects with .item and .weight attributes.
    """
    tag = MagicMock()
    tag.item.get_name.return_value = name
    tag.weight = str(weight)   # pylast returns weight as a string
    return tag


def make_fake_similar(name: str, match: float) -> MagicMock:
    """
    Builds a fake pylast TopItem that looks like a similar artist result.
    """
    similar = MagicMock()
    similar.item.get_name.return_value = name
    similar.match = match
    return similar


# ─────────────────────────────────────────────────────
# fetch_artist_tags
# ─────────────────────────────────────────────────────

def test_fetch_artist_tags_returns_list_of_dicts():
    network = MagicMock()
    network.get_artist.return_value.get_top_tags.return_value = [
        make_fake_tag("indie rock", 95),
        make_fake_tag("psychedelic", 80),
    ]

    result = fetch_artist_tags(network, "Tame Impala")

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0] == {"tag_name": "indie rock", "tag_weight": 95}
    assert result[1] == {"tag_name": "psychedelic", "tag_weight": 80}


def test_fetch_artist_tags_lowercases_tag_names():
    network = MagicMock()
    network.get_artist.return_value.get_top_tags.return_value = [
        make_fake_tag("Indie Rock", 95),
    ]

    result = fetch_artist_tags(network, "Tame Impala")

    assert result[0]["tag_name"] == "indie rock"


def test_fetch_artist_tags_filters_zero_weight():
    network = MagicMock()
    network.get_artist.return_value.get_top_tags.return_value = [
        make_fake_tag("indie rock", 95),
        make_fake_tag("seen live", 0),   # weight 0 — should be filtered
    ]

    result = fetch_artist_tags(network, "Tame Impala")

    assert len(result) == 1
    assert result[0]["tag_name"] == "indie rock"


def test_fetch_artist_tags_returns_empty_on_wserror():
    import pylast
    network = MagicMock()
    network.get_artist.return_value.get_top_tags.side_effect = pylast.WSError(
        network, "6", "Artist not found"
    )

    result = fetch_artist_tags(network, "NonExistentArtist12345")

    assert result == []


def test_fetch_artist_tags_returns_empty_when_no_tags():
    network = MagicMock()
    network.get_artist.return_value.get_top_tags.return_value = []

    result = fetch_artist_tags(network, "Tame Impala")

    assert result == []


def test_fetch_artist_tags_strips_whitespace_from_tag_names():
    network = MagicMock()
    network.get_artist.return_value.get_top_tags.return_value = [
        make_fake_tag("  indie rock  ", 95),
    ]

    result = fetch_artist_tags(network, "Tame Impala")

    assert result[0]["tag_name"] == "indie rock"


# ─────────────────────────────────────────────────────
# fetch_similar_artists
# ─────────────────────────────────────────────────────

def test_fetch_similar_artists_returns_list_of_dicts():
    network = MagicMock()
    network.get_artist.return_value.get_similar.return_value = [
        make_fake_similar("Mild High Club", 0.87),
        make_fake_similar("Pond", 0.75),
    ]

    result = fetch_similar_artists(network, "Tame Impala")

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0] == {"name": "Mild High Club", "similarity": 0.87}


def test_fetch_similar_artists_returns_empty_on_wserror():
    import pylast
    network = MagicMock()
    network.get_artist.return_value.get_similar.side_effect = pylast.WSError(
        network, "6", "Artist not found"
    )

    result = fetch_similar_artists(network, "NonExistentArtist12345")

    assert result == []


def test_fetch_similar_artists_rounds_similarity_score():
    network = MagicMock()
    network.get_artist.return_value.get_similar.return_value = [
        make_fake_similar("Pond", 0.876543),
    ]

    result = fetch_similar_artists(network, "Tame Impala")

    assert result[0]["similarity"] == 0.8765


def test_fetch_similar_artists_skips_empty_names():
    network = MagicMock()
    network.get_artist.return_value.get_similar.return_value = [
        make_fake_similar("", 0.87),
        make_fake_similar("Pond", 0.75),
    ]

    result = fetch_similar_artists(network, "Tame Impala")

    assert len(result) == 1
    assert result[0]["name"] == "Pond"


# ─────────────────────────────────────────────────────
# resolve_artist_name
# ─────────────────────────────────────────────────────

def test_resolve_artist_name_returns_canonical():
    network = MagicMock()
    network.get_artist.return_value.get_name.return_value = "Tame Impala"

    result = resolve_artist_name(network, "tame impala")

    assert result == "Tame Impala"


def test_resolve_artist_name_returns_none_on_wserror():
    import pylast
    network = MagicMock()
    network.get_artist.return_value.get_name.side_effect = pylast.WSError(
        network, "6", "Artist not found"
    )

    result = resolve_artist_name(network, "nonexistent12345")

    assert result is None


def test_resolve_artist_name_strips_whitespace():
    network = MagicMock()
    network.get_artist.return_value.get_name.return_value = "  Tame Impala  "

    result = resolve_artist_name(network, "tame impala")

    assert result == "Tame Impala"


# ─────────────────────────────────────────────────────
# has_sufficient_tags
# ─────────────────────────────────────────────────────

def test_has_sufficient_tags_returns_true_when_enough():
    tags = [
        {"tag_name": "indie rock",   "tag_weight": 95},
        {"tag_name": "psychedelic",  "tag_weight": 80},
        {"tag_name": "dream pop",    "tag_weight": 72},
        {"tag_name": "lo-fi",        "tag_weight": 60},
        {"tag_name": "shoegaze",     "tag_weight": 55},
    ]
    assert has_sufficient_tags(tags) is True


def test_has_sufficient_tags_returns_false_when_too_few():
    tags = [
        {"tag_name": "indie rock", "tag_weight": 95},
        {"tag_name": "psychedelic", "tag_weight": 80},
    ]
    assert has_sufficient_tags(tags) is False


def test_has_sufficient_tags_ignores_low_weight_tags():
    tags = [
        {"tag_name": "indie rock",  "tag_weight": 95},
        {"tag_name": "psychedelic", "tag_weight": 80},
        {"tag_name": "seen live",   "tag_weight": 5},   # weight < 10
        {"tag_name": "favourites",  "tag_weight": 3},   # weight < 10
        {"tag_name": "awesome",     "tag_weight": 1},   # weight < 10
    ]
    # Only 2 meaningful tags (weight >= 10), needs 5
    assert has_sufficient_tags(tags) is False


def test_has_sufficient_tags_custom_min():
    tags = [
        {"tag_name": "indie rock",  "tag_weight": 95},
        {"tag_name": "psychedelic", "tag_weight": 80},
        {"tag_name": "dream pop",   "tag_weight": 72},
    ]
    assert has_sufficient_tags(tags, min_tags=3) is True
    assert has_sufficient_tags(tags, min_tags=4) is False


def test_has_sufficient_tags_empty_list():
    assert has_sufficient_tags([]) is False


# ─────────────────────────────────────────────────────
# save_artist_tags
# ─────────────────────────────────────────────────────

def test_save_artist_tags_returns_zero_for_empty_list():
    result = save_artist_tags(artist_id=1, tags=[])
    assert result == 0


def test_save_artist_tags_calls_execute_many_with_correct_values():
    tags = [
        {"tag_name": "indie rock", "tag_weight": 95},
        {"tag_name": "psychedelic", "tag_weight": 80},
    ]

    with patch("ingestion.lastfm_client.execute_many", return_value=2) as mock_exec:
        result = save_artist_tags(artist_id=42, tags=tags)

    assert result == 2
    mock_exec.assert_called_once()

    call_args = mock_exec.call_args
    values_passed = call_args[0][1]

    assert (42, "indie rock", 95) in values_passed
    assert (42, "psychedelic", 80) in values_passed