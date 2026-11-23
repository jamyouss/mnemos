# Mnemos — Architecture

A technical deep-dive into the retrieval pipeline, the indexing pipeline, and
the memory pipeline. For the product pitch, see [README.md](../README.md).
For the active improvement plan, see [ROADMAP.md](ROADMAP.md).

---

## High-level data flow

```
   ┌────────────────────┐ ┌──────────────────────────┐ ┌────────────────┐
   │ filesystem watcher │ │ git post-merge/-checkout │ │ connectors     │
   └────────┬───────────┘ └────────────┬─────────────┘ └───────┬────────┘
            │ (file change events,     │ (diff of the pull,     │ (pages, tickets,
            │  debounced)              │  one POST per file)    │  repos)
            └────────────┬─────────────┴────────────────────────┘
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
                  │ chunk            │      │  + payload      │
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
                  ┌──────────────────────────────────────┐
                  │     hybrid query per collection      │
                  │                                      │
                  │  dense Prefetch ─┐                   │
                  │                  ├─ weighted RRF 3:1 │
                  │  sparse Prefetch┘                   │
                  └─────────────┬────────────────────────┘
                                ▼
                  ┌──────────────────────────────────────┐
                  │  CRAG grader                         │
                  │  all-low? → rewriter → retry merge   │
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
                  │  results (file_path, score, content) │
                  └─────────────┬────────────────────────┘
                                ▼
                       cache store + query log
```

Cache, grader and rewriter only run on `mnemos_search` (`POST /api/search`);
the dedicated code, skills and memory searches go straight from the hybrid
query to the reranker. Details in [RETRIEVAL_PIPELINE](RETRIEVAL_PIPELINE.md).

## Modules

### Indexing (`packages/core/`)

| File | Role |
|------|------|
| `chunkers/go_chunker.py` | Tree-sitter Go: declarations, types, methods |
| `chunkers/vue_chunker.py` | SFC sections: `<script>`, `<template>`, `<style>` |
| `chunkers/markdown_chunker.py` | One chunk per `##` section |
| `chunkers/tabular_chunker.py` | CSV/TSV rows as `column: value` pairs, so column names ride along in every chunk; delegates to the fallback when the file is not really a table |
| `chunkers/fallback_chunker.py` | Sliding window of 500 words, 50 of overlap |
| `chunkers/splitter.py` | Optional character cap on chunks (`MNEMOS_MAX_CHUNK_CHARS`, 0 = off) |
| `skills.py` | Skill slug from the path + description from frontmatter, so a skill result can name itself |
| `path_filter.py` | Single source of truth for the **built-in** "do not index" rules (vendored bundles, build outputs, generated reports). A pure function: per-project rules are layered on by the indexer, never loaded here. |
| `projects.py` | The projects mapping: `path → tags` (`detect_tags`) and per-prefix ignore rules (`detect_excludes`) |
| `embeddings.py` | sentence-transformers, `EMBEDDING_MODEL` (default `all-MiniLM-L6-v2`, 384d). `embed_query(task)` / `embed_documents(titles)`; per-model profiles frame inputs (EmbeddingGemma 2: query prompts, `title: <path> \| text:` documents) |
| `embedding_migration.py` | Moves every collection to a new model or chunk size: `sync` re-embeds stored `content` into `<name>__<model>_<dim>` (`_c<N>` suffix with `--max-chunk-chars`), `switch` serves the old name as an alias of it |
| `sparse.py` | BM25 encoder — camelCase/snake split, stable 31-bit hash |
| `contextual.py` | LLM-generated preamble per chunk (Anthropic-style) |
| `indexer.py` | Orchestrator: skip check → chunk → split → contextualise → embed → upsert. `should_skip()` (via the module-level `resolve_skip()`) unions the built-in policy, the per-prefix rules from the projects mapping and any caller extras — the one place that resolution happens. |
| `collections.py` | Collection registry; named-vector schema constants |
| `config_store.py` | Shared configuration documents in the `mnemos_config` collection |

The dashboard (`ui/`, Nuxt SPA) is built in a separate Docker stage and served
by the same FastAPI app at `/`, mounted after the API so it only catches what
`/api`, `/mcp`, `/health` and `/theme.css` did not. See [DASHBOARD](DASHBOARD.md).

### Retrieval (`server/search.py` + `packages/core/`)

| File | Role |
|------|------|
| `server/search.py` | `SearchService` — entry point used by REST + MCP |
| `server/trace.py` | Opt-in per-stage record of a search (`trace: true`), shown by the Playground |
| `core/cache.py` | Cosine-similarity cache (`mnemos_cache` Qdrant collection) |
| `core/reranker.py` | Cross-encoder rerank + MMR helper |
| `core/grader.py` | LLM-based CRAG document grader |
| `core/rewriter.py` | Query expansion / decomposition / HyDE |
| `core/observability.py` | JSONL query log |

