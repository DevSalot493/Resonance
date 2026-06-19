import pytest
import time
from fastapi.testclient import TestClient
from api.main import app
from api.cache import cache_get, cache_set, cache_delete, get_redis

client = TestClient(app)


# ─────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_cache():
    """
    Flushes all Redis keys before each test.
    Ensures tests don't interfere with each other through cached state.
    """
    try:
        get_redis().flushdb()
    except Exception:
        pass
    yield
    try:
        get_redis().flushdb()
    except Exception:
        pass


# ─────────────────────────────────────────────────────
# /health
# ─────────────────────────────────────────────────────

def test_health_endpoint_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"]           == "ok"
    assert data["catalog_size"]     > 0
    assert data["similarity_pairs"] > 0
    assert data["cache_status"]     == "ok"


def test_health_shows_correct_catalog_size():
    response = client.get("/health")
    data = response.json()

    # Should have ~858 active artists from the expansion run
    assert data["catalog_size"] > 500


# ─────────────────────────────────────────────────────
# /artists/search
# ─────────────────────────────────────────────────────

def test_search_returns_results_for_known_artist():
    response = client.get("/artists/search?q=Gorillaz")

    assert response.status_code == 200
    data = response.json()
    assert data["query"] == "Gorillaz"
    assert len(data["results"]) > 0
    assert any(
        r["name"].lower() == "gorillaz"
        for r in data["results"]
    )


def test_search_returns_empty_for_unknown():
    response = client.get("/artists/search?q=xyznonexistentartist99999")

    assert response.status_code == 200
    assert response.json()["results"] == []


def test_search_is_case_insensitive():
    response_lower = client.get("/artists/search?q=gorillaz")
    response_upper = client.get("/artists/search?q=Gorillaz")

    assert response_lower.status_code == 200
    assert response_upper.status_code == 200

    names_lower = [r["name"] for r in response_lower.json()["results"]]
    names_upper = [r["name"] for r in response_upper.json()["results"]]
    assert names_lower == names_upper


def test_search_respects_limit():
    response = client.get("/artists/search?q=the&limit=3")

    assert response.status_code == 200
    assert len(response.json()["results"]) <= 3


# ─────────────────────────────────────────────────────
# /artists/similar
# ─────────────────────────────────────────────────────

def test_similar_returns_404_for_unknown_artist():
    response = client.get("/artists/similar?name=xyznonexistentartist99999")
    assert response.status_code == 404


def test_similar_returns_results_for_known_artist():
    response = client.get("/artists/similar?name=Gorillaz&limit=5")

    assert response.status_code == 200
    data = response.json()
    assert data["seed_artist"]  == "Gorillaz"
    assert len(data["results"]) > 0
    assert data["cache_hit"]    is False

    for result in data["results"]:
        assert "name"             in result
        assert "similarity_score" in result
        assert "shared_tag_count" in result
        assert result["similarity_score"] > 0


def test_similar_results_ordered_by_score():
    response = client.get("/artists/similar?name=Gorillaz&limit=10")
    results  = response.json()["results"]

    scores = [r["similarity_score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_similar_second_call_is_cache_hit():
    """
    First call: cache miss → queries database → caches result
    Second call: cache hit → returns from Redis
    """
    first = client.get("/artists/similar?name=Gorillaz&limit=5")
    assert first.json()["cache_hit"] is False

    second = client.get("/artists/similar?name=Gorillaz&limit=5")
    assert second.json()["cache_hit"] is True

    # Results must be identical
    assert first.json()["results"] == second.json()["results"]


def test_similar_different_limits_are_cached_separately():
    """
    Limit is part of the cache key — different limits
    should be cached independently.
    """
    r5  = client.get("/artists/similar?name=Gorillaz&limit=5")
    r10 = client.get("/artists/similar?name=Gorillaz&limit=10")

    assert r5.json()["cache_hit"]  is False
    assert r10.json()["cache_hit"] is False

    # Second call with limit=5 should now be a cache hit
    r5_again = client.get("/artists/similar?name=Gorillaz&limit=5")
    assert r5_again.json()["cache_hit"] is True


# ─────────────────────────────────────────────────────
# /artists/discover
# ─────────────────────────────────────────────────────

def test_discover_returns_404_when_no_seeds_found():
    response = client.get("/artists/discover?seeds=xyzArtist1,xyzArtist2")
    assert response.status_code == 404


def test_discover_returns_results():
    response = client.get("/artists/discover?seeds=Gorillaz&limit=5")

    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert "seeds"   in data
    assert len(data["results"]) > 0


def test_discover_second_call_is_cache_hit():
    first  = client.get("/artists/discover?seeds=Gorillaz&limit=5")
    second = client.get("/artists/discover?seeds=Gorillaz&limit=5")

    assert first.json()["cache_hit"]  is False
    assert second.json()["cache_hit"] is True


# ─────────────────────────────────────────────────────
# /artists/explain
# ─────────────────────────────────────────────────────

def test_explain_returns_404_for_unknown_artist():
    response = client.get(
        "/artists/explain?artist_a=xyzUnknown&artist_b=Gorillaz"
    )
    assert response.status_code == 404


def test_explain_returns_shared_tags():
    """
    Finds two artists that are similar (from the similarity results)
    and verifies the explain endpoint returns their shared tags.
    """
    # Get an artist similar to Gorillaz
    similar_response = client.get("/artists/similar?name=Gorillaz&limit=1")
    assert similar_response.status_code == 200

    results = similar_response.json()["results"]
    if not results:
        pytest.skip("No similar artists found for Gorillaz")

    similar_name = results[0]["name"]

    explain_response = client.get(
        f"/artists/explain?artist_a=Gorillaz&artist_b={similar_name}"
    )
    assert explain_response.status_code == 200

    data = explain_response.json()
    assert data["artist_a"]         == "Gorillaz"
    assert data["artist_b"]         == similar_name
    assert data["similarity_score"] is not None
    assert data["similarity_score"] > 0
    assert len(data["shared_tags"]) > 0

    for tag in data["shared_tags"]:
        assert "tag_name"       in tag
        assert "unified_weight" in tag
        assert "source_count"   in tag


# ─────────────────────────────────────────────────────
# Redis cache directly
# ─────────────────────────────────────────────────────

def test_cache_set_and_get():
    """Verifies Redis can store and retrieve data correctly."""
    test_data = {"artist": "Gorillaz", "score": 0.85}

    cache_set("test:gorillaz", test_data, ttl_seconds=60)
    retrieved = cache_get("test:gorillaz")

    assert retrieved is not None
    assert retrieved["artist"] == "Gorillaz"
    assert retrieved["score"]  == 0.85


def test_cache_returns_none_for_missing_key():
    result = cache_get("test:nonexistent:key:xyz")
    assert result is None


def test_cache_delete_removes_key():
    cache_set("test:delete_me", {"data": "value"}, ttl_seconds=60)
    assert cache_get("test:delete_me") is not None

    cache_delete("test:delete_me")
    assert cache_get("test:delete_me") is None