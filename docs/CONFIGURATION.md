# Mnemos — Configuration

Every Mnemos knob is an **environment variable**, passed through Docker
Compose. The full surface lives in [`.env.example`](../.env.example); this
doc explains what each variable does and when to flip it.

## Defaults at a glance

| Behaviour | Default |
|-----------|--------|
| Hybrid retrieval (BM25 + dense + RRF) | **ON** (collection schema) |
| Cross-encoder reranker | OFF |
| Contextual chunking | OFF |
| CRAG grader / rewriter | OFF |
| Semantic router | OFF |
| Semantic cache | OFF |
| Query log (observability) | OFF |
| Memory dedup | ON (threshold 0.85, strategy `merge`) |
| LLM provider | `ollama` (local) |

The defaults give you a working RAG without any LLM calls at retrieval time —
fast, predictable. Every "advanced" feature is opt-in.

---

## Storage and embeddings

| Var | Default | Notes |
|-----|--------|------|
| `QDRANT_HOST` | `qdrant` | Hostname inside the compose network |
| `QDRANT_PORT` | `6333` | |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Any sentence-transformers model. **Change → must reindex.** Vector size in `collections.py` may need updating too. |
| `CODEBASE_PATH` | `/data/codebase` | Root for code. Override the bind mount, not this var. |
| `CLAUDE_CONFIG_PATH` | `/data/claude-config` | Root for skills + docs. |
| `MNEMOS_STATE_DIR` | `/data/state` | Persistent state (query logs, etc.) |

## LLM provider

Mnemos talks to LLMs through a single abstraction: `rag_core.llm.LLMProvider`.
You pick one at startup; every component that needs an LLM uses the same one.

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_LLM_PROVIDER` | `ollama` | One of `ollama`, `anthropic`, `openai` |
| `MNEMOS_LLM_MODEL` | `llama3.1:8b` | Provider-specific model name |
| `MNEMOS_LLM_API_KEY` | _empty_ | Required for `anthropic` and `openai` |
| `MNEMOS_LLM_BASE_URL` | _empty_ | Override base URL — works with vLLM, LM Studio, Together, Groq, OpenRouter… |
| `MNEMOS_OLLAMA_URL` | `http://ollama:11434` | Legacy fallback (used when provider=ollama and `MNEMOS_LLM_BASE_URL` is empty) |

### Recipes

**Local Ollama (default)**:
```env
MNEMOS_LLM_PROVIDER=ollama
MNEMOS_LLM_MODEL=llama3.1:8b
```

**Anthropic API with prompt caching (fast contextual chunking)**:
```env
MNEMOS_LLM_PROVIDER=anthropic
MNEMOS_LLM_MODEL=claude-haiku-4-5
MNEMOS_LLM_API_KEY=sk-ant-…
```

**Groq for cheap+fast cloud inference**:
```env
MNEMOS_LLM_PROVIDER=openai
MNEMOS_LLM_MODEL=llama-3.1-70b-versatile
MNEMOS_LLM_API_KEY=gsk_…
MNEMOS_LLM_BASE_URL=https://api.groq.com/openai/v1
```

**Local LM Studio**:
```env
MNEMOS_LLM_PROVIDER=openai
MNEMOS_LLM_MODEL=meta-llama-3.1-8b-instruct
MNEMOS_LLM_API_KEY=not-needed
MNEMOS_LLM_BASE_URL=http://host.docker.internal:1234/v1
```

## Indexing

| Var | Default | Description |
|-----|--------|------|
| `WATCHER_DEBOUNCE_MS` | `2000` | Coalesce filesystem events |
| `MNEMOS_CONTEXTUAL_ENABLED` | `false` | Phase 2A.2 — prepend an LLM preamble to every chunk |
| `MNEMOS_CONTEXTUAL_WORKERS` | `4` | Parallel LLM calls during indexing |