The retrieval order is **fixed and feature-flag-driven**. Disabling a feature
removes it from the chain without affecting the others:

1. cache lookup
2. hybrid query per collection (dense + sparse → weighted RRF) — the caller
   picks the collections; nothing infers them
3. CRAG grader drops low-graded chunks; on all-low + rewriter enabled →
   re-retrieve with variants and merge
4. cross-encoder rerank
5. MMR (only if the reranker returned more than `limit`)
6. preview truncation
7. cache store + query log

### Memory (`packages/core/memory_extractor.py` + `deduplicator.py`)

```
git push (pre-push hook, or git commit with post-commit)
        │
        ▼
        ▶ POST /api/memory/extract  with commit message + diff
                                    │
                                    ▼
                           LLM extraction
                           (decisions, patterns, conventions, lessons)
                                    │
                                    ▼
                           Deduplicator (cos ≥ MNEMOS_DEDUP_THRESHOLD)
                                    │
                          ┌─────────┴─────────┐
                          ▼                   ▼
                      `merge` strategy    `replace` strategy
                          │                   │
                          ▼                   ▼
                   LLM-merged text     New entry replaces old
                                │
                                ▼
                       Upsert into mnemos_memory  (status=approved)
                                │
                                ▼
                       searchable at once; reject (CLI / MCP /
                       dashboard) takes a memory back out
```

See [MEMORY_PIPELINE](MEMORY_PIPELINE.md).

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

Every code/docs/skills/knowledge/memory collection uses **named hybrid vectors**:

```python
vectors_config = {
    "dense": VectorParams(size=embeddings.dimension, distance=Distance.COSINE),  # 384 for MiniLM
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
explicit: `mnemos reindex --recreate --full --collection <name> --path <dir>`.

The cache collection (`mnemos_cache`) is a single unnamed dense vector — no
hybrid needed for the cache key itself. The configuration collection
(`mnemos_config`) has no vector at all. Neither is in the collection registry.

### Payload schema

Every chunk carries:

| Field | Type | Notes |
|------|------|-------|
| `file_path` | str | Container path (or synthetic `memory/<id>` for memories) |
| `content` | str | Chunk text |
| `chunk_index` | int | Position in the file; with `file_path`, it makes the point id |
| `language` | str | `go`, `vue`, `csv`, `tsv` — only set by those chunkers |
| `chunk_type` | str | `function`, `type`, `header` (Go), `template` / `script` / `style` (Vue), `section` (Markdown), `rows`, `file`, `window`, `memory` |
| `symbol_name` | str | Function / type name when AST-derived |
| `tags` | list[str] | Scoping labels — first tag is primary (display). Filtered with `tags_any` (OR) and `tags_all` (AND). |
| `last_indexed_at` | iso8601 | When the chunk was last written |
| `file_mtime` | float | Source mtime. Connectors read it back (`GET /api/index/{collection}/files`) to skip unchanged sources; the server itself re-embeds whatever it is sent |

Markdown chunks add `section` and `doc_title`, skills `skill_name` and
`description`, contextualised chunks `preamble`, connector content
`source_url`. Memory entries add `id`, `project`, `memory_type`, `status`,
`created_at`.

`tags` is the single scoping field across `mnemos_code`, `mnemos_knowledge`
and `mnemos_memory`. `mnemos_knowledge` chunks also carry `source_url` (the
wiki page, ticket or web page they came from) and use `<source>` /
`<source>:<scope>` tags set by the connectors.
For `mnemos_code`, the mapping `path → tags` comes from the projects mapping (stored in
`mnemos_config`), with a default cumulative-segment fallback when no prefix matches. See
[`CONFIGURATION.md`](CONFIGURATION.md#tags--scoping-chunks-across-projects).

## Ignored paths

Every ingestion path converges on `Indexer.index_file`: the filesystem
watcher (`/internal/reindex`), the bulk reindex walker (`/api/reindex`), and
the push API (`/api/index`) — which the `post-merge` / `post-checkout` git
hooks drive, one POST per changed file, and the connectors too. The server
decides with `Indexer.should_skip`, which unions the built-in policy of
`core.path_filter` with the projects mapping's per-prefix rules; the walker
asks it before indexing, and `index_file` asks again as a defense-in-depth
check, so a misbehaving push-API client can never bypass the rules. The
watcher pre-filters on the built-in policy alone (`should_skip_path`), to save
the round-trip. Notably the hooks deliberately do **not** re-implement the
policy in shell: they push, the server filters.

What's filtered:

| Category | Examples |
|---|---|
| Dependencies / vendored | `node_modules/`, `vendor/`, `.yarn/` (Yarn PnP cache), `Pods/`, `.pnpm-store/` |
| Build outputs | `dist/`, `build/`, `.nuxt/`, `_nuxt/`, `.next/`, `.output/`, `.turbo/`, `.svelte-kit/`, `.parcel-cache/` |
| Vendored CommonJS bundles | any `.cjs` (covers `.pnp.cjs`, `yarn-X.Y.Z.cjs`) |
| Minified / chunked JS | `*.min.js`, `*.bundle.js`, `*.chunk.js`, `*.map`, `chunk-vendors` |
| Mobile native build artefacts | `webapp/android/…`, `webapp/ios/…`, `App/App/public/…` |
| Generated reports | `report-tnr-*`, `*.lighthouse-report.html`, `lighthouse-report-*`, `*.report.html`, `*.report.json`, Go coverage `*.out` |
| Copies of live files | `worktrees/`, `*.worktrees/`, `.trash/`, `backup/`, `*.bak`, Jest `*.snap` |
| Lockfiles / changelogs | `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `poetry.lock`, `*.lock`, `CHANGELOG.md` |
| Binaries & assets | images, fonts, archives, `.so`/`.dylib`/`.exe`, `.pyc`, `.log` |
| Installed Python deps | any `site-packages/` or `dist-packages/` — matched on the marker dir, since virtualenv names are arbitrary (`venv/`, `.venv/`, `sam-env/`, `.direnv/`) |
| Retired projects | `archived/` — kept on disk, but it answers questions about code that is no longer live |

