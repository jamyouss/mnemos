# Mnemos

Mnemos is an MCP server that brings RAG-powered context retrieval to your Claude agents — semantic search over your codebase, docs, and notes via embeddings + Qdrant.

## Architecture Overview

```
                     ┌─────────────────────┐
                     │   Claude Code / AI   │
                     │      (MCP client)    │
                     └──────────┬──────────┘
                                │ Streamable HTTP (MCP)
                     ┌──────────▼──────────┐
                     │   mnemos-server      │
                     │   :8100              │
                     │  FastAPI + MCP tools │
                     └──────────┬──────────┘
          ┌─────────────────────┼─────────────────────┐
          │                     │                     │
┌─────────▼────────┐  ┌─────────▼────────┐  ┌────────▼─────────┐
│  Qdrant :6333    │  │  watcher service │  │   mnemos CLI     │
│  Vector DB       │  │  (file watcher)  │  │  (local client)  │
└──────────────────┘  └──────────────────┘  └──────────────────┘
```

- **Embedding model**: `all-MiniLM-L6-v2` (384 dimensions, runs locally)
- **Vector DB**: Qdrant
- **Transport**: MCP over Streamable HTTP at `http://localhost:8100/mcp`

---

## Quick Start

### 1. Start the stack

```bash
docker compose up -d
```

This starts three services:
- `qdrant` — vector database on port 6333
- `mnemos-server` — FastAPI + MCP server on port 8100
- `watcher` — file system watcher that auto-indexes changes

By default the server mounts `~/Developments/Projects/digital-gigafactory` as the codebase and `~/.claude` as the config/skills directory. Edit `docker-compose.yml` to point these volumes at your paths.

### 2. Trigger an initial reindex

The watcher only picks up file changes. To index existing content, install and use the CLI:

```bash
# Create and activate a virtual environment (once)
python3 -m venv venv
source venv/bin/activate

# Install the CLI (once)
pip install -e cli/

# Reindex all skills
rag reindex --collection rag_skills --path /data/claude-config/skills --full

# Reindex documentation
rag reindex --collection rag_docs --path /data/claude-config/docs --full

# Reindex the moby codebase
rag reindex --collection rag_code_moby --path /data/codebase/moby --full
```

> **Note:** Paths must be **container paths** since the server reads files from its own filesystem.
>
> | Host path | Container path |
> |-----------|---------------|
> | `~/Developments/Projects/digital-gigafactory` | `/data/codebase` |
> | `~/.claude` | `/data/claude-config` |

### 3. Verify

```bash
rag status
```

Expected output:

```
Status: healthy
            Collections
┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━┓
┃ Collection        ┃ Vectors ┃ Points ┃ Status   ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━┩
│ rag_skills        │    120  │    120 │ green    │
│ rag_docs          │     45  │     45 │ green    │
│ rag_memory        │      0  │      0 │ green    │
│ rag_code_moby     │   8432  │   8432 │ green    │
│ rag_code_trevio   │   3210  │   3210 │ green    │
│ rag_code_infra    │    890  │    890 │ green    │
└───────────────────┴─────────┴────────┴──────────┘
```

---

## CLI Reference

### Installation

```bash
pip install -e cli/
```

The CLI reads `RAG_URL` from the environment (default: `http://localhost:8100`).

```bash
export RAG_URL=http://localhost:8100
```

### Commands

#### `rag status`

Show server health and collection vector counts.

```bash
rag status
```

#### `rag search`

Semantic search across all indexed collections.

```bash
rag search "authentication middleware pattern"

# Restrict to specific collections
rag search "error handling" --collection rag_code_moby --collection rag_docs

# Filter by file type and path
rag search "handler function" --file-type go --path-filter moby/services/core

# Control result count (default 5)
rag search "DDD aggregate" --limit 10
```

#### `rag search-code`

Code-specific search with language, symbol type, and project filters.

```bash
rag search-code "JWT token validation"

# Filter by language
rag search-code "event handler" --language go

# Filter by symbol type (func, type, method, etc.)
rag search-code "repository interface" --symbol-type type --language go

# Filter by project
rag search-code "useAuth composable" --language vue --project trevio

# Combine filters
rag search-code "rate limit" --language go --project moby --limit 3
```

#### `rag search-skills`

Find relevant skills by semantic similarity.

```bash
rag search-skills "golang microservice development"
rag search-skills "vue component patterns" --limit 5
```

#### `rag reindex`

Trigger a reindex operation on the server.

```bash
# Reindex a single file or directory
rag reindex --collection rag_skills --path /data/claude-config/skills

# Recursively reindex all files under a path
rag reindex --collection rag_code_moby --path /data/codebase/moby --full
```

#### `rag memory`

Manage conversation memory entries.

```bash
# List pending memory entries (default)
rag memory list

# List by status
rag memory list --status approved
rag memory list --status rejected

# Add a memory entry (status: approved)
rag memory add "Always use flat resource paths, never /admin prefix" \
  --project moby \
  --type decision \
  --tags routing --tags api

# Approve or reject a pending entry
rag memory approve <id>
rag memory reject <id>
```

