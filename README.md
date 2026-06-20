# Resonance

A music artist similarity engine built entirely on open, community-driven tag data — no Spotify, no proprietary audio analysis, no black-box recommendation algorithm. Resonance tells you *why* two artists are similar, not just *that* they are.

## Why not Spotify?

Spotify locked down its audio-features API (`/audio-features`, `/audio-analysis`) for all new developer applications in November 2024. Resonance was built specifically without it, using three open data sources instead — which turned out to produce more *explainable* results anyway, since every similarity score traces back to specific shared tags rather than an opaque feature vector.

## How it works

Resonance combines structural genre data and crowd-sourced mood/feel tags from two independent communities, then computes similarity using weighted Jaccard overlap — never relying on any third party's own "similar artists" algorithm as a signal.

```
Last.fm tags ──┐
               ├──→ dbt (normalize + merge) ──→ PySpark (weighted Jaccard) ──→ FastAPI
MusicBrainz tags ┘                                                                │
ListenBrainz  ──────────────────→ catalog expansion only ─────────────────────────┘
              (who to add)
```

- **Last.fm** — community mood/feel tags ("dreamy", "late night", "melancholic")
- **MusicBrainz** — structural genre tags ("post-rock", "shoegaze", "jazz-funk")
- **ListenBrainz** — used *exclusively* to discover which new artists to add to the catalog. Never used as a similarity signal — using a third party's behavioral recommendation as your own scoring input would defeat the purpose of building an independent engine.

See [SCORING.md](./SCORING.md) for the full similarity algorithm, including the weighted Jaccard formula and worked examples.

## Tech stack

| Layer              | Technology                                |
|--------------------|--------------------------------------------|
| Ingestion          | Python, pylast, musicbrainzngs, requests   |
| Database           | PostgreSQL 15                              |
| Transformation     | dbt                                        |
| Similarity compute | PySpark 3.5                                |
| API                | FastAPI, Uvicorn                           |
| Caching            | Redis 7                                    |
| Infra              | Docker Compose                             |
| CI/CD              | GitHub Actions                             |
| Testing            | pytest (unit + integration)                |

## Project structure

```
resonance/
├── ingestion/              # API clients + seed/expansion scripts
│   ├── lastfm_client.py
│   ├── mb_client.py
│   ├── listenbrainz_client.py
│   ├── seed_loader.py
│   ├── expand_catalog.py
│   └── utils.py
├── dbt_project/            # SQL transformation layer
│   └── models/
│       ├── staging/
│       ├── intermediate/
│       └── marts/
├── spark_jobs/             # Similarity computation
│   ├── similarity_compute.py
│   └── run_similarity.py
├── api/                    # FastAPI serving layer
│   ├── main.py
│   ├── db.py
│   ├── cache.py
│   ├── models/
│   └── routers/
├── sql/
│   └── init.sql            # Database schema
├── tests/
│   ├── unit/
│   └── integration/
├── seeds/
│   └── my_artists.txt      # Personal seed artist list
├── .github/workflows/      # CI/CD
├── docker-compose.yml
├── SCORING.md               # Algorithm deep-dive
└── README.md
```

## Setup

### Prerequisites

- Python 3.10 (PySpark requires ≤3.11)
- Docker Desktop
- Java 11 (for PySpark — [Eclipse Temurin](https://adoptium.net/))
- A Last.fm API key ([get one here](https://www.last.fm/api/account/create))

### 1. Clone and install

```bash
git clone [https://github.com/YOUR_USERNAME/resonance.git](https://github.com/DevSalot493/Resonance)
cd resonance
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in:

```
LASTFM_API_KEY=your_key
LASTFM_API_SECRET=your_secret
MB_USER_AGENT=resonance/1.0 (your_email@example.com)
DATABASE_URL=postgresql://music_user:musicuser123@localhost:5432/resonance
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
REDIS_URL=redis://localhost:6379
```

### 3. Start the database

```bash
docker compose up -d
```

### 4. Add your seed artists

Edit `seeds/my_artists.txt` — one artist name per line, 30–50 artists you genuinely listen to.

### 5. Run the pipeline

```bash
# Load your seed artists across all three data sources
python -m ingestion.seed_loader

# Expand the catalog using ListenBrainz similar-artist suggestions
python -m ingestion.expand_catalog

# Transform raw tags into unified, normalized profiles
cd dbt_project && dbt run && dbt test && cd ..

# Compute pairwise similarity scores
python spark_jobs/run_similarity.py
```

### 6. Run the API

```bash
uvicorn api.main:app --reload --port 8000
```

Visit `http://localhost:8000/docs` for interactive API documentation.

## API endpoints

| Endpoint | Description |
|----------|--------------|
| `GET /health` | Service status, catalog size, similarity pair count |
| `GET /artists/search?q=` | Find artists by name prefix |
| `GET /artists/similar?name=&limit=` | Top N similar artists |
| `GET /artists/discover?seeds=&limit=` | Aggregated discovery across multiple seed artists |
| `GET /artists/explain?artist_a=&artist_b=` | Shared tags behind a similarity score |

Example:
```bash
curl "http://localhost:8000/artists/similar?name=Gorillaz&limit=5"
```

```json
{
  "seed_artist": "Gorillaz",
  "results": [
    {"name": "Twenty One Pilots", "similarity_score": 0.3127, "shared_tag_count": 6},
    {"name": "Coldplay",          "similarity_score": 0.2558, "shared_tag_count": 6},
    {"name": "Pixies",            "similarity_score": 0.2415, "shared_tag_count": 4}
  ],
  "cache_hit": false
}
```

## Testing

```bash
# Fast, self-contained, mocked dependencies
pytest tests/unit/ -v

# Requires Docker running with real data loaded
pytest tests/integration/ -v
```

~185 tests across unit and integration suites. CI runs the unit suite automatically on every push.

## Current dataset

- 1,050 artists in catalog (859 active, 191 sparse)
- 106,427 computed similarity pairs
- Two-source tag coverage: Last.fm + MusicBrainz

## CI/CD

- `test.yml` — runs the unit test suite on every push (GitHub Actions)
- `daily_catalog_expansion.yml` / `weekly_similarity.yml` — scheduled production automation, require a deployed database and secrets to run live (see workflow files for configuration)

## License

MIT
