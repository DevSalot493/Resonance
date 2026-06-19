import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)


# ─────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_db_and_cache():
    """
    Patches database and cache for all unit tests.
    No real database or Redis needed.
    """
    with patch("api.routers.artists.get_db_connection"), \
         patch("api.db.get_pool"), \
         patch("api.cache.get_redis"):
        yield


# ─────────────────────────────────────────────────────
# /health
# ─────────────────────────────────────────────────────

def test_health_returns_200():
    with patch("api.db.get_db_connection") as mock_db, \
         patch("api.cache.get_redis") as mock_redis:

        mock_cur = MagicMock()
        mock_cur.fetchone.side_effect = [
            {"cnt": 858},
            {"cnt": 106427},
        ]
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_db.return_value.__exit__  = MagicMock(return_value=False)
        mock_redis.return_value.ping.return_value = True

        response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"]           == "ok"
    assert data["catalog_size"]     == 858
    assert data["similarity_pairs"] == 106427
    assert data["cache_status"]     == "ok"

# ─────────────────────────────────────────────────────
# /artists/search
# ─────────────────────────────────────────────────────

def test_search_returns_results():
    with patch("api.routers.artists.get_db_connection") as mock_db:
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            {"artist_id": 1, "name": "Gorillaz",
             "catalog_tier": 0, "catalog_status": "active"},
        ]
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        response = client.get("/artists/search?q=Gorillaz")

    assert response.status_code == 200
    data = response.json()
    assert data["query"]          == "Gorillaz"
    assert len(data["results"])   == 1
    assert data["results"][0]["name"] == "Gorillaz"


def test_search_requires_query_param():
    response = client.get("/artists/search")
    assert response.status_code == 422


def test_search_empty_results():
    with patch("api.routers.artists.get_db_connection") as mock_db:
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        response = client.get("/artists/search?q=xyznonexistent")

    assert response.status_code == 200
    assert response.json()["results"] == []


# ─────────────────────────────────────────────────────
# /artists/similar
# ─────────────────────────────────────────────────────

def test_similar_returns_404_for_unknown_artist():
    with patch("api.routers.artists._get_artist_id", return_value=None), \
         patch("api.cache.cache_get", return_value=None):
        response = client.get("/artists/similar?name=UnknownArtist99999")

    assert response.status_code == 404


def test_similar_returns_results():
    with patch("api.cache.cache_get", return_value=None), \
         patch("api.cache.cache_set"), \
         patch("api.routers.artists._get_artist_id", return_value=42), \
         patch("api.routers.artists._get_similar_for_artist", return_value=[
             {
                 "similar_artist_id": 99,
                 "name":              "Blur",
                 "similarity_score":  0.75,
                 "shared_tag_count":  8,
                 "catalog_tier":      1,
             }
         ]):
        response = client.get("/artists/similar?name=Gorillaz&limit=5")

    assert response.status_code == 200
    data = response.json()
    assert data["seed_artist"]         == "Gorillaz"
    assert len(data["results"])        == 1
    assert data["results"][0]["name"]  == "Blur"


def test_similar_returns_cached_result():   
    cached_data = {
        "seed_artist":  "Gorillaz",
        "artist_id":    42,
        "results":      [],
        "total_found":  0,
        "cache_hit":    False,
    }
    with patch("api.routers.artists.cache_get", return_value=cached_data):
        response = client.get("/artists/similar?name=Gorillaz")

    assert response.status_code == 200
    assert response.json()["cache_hit"] is True

def test_similar_limit_validation():
    response = client.get("/artists/similar?name=Gorillaz&limit=999")
    assert response.status_code == 422


# ─────────────────────────────────────────────────────
# /artists/discover
# ─────────────────────────────────────────────────────

def test_discover_returns_404_when_no_seeds_found():
    with patch("api.cache.cache_get",         return_value=None), \
         patch("api.routers.artists._get_artist_id", return_value=None):
        response = client.get("/artists/discover?seeds=Unknown1,Unknown2")

    assert response.status_code == 404


def test_discover_returns_results():
    with patch("api.cache.cache_get",  return_value=None), \
         patch("api.cache.cache_set"), \
         patch("api.routers.artists._get_artist_id", return_value=42), \
         patch("api.routers.artists._get_similar_for_artist", return_value=[
             {
                 "similar_artist_id": 99,
                 "name":              "Blur",
                 "similarity_score":  0.75,
                 "shared_tag_count":  8,
                 "catalog_tier":      1,
             }
         ]):
        response = client.get("/artists/discover?seeds=Gorillaz,Coldplay&limit=5")

    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert "seeds"   in data


# ─────────────────────────────────────────────────────
# /artists/explain
# ─────────────────────────────────────────────────────

def test_explain_returns_404_for_unknown_artist():
    with patch("api.routers.artists._get_artist_id", side_effect=[None, 99]):
        response = client.get("/artists/explain?artist_a=Unknown&artist_b=Coldplay")

    assert response.status_code == 404


def test_explain_returns_shared_tags():
    with patch("api.routers.artists._get_artist_id", side_effect=[1, 2]), \
         patch("api.routers.artists.get_db_connection") as mock_db:

        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {
            "similarity_score": 0.45,
            "shared_tag_count": 6,
        }
        mock_cur.fetchall.return_value = [
            {"tag_name": "alternative", "unified_weight": 0.9, "source_count": 2},
            {"tag_name": "indie rock",  "unified_weight": 0.8, "source_count": 1},
        ]
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_db.return_value.__exit__  = MagicMock(return_value=False)

        response = client.get(
            "/artists/explain?artist_a=Gorillaz&artist_b=Coldplay"
        )

    assert response.status_code == 200
    data = response.json()
    assert data["similarity_score"]    == 0.45
    assert len(data["shared_tags"])    == 2
    assert data["shared_tags"][0]["tag_name"] == "alternative"