---

## MCP Integration

Mnemos exposes an MCP endpoint over Streamable HTTP. Configure your AI assistant to connect to it.

### Claude Code (`~/.claude/settings.json`)

```json
{
  "mcpServers": {
    "mnemos": {
      "type": "url",
      "url": "http://localhost:8100/mcp"
    }
  }
}
```

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "mnemos": {
      "type": "url",
      "url": "http://localhost:8100/mcp"
    }
  }
}
```

After adding the configuration, restart the client. The server must be running before the client connects.

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `rag_search` | Semantic search across all collections |
| `rag_search_code` | Code search with language/symbol/project filters |
| `rag_search_skills` | Find relevant skills by semantic similarity |
| `rag_search_memory` | Search approved memory entries |
| `rag_index_memory` | Store a new memory entry (status: pending) |
| `rag_memory_list` | List memory entries filtered by project or status |
| `rag_memory_review` | Approve or reject a pending memory entry |
| `rag_reindex` | Trigger collection reindexing |
| `rag_status` | Get current status of all collections |

---

## Collection Structure

Six collections are maintained in Qdrant, all using cosine similarity with 384-dimensional vectors.

| Collection | Source Path | Description |
|------------|-------------|-------------|
| `rag_skills` | `~/.claude/skills/` | Agent skill definitions (metadata + instructions) |
| `rag_docs` | `~/.claude/docs/` | Architecture docs and pattern documentation |
| `rag_memory` | _(via API)_ | Conversation memory entries with approval workflow |
| `rag_code_moby` | `moby/` | Moby application codebase (Go, Vue) |
| `rag_code_trevio` | `trevio/` | Trevio platform codebase (Go modules, UI modules) |
| `rag_code_infra` | `infra/`, `github-cicd/` | Infrastructure and CI/CD configuration |

### Chunk Types

The indexer uses language-aware chunkers:

- **`.go` files** — chunked by top-level declarations (functions, types, methods)
- **`.vue` files** — chunked by component sections (script, template, style)
- **`.md` files** — chunked by heading sections
- **All other files** — fixed-size overlapping text windows

Each chunk stores metadata: `file_path`, `chunk_type`, `language`, `file_mtime`, `last_indexed_at`.

---

## Reindexing Strategies

### Automatic (watcher)

The `watcher` service monitors the codebase and config directories using `watchdog`. On any file create, modify, delete, or move event, it debounces (default 2s) and calls `/internal/reindex` on the server. Changes are indexed automatically with no manual steps.

Watched directories:
- `CODEBASE_PATH` (default: `~/Developments/Projects/digital-gigafactory`)
- `CLAUDE_CONFIG_PATH` (default: `~/.claude`)

Automatically ignored:
- `node_modules/`, `vendor/`, `.git/`, `dist/`, `build/`, `.nuxt/`, `.output/`, `__pycache__/`
- Extensions: `.min.js`, `.map`, `.lock`

### Manual via CLI

```bash
# Reindex a specific collection incrementally
rag reindex --collection rag_docs --path /data/claude-config/docs

# Full recursive reindex of a directory
rag reindex --collection rag_code_moby --path /data/codebase/moby --full
```

### Init Script

Run once to create all collections and prepare Qdrant:

```bash
python scripts/init_collections.py
```

The script is idempotent — it skips collections that already exist.

---

## Deployed Mode

For running on a server shared by multiple users or for CI/CD push-based indexing.

### Setup

1. Copy the production compose override:

```bash
cp docker-compose.prod.yml docker-compose.override.yml
```

2. Configure tenants in `config/tenants.yaml`:

```yaml
tenants:
  tenant_yourname:
    api_key: "your-secret-key-here"
    collections_prefix: "yourname_"
    max_documents: 0  # 0 = unlimited
```

3. Start with the production config:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

In deployed mode:
- `RAG_MODE=deployed` is set
- `RAG_AUTH_ENABLED=true` is set
- The `watcher` service is disabled (push-only indexing)
- Files are indexed via the push API

### Push API

Send file content directly to the server — no filesystem access required:

```bash
# Index a file
curl -X POST http://your-server:8100/api/index \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "moby/services/core/handler.go",
    "collection": "rag_code_moby",
    "content": "<file content here>"
  }'

# Delete a file from the index
curl -X DELETE http://your-server:8100/api/index/rag_code_moby/moby/services/core/handler.go
```

---

## GitHub Actions Integration

Use the push API to keep collections up to date on every commit.

### Reusable workflow (`.github/workflows/rag-sync.yml`)

```yaml
name: RAG Sync

