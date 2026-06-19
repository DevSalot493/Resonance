import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from api.routers.artists import router as artists_router
from api.db import get_pool, close_pool
from api.cache import get_redis
from api.models.schemas import HealthResponse

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handles startup and shutdown events.
    - Startup:  initialises the database connection pool and Redis client
    - Shutdown: closes all database connections cleanly
    """
    get_pool()
    get_redis()
    yield
    close_pool()


app = FastAPI(
    title="Resonance",
    description=(
        "Music artist similarity engine built on open tag data. "
        "Finds artists that genuinely sound and feel like what you love."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(artists_router)


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health_check():
    """Returns service health status and catalog statistics."""
    from api.db import get_db_connection
    from api.cache import get_redis

    with get_db_connection() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM artists WHERE catalog_status = 'active'")
        catalog_size = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) AS cnt FROM artist_similarity")
        similarity_pairs = cur.fetchone()["cnt"]

    try:
        get_redis().ping()
        cache_status = "ok"
    except Exception:
        cache_status = "unavailable"

    return HealthResponse(
        status="ok",
        catalog_size=catalog_size,
        similarity_pairs=similarity_pairs,
        cache_status=cache_status,
    )