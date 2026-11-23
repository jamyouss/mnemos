# Mnemos — Retrieval Pipeline

Anatomy of a query, stage by stage. For the indexing side, see
[`MEMORY_PIPELINE.md`](MEMORY_PIPELINE.md) (memory) and the
[`ARCHITECTURE.md`](ARCHITECTURE.md) overview.

## Pipeline overview

```
query
  │
  ▼
┌──────────────────┐
│ semantic cache   │  → hit? return immediately
└────────┬─────────┘
         │ miss
         ▼
┌──────────────────────────────────────────────────────┐
│ hybrid query per collection                          │
│  dense Prefetch (cosine, 20 cands)                   │
│  sparse Prefetch (BM25, 20 cands)                    │
│  fused server-side via weighted RRF (dense 3 : 1)    │
└────────┬─────────────────────────────────────────────┘
         ▼
┌──────────────────┐         all low?
│ CRAG grader      │  ─────────────────────┐
└────────┬─────────┘                       │
         │ low chunks dropped              ▼
         │                          ┌──────────────────┐
         │                          │ query rewriter   │
         │                          │ + re-retrieve    │
         │                          └────────┬─────────┘
         │                                   │ merged, deduped
         ▼◀──────────────────────────────────┘
┌──────────────────┐
│ cross-encoder    │  → re-score (query, chunk) pairs
│ reranker         │
└────────┬─────────┘
         ▼
┌──────────────────┐
│ MMR (optional)   │  → diversify top-K
└────────┬─────────┘
         ▼
   preview truncation
         ▼
   cache write + query log
         ▼
      results
```

Every stage except the hybrid query is **toggleable**, and off by default.
Disabling a stage removes it from the chain without affecting any other
stage — there's a single linear pipeline in `server/search.py`. Each flag can
be set in `.env` or flipped live from the dashboard's Configuration page
(see [CONFIGURATION](CONFIGURATION.md)).

The full chain only runs on `mnemos_search` (`POST /api/search`). The
dedicated entry points — `mnemos_search_code`, `mnemos_search_skills`,
`mnemos_search_memory` — run the hybrid query and the reranker; code and
memory searches also truncate previews, and `search_code` applies MMR. They
skip the cache, the grader and the rewriter.

## Stage 0 — Semantic cache (optional)

When `MNEMOS_CACHE_ENABLED=true`, the first thing `SearchService.search`
does is look up a previously-served result with a sufficiently close query.

- **Storage**: dedicated `mnemos_cache` Qdrant collection (unnamed dense vector).
- **Key**: dense embedding of the incoming query.
- **Namespace**: `search:limit=K:mode=…:colls=A,B:types=…:path=…:tagsAny=…:tagsAll=…:read=…`
  — a cache entry for `limit=3` cannot satisfy a `limit=10` call, and an
  entry cached for one tenant's read scope is never served to another.
- **Hit criterion**: cosine similarity ≥ `MNEMOS_CACHE_THRESHOLD` (0.95
  default) **and** namespace match **and** age ≤ `MNEMOS_CACHE_TTL_SECONDS`
  (3600 default).
