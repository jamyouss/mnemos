# Mnemos — Architecture

A technical deep-dive into the retrieval pipeline, the indexing pipeline, and
the memory pipeline. For the product pitch, see [README.md](../README.md).
For the active improvement plan, see [ROADMAP.md](ROADMAP.md).

---

## High-level data flow

```
                      ┌────────────────────┐
                      │  filesystem watcher│
                      └────────┬───────────┘
                               │ (file change events, debounced)
                               ▼
┌──────────┐ chunker  ┌──────────────┐ embed  ┌──────────────┐
│  source  │─────────▶│   AST/SFC    │───────▶│   dense vec  │
│  files   │          │   chunks     │        │  + sparse vec│
└──────────┘          └──────┬───────┘        └──────┬───────┘
                             │                       │
                  (optional) │                       │
                             ▼                       ▼
                  ┌──────────────────┐      ┌─────────────────┐
                  │ contextual LLM   │      │  Qdrant         │
                  │ preamble per     │      │  named vectors  │
                  │ chunk (Phase 2A.2)│      │  + payload      │
                  └────────┬─────────┘      └─────────────────┘
                           │
                           ▼
                      [back to embed]


                                    ┌────────────────┐
                                    │  agent / CLI   │
                                    │  user query    │
                                    └──────┬─────────┘
                                           ▼
                              ┌───────────────────────┐
                              │  semantic cache       │
                              │  (cos sim ≥ 0.95)     │── hit ─▶ return
                              └──────┬────────────────┘
                                   miss
                                     ▼
                              ┌───────────────────────┐
                              │  semantic router      │
                              │  top-K collections    │
                              └──────┬────────────────┘
                                     ▼
                  ┌──────────────────────────────────────┐
                  │     hybrid query per collection      │
                  │                                      │
                  │  dense Prefetch ─┐                   │
                  │                  ├─ RRF fusion (k=60)│
                  │  sparse Prefetch┘                   │
                  └─────────────┬────────────────────────┘
                                ▼
                  ┌──────────────────────────────────────┐
                  │  cross-encoder reranker (top-20→K)   │
                  └─────────────┬────────────────────────┘
                                ▼
                  ┌──────────────────────────────────────┐
                  │  optional MMR diversification        │
                  └─────────────┬────────────────────────┘
                                ▼
                  ┌──────────────────────────────────────┐
                  │  CRAG grader                         │
                  │  all-low? → rewriter → retry merge   │
                  └─────────────┬────────────────────────┘
                                ▼
                  ┌──────────────────────────────────────┐
                  │  results (file_path, score, content) │
                  └─────────────┬────────────────────────┘
                                ▼
                       cache store + query log
```

## Modules

### Indexing (`packages/core/`)

| File | Role |
|------|------|
| `chunkers/go_chunker.py` | Tree-sitter Go: declarations, types, methods |
| `chunkers/vue_chunker.py` | SFC sections: `<script>`, `<template>`, `<style>` |
| `chunkers/markdown_chunker.py` | Header-based splits |
| `chunkers/fallback_chunker.py` | Fixed-size sliding window |
| `embeddings.py` | sentence-transformers (`all-MiniLM-L6-v2`, 384d, normalised) |
| `sparse.py` | BM25 encoder — camelCase/snake split, stable 31-bit hash |
| `contextual.py` | LLM-generated preamble per chunk (Anthropic-style) |
| `indexer.py` | Orchestrator: chunk → contextualise → embed → upsert |
| `collections.py` | Collection registry; named-vector schema constants |

### Retrieval (`server/search.py` + `packages/core/`)

| File | Role |
|------|------|
| `server/search.py` | `SearchService` — entry point used by REST + MCP |
| `core/cache.py` | Cosine-similarity cache (`mnemos_cache` Qdrant collection) |
| `core/router.py` | Cosine route over per-collection description embeddings |
| `core/reranker.py` | Cross-encoder rerank + MMR helper |
| `core/grader.py` | LLM-based CRAG document grader |
| `core/rewriter.py` | Query expansion / decomposition / HyDE |
| `core/observability.py` | JSONL query log |

The retrieval order is **fixed and feature-flag-driven**. Disabling a feature
removes it from the chain without affecting the others:

1. cache lookup
2. router → trim collection set
3. hybrid query per collection (dense + sparse → RRF)
4. cross-encoder rerank
5. MMR (only if reranker pulled more than `limit`)
6. CRAG grader; on all-low + rewriter enabled → re-issue with variants
7. cache store + query log

### Memory (`packages/core/memory_extractor.py` + `deduplicator.py`)

```
git commit (pre-push hook)
        │
        ▼
        ▶ POST /api/memory/extract  with commit message + diff
                                    │
                                    ▼
                           LLM extraction
                           (decisions, patterns, conventions, lessons)
                                    │
                                    ▼
                           Deduplicator (cos ≥ 0.85)
                                    │
                          ┌─────────┴─────────┐
                          ▼                   ▼
                      `merge` strategy    `replace` strategy
                          │                   │
                          ▼                   ▼
                   LLM-merged text     New entry replaces old
                                │
                                ▼
                       Upsert into mnemos_memory  (status=pending)
                                │
                                ▼
                       Human review (CLI / MCP)
                                │
                                ▼
                  status=approved → visible to search
```

### LLM abstraction (`packages/core/llm/`)

```
LLMConfig(provider, model, api_key, base_url)
        │
        ▼
make_llm_provider() → LLMProvider Protocol
                          │
        ┌─────────────────┼──────────────────┐
        ▼                 ▼                  ▼
    OllamaProvider   AnthropicProvider  OpenAIProvider
    (httpx)          (anthropic SDK)    (openai SDK)
                                          │
                                          └─ also vLLM, LM Studio, Groq, Together…
```

Every component that calls an LLM (extractor, dedup merge, contextual chunker,
grader, rewriter, eval generator) receives an `LLMProvider` through its
constructor. They only know about `complete()` / `complete_prompt()`.

## Collection schemas

Every code/docs/skills/memory collection now uses **named hybrid vectors**:

```python
vectors_config = {
    "dense": VectorParams(size=384, distance=Distance.COSINE),
}
sparse_vectors_config = {
    "sparse": SparseVectorParams(modifier=Modifier.IDF),
}
```

This lets us issue a single Qdrant `query_points` with two `Prefetch` legs and
let the server perform RRF fusion. The IDF modifier means Qdrant computes the
inverse document frequency on its side — clients only ship term frequencies.

A legacy collection (single unnamed dense vector) still works in dense-only
mode via the fallback inside `SearchService._hybrid_query`. Migration is
explicit: `mnemos reindex --recreate --collection <name>`.

The cache collection (`mnemos_cache`) is a single unnamed dense vector — no
hybrid needed for the cache key itself.

## Configuration model

All knobs are **environment variables**. Defaults are conservative:
- Hybrid retrieval is **on** by default (it's just the new collection schema).
- Every advanced feature (contextual, reranker, grader, rewriter, router, cache, query log) is **off** by default.

To enable a feature, set the corresponding `MNEMOS_*_ENABLED=true` in your
shell or in `docker-compose.yml`. No rebuild is needed for a flag flip —
just restart the rag-server container.

See [`.env.example`](../.env.example) for the full surface.

## Testing

- Unit tests in `tests/`: 112+ tests covering each module in isolation with
  fakes / mocks (no live Qdrant or Ollama dependency).
- Integration: end-to-end MCP / API tests use a real server.
- Quality regression: `mnemos eval run --tag …` against a golden set produces
  recall / precision / MRR / NDCG / hit-rate; `mnemos eval compare` diffs
  two runs.