To add a pattern, edit `packages/core/path_filter.py` and add a test in
`tests/test_path_filter.py`. The rule applies to every ingestion path
without further change. Chunks indexed before the rule stay in Qdrant until
`scripts/purge-filtered.py --apply` removes them.

**Built-in vs configured.** The lists above are global policy: things that are
never source anywhere. Noise that is specific to one project — an `exports/`
directory, a generated `.csv` — belongs in the projects mapping instead, as
per-prefix `exclude_dirs` / `exclude_exts`. See
[CONFIGURATION](CONFIGURATION.md#per-project-ignore-rules). The two add up;
neither can re-allow what the other denies.

Note what is deliberately *not* denied globally: tabular data. Mnemos is not
only a code RAG, and a default-deny loses data silently — nothing in the
results says a file was never indexed. Noise, by contrast, is visible and
fixable. `chunkers/tabular_chunker.py` makes an indexed CSV actually
retrievable; the projects mapping turns it off where it is not wanted.

## Configuration model

Process knobs are **environment variables**, read from `.env` (every Compose
service loads it with `env_file`; a variable set in the shell in front of
`docker compose` does not reach the containers). Defaults are conservative:
- Hybrid retrieval is **on** by default (it's just the collection schema).
- Every advanced feature (contextual, reranker, MMR, grader, rewriter, cache,
  query log) is **off** by default.

To enable a feature, either flip it on the dashboard's Configuration page —
the flags apply live, no restart — or set the corresponding
`MNEMOS_*_ENABLED=true` in `.env` and recreate the container
(`docker compose up -d rag-server`). No rebuild is needed.

See [`.env.example`](../.env.example) for the full surface.

Shared configuration — connectors, projects (`path → tags`), dashboard
settings overrides, tenants — lives in Qdrant, in the `mnemos_config`
collection (one vectorless point per key, with a `revision`; not searchable,
outside the collection registry). `server/config_bootstrap.py` imports the
`config/` files (and `<state_dir>/settings.json`) once for each absent key at
start-up; `server/config_sync.py` polls the revisions every
`MNEMOS_CONFIG_POLL_SECONDS` and re-applies what moved on each pod. Overrides
of the editable settings win over the env vars. See
[`CONFIGURATION.md`](CONFIGURATION.md#shared-configuration).

## Testing

- Unit tests in `tests/` (~960, `pytest --collect-only -q tests/`) cover each
  module in isolation with fakes / mocks — no live Qdrant or LLM needed.
- API and MCP tests (`test_api.py`, `test_mcp_tools.py`,
  `test_mcp_integration.py`) drive the real FastAPI app in-process through
  ASGI, with Qdrant mocked. The git hooks are tested by running the real shell
  scripts against a temporary repo and an HTTP stub
  (`test_hooks_incremental.py`).
- The dashboard has its own vitest suite (`cd ui && npm test`).
- Quality regression: `mnemos eval run --tag …` against a golden set produces
  recall / precision / MRR / NDCG / hit-rate; `mnemos eval compare` diffs
  two runs. See [EVALUATION](EVALUATION.md).
