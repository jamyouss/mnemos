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
│  fused server-side via RRF (k=60)                    │
└────────┬─────────────────────────────────────────────┘
         ▼
┌──────────────────┐
│ cross-encoder    │  → re-score (query, chunk) pairs
│ reranker         │
└────────┬─────────┘
         ▼
┌──────────────────┐
│ MMR (optional)   │  → diversify top-K
└────────┬─────────┘
         ▼
┌──────────────────┐         all low?
│ CRAG grader      │  ─────────────────────┐
└────────┬─────────┘                       │
         │ good chunks                     ▼
         ▼                          ┌──────────────────┐
       results                      │ query rewriter   │
         │                          │ + retry          │
         ▼                          └────────┬─────────┘
   cache + log                               │
                                             ▼
                                       merge + rerank
                                             │
                                             ▼
                                          results
```

Every stage is **toggleable**. Disabling a stage removes it from the chain
without affecting any other stage — there's a single linear pipeline in
`server/search.py`.

## Stage 0 — Semantic cache (optional)

When `MNEMOS_CACHE_ENABLED=true`, the first thing `SearchService.search`
does is look up a previously-served result with a sufficiently close query.

- **Storage**: dedicated `mnemos_cache` Qdrant collection (unnamed dense vector).
- **Key**: dense embedding of the incoming query.
- **Namespace**: `search:limit=K:colls=A,B:types=…:path=…` — a cache entry
  for `limit=3` cannot satisfy a `limit=10` call.
- **Hit criterion**: cosine similarity ≥ `MNEMOS_CACHE_THRESHOLD` (0.95
  default) **and** namespace match **and** age ≤ `MNEMOS_CACHE_TTL_SECONDS`.
- **Invalidation**: every `POST /api/reindex` calls `cache.invalidate()`
  before scheduling the background reindex, so stale results can never
  outlive the index.
- **Failure mode**: fail-open. Any Qdrant error → cache miss → normal path.

Cache hits also get logged (`cache_hit: true, cache_score: 0.97`) for analysis.

## Stage 1 — Choosing collections

The caller decides. `mnemos_search` takes a `collections` list, and the
dedicated tools (`mnemos_search_code`, `mnemos_search_skills`,
`mnemos_search_memory`) each pin one. Omitting it fans out to code + docs +
skills.

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
query  ──▶  dense embedding (384d, all-MiniLM-L6-v2, normalised)
       └─▶  sparse encoding (in-house BM25, camelCase/snake-aware)

per collection:
  query_points(
    prefetch=[
      Prefetch(query=dense_vec,  using="dense",  limit=20),
      Prefetch(query=sparse_vec, using="sparse", limit=20),
    ],
    query=FusionQuery(fusion=Fusion.RRF),  # k=60 by default
    limit=20 (when reranking) or limit (otherwise),
    filter=...,  # language / chunk_type / file_path
  )
```

