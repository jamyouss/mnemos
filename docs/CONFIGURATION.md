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

Mnemos talks to LLMs through a single abstraction: `core.llm.LLMProvider`.
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

### Tags — scoping chunks across projects

Every chunk in `mnemos_code` carries a `tags: list[str]` payload. The first
tag is the **primary** (used as the default display label); the rest are
cross-cutting labels (parent projects, techno, team) that enable OR/AND
filtering at query time.

**Where tags come from**, in order of precedence:

1. **CLI override** — `mnemos reindex --tags webshop,globex,go` applies the same
   list to every file under `--path`.
2. **YAML mapping** — `config/projects.yaml` declares `path-prefix → tags`.
   Longest-matching prefix wins.
3. **Default fallback** — cumulative path segments
   (`foo/bar/baz/file.go` → `["foo", "foo/bar", "foo/bar/baz"]`).

`config/projects.yaml` (copy from `config/projects.example.yaml`, gitignored):

```yaml
paths:
  myorg/services/billing/:
    - billing                    # primary (display label)
    - myorg
    - go
  apps/dashboard/:
    - dashboard
    - vue3
    - typescript
```

### Per-project ignore rules

An entry may use an extended form that carries ignore rules next to its tags.
Use it when a directory holds files that are noise **for that project** —
analytics exports, generated fixtures — without banning the pattern globally:

```yaml
paths:
  myorg/services/billing/:        # shorthand — tags only
    - billing
    - myorg

  myorg/reports/:                 # extended form
    tags: [reports, myorg]
    exclude_dirs: [exports, snapshots]
    exclude_exts: [.csv, tsv]     # leading dot optional
```

Both forms may be mixed freely; the list shorthand stays valid and means
"tags only", so an existing config needs no migration.

**The three sources add up.** A file is skipped if the built-in policy
(`core.path_filter`), the rules resolved from this file, or the per-run
`--exclude-ext` / `--exclude-dir` flags reject it. None of them overrides
another, and there is no way to re-allow something a built-in rule denies.

Resolution is longest-matching-prefix, like tags. Unlike tags there is no
convention-based fallback: a path with no matching entry simply has no extra
rules.

Because the rules are resolved inside `Indexer.index_file` — the chokepoint
every ingestion path funnels through — they hold for the watcher, a bulk
reindex and the push API alike. That is what makes them durable, where the
CLI flags are not.

> **One caveat.** The watcher pre-filters with the built-in list only, before
> POSTing to the server: it runs in its own container, where `config/` is not
> mounted, so it cannot read this file. A file excluded here is still rejected
> server-side — correctness holds — but the watcher wastes a round-trip on it.

### Deriving the hook lists

The git hooks read `~/.config/mnemos/{repos,index-repos}`: one path per line,
because they are POSIX shell and must keep working with the server down.
Maintaining them by hand meant two sources of truth for one decision — which
of your projects mnemos cares about — free to drift apart.

Generate them from `projects.yaml` instead:

```bash
./scripts/install-hooks.sh --global \
    --codebase-root ~/code --from-projects
```

`paths:` declares path prefixes, often subdirectories; a hook fires at a
repository root, so each prefix is walked up to its enclosing `.git`. Roots
nested inside another are dropped — the hooks match on prefix, so the parent
already covers them.

The installer refuses to overwrite a list it did not generate; pass
`--force-derive` to replace a hand-written one. `derive-hook-repos.py
--print` shows the result without writing anything.

`repos` and `index-repos` get the same content. They stay separate files
because they answer different questions — memory extraction costs an LLM
call per push, indexing costs an HTTP POST per file — so you can still narrow
one without the other by editing it directly (which then opts it out of
regeneration).

### Indexing only what is declared

The watcher walks the **whole** codebase mount. It has no notion of which
repos you meant to index, so on a mount that holds more than your own
projects it will index all of it — vendored clones, model weights, whatever
is on disk. `~/.config/mnemos/index-repos` gates the git hooks, not the
watcher.

