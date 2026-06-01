-- ─────────────────────────────────────────────────────
-- RESONANCE — Database Schema
-- ─────────────────────────────────────────────────────

-- Core artist catalog
CREATE TABLE IF NOT EXISTS artists (
    artist_id       SERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    lastfm_name     VARCHAR(255),
    mb_id           UUID UNIQUE,
    catalog_tier    SMALLINT    DEFAULT 0,
    catalog_status  VARCHAR(20) DEFAULT 'active',
    lastfm_listeners INTEGER,
    created_at      TIMESTAMP   DEFAULT NOW(),
    updated_at      TIMESTAMP   DEFAULT NOW()
);

-- Raw Last.fm tags
CREATE TABLE IF NOT EXISTS raw_lastfm_tags (
    id          SERIAL PRIMARY KEY,
    artist_id   INTEGER     NOT NULL REFERENCES artists(artist_id) ON DELETE CASCADE,
    tag_name    VARCHAR(150) NOT NULL,
    tag_weight  SMALLINT    NOT NULL,
    fetched_at  TIMESTAMP   DEFAULT NOW(),
    UNIQUE(artist_id, tag_name)
);

-- Raw MusicBrainz tags
CREATE TABLE IF NOT EXISTS raw_mb_tags (
    id          SERIAL PRIMARY KEY,
    artist_id   INTEGER     NOT NULL REFERENCES artists(artist_id) ON DELETE CASCADE,
    tag_name    VARCHAR(150) NOT NULL,
    vote_count  SMALLINT    NOT NULL,
    fetched_at  TIMESTAMP   DEFAULT NOW(),
    UNIQUE(artist_id, tag_name)
);

-- Raw ListenBrainz similar artists (catalog expansion only)
CREATE TABLE IF NOT EXISTS raw_lb_similar_artists (
    id                    SERIAL PRIMARY KEY,
    source_artist_id      INTEGER NOT NULL REFERENCES artists(artist_id) ON DELETE CASCADE,
    similar_artist_mbid   UUID    NOT NULL,
    similar_artist_name   VARCHAR(255),
    lb_similarity_score   FLOAT,
    fetched_at            TIMESTAMP DEFAULT NOW(),
    UNIQUE(source_artist_id, similar_artist_mbid)
);

-- dbt-managed: unified normalised tag profiles
-- Written by dbt mart model, read by PySpark
CREATE TABLE IF NOT EXISTS mart_artist_tag_profiles (
    artist_id       INTEGER     NOT NULL REFERENCES artists(artist_id),
    tag_name        VARCHAR(150) NOT NULL,
    unified_weight  FLOAT       NOT NULL,
    source_count    SMALLINT    NOT NULL,
    computed_at     TIMESTAMP   DEFAULT NOW(),
    PRIMARY KEY (artist_id, tag_name)
);

-- PySpark-computed pairwise similarity scores
CREATE TABLE IF NOT EXISTS artist_similarity (
    id               SERIAL PRIMARY KEY,
    artist_a_id      INTEGER NOT NULL REFERENCES artists(artist_id),
    artist_b_id      INTEGER NOT NULL REFERENCES artists(artist_id),
    similarity_score FLOAT   NOT NULL,
    shared_tag_count SMALLINT,
    computed_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(artist_a_id, artist_b_id),
    CHECK(artist_a_id < artist_b_id)
);

-- Personal seed list
CREATE TABLE IF NOT EXISTS seed_artists (
    id          SERIAL PRIMARY KEY,
    artist_id   INTEGER NOT NULL REFERENCES artists(artist_id) UNIQUE,
    added_at    TIMESTAMP DEFAULT NOW(),
    notes       TEXT
);

-- ─────────────────────────────────────────────────────
-- Indexes
-- ─────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_similarity_a
    ON artist_similarity(artist_a_id, similarity_score DESC);

CREATE INDEX IF NOT EXISTS idx_similarity_b
    ON artist_similarity(artist_b_id, similarity_score DESC);

CREATE INDEX IF NOT EXISTS idx_lastfm_tags_artist
    ON raw_lastfm_tags(artist_id);

CREATE INDEX IF NOT EXISTS idx_mb_tags_artist
    ON raw_mb_tags(artist_id);

CREATE INDEX IF NOT EXISTS idx_tag_profiles_artist
    ON mart_artist_tag_profiles(artist_id);

CREATE INDEX IF NOT EXISTS idx_artists_mb_id
    ON artists(mb_id);

CREATE INDEX IF NOT EXISTS idx_artists_name
    ON artists(name text_pattern_ops);