Qdrant computes RRF (Reciprocal Rank Fusion) **server-side**. The IDF for
BM25 is also computed server-side via `Modifier.IDF` — the client only
ships term frequencies. This means the client stays simple even if the
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
mnemos reindex --recreate --full --collection mnemos_code_X --path /…
```

## Stage 3 — Cross-encoder reranker (optional)

When `MNEMOS_RERANKER_ENABLED=true`, the candidate pool returned by the
hybrid retrieval (up to `HYBRID_TOP = 20` per collection) is re-scored by
a cross-encoder.

- **Default model**: `BAAI/bge-reranker-base` (~280 MB).
- **Recommended for quality**: `BAAI/bge-reranker-v2-m3` (~568 MB, multilingual).
- **Recommended for speed**: `cross-encoder/ms-marco-MiniLM-L-6-v2` (~80 MB).
- **Backend**: [`rerankers`](https://github.com/AnswerDotAI/rerankers) lib
  — unified API across cross-encoders, ColBERT, T5-based, LLM rerankers, etc.
- **Loading**: lazy — first rerank call downloads + loads weights. Subsequent
  calls reuse the in-memory model.
- **CPU latency**: 12-50 s per query on a M-series Mac with `bge-reranker-base`.
  Plan for GPU in production. See [`EVAL.md`](EVAL.md#latency-reality-check).

The reranker **dominates** the latency budget. It's also the **single biggest
quality lever** in the entire plan (+47 % MRR vs baseline). Both facts matter.

## Stage 4 — MMR diversification (optional)

When `MNEMOS_MMR_ENABLED=true`, the reranked list is passed through
Maximum Marginal Relevance to pick top-K diverse items.

- Picks the most relevant doc first.
- Then iteratively picks the doc that maximises
  `λ·relevance − (1−λ)·max(sim_to_already_picked)`.
- `MNEMOS_MMR_LAMBDA=0.5` balances relevance and novelty (default).
- **No-op when reranker returns ≤ K results** — MMR needs a pool to choose from.

Useful when your index has many near-duplicate chunks (e.g. several versions
of the same file).

## Stage 5 — CRAG grader (optional)

When `MNEMOS_GRADER_ENABLED=true`, each retrieved chunk is sent to the LLM
with the original query, and the LLM returns a grade:

- `high` — strongly relevant, surface this chunk
- `medium` — partially relevant
- `low` — irrelevant or noise

- **Parallelisation**: `MNEMOS_GRADER_WORKERS` (4 default) threads.
- **Failure mode**: any LLM error or unparseable response → `medium` (safest
  neutral grade).
- **All-low handling**: when **every** chunk grades `low`, the grader signals
  retrieval failure and the rewriter (if enabled) kicks in.
- **Otherwise**: chunks graded `low` are dropped before reranking.

This is N LLM calls per query — expensive. Combine with a fast LLM provider
(Groq, Anthropic Haiku) for production.

## Stage 6 — Query rewriter (optional)

When `MNEMOS_REWRITER_ENABLED=true` **and** the grader signalled retrieval
failure, the rewriter generates alternative phrasings.

Strategies (`MNEMOS_REWRITER_STRATEGY`):
- `expansion` — synonyms + technical terms (default, cheap)
- `decompose` — split a multi-hop question into sub-questions
- `hyde` — generate a hypothetical answer document for embedding

The original query is always kept at position 0. Up to
`MNEMOS_REWRITER_MAX_VARIANTS` alternatives are generated. Each alternative
is re-issued through stages 2-5, results are merged & deduped by
`(collection, file_path)`, then sent back through stage 3 (rerank).

## Stage 7 — Cache write-through

When the cache is enabled, the final results are written back to
`mnemos_cache` with:
- vector = embedding of the **original** query
- payload = serialised results (JSON), namespace, timestamp

## Stage 8 — Observability

When `MNEMOS_QUERY_LOG_ENABLED=true`, one JSONL line is appended per request
to `MNEMOS_QUERY_LOG_PATH`. The entry includes which features were active
(`reranker`, `grader`, `rewriter`, `cache_hit`) so you can do replay analysis
or A/B comparisons.

## Reading the scores you get back

The score in `SearchResult.score` reflects the **last** stage that ran:

- **Without reranker**: Qdrant's RRF score. Bounded in `[0, ~0.03]` — these
  are reciprocal-rank sums, not similarities. Higher = better.
- **With reranker**: the cross-encoder logit. Can be **negative**. Compare
  scores within the same query, never across queries.

The `--limit` you request is the size of the **final** list, after every
stage. The internal pool can be much larger (20 × number of collections).

## Tuning checklist

| Goal | Knobs |
|------|-------|
| Better recall on niche terms | Enable hybrid (default), raise `HYBRID_TOP` (code change) |
| Better precision | Enable reranker, then enable grader |
| Lower latency | Enable the cache, narrow `collections` at the call site, disable the reranker (or use a small one on GPU) |
| Better recall on vague queries | Enable rewriter (requires grader) |
| Diverse top-K | Enable MMR |
| Better recall on long files | Enable contextual chunking (indexing-side, expensive) |
