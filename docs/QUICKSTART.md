# Mnemos — Quick Start

This guide gets you from zero to a working Mnemos instance with one project
indexed and a Claude Code agent talking to it, in about 10 minutes.

## Prerequisites

- **Docker** + **Docker Compose** (Compose v2: `docker compose`, not `docker-compose`)
- **Python 3.12+** for the CLI
- **Ollama** for local LLM features (memory extraction, contextual chunking,
  grader, rewriter). Either install [Ollama](https://ollama.ai) on the host or
  use the bundled container via `--profile llm`.

Optional:
- An **Anthropic API key** if you want cloud-grade LLM features with
  prompt caching (especially fast contextual chunking).
- A **GPU** if you want the cross-encoder reranker to respond in milliseconds
  instead of seconds.

## 1. Clone and configure

```bash
git clone https://github.com/your-org/mnemos.git
cd mnemos
cp .env.example .env
```

Edit `.env` to taste — the defaults are sensible for local dev (everything OFF
except the hybrid retrieval, which is always on).

> Want to point Mnemos at your codebase? Edit `docker-compose.yml` and change
> the bind mounts for `rag-server` + `watcher`:
> ```yaml
> volumes:
>   - ~/code:/data/codebase:ro
>   - ~/.claude:/data/claude-config:ro
> ```
> Map whatever host directories you want to be available as `/data/codebase`
> inside the container.

## 2. Start the stack

```bash
# Core services
docker compose up -d

# + local Ollama (recommended for first run)
docker compose --profile llm up -d

# Pull the default model
ollama pull llama3.1:8b
```

Three containers come up:
- `mnemos-qdrant-1` — vector DB on `localhost:6333`
- `mnemos-rag-server-1` — FastAPI + MCP server on `localhost:8100`
- `mnemos-watcher-1` — filesystem watcher (incremental indexing)

Check health:

```bash
curl http://localhost:8100/health
# {"status":"healthy"}
```

## 3. Install the CLI

```bash
make install        # creates ./venv + installs all packages in editable mode
source venv/bin/activate
export MNEMOS_URL=http://localhost:8100
mnemos --help
```

## 4. Index your first project

The bundled `docker-compose.yml` mounts host directories as `/data/codebase`
and `/data/claude-config`. Paths passed to `mnemos reindex --path` are
**container paths**, not host paths.

```bash
# Skills (the agent's playbook directory)
mnemos reindex --recreate --full \
       --collection mnemos_skills \
       --path /data/claude-config/skills

# Markdown docs
mnemos reindex --recreate --full \
       --collection mnemos_docs \
       --path /data/claude-config/docs

# A specific project's code
mnemos reindex --recreate --full \
       --collection mnemos_code_myproject \
       --path /data/codebase/myproject
```

The `--recreate` flag drops the collection before reindexing. **You need it
once per collection** to migrate to the hybrid (named-vector) schema. After
that, drop the flag for incremental re-runs.

Monitor progress:

```bash
mnemos status
# Collections
# ─────────────────────────────────────────────────────
# mnemos_skills        737 points    status=green
# mnemos_docs           79 points    status=green
# mnemos_code_myproject    6755 points    status=green
```

## 5. Search it from the CLI

```bash
mnemos search "JWT validation middleware"
mnemos search-code "ride cancel" --project myproject --language go
mnemos search-skills "performance bottleneck"
mnemos search-memory "API routing decision"
```

## 6. Plug it into Claude Code

Add to `~/.claude/settings.json`:

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

Restart Claude Code. Your agent now has 9 new tools — try asking it
"can you search for X in mnemos and summarise?"

To make Claude use Mnemos by default before grep / glob / read, paste this
into your `~/.claude/CLAUDE.md`:

```markdown
## Mnemos MCP — Search Priority

Always try Mnemos MCP tools before Grep / Glob / Read.
Use mnemos_search_code for implementations, mnemos_search for cross-collection,
mnemos_search_skills for skills, mnemos_search_memory for past decisions.
Fall back to filesystem search only when Mnemos returns scores < 0.5.
```

## 7. Turn on memories from git (optional but recommended)

```bash
./scripts/install-hooks.sh --global --watch ~/code/your-org
```

Now every `git push` from a repo under that path triggers an async LLM
extraction. Memories land in `pending`; approve them with:

```bash
mnemos memory list                  # see what's queued
mnemos memory approve <id>          # accept one
mnemos memory reject <id>           # drop one
```

Details: [`MEMORY_PIPELINE.md`](MEMORY_PIPELINE.md).

## 8. Enable advanced retrieval features (optional)

Every advanced feature is off by default. Flip them via env vars and restart
the rag-server container:

```bash
# Cross-encoder reranker (+47% MRR vs baseline; 12-50s/query on CPU)
MNEMOS_RERANKER_ENABLED=true docker compose up -d rag-server

# Semantic cache (instant replies for repeated queries)
MNEMOS_CACHE_ENABLED=true docker compose up -d rag-server

# Semantic router (trim collections per query, faster)
MNEMOS_ROUTER_ENABLED=true docker compose up -d rag-server
```

Full reference: [`CONFIGURATION.md`](CONFIGURATION.md).

## Troubleshooting

### `mnemos eval generate` says "No chunks returned"
The collection is empty. Run `mnemos reindex` first.

### Reindex returns 0 points
The `--path` is a **container path**. Inspect what's actually mounted:
```bash
docker exec mnemos-rag-server-1 ls /data/codebase
```

### First search takes 60 s, subsequent ones are fast
That's the embedding (and reranker, if enabled) cold-starting. Subsequent
queries reuse the in-memory model.

### `Path traversal detected`
Mnemos refuses to index paths outside the mounted directories. Either
update the bind mounts or move your code under one of the allowed roots.

### Container can't reach Ollama (`connection refused`)
If you installed Ollama on the host (not via the `llm` profile), the
container needs to reach it via `host.docker.internal:11434` on Mac /
Windows, or the host gateway IP on Linux. Set `MNEMOS_OLLAMA_URL`
accordingly.

### Slow reranker
Use a smaller checkpoint:
```bash
MNEMOS_RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2 \
docker compose up -d rag-server
```
or move to a GPU host.

## Next steps

- Read [`MCP_INTEGRATION.md`](MCP_INTEGRATION.md) for other agent clients.
- Read [`CONFIGURATION.md`](CONFIGURATION.md) for every env var.
- Read [`ARCHITECTURE.md`](ARCHITECTURE.md) to understand the pipeline.
- Read [`EVALUATION.md`](EVALUATION.md) to measure your own setup.
