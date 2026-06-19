import os
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import FloatType
from dotenv import load_dotenv

load_dotenv()


# ─────────────────────────────────────────────────────
# Spark Session
# ─────────────────────────────────────────────────────

def create_spark_session() -> SparkSession:
    import sys
    python_exec = sys.executable.replace("\\", "/")

    os.environ["PYSPARK_PYTHON"]        = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable
    os.environ["HADOOP_HOME"]           = r"C:\hadoop"
    os.environ["PATH"]                  = os.environ["PATH"] + r";C:\hadoop\bin"

    return (
        SparkSession.builder
        .appName("Resonance Similarity Compute")
        .master("local[*]")
        .config("spark.jars.packages",             "org.postgresql:postgresql:42.7.3")
        .config("spark.driver.memory",             "2g")
        .config("spark.sql.shuffle.partitions",    "8")
        .config("spark.pyspark.python",            python_exec)
        .config("spark.pyspark.driver.python",     python_exec)
        .config("spark.driver.extraJavaOptions",   "-Duser.timezone=UTC")
        .config("spark.executor.extraJavaOptions", "-Duser.timezone=UTC")
        .getOrCreate()
    )


# ─────────────────────────────────────────────────────
# JDBC Helpers
# ─────────────────────────────────────────────────────

def get_jdbc_url() -> str:
    """Returns the PostgreSQL JDBC URL from environment."""
    host     = os.getenv("POSTGRES_HOST",     "localhost")
    port     = os.getenv("POSTGRES_PORT",     "5432")
    database = os.getenv("POSTGRES_DB",       "resonance")
    return f"jdbc:postgresql://{host}:{port}/{database}"


def get_jdbc_props() -> dict:
    """Returns JDBC connection properties."""
    return {
        "user":     os.getenv("POSTGRES_USER",     "music_user"),
        "password": os.getenv("POSTGRES_PASSWORD", "musicuser123"),
        "driver":   "org.postgresql.Driver",
    }


# ─────────────────────────────────────────────────────
# Data Loading
# ─────────────────────────────────────────────────────

def load_tag_profiles(spark: SparkSession):
    """
    Loads mart_artist_tag_profiles from PostgreSQL.
    Returns a Spark DataFrame with columns:
        artist_id, tag_name, unified_weight, source_count
    """
    jdbc_url   = get_jdbc_url()
    jdbc_props = get_jdbc_props()

    df = spark.read.jdbc(
        url=jdbc_url,
        table="mart_artist_tag_profiles",
        properties=jdbc_props,
    )

    # Only keep columns needed for similarity
    return df.select("artist_id", "tag_name", "unified_weight")


# ─────────────────────────────────────────────────────
# Similarity Computation
# ─────────────────────────────────────────────────────

def compute_similarity(tag_profiles):
    """
    Computes pairwise weighted Jaccard similarity between all artists.

    Weighted Jaccard:
        similarity(A, B) = intersection / union
        intersection = sum(min(wA, wB)) for shared tags
        union        = total_A + total_B - intersection
                     (accounts for tags unique to each artist)
    """
    # Step 1: Total weight per artist — needed for correct union calculation
    artist_totals = tag_profiles.groupBy("artist_id").agg(
        F.sum("unified_weight").alias("total_weight")
    )

    # Step 2: Self-join on tag_name — produces rows only for shared tags
    a = tag_profiles.alias("a")
    b = tag_profiles.alias("b")

    pairs = a.join(b, on="tag_name").filter(
        F.col("a.artist_id") < F.col("b.artist_id")
    )

    # Step 3: Intersection weight and shared tag count per pair
    pair_stats = (
        pairs
        .groupBy(
            F.col("a.artist_id").alias("artist_a_id"),
            F.col("b.artist_id").alias("artist_b_id"),
        )
        .agg(
            F.sum(
                F.least(
                    F.col("a.unified_weight"),
                    F.col("b.unified_weight"),
                )
            ).alias("intersection_weight"),
            F.count("*").cast("short").alias("shared_tag_count"),
        )
    )

    # Step 4: Join total weights for both artists in each pair
    totals_a = (
        artist_totals
        .withColumnRenamed("artist_id",    "a_id")
        .withColumnRenamed("total_weight", "total_weight_a")
    )
    totals_b = (
        artist_totals
        .withColumnRenamed("artist_id",    "b_id")
        .withColumnRenamed("total_weight", "total_weight_b")
    )

    pair_stats = (
        pair_stats
        .join(totals_a, F.col("artist_a_id") == F.col("a_id"))
        .drop("a_id")
        .join(totals_b, F.col("artist_b_id") == F.col("b_id"))
        .drop("b_id")
    )

    # Step 5: union = total_A + total_B - intersection
    # This correctly includes tags unique to either artist
    similarity = (
        pair_stats
        .withColumn(
            "union_weight",
            F.col("total_weight_a") + F.col("total_weight_b")
            - F.col("intersection_weight")
        )
        .withColumn(
            "similarity_score",
            F.round(
                F.col("intersection_weight") / F.col("union_weight"),
                4,
            ).cast(FloatType())
        )
    )

    return similarity.select(
        "artist_a_id",
        "artist_b_id",
        "similarity_score",
        "shared_tag_count",
    )

    
def filter_similarity(similarity, threshold: float = 0.05):
    """
    Filters out pairs below the similarity threshold.
    Removes noise from artists with very little in common.
    """
    return similarity.filter(
        F.col("similarity_score") >= threshold
    )


# ─────────────────────────────────────────────────────
# Writing Results
# ─────────────────────────────────────────────────────

def write_similarity(similarity, jdbc_url: str, jdbc_props: dict) -> int:
    """
    Writes similarity results to artist_similarity table.
    Overwrites any existing results.
    Returns the count of rows written.
    """
    count = similarity.count()

    (
        similarity.write
        .mode("overwrite")
        .option("truncate", "true")
        .jdbc(
            url=jdbc_url,
            table="artist_similarity",
            properties=jdbc_props,
        )
    )

    return count


# ─────────────────────────────────────────────────────
# Main Job
# ─────────────────────────────────────────────────────

def run_similarity_job(threshold: float = 0.05) -> int:
    """
    Main entry point for the similarity computation job.

    1. Loads tag profiles from PostgreSQL
    2. Computes pairwise weighted Jaccard similarity
    3. Filters by threshold
    4. Writes results to artist_similarity table

    Returns the number of similarity pairs written.
    """
    print("Starting Resonance similarity computation...")

    spark      = create_spark_session()
    jdbc_url   = get_jdbc_url()
    jdbc_props = get_jdbc_props()

    try:
        # Load
        print("Loading tag profiles from PostgreSQL...")
        tag_profiles = load_tag_profiles(spark)
        artist_count = tag_profiles.select("artist_id").distinct().count()
        tag_count    = tag_profiles.count()
        print(f"Loaded {tag_count} tag rows for {artist_count} artists")

        # Compute
        print("Computing pairwise similarity...")
        similarity = compute_similarity(tag_profiles)

        # Filter
        print(f"Filtering pairs below threshold={threshold}...")
        similarity = filter_similarity(similarity, threshold)

        # Write
        print("Writing results to PostgreSQL...")
        count = write_similarity(similarity, jdbc_url, jdbc_props)
        print(f"Written {count} similarity pairs")

        return count

    finally:
        spark.stop()
        print("Spark session stopped.")