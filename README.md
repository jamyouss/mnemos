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

Mnemos is an intelligent memory layer for AI coding agents. It runs entirely locally, indexes your codebase automatically, extracts memories from your git history, and integrates with any MCP-compatible client like Claude Code or Claude Desktop.

## Features

### Semantic Search
- **Multi-collection search** -- Search across code, docs, skills, and memory simultaneously
- **Code-aware search** -- Filter by language, symbol type, and project
- **Skill discovery** -- Find relevant agent skills by semantic similarity
- **Memory search** -- Query approved conversation memory entries

### Smart Memory
- **Auto-extraction from commits** -- Ollama analyzes your git diffs to extract decisions, patterns, and lessons learned
- **Deduplication with merge** -- Similar memories are automatically detected and consolidated via LLM
- **Approval workflow** -- Extracted memories start as pending, require review before surfacing in search
- **Project scoping** -- Tag memories by project for targeted retrieval
- **Configurable strategy** -- Choose merge or replace for duplicate handling

### Automatic Indexing
- **File watcher** -- Monitors your codebase and config directories, indexes changes on the fly
- **Language-aware chunking** -- Go files split by declarations, Vue by sections, Markdown by headings
- **Incremental reindex** -- Only re-processes changed files
- **Push API** -- Send file content directly for CI/CD integration

### Deployed Mode
- **Multi-tenant** -- API key auth with per-tenant collection prefixes
- **Push-only indexing** -- No filesystem access required, files sent via API
- **GitHub Actions** -- Reusable workflow to sync collections on every commit

## Quick Start

### Prerequisites