- **Invalidation**: every write to an index drops the whole cache — a
  single-file push or delete (`POST /api/index`, `DELETE /api/index/...`,
  the watcher's `/internal/reindex`), a memory written, reviewed or
  extracted, and a bulk reindex (`POST /api/reindex`, MCP `mnemos_reindex`)
  both when it starts and when its walk ends. The cache lives in Qdrant, so
  the wipe reaches every pod. A burst of pushes empties it repeatedly: the
  cache pays off on a quiet index, not during a sync.
- **Failure mode**: fail-open. Any Qdrant error → cache miss → normal path.

Cache hits also get logged (`cache_hit: true, cache_score: 0.97`) for analysis.
A request can bypass the cache with `use_cache: false`.

## Stage 1 — Choosing collections

The caller decides. `mnemos_search` takes a `collections` list, and the
dedicated tools (`mnemos_search_code`, `mnemos_search_skills`,
`mnemos_search_memory`) each pin one. Omitting it fans out to every
collection except `mnemos_memory`: code + docs + skills + knowledge.

There used to be a semantic router here, comparing the query against each
collection's `description`. It was removed: measured against a real index it
never once cleared its own confidence threshold, and forcing it to fire made
things worse — a pure code query ranked the code collection *last*, so an
active router would have excluded the very collection being asked about. The
signal in a short category label is too weak to route on.

It was also solving a problem that does not exist here. Fanning out to every
collection costs nothing measurable: outside the code collection, the others
hold a few thousand points between them.

## Stage 2 — Hybrid retrieval per collection

This is the **default-on** part of the pipeline. It happens for every search,
regardless of feature flags.

```
query  ──▶  dense embedding (EMBEDDING_MODEL, normalised; code prompt on search_code)
       └─▶  sparse encoding (in-house BM25, camelCase/snake-aware)

per collection:
  query_points(
    prefetch=[
      Prefetch(query=dense_vec,  using="dense",  limit=20),
      Prefetch(query=sparse_vec, using="sparse", limit=20),
    ],
    query=RrfQuery(rrf=Rrf(weights=[3.0, 1.0])),  # MNEMOS_FUSION_DENSE_WEIGHT
    limit=20 (when reranking) or limit (otherwise),
    filter=...,  # language / chunk_type / file_path / tags / tenant read scope
  )
```

Qdrant computes RRF (Reciprocal Rank Fusion) **server-side**, with its
default constant `k = 2`. The dense side weighs 3 times the BM25 side
(`MNEMOS_FUSION_DENSE_WEIGHT`, 3.0 default; 1.0 falls back to plain RRF):
equal weights let BM25 drag the ranking down, because camelCase/snake
splitting makes a symbol match every file sharing its words — see
[EVAL](EVAL.md#2026-11-22-evening--fusion-weight-index-cleanup-chunk-size).
The IDF for BM25 is also computed server-side via `Modifier.IDF` — the client
only ships term frequencies. This means the client stays simple even if the
corpus grows.

**Sparse encoding details** (`core/sparse.py`):
- Tokenisation: `[A-Za-z_][A-Za-z0-9_]*` and pure digits.
- camelCase + snake_case are split, **original token kept too** — searching
  for `handleHTTPRequest` matches both `handleHTTPRequest` and `request`.
- Min token length 2, English stop list filtered.
- Token IDs are blake2b-hashed (31-bit) — stable across Python invocations
  (unlike the built-in `hash()`).
- An empty / all-stopword input emits a placeholder bucket so Qdrant accepts it.

**Migrating a legacy unnamed-dense collection**:
Old collections (single unnamed vector) coexist with hybrid collections —
the search falls back to a dense-only `query_points` when the hybrid
prefetch raises. Migrate explicitly with:
```bash
mnemos reindex --recreate --full --collection mnemos_code --path /data/codebase/…
```

## Stage 3 — CRAG grader (optional, `mnemos_search` only)

When `MNEMOS_GRADER_ENABLED=true`, each retrieved chunk (its first 800
characters) is sent to the LLM with the original query, and the LLM returns
a grade:

- `high` — strongly relevant, surface this chunk
- `medium` — partially relevant
- `low` — irrelevant or noise

- **Parallelisation**: `MNEMOS_GRADER_WORKERS` (4 default) threads.
- **Failure mode**: any LLM error or unparseable response → `medium` (safest
  neutral grade).
- **All-low handling**: when **every** chunk grades `low`, the grader signals
  retrieval failure and the rewriter (if enabled) kicks in. With the
  rewriter off, nothing is dropped.
- **Otherwise**: chunks graded `low` are dropped before reranking.

This is N LLM calls per query — expensive. Measured on this index it took
the p50 from 42 ms to 16 s ([EVAL](EVAL.md#2026-09-07--grader-ab-on-the-current-index)).
Combine with a fast LLM provider (Groq, Anthropic Haiku) if you turn it on.

## Stage 4 — Query rewriter (optional, `mnemos_search` only)

When `MNEMOS_REWRITER_ENABLED=true` **and** the grader signalled retrieval
failure, the rewriter generates alternative phrasings. It never runs without
the grader.

Strategies (`MNEMOS_REWRITER_STRATEGY`):
- `expansion` — synonyms + technical terms (default, cheap)
- `decompose` — split a multi-hop question into sub-questions
- `hyde` — generate a hypothetical answer document for embedding

Up to `MNEMOS_REWRITER_MAX_VARIANTS` (3 default) alternatives are generated.
Each alternative goes back through stage 2 (hybrid retrieval, not the
grader); its hits are appended to the original ones, deduped by
`(collection, file_path)`, and the merged pool continues to the reranker.

## Stage 5 — Cross-encoder reranker (optional)

When `MNEMOS_RERANKER_ENABLED=true`, the candidate pool returned by the
hybrid retrieval (up to `_HYBRID_TOP = 20` per collection) is re-scored by
a cross-encoder. Unlike the grader, this runs on every entry point.

- **Default model**: `BAAI/bge-reranker-base` (~1.1 GB download).
- **Recommended for quality**: `BAAI/bge-reranker-v2-m3` (~568 MB, multilingual).
- **Recommended for speed**: `cross-encoder/ms-marco-MiniLM-L-6-v2` (~80 MB).
- **Pair length**: each (query, chunk) pair is truncated to
  `MNEMOS_RERANKER_MAX_LENGTH` tokens (256 default; 0 keeps the model's own
  limit).
- **Backend**: [`rerankers`](https://github.com/AnswerDotAI/rerankers) lib
  — unified API across cross-encoders, ColBERT, T5-based, LLM rerankers, etc.
- **Loading**: lazy — first rerank call downloads + loads weights. Subsequent
  calls reuse the in-memory model.
- **CPU latency**: about 1.1 s per 20 candidates with `bge-reranker-base`
  (2026-11-22), roughly half that at the 256-token default. The first
  measurements, in May, recorded 12–50 s per query for 20–40 candidates.
  See [`EVAL.md`](EVAL.md#latency-reality-check).
- **Failure mode**: a reranker error keeps the original order.

The reranker **dominates** the latency budget. On MiniLM embeddings it was
also the single biggest quality lever measured (+47 % MRR on the legacy
golden set, MRR 0.283 → 0.492 on the 62-question one); on EmbeddingGemma 2
the gain shrinks (0.556 → 0.658). Measure before turning it on.

## Stage 6 — MMR diversification (optional)

When `MNEMOS_MMR_ENABLED=true`, the reranked list is passed through
Maximum Marginal Relevance to pick top-K diverse items.

- Picks the most relevant doc first.
- Then iteratively picks the doc that maximises
  `λ·relevance − (1−λ)·max(sim_to_already_picked)`.
- `MNEMOS_MMR_LAMBDA=0.5` balances relevance and novelty (default).
- Re-embeds the candidates to compare them — one extra embedding batch per query.
- **Runs inside the rerank step**: a no-op when the reranker is off, or when
  it returns ≤ K results — MMR needs a pool to choose from.

Useful when your index has many near-duplicate chunks (e.g. several versions
of the same file).

## Stage 7 — Preview truncation

With `mode=preview` (the default), each result's content is capped at 30
lines / 1 500 characters and marked `metadata.truncated=true`. `mode=full`
returns whole chunks.

## Stage 8 — Cache write-through

When the cache is enabled, the final results of `mnemos_search` are written
back to `mnemos_cache` with:
- vector = embedding of the **original** query
- payload = serialised results (JSON), namespace, timestamp

## Stage 9 — Observability

When `MNEMOS_QUERY_LOG_ENABLED=true`, one JSONL line is appended per request
to `MNEMOS_QUERY_LOG_PATH`, from every entry point (`intent` says which). The
entry records whether the reranker ran; `mnemos_search` entries add
`grader`, `rewriter` and `cache_hit`, so you can do replay analysis or A/B
comparisons. The dashboard's Search log page reads it.

## Contextual chunking

The one quality feature on the **indexing** side. When
`MNEMOS_CONTEXTUAL_ENABLED=true`, the indexer asks the LLM for a one-sentence
preamble per chunk — situated by the first 1 500 characters of its file —
and prepends it to the chunk before the dense and BM25 vectors are computed
(`MNEMOS_CONTEXTUAL_WORKERS` parallel calls, 4 default). The preamble is also
kept in the `preamble` payload field.

It only affects files indexed after the flag is turned on: chunks already in
the index keep no preamble until their files are reindexed, so enabling it
for a whole collection means a full reindex — one LLM call per chunk. A first
attempt on the skills collection showed no gain
([EVAL](EVAL.md#phase-2a2-partial-2026-05-15--contextual-chunking-on-mnemos_skills-only)).

## Reading the scores you get back

The score in `SearchResult.score` reflects the **last** stage that ran:

- **Without reranker**: Qdrant's RRF score. These are weighted
  reciprocal-rank sums, not similarities: at most 1.25 with the default 3:1
  weights (first on both sides), well under 1 for most hits. Higher = better.
- **With reranker**: the cross-encoder logit. Can be **negative**. Compare
  scores within the same query, never across queries.

The `--limit` you request is the size of the **final** list, after every
stage. The internal pool can be much larger (20 × number of collections).

## Tuning checklist

| Goal | Knobs |
|------|-------|
| Better recall on niche terms | Hybrid (always on), lower `MNEMOS_FUSION_DENSE_WEIGHT` to give BM25 more say, raise `_HYBRID_TOP` (code change) |
| Better precision | Enable reranker, then enable grader |
| Lower latency | Enable the cache, narrow `collections` at the call site, disable the reranker (or use a small one on GPU) |
| Better recall on vague queries | Enable rewriter (requires grader) |
| Diverse top-K | Enable MMR (requires reranker) |
| Better recall on long files | Enable contextual chunking (indexing-side, expensive) |
