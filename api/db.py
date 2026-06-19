import os
import psycopg2
import psycopg2.pool
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """
    Returns the global connection pool, creating it if it doesn't exist.
    Uses a ThreadedConnectionPool which is safe for use with FastAPI's
    threaded request handling.
    """
    global _pool
    if _pool is None:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise ValueError("DATABASE_URL not set. Check your .env file.")
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            dsn=database_url,
        )
    return _pool


def get_db_connection():
    """
    Context manager that borrows a connection from the pool,
    yields it with a RealDictCursor, and returns it when done.

    Usage:
        with get_db_connection() as cur:
            cur.execute("SELECT ...")
            rows = cur.fetchall()
    """
    from contextlib import contextmanager

    @contextmanager
    def _get():
        pool = get_pool()
        conn = pool.getconn()
        try:
            with conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor
            ) as cur:
                yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            pool.putconn(conn)

    return _get()


def close_pool() -> None:
    """Closes all connections in the pool. Called on app shutdown."""
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None