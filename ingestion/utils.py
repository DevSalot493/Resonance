import os
import time
import psycopg2
import psycopg2.extras
from contextlib import contextmanager
from functools import wraps
from dotenv import load_dotenv
from loguru import logger
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)
import logging

load_dotenv()

# ─────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────

logger.remove()
logger.add(
    sink=lambda msg: print(msg, end=""),
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
           "<level>{level: <8}</level> | "
           "<cyan>{name}</cyan>:<cyan>{line}</cyan> — "
           "<level>{message}</level>",
    colorize=True,
)


# ─────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────

def get_connection():
    """
    Opens and returns a new psycopg2 connection using DATABASE_URL from .env.
    Caller is responsible for closing it.
    Use get_db() context manager instead wherever possible.
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError(
            "DATABASE_URL is not set. "
            "Check your .env file."
        )
    return psycopg2.connect(database_url)


@contextmanager
def get_db():
    """
    Context manager that provides a database connection and cursor.
    Automatically commits on success, rolls back on error, and
    always closes the connection.

    Usage:
        with get_db() as (conn, cur):
            cur.execute("INSERT INTO artists ...")
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield conn, cur
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Database error, rolling back: {e}")
        raise
    finally:
        conn.close()


def execute_many(query: str, values: list[tuple]) -> int:
    """
    Executes a query for multiple rows efficiently using executemany.
    Returns the number of rows affected.

    Usage:
        execute_many(
            "INSERT INTO raw_lastfm_tags (artist_id, tag_name, tag_weight)
             VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            [(1, "indie rock", 95), (1, "psychedelic", 80)]
        )
    """
    if not values:
        logger.debug("execute_many called with empty values list, skipping.")
        return 0

    with get_db() as (conn, cur):
        cur.executemany(query, values)
        return cur.rowcount


# ─────────────────────────────────────────────────────
# Rate Limiting
# ─────────────────────────────────────────────────────

def rate_limit(seconds: float):
    """
    Decorator that enforces a minimum delay between calls to a function.
    Prevents hitting API rate limits.

    Usage:
        @rate_limit(0.25)  # max 4 calls per second
        def fetch_artist_tags(artist_name):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            time.sleep(seconds)
            return result
        return wrapper
    return decorator


# ─────────────────────────────────────────────────────
# Retry Logic
# ─────────────────────────────────────────────────────

def make_retry_decorator(
    max_attempts: int = 3,
    min_wait: float = 2.0,
    max_wait: float = 30.0,
    exceptions: tuple = (Exception,),
):
    """
    Returns a tenacity retry decorator configured for API calls.

    - Retries up to max_attempts times
    - Waits exponentially between retries (2s, 4s, 8s...)
    - Only retries on the specified exception types
    - Logs a warning before each retry

    Usage:
        @make_retry_decorator(max_attempts=3, exceptions=(NetworkError,))
        def fetch_from_api():
            ...
    """
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
        retry=retry_if_exception_type(exceptions),
        before_sleep=before_sleep_log(
            logger.bind(name="tenacity"),
            logging.WARNING
        ),
        reraise=True,
    )


# Pre-built decorators for each API's rate limit requirements
# Last.fm: 5 requests/sec max → 0.2s delay
lastfm_retry = make_retry_decorator(
    max_attempts=3,
    min_wait=2.0,
    max_wait=30.0,
    exceptions=(Exception,),
)

# MusicBrainz: 1 request/sec max → 1.1s delay (slight buffer)
mb_retry = make_retry_decorator(
    max_attempts=3,
    min_wait=5.0,
    max_wait=60.0,
    exceptions=(Exception,),
)

# ListenBrainz: no strict limit but be respectful
lb_retry = make_retry_decorator(
    max_attempts=3,
    min_wait=2.0,
    max_wait=30.0,
    exceptions=(Exception,),
)


# ─────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────

def normalise_artist_name(name: str) -> str:
    """
    Lowercases and strips whitespace from an artist name.
    Used for deduplication and lookup comparisons.

    >>> normalise_artist_name("  Tame Impala  ")
    'tame impala'
    """
    return name.strip().lower()


def chunk_list(lst: list, size: int) -> list[list]:
    """
    Splits a list into chunks of a given size.
    Used when APIs accept batch requests with a maximum size.

    >>> chunk_list([1, 2, 3, 4, 5], 2)
    [[1, 2], [3, 4], [5]]
    """
    return [lst[i:i + size] for i in range(0, len(lst), size)]