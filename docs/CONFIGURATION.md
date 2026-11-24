# Mnemos — Configuration

Every Mnemos knob is an **environment variable**. Under Docker Compose every
service reads them from `.env` (`env_file: .env`, required:
`cp .env.example .env`); [`.env.example`](../.env.example) holds the full
surface with comments. A variable set in the shell in front of
`docker compose` does **not** reach the containers: edit `.env`, then
`docker compose up -d`. This doc explains what each variable does and when
to flip it. Most retrieval, cache, LLM, memory and indexing settings can also
be changed from the dashboard, where a saved value wins over `.env`: see
[Dashboard overrides](#dashboard-overrides).

## Defaults at a glance

| Behaviour | Default |
|-----------|--------|
| Hybrid retrieval (BM25 + dense + RRF) | **ON** (collection schema) |
| Cross-encoder reranker | OFF |
| Contextual chunking | OFF |
| CRAG grader / rewriter | OFF |
| Semantic cache | OFF |
| Query log (observability) | OFF |
| Memory dedup | ON (threshold 0.80 in `.env.example`, 0.85 when unset; strategy `merge`) |
| LLM provider | `ollama` (local) |

The defaults give you a working RAG without any LLM calls at retrieval time —
fast, predictable. Every "advanced" feature is opt-in.

---

## Compose wiring

Read by `docker-compose.yml` itself (interpolation) or by one container only.

| Var | `.env.example` | Notes |
|-----|----------------|------|
| `MNEMOS_CODEBASE_HOST_PATH` | `./data/codebase` | Host directory mounted read-only at `/data/codebase` (server, watcher) |
| `MNEMOS_CLAUDE_CONFIG_HOST_PATH` | `./data/claude-config` | Host directory mounted at `/data/claude-config` (skills + docs) |
| `MNEMOS_SERVER_IMAGE` / `MNEMOS_WATCHER_IMAGE` / `MNEMOS_CLI_IMAGE` | `jamyouss/mnemos-*:latest` | Images of `rag-server`, `watcher`, `ingester`; `mnemos-*:dev` with `docker-compose.dev.yml` |
| `MNEMOS_PULL_POLICY` | `missing` | Keep `missing` for local images, which cannot be pulled |
| `MNEMOS_SERVER_URL` | `http://rag-server:8100` | Server the watcher pushes to |
| `MNEMOS_URL` | `http://rag-server:8100` | Server the ingester talks to (host clients default to `http://localhost:8100`) |
| `QDRANT__SERVICE__API_KEY` | _(commented)_ | Read by the Qdrant container; same value as `QDRANT_API_KEY`, see [Authentication](#authentication) |

## Storage and embeddings

| Var | Default | Notes |
|-----|--------|------|
| `QDRANT_HOST` | `qdrant` (`.env.example`; `localhost` when unset) | Hostname inside the compose network |
| `QDRANT_PORT` | `6333` | |
| `QDRANT_API_KEY` | _(unset)_ | Sent to Qdrant as `api-key`. Must equal Qdrant's `QDRANT__SERVICE__API_KEY`. **Required when `MNEMOS_AUTH_ENABLED=true`**: the server refuses to start without it. Also read by `core.embedding_migration` and `scripts/purge-filtered.py` |
| `QDRANT_HTTPS` | `false` | Connect to Qdrant over TLS (Qdrant Cloud, a TLS-terminated Qdrant). qdrant-client would otherwise switch to TLS as soon as a key is set |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Any sentence-transformers model; vector size is read from the model. `google/embeddinggemma-2` is profiled (query prompts, `title: <path>` document framing, 512 tokens). **Change on an existing index → migrate**, see [Changing the embedding model](#changing-the-embedding-model). |
| `CODEBASE_PATH` | `/data/codebase` | Container path of the code mount. To change what is mounted, set `MNEMOS_CODEBASE_HOST_PATH`, not this var. |
| `CLAUDE_CONFIG_PATH` | `/data/claude-config` | Container path of skills + docs (`MNEMOS_CLAUDE_CONFIG_HOST_PATH` on the host side). |
| `MNEMOS_STATE_DIR` | `/data/state` | Persistent state: the query log, and a legacy `settings.json` imported once (see [Shared configuration](#shared-configuration)) |

## LLM provider

Mnemos talks to LLMs through a single abstraction: `core.llm.LLMProvider`.
You pick one at startup; every component that needs an LLM uses the same one.

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_LLM_PROVIDER` | `ollama` | One of `ollama`, `anthropic`, `openai` |
| `MNEMOS_LLM_MODEL` | `llama3.1:8b` | Provider-specific model name |
| `MNEMOS_LLM_API_KEY` | _empty_ | Required for `anthropic` and `openai` |
| `MNEMOS_LLM_BASE_URL` | _empty_ | Override base URL — works with vLLM, LM Studio, Together, Groq, OpenRouter… |
| `MNEMOS_OLLAMA_URL` | empty (`.env.example`; `http://localhost:11434` when unset) | Ollama endpoint, used when provider=ollama and `MNEMOS_LLM_BASE_URL` is empty. Empty: probes `http://ollama:11434`, `http://host.docker.internal:11434`, then `http://localhost:11434`, and keeps the first one with a model pulled |

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

Contextual chunking only enriches what is indexed after it is switched on;
chunks already in Qdrant keep their old text until their files are
reindexed (`mnemos reindex --full …`). Each chunk costs one LLM call: plan for
it on a large codebase.

### Tags — scoping chunks across projects

Every chunk in `mnemos_code` carries a `tags: list[str]` payload. The first
tag is the **primary** (used as the default display label); the rest are
cross-cutting labels (parent projects, techno, team) that enable OR/AND
filtering at query time.

**Where tags come from**, in order of precedence:

1. **CLI override** — `mnemos reindex --tags webshop,globex,go` applies the same
   list to every file under `--path`.
2. **Projects mapping** — the `projects` configuration declares
   `path-prefix → tags`. Longest-matching prefix wins.
3. **Default fallback** — cumulative path segments
   (`foo/bar/baz/file.go` → `["foo", "foo/bar", "foo/bar/baz"]`).

The mapping is stored in the database ([Shared configuration](#shared-configuration))
and edited from the dashboard's Projects page or `PUT /api/projects`. At the
first start the server imports `MNEMOS_PROJECTS_CONFIG_PATH`
(`config/projects.yaml`, copied from `config/projects.example.yaml`) if the
database holds no mapping yet. Same shape in the file and the API:

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
(`core.path_filter`), the rules resolved from this mapping, or the per-run
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
> POSTing to the server: it does not read the projects mapping. A file
> excluded here is still rejected server-side — correctness holds — but the
> watcher wastes a round-trip on it.

### Deriving the hook lists

The git hooks read `~/.config/mnemos/{repos,index-repos}`: one path per line,
because they are POSIX shell and must keep working with the server down.
Maintaining them by hand meant two sources of truth for one decision — which
of your projects mnemos cares about — free to drift apart.

Generate them from the projects mapping instead (read from `GET /api/projects`
on `MNEMOS_URL` with `MNEMOS_API_KEY`, a key with admin or write-all scope):

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

Off by default, so an absent or empty projects mapping keeps indexing
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

A saved projects mapping applies on every pod within
`MNEMOS_CONFIG_POLL_SECONDS`, from the next indexing event on.
Re-tag an existing project by reindexing it: each file's chunks are replaced
with freshly tagged ones.

```bash
mnemos reindex --full --collection mnemos_code --path /data/codebase/myproject
```

Add `--tags` only to override the mapping. Do **not** add `--recreate` for
this: it drops the whole collection — every other project, and the
repositories the git connector synced into `mnemos_code` — before indexing
`--path` alone.

## Retrieval

### Cross-encoder reranker (Phase 2B)

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_RERANKER_ENABLED` | `false` | Master switch |
| `MNEMOS_RERANKER_MODEL` | `BAAI/bge-reranker-base` | Any rerankers-supported checkpoint |
| `MNEMOS_RERANKER_TYPE` | `cross-encoder` | rerankers backend type |
| `MNEMOS_RERANKER_MAX_LENGTH` | `256` | Tokens per (query, chunk) pair; `0` keeps the model's limit (8 192 for `bge-reranker-v2-m3`) |
| `MNEMOS_MMR_ENABLED` | `false` | MMR post-rerank for diversity |
| `MNEMOS_MMR_LAMBDA` | `0.5` | 0 = pure novelty, 1 = pure relevance |

With EmbeddingGemma 2 the reranker adds ~+0.10 MRR for ~0.8 s per query on a
Mac CPU (more inside Docker), so it stays off by default. Without the length
cap, `bge-reranker-v2-m3` reads whole chunks and took 68 s per query.

### Fusion, chunk size, response size

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_FUSION_DENSE_WEIGHT` | `3.0` | Weight of the dense side in the dense/BM25 RRF fusion. `1.0` is plain RRF, which let BM25 noise outrank dense matches (MRR 0.70 vs 0.84, docs/EVAL.md) |
| `MNEMOS_MAX_CHUNK_CHARS` | `0` (off) | Upper bound on a chunk; longer ones are split into overlapping windows on line boundaries. Off by default: at 1 500 (≈ what the embedder reads) MRR fell 0.835 → 0.789, the extra chunks crowding out the right file (docs/EVAL.md). Changing it on an existing index needs a rebuild: `core.embedding_migration sync --max-chunk-chars N`, then `switch` with the same flag |
| `MNEMOS_RESPONSE_BUDGET_CHARS` | `32000` | Cap on the JSON size of one MCP search tool response (≈ 8 K tokens). Trailing results past it are dropped and counted in the response's `dropped`; the first result is always kept. Read once at start-up by `server/mcp_tools.py`: env only, not on the dashboard. REST endpoints are not capped |

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
[RETRIEVAL_PIPELINE](RETRIEVAL_PIPELINE.md#stage-1--choosing-collections). The caller knows the intent, so the
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

Stored in a dedicated `mnemos_cache` Qdrant collection. Wiped on every write
to an index while the cache is on: per-file pushes and deletes (watcher, git
hooks, connectors), memory writes, and bulk reindexes at start and end. See
[RETRIEVAL_PIPELINE.md](RETRIEVAL_PIPELINE.md).

## Memory pipeline

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_DEDUP_THRESHOLD` | `0.80` (`.env.example`; `0.85` when unset) | Cosine threshold above which a new memory merges into an existing one. Calibrated against a real corpus, not a constant — see below |
| `MNEMOS_DEDUP_STRATEGY` | `merge` | `merge` (LLM consolidates) or `replace` (newer wins) |
| `MNEMOS_HOOK_TRIGGER` | `pre-push` | `pre-push` or `post-commit`: the git hook that runs memory extraction. Read on the host by `scripts/install-hooks.sh` and the hooks, see [Git hooks](#git-hooks-host-side) |

Memories are written **searchable** — `status: approved` — rather than held for
review. Gating them made the empty state the default: six days of extraction
produced fifteen entries and zero approvals, so nothing the pipeline learned
could be retrieved. No comparable system gates writes this way; Mem0 dropped
its write-time reconciliation pass in v3 and lets retrieval ranking sort
conflicting facts out at read time. `status` remains for taking a bad memory
back out: reject it from the dashboard's Memories page or with
`mnemos memory reject <id>`.

On the dedup threshold: 0.85 — still the default when the variable is unset —
was a guess. On a real 15-memory corpus the one
genuine near-duplicate pair scored 0.844 and the next closest 0.646, so the
old value missed the duplicate by six thousandths while 0.80 sits in the
middle of that gap. Re-measure as the corpus grows.

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
| `MNEMOS_QUERY_LOG_ENABLED` | `false` | Append every retrieval call to a JSONL log. **Required by the dashboard's Search log and search charts** — without it there is nothing to display |
| `MNEMOS_EVAL_RUNS_PATH` | `/app/evals/runs` | Where `GET /api/eval/runs` reads eval runs. Bind-mounted read-only: runs are local artefacts, never baked into the image |
| `MNEMOS_UI_PATH` | `/app/ui` | Built dashboard. Absent → the server serves the API alone |
| `MNEMOS_UI_THEME_PATH` | `/app/config/theme.css` | Instance stylesheet served at `/theme.css`, see [Instance theme](#instance-theme) |
| `MNEMOS_QUERY_LOG_PATH` | `/data/state/query-log.jsonl` | Log location (inside the container) |
| `MNEMOS_AUDIT_RETENTION_DAYS` | `365` | How long the audit trail (Activity page) keeps an entry. Live from the Configuration page. See [DEPLOYMENT.md](DEPLOYMENT.md#audit-trail) |
| `MNEMOS_INDEX_EVENTS_RETENTION_DAYS` | `30` | How long the Indexing page keeps file events and reindex jobs. Live |

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

## Authentication

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_AUTH_ENABLED` | `false` | Require `Authorization: Bearer <api-key>` on every `/api`, `/internal` and `/mcp` path. `/health` and the dashboard files stay open |
| `MNEMOS_TENANTS_PATH` | `/data/config/tenants.yaml` | Import source: read once, at a start where the database holds no tenant; every key in it becomes an admin with every tag. Ignored afterwards |
| `MNEMOS_BOOTSTRAP_ADMIN_KEY` | _(unset)_ | Key of the root tenant `admin`, created or updated at every start, whatever other tenants exist. The root cannot be edited, rotated or deleted from the dashboard or the API; change the variable and restart to rotate its key |
| `QDRANT_API_KEY` | _(unset)_ | Required with auth on, see [Storage](#storage-and-embeddings): API keys protect nothing if Qdrant itself is open |
| `MNEMOS_API_KEY` | _(unset)_ | Client side: the key sent by the CLI, watcher, ingester, `scripts/*` and git hooks |

Tenants live in the database (see [Shared configuration](#shared-configuration)).
Each has an `admin` role plus `read` and `write` tag scopes (`"*"` = every tag);
keys are stored as SHA-256 only and managed from the dashboard's Tenants page or
`/api/tenants`. With `MNEMOS_AUTH_ENABLED=true` and no tenant at all, the server
refuses to start. Scope rules and endpoints:
[`DEPLOYMENT.md`](DEPLOYMENT.md#authentication).

## Knowledge connectors

Confluence spaces, Jira projects, web sites and remote git repositories
(synced into `mnemos_knowledge`, or `mnemos_code` for git by default) are
declared in the `connectors` configuration, stored in the database and edited
from the dashboard's Connectors page (`PUT /api/connectors`). The tokens stay
in the ingester's environment, in the variables named by each source's
`token_env`.

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_CONNECTORS_CONFIG` | _(unset)_ (CLI) · `/app/config/connectors.yaml` (server) | Server: import source, read once at the first start when the database holds no connectors. CLI: when set (like `--config`), `mnemos ingest run` / `serve` read this file instead of the server. Every compose service reads `.env`, so setting it there also points the `ingester` container at a path it does not mount: leave it commented unless you mount the file there |

`mnemos ingest serve` (the opt-in `ingester` compose service) syncs at
start-up, then every `interval`. Without `--config` it reads the sources from
`GET /api/connectors` (key with admin or write-all scope) and resyncs within
5 s of a saved change. The server never syncs and never sees the tokens. See
[`CONNECTORS.md`](CONNECTORS.md#configuration-file).

## Shared configuration

Connectors, projects (`path → tags`), dashboard settings overrides and tenants
live in the Qdrant collection `mnemos_config` (one document per key, not
searchable). Every server pod reads the same documents.

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_CONFIG_POLL_SECONDS` | `5` | How often each pod checks the database for configuration changes |

- **First start.** For each key absent from the database, the server imports
  its file once: `MNEMOS_CONNECTORS_CONFIG`, `MNEMOS_PROJECTS_CONFIG_PATH`,
  `$MNEMOS_STATE_DIR/settings.json`, `MNEMOS_TENANTS_PATH`. Missing file →
  nothing imported. After that the database is the source of truth and the
  files are ignored; editing them changes nothing. `MNEMOS_BOOTSTRAP_ADMIN_KEY`
  is the exception: checked at every start, it keeps the root tenant `admin`
  on its key.
- **Propagation.** The pod that saves applies at once; every other pod picks
  the change up within `MNEMOS_CONFIG_POLL_SECONDS`. A failed poll is logged
  and retried; the last good configuration stays in force.
- **Concurrent saves.** Each document carries a `revision`. A save that sends
  the revision it loaded and finds another one answers `409` with the current
  revision (the dashboard offers to refresh). A save without `revision` overwrites
  (last write wins).

## Dashboard overrides

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_UI_CONFIG_WRITABLE` | `false` | Let the dashboard save the shared configuration: settings overrides (`PATCH`/`DELETE /api/config`), connectors, projects and tenants. Off → those pages are read-only and writes answer `403` |

The dashboard edits the settings `server/settings_registry.py` marks
editable: the reranker (switch, model, max length), fusion weight, MMR, the
CRAG grader and rewriter, the semantic cache, LLM provider and model, dedup
threshold and strategy, contextual chunking and its workers, declared-paths
indexing, the query log. Everything else stays env-only: secrets (LLM API key,
Qdrant key, bootstrap key), URLs, Qdrant, paths, authentication, the
reranker backend, the embedding model, the chunk size, the poll interval and
`MNEMOS_UI_CONFIG_WRITABLE` itself.

**Precedence: override > env var > default.** Overrides are stored in the
database (`settings` key, see [Shared configuration](#shared-configuration))
and applied on every pod, so an override keeps winning over the env var in
`.env` until it is removed from the page. A `PATCH` naming an unknown or
non-editable key is refused as a whole (`422`, nothing saved); such a key
found in the stored document or an imported `settings.json` is logged as a
warning and ignored.

How a change applies depends on the setting (`apply` in `GET /api/config`):

| `apply` | Effect |
|---------|--------|
| `live` | The running components switch at once, no restart |
| `restart` | Saved, effective at each pod's next start; listed in `pending_restart` until then |
| `reindex` | Needs a re-embedding: the embedding model and `MNEMOS_MAX_CHUNK_CHARS`, both env only (see [Changing the embedding model](#changing-the-embedding-model)) |

Secrets (`MNEMOS_LLM_API_KEY`, `QDRANT_API_KEY`, `MNEMOS_BOOTSTRAP_ADMIN_KEY`)
are never returned by the API, not even masked: the page only learns whether
each is set.

> **Security.** `/api/config`, `/api/connectors`, `/api/projects` and
> `/api/tenants` sit behind the API key when `MNEMOS_AUTH_ENABLED=true`. With
> auth off and `MNEMOS_UI_CONFIG_WRITABLE=true`, anyone who can reach port
> 8100 can change retrieval settings, create API keys and point the ingester
> at other sites. Turn auth on first on anything but a private machine.

## Instance theme

`GET /theme.css` serves the file at `MNEMOS_UI_THEME_PATH`
(`/app/config/theme.css`, i.e. `config/theme.css` through the compose bind
mount) with `Cache-Control: no-cache`. No file → an empty stylesheet, i.e. the
built-in theme. The route is outside `/api`, so it needs no API key.

```bash
cp config/theme.example.css config/theme.css   # gitignored
```

[`config/theme.example.css`](../config/theme.example.css) lists every CSS
variable the dashboard reads (`--background`, `--primary`, `--radius`,
`--chart-1..5`, `--sidebar-*` …), in oklch, with a `:root` block for the light
theme and `.dark` for the dark one. Keep only the variables you change. The
file is read on every request: reload the page, no restart.

## How to apply a config change

[The editable settings](#dashboard-overrides) are changed from the dashboard's
Configuration page — a saved override wins over `.env`. Everything else is a
line in `.env`, then a recreate of the containers that read it (shell
variables in front of `docker compose` do not reach them):

```bash
# .env: MNEMOS_RESPONSE_BUDGET_CHARS=16000
docker compose up -d rag-server
```

After switching contextual chunking on, reindex what should be enriched
(the files' chunks are replaced; no `--recreate`, which would drop every
other project of the collection):

```bash
mnemos reindex --full --collection mnemos_code --path /data/codebase/myproject
```

### Changing the embedding model

Collections are sized for one model, and the server refuses to start on
collections sized for another. `core.embedding_migration` moves every
collection, memories and connector pages included, without re-reading any
source file: it re-embeds the `content` stored in Qdrant into a copy named
`<collection>__<model>_<dim>`, then serves the old name as an alias of it.

```bash
M=google/embeddinggemma-2
# 1. Copy while the server keeps serving the old model.
docker compose exec rag-server python -m core.embedding_migration sync --model $M
# 2. Stop the writers, catch up what changed meanwhile, switch.
docker compose stop watcher ingester rag-server
docker compose run --rm rag-server python -m core.embedding_migration sync --model $M
docker compose run --rm rag-server python -m core.embedding_migration switch --model $M
# 3. Set EMBEDDING_MODEL=$M in .env, then restart on the new model.
docker compose up -d rag-server watcher   # + ingester if you run connectors
```

Step 1 is the long one. Docker on a Mac has no GPU and EmbeddingGemma 2
embeds ~3 chunks/s there (MiniLM: ~60/s), so ~40 000 chunks take hours. On
Apple Silicon, run `sync` on the host instead, against the same Qdrant, with
`--device mps` (~17 chunks/s, 44 min for 43 500 points). Use a Python env with
`packages/core` and `sentence-transformers[image]>=6.1` installed:

```bash
QDRANT_HOST=localhost QDRANT_API_KEY=$QDRANT_API_KEY CODEBASE_PATH=/data/codebase \
  python -m core.embedding_migration sync --model $M --device mps
```

`CODEBASE_PATH` must be the container path even on the host: it only strips
the prefix stored in each point's `file_path` to build the document title.

The first `switch` snapshots each original collection before deleting it
(an alias cannot share a collection's name). Going back is `switch --model
<previous>` once a copy for it exists, which only moves the alias.
