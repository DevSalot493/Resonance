import os
import json
import redis
from dotenv import load_dotenv

load_dotenv()

_redis: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """Returns the global Redis client, creating it if needed."""
    global _redis
    if _redis is None:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        _redis = redis.from_url(redis_url, decode_responses=True)
    return _redis


def cache_get(key: str) -> dict | list | None:
    """
    Retrieves a cached value by key.
    Returns the deserialised Python object, or None if not found.
    """
    try:
        value = get_redis().get(key)
        return json.loads(value) if value else None
    except Exception:
        return None


def cache_set(key: str, value: dict | list, ttl_seconds: int = 604800) -> None:
    """
    Stores a value in Redis with a TTL.
    Default TTL is 7 days (604800 seconds) — aligns with weekly
    similarity recomputation schedule.
    Fails silently so cache errors never break API responses.
    """
    try:
        get_redis().setex(key, ttl_seconds, json.dumps(value))
    except Exception:
        pass


def cache_delete(key: str) -> None:
    """Deletes a cached key. Used for cache invalidation."""
    try:
        get_redis().delete(key)
    except Exception:
        pass