on:
  workflow_call:
    inputs:
      collection:
        required: true
        type: string
      changed_files:
        required: true
        type: string  # JSON array of file paths
    secrets:
      RAG_URL:
        required: true
      RAG_API_KEY:
        required: true

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Push changed files to RAG index
        env:
          RAG_URL: ${{ secrets.RAG_URL }}
          RAG_API_KEY: ${{ secrets.RAG_API_KEY }}
          COLLECTION: ${{ inputs.collection }}
        run: |
          echo '${{ inputs.changed_files }}' | jq -r '.[]' | while read file; do
            if [ -f "$file" ]; then
              content=$(cat "$file")
              curl -sf -X POST "$RAG_URL/api/index" \
                -H "Authorization: Bearer $RAG_API_KEY" \
                -H "Content-Type: application/json" \
                -d "$(jq -n \
                  --arg fp "$file" \
                  --arg col "$COLLECTION" \
                  --arg ct "$content" \
                  '{file_path: $fp, collection: $col, content: $ct}')"
              echo "Indexed: $file"
            else
              curl -sf -X DELETE "$RAG_URL/api/index/$COLLECTION/$file" \
                -H "Authorization: Bearer $RAG_API_KEY"
              echo "Deleted: $file"
            fi
          done
```

### Per-repo caller example

```yaml
name: Sync to RAG

on:
  push:
    branches: [main]

jobs:
  detect-changes:
    runs-on: ubuntu-latest
    outputs:
      changed: ${{ steps.changes.outputs.files }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 2
      - id: changes
        run: |
          files=$(git diff --name-only HEAD~1 HEAD | jq -R -s -c 'split("\n") | map(select(. != ""))')
          echo "files=$files" >> $GITHUB_OUTPUT

  rag-sync:
    needs: detect-changes
    uses: ./.github/workflows/rag-sync.yml
    with:
      collection: rag_code_moby
      changed_files: ${{ needs.detect-changes.outputs.changed }}
    secrets:
      RAG_URL: ${{ secrets.RAG_URL }}
      RAG_API_KEY: ${{ secrets.RAG_API_KEY }}
```

---

## Environment Variables

### Server (`mnemos-server`)

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_HOST` | `localhost` | Qdrant hostname |
| `QDRANT_PORT` | `6333` | Qdrant port |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformers model name |
| `CODEBASE_PATH` | `/data/codebase` | Root path of the codebase to index |
| `CLAUDE_CONFIG_PATH` | `/data/claude-config` | Root path of Claude config (`~/.claude`) |
| `RAG_MODE` | `local` | `local` or `deployed` |
| `RAG_AUTH_ENABLED` | `false` | Enable API key authentication |
| `RAG_STATE_DIR` | `/data/state` | Directory for persistent state |

### Watcher (`watcher`)

| Variable | Default | Description |
|----------|---------|-------------|
| `RAG_SERVER_URL` | `http://rag-server:8100` | URL of the RAG server |
| `CODEBASE_PATH` | `/data/codebase` | Root path to watch |
| `CLAUDE_CONFIG_PATH` | `/data/claude-config` | Config path to watch |
| `WATCHER_DEBOUNCE_MS` | `2000` | Debounce delay before flushing events (ms) |

### CLI

| Variable | Default | Description |
|----------|---------|-------------|
| `RAG_URL` | `http://localhost:8100` | Base URL of the Mnemos server |

---

## Ignore Patterns

The watcher skips files and directories matching these patterns:

**Directory patterns** (any path component matching is skipped):
```
node_modules/
vendor/
.git/
dist/
build/
.nuxt/
.output/
__pycache__/
```

**File extensions** (files with these extensions are skipped):
```
.min.js
.map
.lock
```

These patterns apply to the watcher only. The CLI `reindex` command and push API do not filter automatically — callers are responsible for excluding irrelevant files.

---

## Development

### Running tests

```bash
# From the repo root
pip install pytest pytest-asyncio
pip install -e packages/rag_core/
pip install -r server/requirements.txt

pytest tests/
```

### Project structure

```
mnemos/
├── packages/
│   └── rag_core/           # Shared library: models, embeddings, chunkers, indexer
│       ├── chunkers/       # Language-aware chunkers (Go, Vue, Markdown, fallback)
│       ├── collections.py  # Collection definitions and path routing
│       ├── embeddings.py   # EmbeddingService (sentence-transformers)
│       ├── indexer.py      # Indexer: chunk, embed, upsert to Qdrant
│       └── models.py       # Shared Pydantic models
├── server/                 # FastAPI + MCP server (port 8100)
│   ├── api.py              # REST endpoints (search, reindex, memory, status)
│   ├── config.py           # Settings via pydantic-settings
│   ├── main.py             # App factory, MCP mount
│   ├── mcp_tools.py        # MCP tool definitions and dispatch
│   └── search.py           # SearchService
├── watcher/                # File watcher service
│   └── main.py             # watchdog-based observer with debouncing
├── cli/                    # Click CLI client
│   └── main.py             # Commands: search, search-code, search-skills, reindex, memory
├── config/
│   └── tenants.yaml        # Tenant config for deployed mode
├── scripts/
│   └── init_collections.py # One-time collection initialization
├── tests/                  # pytest test suite
├── docker-compose.yml      # Local development stack
└── docker-compose.prod.yml # Production overrides
```
