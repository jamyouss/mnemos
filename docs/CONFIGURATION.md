# Mnemos — Configuration

Every Mnemos knob is an **environment variable**, passed through Docker
Compose. The full surface lives in [`.env.example`](../.env.example); this
doc explains what each variable does and when to flip it. A subset can also
be changed from the dashboard, see [Dashboard overrides](#dashboard-overrides).

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
| `QDRANT_API_KEY` | _(unset)_ | Sent to Qdrant as `api-key`. Must equal Qdrant's `QDRANT__SERVICE__API_KEY`. **Required when `MNEMOS_AUTH_ENABLED=true`**: the server refuses to start without it. Also read by `core.embedding_migration` and `scripts/purge-filtered.py` |
| `QDRANT_HTTPS` | `false` | Connect to Qdrant over TLS (Qdrant Cloud, a TLS-terminated Qdrant). qdrant-client would otherwise switch to TLS as soon as a key is set |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Any sentence-transformers model; vector size is read from the model. `google/embeddinggemma-2` is profiled (query prompts, `title: <path>` document framing, 512 tokens). **Change on an existing index → migrate**, see [Changing the embedding model](#changing-the-embedding-model). |
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
| `MNEMOS_RERANKER_MAX_LENGTH` | `256` | Tokens per (query, chunk) pair; `0` keeps the model's limit (8 192 for `bge-reranker-v2-m3`) |
| `MNEMOS_MMR_ENABLED` | `false` | MMR post-rerank for diversity |
| `MNEMOS_MMR_LAMBDA` | `0.5` | 0 = pure novelty, 1 = pure relevance |

With EmbeddingGemma 2 the reranker adds ~+0.10 MRR for ~0.8 s per query on a
Mac CPU (more inside Docker), so it stays off by default. Without the length
cap, `bge-reranker-v2-m3` reads whole chunks and took 68 s per query.

### Fusion and chunk size

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_FUSION_DENSE_WEIGHT` | `3.0` | Weight of the dense side in the dense/BM25 RRF fusion. `1.0` is plain RRF, which let BM25 noise outrank dense matches (MRR 0.70 vs 0.84, docs/EVAL.md) |
| `MNEMOS_MAX_CHUNK_CHARS` | `0` (off) | Upper bound on a chunk; longer ones are split into overlapping windows on line boundaries. Off by default: at 1 500 (≈ what the embedder reads) MRR fell 0.835 → 0.789, the extra chunks crowding out the right file (docs/EVAL.md). Changing it on an existing index needs a rebuild: `core.embedding_migration sync --max-chunk-chars N`, then `switch` with the same flag |

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
| `MNEMOS_DEDUP_THRESHOLD` | `0.80` | Cosine threshold above which a new memory merges into an existing one. Calibrated against a real corpus, not a constant — see below |
| `MNEMOS_DEDUP_STRATEGY` | `merge` | `merge` (LLM consolidates) or `replace` (newer wins) |
| `MNEMOS_HOOK_TRIGGER` | `pre-push` | Git hook trigger mode (used by `scripts/install-hooks.sh`) |

Memories are written **searchable** — `status: approved` — rather than held for
review. Gating them made the empty state the default: six days of extraction
produced fifteen entries and zero approvals, so nothing the pipeline learned
could be retrieved. No comparable system gates writes this way; Mem0 dropped
its write-time reconciliation pass in v3 and lets retrieval ranking sort
conflicting facts out at read time. `status` remains for taking a bad memory
back out, and `mnemos memory review` walks the queue for pruning.

On the dedup threshold: 0.85 was a guess. On a real 15-memory corpus the one
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
| `MNEMOS_QUERY_LOG_ENABLED` | `false` | Append every retrieval call to a JSONL log. **Required by the dashboard's Search screen** — without it there is nothing to display |
| `MNEMOS_EVAL_RUNS_PATH` | `/app/evals/runs` | Where `GET /api/eval/runs` reads eval runs. Bind-mounted read-only: runs are local artefacts, never baked into the image |
| `MNEMOS_UI_PATH` | `/app/ui` | Built dashboard. Absent → the server serves the API alone |
| `MNEMOS_UI_THEME_PATH` | `/app/config/theme.css` | Instance stylesheet served at `/theme.css`, see [Instance theme](#instance-theme) |
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

## Authentication

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_AUTH_ENABLED` | `false` | Require `Authorization: Bearer <api-key>` on every `/api`, `/internal` and `/mcp` path. `/health` and the dashboard files stay open |
| `MNEMOS_TENANTS_PATH` | `/data/config/tenants.yaml` | API keys, read once at startup (restart to apply changes) |
| `QDRANT_API_KEY` | _(unset)_ | Required with auth on, see [Storage](#storage-and-embeddings): API keys protect nothing if Qdrant itself is open |
| `MNEMOS_API_KEY` | _(unset)_ | Client side: the key sent by the CLI, watcher, `scripts/reindex-all.py` and git hooks |

Keys live in `config/tenants.yaml` (gitignored — copy
`config/tenants.example.yaml` and replace the `CHANGE_ME` keys). A tenant is
an identity only: all tenants share the same collections. See
[`DEPLOYMENT.md`](DEPLOYMENT.md#authentication).

## Knowledge connectors

Confluence spaces, Jira projects and web sites synced into `mnemos_knowledge`
are declared in `config/connectors.yaml` (gitignored — copy
`config/connectors.example.yaml`). The tokens stay in environment variables
named by each source's `token_env`.

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_CONNECTORS_CONFIG` | `config/connectors.yaml` (CLI) · `/app/config/connectors.yaml` (server) | File read by `mnemos ingest run` / `mnemos ingest serve`, and edited by the dashboard's Connectors page |

`mnemos ingest serve` (the opt-in `ingester` compose service) syncs at
start-up, then every `interval`, when the file changes, and on `SIGHUP`. The
server only edits the file (with `MNEMOS_UI_CONFIG_WRITABLE=true`); it never
syncs and never sees the tokens. See
[`CONNECTORS.md`](CONNECTORS.md#configuration-file).

## Dashboard overrides

| Var | Default | Description |
|-----|--------|------|
| `MNEMOS_UI_CONFIG_WRITABLE` | `false` | Let the dashboard save: settings overrides (`PATCH`/`DELETE /api/config`) and `connectors.yaml` (`PUT /api/connectors`). Off → both pages are read-only and writes answer `403` |

The dashboard edits a fixed set of settings — retrieval flags, cache, CRAG,
query log, contextual chunking, dedup, LLM provider/model, declared-paths
indexing. Everything else (API key, URLs, Qdrant, paths, auth, the embedding
model) stays env-only. `server/settings_registry.py` is the list.

**Precedence: override > env var > default.** Overrides are stored as
`{key: value}` JSON in `$MNEMOS_STATE_DIR/settings.json` (the `rag_state`
volume, `/data/state/settings.json` in compose), written atomically. They are
applied every time the server starts, so an override keeps winning over the
env var in `.env` until it is removed — from the page, or by deleting the key
from the file. A corrupt file, or a key that is unknown or not editable, is
logged as a warning and ignored; the server still boots.

How a change applies depends on the setting (`apply` in `GET /api/config`):

| `apply` | Effect |
|---------|--------|
| `live` | The running components switch at once, no restart |
| `restart` | Saved, effective at the next start; listed in `pending_restart` until then |
| `reindex` | Needs a re-embedding (the embedding model — env only, see below) |

`MNEMOS_LLM_API_KEY` is never returned by the API, not even masked: the page
only learns whether it is set.

> **Security.** `/api/config` and `/api/connectors` sit behind the API key when
> `MNEMOS_AUTH_ENABLED=true`. With auth off and `MNEMOS_UI_CONFIG_WRITABLE=true`,
> anyone who can reach port 8100 can change retrieval settings and point the
> ingester at other sites. Turn auth on first on anything but a private machine.

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

For most flags it's just an env var + a container restart (or the dashboard,
for [the editable ones](#dashboard-overrides) — an override in
`settings.json` wins over the env var):

```bash
MNEMOS_RERANKER_ENABLED=true docker compose up -d rag-server
```

For a contextual-chunking change, reindex with `--recreate`:

```bash
mnemos reindex --recreate --full --collection mnemos_code --tags myproject,go \
       --path /data/codebase/myproject
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
# 3. Restart on the new model (set EMBEDDING_MODEL in .env so it sticks).
EMBEDDING_MODEL=$M docker compose up -d rag-server watcher   # + ingester if you run connectors
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
