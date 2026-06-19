import pytest
from unittest.mock import MagicMock, patch
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spark_jobs.similarity_compute import (
    compute_similarity,
    filter_similarity,
    get_jdbc_url,
    get_jdbc_props,
)


@pytest.fixture(scope="module")
def spark():
    import sys
    os.environ["PYSPARK_PYTHON"]        = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

    session = (
        SparkSession.builder
        .appName("resonance-test")
        .master("local[2]")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.ui.enabled",             "false")
        .config("spark.pyspark.python",         sys.executable)
        .config("spark.pyspark.driver.python",  sys.executable)
        .getOrCreate()
    )
    yield session
    session.stop()


# ─────────────────────────────────────────────────────
# get_jdbc_url and get_jdbc_props
# ─────────────────────────────────────────────────────

def test_get_jdbc_url_contains_host_and_db():
    url = get_jdbc_url()
    assert "jdbc:postgresql://" in url
    assert "resonance" in url


def test_get_jdbc_props_contains_required_keys():
    props = get_jdbc_props()
    assert "user"     in props
    assert "password" in props
    assert "driver"   in props
    assert props["driver"] == "org.postgresql.Driver"


# ─────────────────────────────────────────────────────
# compute_similarity
# ─────────────────────────────────────────────────────

def test_compute_similarity_returns_correct_columns(spark):
    data = [
        (1, "indie rock",  0.9),
        (1, "psychedelic", 0.8),
        (2, "indie rock",  0.7),
        (2, "psychedelic", 0.6),
    ]
    schema = ["artist_id", "tag_name", "unified_weight"]
    df = spark.createDataFrame(data, schema)

    result = compute_similarity(df)
    columns = result.columns

    assert "artist_a_id"      in columns
    assert "artist_b_id"      in columns
    assert "similarity_score" in columns
    assert "shared_tag_count" in columns


def test_compute_similarity_only_stores_lower_id_first(spark):
    data = [
        (1, "indie rock",  0.9),
        (2, "indie rock",  0.9),
        (3, "indie rock",  0.9),
    ]
    schema = ["artist_id", "tag_name", "unified_weight"]
    df = spark.createDataFrame(data, schema)

    result = compute_similarity(df)
    rows = result.collect()

    for row in rows:
        assert row["artist_a_id"] < row["artist_b_id"]


def test_compute_similarity_correct_jaccard_score(spark):
    """
    Two artists sharing one tag with equal weights should score 1.0.
    min(0.8, 0.8) / max(0.8, 0.8) = 0.8 / 0.8 = 1.0
    """
    data = [
        (1, "indie rock", 0.8),
        (2, "indie rock", 0.8),
    ]
    schema = ["artist_id", "tag_name", "unified_weight"]
    df = spark.createDataFrame(data, schema)

    result = compute_similarity(df)
    rows = result.collect()

    assert len(rows) == 1
    assert abs(rows[0]["similarity_score"] - 1.0) < 0.001


def test_compute_similarity_partial_overlap(spark):
    """
    Artist 1: indie rock (0.9), psychedelic (0.8)
    Artist 2: indie rock (0.9)
    intersection = min(0.9, 0.9) = 0.9
    union = max(0.9, 0.9) + max(0.8, 0) = 0.9 + 0.8 = 1.7
    similarity = 0.9 / 1.7 = 0.5294
    """
    data = [
        (1, "indie rock",  0.9),
        (1, "psychedelic", 0.8),
        (2, "indie rock",  0.9),
    ]
    schema = ["artist_id", "tag_name", "unified_weight"]
    df = spark.createDataFrame(data, schema)

    result = compute_similarity(df)
    rows = result.collect()

    assert len(rows) == 1
    assert abs(rows[0]["similarity_score"] - 0.5294) < 0.01


def test_compute_similarity_no_shared_tags_produces_no_pairs(spark):
    """
    Artists with completely different tags produce no pairs.
    """
    data = [
        (1, "indie rock",  0.9),
        (2, "jazz",        0.8),
    ]
    schema = ["artist_id", "tag_name", "unified_weight"]
    df = spark.createDataFrame(data, schema)

    result = compute_similarity(df)
    assert result.count() == 0


def test_compute_similarity_shared_tag_count_correct(spark):
    data = [
        (1, "indie rock",  0.9),
        (1, "psychedelic", 0.8),
        (1, "shoegaze",    0.7),
        (2, "indie rock",  0.8),
        (2, "psychedelic", 0.7),
        (2, "shoegaze",    0.6),
    ]
    schema = ["artist_id", "tag_name", "unified_weight"]
    df = spark.createDataFrame(data, schema)

    result = compute_similarity(df)
    rows = result.collect()

    assert rows[0]["shared_tag_count"] == 3


# ─────────────────────────────────────────────────────
# filter_similarity
# ─────────────────────────────────────────────────────

def test_filter_similarity_removes_below_threshold(spark):
    data = [
        (1, 2, 0.8,  3),
        (1, 3, 0.03, 1),
        (2, 3, 0.5,  2),
    ]
    schema = ["artist_a_id", "artist_b_id", "similarity_score", "shared_tag_count"]
    df = spark.createDataFrame(data, schema)

    result = filter_similarity(df, threshold=0.05)
    rows = result.collect()

    scores = [r["similarity_score"] for r in rows]
    assert 0.03 not in scores
    assert 0.8  in scores
    assert 0.5  in scores


def test_filter_similarity_keeps_exact_threshold(spark):
    data = [(1, 2, 0.05, 1)]
    schema = ["artist_a_id", "artist_b_id", "similarity_score", "shared_tag_count"]
    df = spark.createDataFrame(data, schema)

    result = filter_similarity(df, threshold=0.05)
    assert result.count() == 1


def test_filter_similarity_empty_result_when_all_below(spark):
    data = [
        (1, 2, 0.01, 1),
        (1, 3, 0.02, 1),
    ]
    schema = ["artist_a_id", "artist_b_id", "similarity_score", "shared_tag_count"]
    df = spark.createDataFrame(data, schema)

    result = filter_similarity(df, threshold=0.05)
    assert result.count() == 0