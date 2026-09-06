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
# Core services — pulls jamyouss/mnemos-server + jamyouss/mnemos-watcher from Docker Hub.
docker compose up -d

# + local Ollama (recommended for first run)
docker compose --profile llm up -d

# Pull the default model
ollama pull llama3.1:8b
```

> **Building from source instead?** Layer in the dev override:
> ```bash
> docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
> ```
> Useful when you've modified the server/watcher Python code.

> **One-shot `docker run` (no compose)?**
> ```bash
> docker run -d --name mnemos-qdrant -p 6333:6333 qdrant/qdrant:latest
> docker run -d --name mnemos-server -p 8100:8100 \
>   --link mnemos-qdrant:qdrant \
>   -v ~/code:/data/codebase:ro \
>   -e QDRANT_HOST=qdrant \
>   jamyouss/mnemos-server:latest
> ```

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

# A specific project's code — single mnemos_code collection, tagged for scoping
mnemos reindex --recreate --full \
       --collection mnemos_code \
       --path /data/codebase/myproject \
       --tags myproject,go

# Index another project into the same collection (no --recreate this time)
mnemos reindex --full \
       --collection mnemos_code \
       --path /data/codebase/otherproject \
       --tags otherproject,vue3
```

The `--recreate` flag drops the collection before reindexing. **You need it
once per collection** to migrate to the hybrid (named-vector) schema. After
that, drop the flag for incremental re-runs. The `--tags` list is stored in
each chunk's `tags` payload — searches scope with `--tags` (OR) and
`--tags-all` (AND). Without `--tags`, Mnemos derives tags from
`config/projects.yaml` (or cumulative path segments by default). See
[`CONFIGURATION.md`](CONFIGURATION.md#tags--scoping-chunks-across-projects).

Monitor progress:

```bash
mnemos status
# Collections
# ─────────────────────────────────────────────────────
# mnemos_skills    737 points     status=green
# mnemos_docs       79 points     status=green
# mnemos_code    6755 points      status=green
```

## 5. Search it from the CLI

```bash
mnemos search "JWT validation middleware"
mnemos search-code "ride cancel" --tags myproject --language go
mnemos search-code "auth"        --tags-all myproject,vue3
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
   - `mnemos_status` shows the collection is empty or Mnemos is down, OR
   - The results are about something else entirely — judge the content, not the number

   Then use Grep / Glob / Read as usual.

   **On scores:** hybrid fusion puts a normal top-1 around **0.5–0.8**. `0.54` is
   typical, not weak. There is no absolute threshold that separates good from
   bad, so do not treat one. Compare results against each other and read what
   came back.

### When to skip Mnemos

- Reading a specific known file path → use Read directly
- Listing directory contents → use Glob directly
- Checking git state → use git commands directly
- Searching in files just created in current session (not yet indexed)

### Storing what you learn

`mnemos_memory` writes an entry. Use it when a session produced something a
future session would have to rediscover: a decision and its reasoning, a
non-obvious constraint, the root cause of a bug that cost real time.

- **`memory_type`**: `decision`, `pattern`, `lesson`, `convention`, `note`.
- **`tags`**: the scoping mechanism, and the only one. An untagged memory is
  findable by wording alone. Tag with the project plus any cross-cutting label
  (`vue3`, `auth`, `ci`) that a future search might start from.
- **Write the reasoning, not the conclusion.** "Chose X over Y because Z"
  survives; "we use X" does not, because nobody can tell whether it still
  applies.

⚠️ **A stored memory is not immediately searchable.** `mnemos_memory` writes
with status `pending`, and `mnemos_search_memory` only returns `approved`
entries. It waits for human review — `mnemos memory list` then
`mnemos memory approve <id>`. Do not store something and assume you can read it
back in the same session.

### Mnemos memory vs. built-in agent memory

They are not interchangeable, and the split matters:

| | Built-in memory | Mnemos |
|---|---|---|
| Scope | one working directory | global, sliced at query time by tags |
| Cost | loaded into context every session | nothing until you search |
| Retrieval | you read an index of titles | semantic search over full content |
| Review | written directly | `pending` → `approved` |

Built-in memory is **siloed per project directory**: what you learn in one repo
is invisible from another. It also grows the context of every session, so it
does not scale past a few dozen entries.

- **Built-in memory** → what must be present without being asked: the user's
  preferences, how they want you to work, standing constraints.
- **Mnemos** → what must be findable when the need arises: technical decisions,
  patterns, incident lessons — especially anything worth reaching for from a
  *different* project.

### Rationale

Mnemos is indexed with language-aware chunking (AST for Go/Vue, SFC for Vue,
headings for Markdown, column-aware for CSV/TSV), hybrid dense + BM25
retrieval, and memories auto-extracted from git history. A single
`mnemos_search_code` call often replaces 5-10 Grep invocations. Use it.
```

## 7. Turn on the git hooks (recommended)

```bash
./scripts/install-hooks.sh --global \
    --codebase-root ~/code \
    --watch-index ~/code \
    --watch ~/code/your-org
```

Four hooks, two jobs, two lists:

| Flag | List | Hooks | Effect |
|---|---|---|---|
| `--watch-index` | `~/.config/mnemos/index-repos` | `post-merge`, `post-checkout` | a `git pull` or branch switch reindexes just the files that changed |
| `--watch` | `~/.config/mnemos/repos` | `pre-push`, `post-commit` | a push extracts memories from the diff via the LLM |

Keep `--watch` narrower than `--watch-index`: indexing is a cheap HTTP POST
per changed file, memory extraction is one LLM call per trigger, and memories
stay useful only while they come from repos where real decisions get made.

`--codebase-root` is the host directory bind-mounted at `/data/codebase`
(it must match `MNEMOS_CODEBASE_HOST_PATH` in `.env`). It is persisted to
`~/.config/mnemos/config`, so hooks fired from a GUI git client or an IDE —
which never read your shell profile — still resolve container paths.

Memories land in `pending`; approve them with:

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