Enabling contextual chunking forces a full reindex on the next ingest. Plan a
maintenance window — see [`RETRIEVAL_PIPELINE.md`](RETRIEVAL_PIPELINE.md#contextual-chunking).

## Retrieval

### Cross-encoder reranker (Phase 2B)

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_RERANKER_ENABLED` | `false` | Master switch |
| `MNEMOS_RERANKER_MODEL` | `BAAI/bge-reranker-base` | Any rerankers-supported checkpoint |
| `MNEMOS_RERANKER_TYPE` | `cross-encoder` | rerankers backend type |
| `MNEMOS_MMR_ENABLED` | `false` | MMR post-rerank for diversity |
| `MNEMOS_MMR_LAMBDA` | `0.5` | 0 = pure novelty, 1 = pure relevance |

The reranker is the **largest quality lever** (+47 % MRR vs baseline) and the
**largest latency cost** (~14 s/query on CPU). Recommended setups:

- **Local dev, CPU-only**: disable, or use the lighter
  `cross-encoder/ms-marco-MiniLM-L-6-v2`.
- **Prod with GPU**: enable with `BAAI/bge-reranker-v2-m3` for max quality.

### CRAG corrective loop (Phase 3)

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_GRADER_ENABLED` | `false` | LLM judges each retrieved chunk (high/medium/low) |
| `MNEMOS_GRADER_WORKERS` | `4` | Parallel grading calls |
| `MNEMOS_REWRITER_ENABLED` | `false` | When all chunks score low, rewrite query and retry |
| `MNEMOS_REWRITER_STRATEGY` | `expansion` | `expansion` / `decompose` / `hyde` |
| `MNEMOS_REWRITER_MAX_VARIANTS` | `3` | Cap alternatives generated per failure |

The grader adds N parallel LLM calls per query (where N = number of retrieved
chunks). It's expensive — combine with a fast LLM (Groq, Anthropic Haiku) for
production use.

### Semantic router (Phase 4D)

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_ROUTER_ENABLED` | `false` | Trim collection set per query |
| `MNEMOS_ROUTER_TOP_K` | `2` | Number of collections to keep per query |
| `MNEMOS_ROUTER_MIN_SCORE` | `0.4` | Fall back to ALL collections when top score below this |

Cheap and pure-Python — no LLM call at query time. Useful when you have many
collections.

### Semantic cache (Phase 4E)

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_CACHE_ENABLED` | `false` | Cache results by query embedding |
| `MNEMOS_CACHE_THRESHOLD` | `0.95` | Cosine sim threshold for a cache hit |
| `MNEMOS_CACHE_TTL_SECONDS` | `3600` | Entry expiry |

Stored in a dedicated `mnemos_cache` Qdrant collection. Automatically wiped
on every reindex (`POST /api/reindex`).

## Memory pipeline

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_DEDUP_THRESHOLD` | `0.85` | Cosine threshold above which memories are deduped |
| `MNEMOS_DEDUP_STRATEGY` | `merge` | `merge` (LLM consolidates) or `replace` (newer wins) |
| `MNEMOS_HOOK_TRIGGER` | `pre-push` | Git hook trigger mode (used by `scripts/install-hooks.sh`) |
| `MAX_DOCUMENTS` | `0` | Per-tenant max document count (`0` = unlimited) |

## Observability

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_QUERY_LOG_ENABLED` | `false` | Append every retrieval call to a JSONL log |
| `MNEMOS_QUERY_LOG_PATH` | `/data/state/query-log.jsonl` | Log location (inside the container) |

Each line is a flat JSON object:
```json
{
  "ts": 1747400000.12,
  "query": "ride cancel",
  "intent": "search",
  "n_results": 5,
  "top_files": ["…ride/service.go", …],
  "top_scores": [-0.41, -0.92, …],
  "latency_ms": 1432.5,
  "cache_hit": false,
  "reranker": true,
  "router": true,
  "grader": false
}
```

## Deployment-mode flags

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_MODE` | `local` | `local` (single-tenant, full read access) or `deployed` (multi-tenant, auth required) |
| `MNEMOS_AUTH_ENABLED` | `false` | Toggle API-key auth for deployed mode |

Multi-tenant config lives in `config/tenants.yaml`. See
[`DEPLOYMENT.md`](DEPLOYMENT.md).

## How to apply a config change

For most flags it's just an env var + a container restart:

```bash
MNEMOS_RERANKER_ENABLED=true docker compose up -d rag-server
```

For changes that affect indexing (embedding model, contextual flag), you must
reindex with `--recreate`:

```bash
mnemos reindex --recreate --full --collection mnemos_code_moby \
       --path /data/codebase/digital-gigafactory/moby
```
