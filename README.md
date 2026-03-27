<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/logo-light.svg">
    <img alt="Mnemos" src="docs/logo-light.svg" width="400">
  </picture>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> &middot;
  <a href="#features">Features</a> &middot;
  <a href="#mcp-integration">MCP Integration</a> &middot;
  <a href="#tech-stack">Tech Stack</a>
</p>

---

Mnemos is an MCP server that brings RAG-powered context retrieval to your AI coding agents. It runs entirely locally, indexes your files automatically, and integrates with any MCP-compatible client like Claude Code or Claude Desktop.

## Features

### Semantic Search
- **Multi-collection search** -- Search across code, docs, skills, and memory simultaneously
- **Code-aware search** -- Filter by language, symbol type, and project
- **Skill discovery** -- Find relevant agent skills by semantic similarity
- **Memory search** -- Query approved conversation memory entries

### Automatic Indexing
- **File watcher** -- Monitors your codebase and config directories, indexes changes on the fly
- **Language-aware chunking** -- Go files split by declarations, Vue by sections, Markdown by headings
- **Incremental reindex** -- Only re-processes changed files
- **Push API** -- Send file content directly for CI/CD integration

### Memory Management
- **Conversation memory** -- Store decisions, patterns, and lessons learned
- **Approval workflow** -- New memories start as pending, require review before surfacing in search
- **Project scoping** -- Tag memories by project for targeted retrieval

### Deployed Mode
- **Multi-tenant** -- API key auth with per-tenant collection prefixes
- **Push-only indexing** -- No filesystem access required, files sent via API
- **GitHub Actions** -- Reusable workflow to sync collections on every commit

## Quick Start

### Prerequisites

- Docker & Docker Compose

### Development

```bash
docker compose up -d
```

This starts three services:
- `qdrant` -- Vector database on port 6333
- `mnemos-server` -- FastAPI + MCP server on port 8100
- `watcher` -- File system watcher that auto-indexes changes

Open the MCP endpoint at `http://localhost:8100/mcp`.

### Initial Reindex

The watcher only picks up changes. To index existing content:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -e cli/

rag reindex --collection rag_skills --path /data/claude-config/skills --full
rag reindex --collection rag_docs --path /data/claude-config/docs --full
rag reindex --collection rag_code_moby --path /data/codebase/moby --full
```

> **Note:** Paths are **container paths**. By default `~/Developments/Projects/digital-gigafactory` maps to `/data/codebase` and `~/.claude` maps to `/data/claude-config`. Edit `docker-compose.yml` to change.

### Verify

```bash
rag status
```

## MCP Integration

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

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `rag_search` | Semantic search across all collections |
| `rag_search_code` | Code search with language/symbol/project filters |
| `rag_search_skills` | Find relevant skills by semantic similarity |
| `rag_search_memory` | Search approved memory entries |
| `rag_index_memory` | Store a new memory entry (pending) |
| `rag_memory_list` | List memory entries by project or status |
| `rag_memory_review` | Approve or reject a pending entry |
| `rag_reindex` | Trigger collection reindexing |
| `rag_status` | Get status of all collections |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Server | FastAPI, MCP (Streamable HTTP), Python 3.12+ |
| Embeddings | `all-MiniLM-L6-v2` (384 dims, sentence-transformers) |
| Vector DB | Qdrant (cosine similarity) |
| Watcher | watchdog, debounced event batching |
| CLI | Click, Rich |
| Deployment | Docker Compose, multi-tenant auth |

## CLI Reference

```bash
pip install -e cli/
export RAG_URL=http://localhost:8100
```

### Search

```bash
rag search "authentication middleware pattern"
rag search "error handling" --collection rag_code_moby --file-type go --limit 10

rag search-code "JWT validation" --language go --symbol-type func --project moby
rag search-skills "golang microservice development"
```

### Indexing

```bash
rag reindex --collection rag_skills --path /data/claude-config/skills
rag reindex --collection rag_code_moby --path /data/codebase/moby --full
```

### Memory

```bash
rag memory list
rag memory list --status approved
rag memory add "Always use flat resource paths" --project moby --type decision --tags routing
rag memory approve <id>
rag memory reject <id>
```

## Collections

Six collections in Qdrant, all cosine similarity with 384-dimensional vectors:

| Collection | Source | Description |
|------------|--------|-------------|
| `rag_skills` | `~/.claude/skills/` | Agent skill definitions |
| `rag_docs` | `~/.claude/docs/` | Architecture & pattern docs |
| `rag_memory` | _(API)_ | Memory entries with approval workflow |
| `rag_code_moby` | `moby/` | Moby codebase (Go, Vue) |
| `rag_code_trevio` | `trevio/` | Trevio platform (Go, UI modules) |
| `rag_code_infra` | `infra/`, `github-cicd/` | Infrastructure & CI/CD |

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_HOST` | `localhost` | Qdrant hostname |
| `QDRANT_PORT` | `6333` | Qdrant port |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformers model |
| `CODEBASE_PATH` | `/data/codebase` | Root codebase path |
| `CLAUDE_CONFIG_PATH` | `/data/claude-config` | Claude config path |
| `RAG_MODE` | `local` | `local` or `deployed` |
| `RAG_AUTH_ENABLED` | `false` | API key authentication |
| `RAG_STATE_DIR` | `/data/state` | Persistent state directory |
| `RAG_SERVER_URL` | `http://rag-server:8100` | RAG server URL (watcher) |
| `WATCHER_DEBOUNCE_MS` | `2000` | Debounce delay in ms (watcher) |
| `RAG_URL` | `http://localhost:8100` | Mnemos server URL (CLI) |

### Deployed Mode

For shared servers or CI/CD push-based indexing:

```bash
cp docker-compose.prod.yml docker-compose.override.yml
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Configure tenants in `config/tenants.yaml`:

```yaml
tenants:
  tenant_yourname:
    api_key: "your-secret-key-here"
    collections_prefix: "yourname_"
    max_documents: 0
```

### Push API

```bash
# Index a file
curl -X POST http://your-server:8100/api/index \
  -H "Content-Type: application/json" \
  -d '{"file_path": "services/core/handler.go", "collection": "rag_code_moby", "content": "..."}'

# Delete from index
curl -X DELETE http://your-server:8100/api/index/rag_code_moby/services/core/handler.go
```

## Architecture

```
mnemos/
  packages/
    rag_core/       -- Shared library: models, embeddings, chunkers, indexer
  server/           -- FastAPI + MCP server (port 8100)
  watcher/          -- File watcher service (watchdog)
  cli/              -- Click CLI client
  config/           -- Tenant configuration
  scripts/          -- Init scripts
  tests/            -- Test suite
  docker-compose.yml      -- Local development stack
  docker-compose.prod.yml -- Production overrides
```

## Development

```bash
pip install pytest pytest-asyncio
pip install -e packages/rag_core/
pip install -r server/requirements.txt

pytest tests/
```

## License

MIT
