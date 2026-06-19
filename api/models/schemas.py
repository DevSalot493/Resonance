from pydantic import BaseModel, Field
from typing import Optional


class ArtistResult(BaseModel):
    """One artist in a similarity result."""
    artist_id:        int
    name:             str
    similarity_score: float
    shared_tag_count: int
    catalog_tier:     int


class SimilarArtistsResponse(BaseModel):
    """Response for /artists/similar"""
    seed_artist:  str
    artist_id:    int
    results:      list[ArtistResult]
    total_found:  int
    cache_hit:    bool = False


class DiscoverResponse(BaseModel):
    """Response for /artists/discover"""
    seeds:        list[str]
    results:      list[ArtistResult]
    total_found:  int
    cache_hit:    bool = False


class SearchResult(BaseModel):
    """One artist in a search result."""
    artist_id:      int
    name:           str
    catalog_tier:   int
    catalog_status: str


class SearchResponse(BaseModel):
    """Response for /artists/search"""
    query:   str
    results: list[SearchResult]


class SharedTag(BaseModel):
    """One shared tag in an explain response."""
    tag_name:       str
    unified_weight: float
    source_count:   int


class ExplainResponse(BaseModel):
    """Response for /artists/explain"""
    artist_a:         str
    artist_b:         str
    similarity_score: Optional[float]
    shared_tag_count: Optional[int]
    shared_tags:      list[SharedTag]


class HealthResponse(BaseModel):
    """Response for /health"""
    status:           str
    catalog_size:     int
    similarity_pairs: int
    cache_status:     str