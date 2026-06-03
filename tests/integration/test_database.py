import os
import pytest
import psycopg2
from dotenv import load_dotenv

load_dotenv()


@pytest.fixture(scope="module")
def db_connection():
    """
    Provides a real database connection for integration tests.
    Requires Docker Compose to be running.
    """
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    yield conn
    conn.close()


def test_database_is_reachable(db_connection):
    """Confirm we can connect and run a basic query."""
    with db_connection.cursor() as cur:
        cur.execute("SELECT 1")
        result = cur.fetchone()
    assert result[0] == 1


def test_all_tables_exist(db_connection):
    """Confirm init.sql created all expected tables."""
    expected_tables = {
        "artists",
        "raw_lastfm_tags",
        "raw_mb_tags",
        "raw_lb_similar_artists",
        "mart_artist_tag_profiles",
        "artist_similarity",
        "seed_artists",
    }
    with db_connection.cursor() as cur:
        cur.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
        """)
        actual_tables = {row[0] for row in cur.fetchall()}

    assert expected_tables.issubset(actual_tables)


def test_artists_table_has_correct_columns(db_connection):
    """Confirm the artists table has the expected schema."""
    expected_columns = {
        "artist_id", "name", "lastfm_name", "mb_id",
        "catalog_tier", "catalog_status", "lastfm_listeners",
        "created_at", "updated_at"
    }
    with db_connection.cursor() as cur:
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'artists'
        """)
        actual_columns = {row[0] for row in cur.fetchall()}

    assert expected_columns == actual_columns

from ingestion.utils import get_db, execute_many


def test_get_db_context_manager_works():
    """
    Confirms get_db() can execute a real query against the live database.
    """
    with get_db() as (conn, cur):
        cur.execute("SELECT 1 AS value")
        result = cur.fetchone()

    assert result["value"] == 1


def test_get_db_returns_real_dict_cursor():
    """
    Confirms get_db() uses RealDictCursor — rows are dicts, not tuples.
    Accessing result["value"] should work. result[0] should not.
    """
    with get_db() as (conn, cur):
        cur.execute("SELECT 1 AS value")
        result = cur.fetchone()

    assert isinstance(result, dict)
    assert "value" in result


def test_execute_many_inserts_and_deduplicates(db_connection):
    """
    Inserts two rows into artists using execute_many, then verifies
    they exist. Cleans up after itself.
    """
    # Insert two test artists
    execute_many(
        """
        INSERT INTO artists (name, catalog_tier)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        """,
        [("__test_artist_a__", 0), ("__test_artist_b__", 0)]
    )

    # Verify they were inserted
    with get_db() as (conn, cur):
        cur.execute(
            "SELECT name FROM artists WHERE name LIKE %s",
            ("__test_artist_%",)
        )
        results = cur.fetchall()

    names = {row["name"] for row in results}
    assert "__test_artist_a__" in names
    assert "__test_artist_b__" in names

    # Cleanup — remove test rows
    with get_db() as (conn, cur):
        cur.execute(
            "DELETE FROM artists WHERE name LIKE %s",
            ("__test_artist_%",)
        )