- Docker & Docker Compose
- [Ollama](https://ollama.ai) (for memory extraction) or use the bundled service

### Development

```bash
# Start core services
docker compose up -d

# Optional: start with Ollama for memory extraction
docker compose --profile llm up -d

# Pull the default model (if using local Ollama)
ollama pull llama3.1:8b
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

mnemos reindex --collection mnemos_skills --path /data/claude-config/skills --full
mnemos reindex --collection mnemos_docs --path /data/claude-config/docs --full
mnemos reindex --collection mnemos_code_moby --path /data/codebase/moby --full
```

> **Note:** Paths are **container paths**. By default `~/Developments/Projects/digital-gigafactory` maps to `/data/codebase` and `~/.claude` maps to `/data/claude-config`. Edit `docker-compose.yml` to change.

### Verify

```bash
mnemos status
```

## Git Hooks Setup

Mnemos can automatically extract memories from your git commits using global hooks.

### Install

```bash
./scripts/install-hooks.sh --global --watch ~/Developments/Projects/digital-gigafactory
```

This:
1. Copies hooks to `~/.config/git/hooks/`
2. Sets `git config --global core.hooksPath`
3. Adds the path to `~/.config/mnemos/repos`

### How it works

- **pre-push** (default): analyzes all commits being pushed, extracts memories
- **post-commit**: analyzes each commit individually
- Hooks are **non-blocking** -- they fire and forget, never slow down git
- Hooks are **fail-safe** -- if Mnemos is unreachable, they silently succeed
- Hooks **chain** to repo-local hooks in `.git/hooks/` -- existing hooks still work
- Only triggers for repos listed in `~/.config/mnemos/repos`

### Configuration

```bash
# Change trigger mode (default: pre-push)
export MNEMOS_HOOK_TRIGGER=both  # or: pre-push, post-commit

# Change Mnemos server URL (default: http://localhost:8100)
export MNEMOS_URL=http://localhost:8100
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
| `mnemos_search` | Semantic search across all collections |
| `mnemos_search_code` | Code search with language/symbol/project filters |
| `mnemos_search_skills` | Find relevant skills by semantic similarity |
| `mnemos_search_memory` | Search approved memory entries |
| `mnemos_memory` | Store a new memory entry (with deduplication) |
| `mnemos_memory_list` | List memory entries by project or status |
| `mnemos_memory_review` | Approve or reject a pending entry |
| `mnemos_reindex` | Trigger collection reindexing |
| `mnemos_status` | Get status of all collections |

## Recommended Agent Instructions

To make your AI agent use Mnemos as the primary search mechanism, add the following to your `~/.claude/CLAUDE.md` (or equivalent agent config):

```markdown
## Mnemos MCP — Search Priority

**ALWAYS try Mnemos MCP tools before falling back to traditional search (Grep, Glob, Read).** Mnemos provides semantic search across your indexed codebase, docs, skills, and memory — it is faster and more relevant than text-based search for most questions.

### Search Order

1. **First**: Use Mnemos MCP tools based on intent:
   - `mnemos_search_code` — looking for functions, types, patterns, implementations
   - `mnemos_search` — general cross-collection search (docs + code + skills)
   - `mnemos_search_skills` — finding relevant agent skills
   - `mnemos_search_memory` — past decisions, conventions, lessons learned
   - `mnemos_status` — check if Mnemos is available and collections are populated

2. **Fallback only if Mnemos returns no useful results**:
   - No results at all, OR
   - Results have low relevance scores (< 0.5), OR
   - `mnemos_status` shows collection is empty or Mnemos is down

   Then use Grep / Glob / Read as usual.

3. **Always store insights**: After resolving a non-trivial question, use `mnemos_memory` to store the decision, pattern, or lesson — so future sessions benefit.

### When to skip Mnemos

- Reading a specific known file path → use Read directly
- Listing directory contents → use Glob directly
- Checking git state → use git commands directly
- Searching in files just created in current session (not yet indexed)
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Server | FastAPI, MCP (Streamable HTTP), Python 3.12+ |
| Embeddings | `all-MiniLM-L6-v2` (384 dims, sentence-transformers) |
| Vector DB | Qdrant (cosine similarity) |
| LLM | Ollama (`llama3.1:8b` default, configurable) |
| Watcher | watchdog, debounced event batching |
| CLI | Click, Rich |
| Deployment | Docker Compose, multi-tenant auth |

## CLI Reference

```bash
pip install -e cli/
export MNEMOS_URL=http://localhost:8100
```

### Search

```bash
mnemos search "authentication middleware pattern"
mnemos search "error handling" --collection mnemos_code_moby --file-type go --limit 10

mnemos search-code "JWT validation" --language go --symbol-type func --project moby
mnemos search-skills "golang microservice development"
```

### Indexing

```bash
mnemos reindex --collection mnemos_skills --path /data/claude-config/skills
mnemos reindex --collection mnemos_code_moby --path /data/codebase/moby --full
```

### Memory

```bash
mnemos memory list
mnemos memory list --status approved
mnemos memory add "Always use flat resource paths" --project moby --type decision --tags routing
mnemos memory approve <id>
mnemos memory reject <id>
```

## Collections

Six collections in Qdrant, all cosine similarity with 384-dimensional vectors:

| Collection | Source | Description |
|------------|--------|-------------|
| `mnemos_skills` | `~/.claude/skills/` | Agent skill definitions |
| `mnemos_docs` | `~/.claude/docs/` | Architecture & pattern docs |
| `mnemos_memory` | _(API / git hooks)_ | Memory entries with approval workflow |
| `mnemos_code_moby` | `moby/` | Moby codebase (Go, Vue) |
| `mnemos_code_trevio` | `trevio/` | Trevio platform (Go, UI modules) |
| `mnemos_code_infra` | `infra/`, `github-cicd/` | Infrastructure & CI/CD |

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_HOST` | `localhost` | Qdrant hostname |
| `QDRANT_PORT` | `6333` | Qdrant port |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformers model |
| `CODEBASE_PATH` | `/data/codebase` | Root codebase path |
| `CLAUDE_CONFIG_PATH` | `/data/claude-config` | Claude config path |
| `MNEMOS_MODE` | `local` | `local` or `deployed` |
| `MNEMOS_AUTH_ENABLED` | `false` | API key authentication |
| `MNEMOS_STATE_DIR` | `/data/state` | Persistent state directory |
| `MNEMOS_LLM_PROVIDER` | `ollama` | LLM provider |
| `MNEMOS_LLM_MODEL` | `llama3.1:8b` | Ollama model for extraction |
| `MNEMOS_OLLAMA_URL` | `http://localhost:11434` | Ollama API URL |
| `MNEMOS_DEDUP_THRESHOLD` | `0.85` | Cosine similarity threshold for dedup |
| `MNEMOS_DEDUP_STRATEGY` | `merge` | `merge` or `replace` |
| `MNEMOS_HOOK_TRIGGER` | `pre-push` | Git hook trigger mode |
| `MNEMOS_URL` | `http://localhost:8100` | Mnemos server URL (CLI/hooks) |
| `MNEMOS_SERVER_URL` | `http://rag-server:8100` | Internal server URL (watcher) |
| `WATCHER_DEBOUNCE_MS` | `2000` | Debounce delay in ms (watcher) |

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
  -d '{"file_path": "services/core/handler.go", "collection": "mnemos_code_moby", "content": "..."}'

# Delete from index
curl -X DELETE http://your-server:8100/api/index/mnemos_code_moby/services/core/handler.go
```

## Architecture

```
mnemos/
  packages/
    rag_core/       -- Shared library: models, embeddings, chunkers, indexer,
                       memory_extractor, deduplicator
  server/           -- FastAPI + MCP server (port 8100)
  watcher/          -- File watcher service (watchdog)
  cli/              -- Click CLI client
  config/           -- Tenant configuration
  scripts/
    hooks/          -- Global git hooks (post-commit, pre-push)
    install-hooks.sh -- Hook installer
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