`MNEMOS_INDEX_ONLY_DECLARED_PATHS=true` turns `paths:` into an allowlist:
anything under the codebase mount that no declared prefix covers is skipped,
whichever ingestion path offered it. Skills and docs live under a different
mount root and are never affected.

Off by default, so an absent or empty `projects.yaml` keeps indexing
everything rather than silently indexing nothing.

To clear what a mount indexed before you turned it on:

```bash
./scripts/purge-filtered.py --dry-run --only-declared   # then --apply
```

### Search filters

Search filters are exposed everywhere:

```bash
mnemos search "auth"  --tags acme,webshop           # OR  → tags_any
mnemos search "auth"  --tags-all acme,vue3       # AND → tags_all
```

```python
mnemos_search_code(query="auth", tags_any=["acme-front-app-ecommerce", "acme-front-lib-auth"])
mnemos_search_code(query="auth", tags_all=["acme", "vue3"])
```

Changes to `config/projects.yaml` take effect on the next indexing event.
Re-tag an existing project with:

```bash
mnemos reindex --recreate --full --collection mnemos_code \
       --path /data/codebase/myproject --tags myproject,go
```

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

### Choosing collections

There is no automatic collection routing, by design — see
[ROADMAP](ROADMAP.md#retrieval-quality). The caller knows the intent, so the
caller narrows: pass `collections` to `mnemos_search`, or use the dedicated
tool (`mnemos_search_code`, `mnemos_search_skills`, `mnemos_search_memory`).
Searching all of them costs nothing measurable anyway — everything except the
code collection is small.

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

## Git hooks (host side)

These are read by `scripts/hooks/*`, which run on your machine — not by the
server — so they are **not** container environment variables.

Precedence is **environment > `~/.config/mnemos/config` > default**. The
settings file exists because git hooks also fire from GUI clients and IDEs
that never source a shell profile, where an exported variable would be lost.

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_URL` | `http://localhost:8100` | Server the hooks talk to |
| `MNEMOS_CODEBASE_ROOT` | `$HOME/code` | Host dir bind-mounted at `/data/codebase`. Must match `MNEMOS_CODEBASE_HOST_PATH`, otherwise hooks cannot map host paths to container paths and silently index nothing |
| `MNEMOS_CONFIG_FILE` | `$HOME/.config/mnemos/config` | Machine-local settings, written by `install-hooks.sh --codebase-root` |
| `MNEMOS_REPOS_CONFIG` | `$HOME/.config/mnemos/repos` | Roots whose pushes trigger **memory extraction** |
| `MNEMOS_INDEX_REPOS_CONFIG` | `$HOME/.config/mnemos/index-repos` | Roots whose pulls trigger **code indexing**. Falls back to `repos` when the file is absent |
| `MNEMOS_MAX_INCREMENTAL_FILES` | `200` | Above this many changed files, one bulk `/api/reindex` replaces the per-file pushes |
| `MNEMOS_MAX_FILE_BYTES` | `1048576` | Files larger than this are not pushed (transport guard; path rules live in `core.path_filter`) |

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
  "grader": false
}
```

## Deployment-mode flags

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_MODE` | `local` | `local` (single-tenant, full read access) or `deployed` (multi-tenant, auth required) |
| `MNEMOS_AUTH_ENABLED` | `false` | Toggle API-key auth for deployed mode |

Multi-tenant config lives in `config/tenants.yaml` (gitignored — copy
`config/tenants.example.yaml` and edit the api_keys). See
[`DEPLOYMENT.md`](DEPLOYMENT.md).

## How to apply a config change

For most flags it's just an env var + a container restart:

```bash
MNEMOS_RERANKER_ENABLED=true docker compose up -d rag-server
```

For changes that affect indexing (embedding model, contextual flag), you must
reindex with `--recreate`:

```bash
mnemos reindex --recreate --full --collection mnemos_code --tags myproject,go \
       --path /data/codebase/myproject
```
