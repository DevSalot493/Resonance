# Scoring Algorithm

How Resonance decides two artists are similar.

## Data sources

Two independent tag sources are combined, each capturing a different dimension of an artist's identity:

| Source | What it captures | Raw scale |
|--------|--------------------|-----------|
| Last.fm | Mood, feel, listener-perceived vibe | Weight 0–100 |
| MusicBrainz | Structural genre, formally voted by community editors | Unbounded vote count |

ListenBrainz is **not** a scoring input. It is used only during catalog expansion (Phase 8) to discover which new artists to add — using a third party's own collaborative-filtering similarity as your own similarity signal would make this project a wrapper around someone else's algorithm rather than an independent one.

## Step 1 — Quality gate

An artist needs sufficient signal from at least one source to be included in scoring:

- Last.fm: ≥5 tags with weight ≥10
- MusicBrainz: ≥3 tags with vote_count ≥1

Artists failing both are marked `catalog_status = 'sparse'` — kept in the database but excluded from similarity computation. This prevents thin, unreliable data from producing misleading scores.

## Step 2 — Per-source normalization

Each source uses a different normalization approach, because the raw scales mean different things.

**Last.fm** — fixed 0–100 scale, normalized by simple division:

```
normalized_weight = tag_weight / 100.0
```

**MusicBrainz** — unbounded vote counts, normalized relative to each artist's own maximum:

```
normalized_weight = vote_count / MAX(vote_count) for that artist
```

This means every artist's single most-voted MusicBrainz tag always normalizes to exactly 1.0, regardless of whether that vote count was 3 or 50 in absolute terms. A fixed denominator would unfairly shrink less-tagged artists' signal even when their tagging is internally consistent.

Each source keeps its top N tags per artist after normalization (top 30 for Last.fm, top 20 for MusicBrainz) — both ranked by normalized weight.

## Step 3 — Merging into a unified profile

Both normalized sources are combined per artist+tag:

```sql
unified_weight = MAX(normalized_weight)  -- across sources, if tag appears in both
source_count   = COUNT(*)                -- 1 = one source, 2 = both sources confirmed it
```

A tag confirmed by **both** Last.fm and MusicBrainz (`source_count = 2`) is considered stronger evidence than a tag from only one source — ranked higher in the final profile, kept in the top 50 tags per artist regardless of raw weight.

## Step 4 — Weighted Jaccard similarity

For every pair of artists sharing at least one tag:

```
similarity(A, B) = intersection / union

intersection = Σ min(weight_A, weight_B)   for each shared tag
union        = total_weight_A + total_weight_B - intersection
```

**Why weighted Jaccard rather than cosine similarity:** tag data is sparse and categorical — most artist pairs share zero or very few tags, and the meaningful signal is in *which* tags overlap and *how strongly*, not the angle between dense vectors. Jaccard's intersection-over-union naturally handles sparsity and produces intuitive scores: a score of 1.0 means complete tag overlap, 0.0 means no overlap.

**Why `union = total_A + total_B - intersection` instead of summing `max()` over shared tags directly:** computing the union directly would require knowing every tag unique to either artist — effectively a full outer join, which is computationally awkward at scale. The identity above is mathematically equivalent and only requires each artist's total weight (computed once) plus the intersection (computed via inner join on shared tags) — far cheaper to compute across hundreds of thousands of pairs.

### Worked example

Artist A (Gorillaz): `indie rock (0.9)`, `psychedelic (0.8)`
Artist B: `indie rock (0.9)`

```
intersection = min(0.9, 0.9) = 0.9
total_A      = 0.9 + 0.8 = 1.7
total_B      = 0.9
union        = 1.7 + 0.9 - 0.9 = 1.7
similarity   = 0.9 / 1.7 = 0.5294
```

Note that Artist A's unique tag (`psychedelic`) correctly lowers the score — A and B aren't a perfect match, even though every tag B has is also in A.

## Step 5 — Threshold filtering

Pairs scoring below **0.05** are discarded before storage. This removes near-zero overlaps (one shared low-weight tag and nothing else) that add noise without meaningful signal. At this threshold, the current dataset produces 106,427 stored pairs across 858 active artists — an average of roughly 248 similar artists per artist.

## Known limitations

- **Catalog depth**: only Hop 1 expansion has run (artists directly suggested as similar to your seeds). Hop 2 (artists similar to Hop 1 artists) would roughly triple catalog size but hasn't been run — noted as future work.
- **Tag vocabulary mismatch**: Last.fm and MusicBrainz communities don't always use identical terminology for the same concept (e.g. "shoegaze" vs "dream pop" for closely related sounds) — these are treated as entirely separate tags rather than being merged, which can slightly undercount true overlap.
- **No temporal weighting**: a tag added by one person ten years ago counts the same as one added yesterday. Recency-weighting tag relevance is a possible future improvement.