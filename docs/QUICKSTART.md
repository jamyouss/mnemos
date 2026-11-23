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
git clone https://github.com/jamyouss/mnemos.git
cd mnemos
cp .env.example .env
cp config/projects.example.yaml config/projects.yaml
```

Edit `.env` to taste — the defaults are sensible for local dev (everything OFF
except the hybrid retrieval, which is always on). Every service reads it
through `env_file`, so a variable set in your shell in front of
`docker compose` never reaches the containers.

`config/projects.yaml` is bind-mounted as a file: copy it before the first
`docker compose up`, or Docker creates a directory in its place.

> Want to point Mnemos at your codebase? Set the host directories in `.env`:
> ```dotenv
> MNEMOS_CODEBASE_HOST_PATH=~/code
> MNEMOS_CLAUDE_CONFIG_HOST_PATH=~/.claude
> ```
> They are mounted read-only at `/data/codebase` and `/data/claude-config`
> in the `rag-server` and `watcher` containers.

## 2. Start the stack

```bash
# Core services — pulls jamyouss/mnemos-server + jamyouss/mnemos-watcher from Docker Hub.
docker compose up -d

# + local Ollama (recommended for first run)
docker compose --profile llm up -d

# Pull the default model into the bundled Ollama (host Ollama: `ollama pull llama3.1:8b`)
docker compose exec ollama ollama pull llama3.1:8b
```

> **Building from source instead?** Set `MNEMOS_SERVER_IMAGE`,
> `MNEMOS_WATCHER_IMAGE` and `MNEMOS_CLI_IMAGE` in `.env` to local tags
> (`mnemos-server:dev`, …) so a build never shadows the published images,
> then layer in the dev override:
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

Three containers come up (four with `--profile llm`, which adds
`mnemos-ollama-1`):
- `mnemos-qdrant-1` — vector DB on `localhost:6333`
- `mnemos-rag-server-1` — FastAPI + MCP server and dashboard on `localhost:8100`
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
mnemos reindex --full \
       --collection mnemos_skills \
       --path /data/claude-config/skills

# Markdown docs
mnemos reindex --full \
       --collection mnemos_docs \
       --path /data/claude-config/docs

# A specific project's code — single mnemos_code collection, tagged for scoping
mnemos reindex --full \
       --collection mnemos_code \
       --path /data/codebase/myproject \
       --tags myproject,go

# Index another project into the same collection
mnemos reindex --full \
       --collection mnemos_code \
       --path /data/codebase/otherproject \
       --tags otherproject,vue3
```

The server creates every collection with the hybrid (named-vector) schema at
start-up, so a fresh install needs no `--recreate`. That flag drops the
collection before reindexing: it only serves to migrate a collection created
before hybrid retrieval, and on `mnemos_code` it wipes every project, not just
the one under `--path`. The `--tags` list is stored in
each chunk's `tags` payload — searches scope with `--tags` (OR) and
`--tags-all` (AND). Without `--tags`, Mnemos derives tags from the projects
mapping (imported from `config/projects.yaml` at the first start, then edited
from the dashboard) or cumulative path segments by default. See
[`CONFIGURATION.md`](CONFIGURATION.md#tags--scoping-chunks-across-projects).

The reindex runs in the background on the server. Monitor progress:

```bash
mnemos status
# Status: healthy, then a Collections table: vectors, points and status of
# each collection — the point counts grow while the reindex runs.
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

Register the server once, for every project:

```bash
claude mcp add --transport http --scope user mnemos http://localhost:8100/mcp/
```

Or, for one repository, in its `.mcp.json`:

```json
{
  "mcpServers": {
    "mnemos": {
      "type": "http",
      "url": "http://localhost:8100/mcp/"
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
   - `mnemos_search` — general cross-collection search (code, docs, skills, knowledge; memory only on request)
   - `mnemos_search_skills` — finding relevant agent skills
   - `mnemos_search_memory` — past decisions, conventions, lessons learned
   - `mnemos_status` — check if Mnemos is available and collections are populated

2. **Fallback only if Mnemos returns no useful results**:
   - No results at all, OR
   - `mnemos_status` shows the collection is empty or Mnemos is down, OR
   - The results are about something else entirely — judge the content, not the number

   Then use Grep / Glob / Read as usual.

   **On scores:** they are rank-fusion scores, not similarities. With the
   default fusion (dense weighted 3:1) a top-1 lands between **0.75** (first
   in the dense ranking only) and **1.25** (first in both dense and BM25), so
   `0.75` is typical, not weak. There is no absolute threshold that separates
   good from bad, so do not treat one. Compare results against each other and
   read what came back.

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

A stored memory is searchable at once: `mnemos_memory` writes it `approved`,
merged into a near-duplicate when one exists. A human can still reject it
later (`mnemos memory reject`, or the dashboard's Memories page), which takes
it out of search.

### Mnemos memory vs. built-in agent memory

They are not interchangeable, and the split matters:

| | Built-in memory | Mnemos |
|---|---|---|
| Scope | one working directory | global, sliced at query time by tags |
| Cost | loaded into context every session | nothing until you search |
| Retrieval | you read an index of titles | semantic search over full content |
| Review | written directly | searchable once written; rejected entries leave search |

Built-in memory is **siloed per project directory**: what you learn in one repo
is invisible from another. It also grows the context of every session, so it
does not scale past a few dozen entries.

- **Built-in memory** → what must be present without being asked: the user's
  preferences, how they want you to work, standing constraints.
- **Mnemos** → what must be findable when the need arises: technical decisions,
  patterns, incident lessons — especially anything worth reaching for from a
  *different* project.

### Rationale

Mnemos is indexed with language-aware chunking (AST for Go, SFC sections for Vue,
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

Extracted memories are searchable as soon as they are stored. Prune the wrong
ones:

```bash
mnemos memory list --status approved   # what was stored (defaults to pending)
mnemos memory reject <id>              # take one out of search
```

The dashboard's Memories page does the same.

Details: [`MEMORY_PIPELINE.md`](MEMORY_PIPELINE.md).

## 8. Enable advanced retrieval features (optional)

Every advanced feature is off by default. Turn one on from the dashboard's
Configuration page (applies live, no restart; saving needs an admin key, or
auth off, and `MNEMOS_UI_CONFIG_WRITABLE=true`), or set it in `.env` and
recreate the server:

```bash
# .env
MNEMOS_RERANKER_ENABLED=true   # cross-encoder reranker (+47% MRR in the first campaign; ~0.8 s/query on CPU)
MNEMOS_CACHE_ENABLED=true      # semantic cache (instant replies for repeated queries)
```

```bash
docker compose up -d rag-server
```

A value saved from the dashboard wins over `.env`: reset it there for the
`.env` value to apply again.

Full reference: [`CONFIGURATION.md`](CONFIGURATION.md).

## Troubleshooting

### `mnemos eval generate` says "No chunks returned"
The collection is empty. Run `mnemos reindex` first.

### Reindex returns 0 points
The `--path` is a **container path**. Inspect what's actually mounted:
```bash
docker compose exec rag-server ls /data/codebase
```

### First reranked search is slow, subsequent ones are fast
The reranker model is downloaded (~1.1 GB, kept in the `hf_cache` volume) and
loaded on the first rerank, then stays in memory. The embedding model loads
at server start, before `/health` answers.

### `Path traversal detected`
Mnemos refuses to index paths outside the mounted directories. Either
update the bind mounts or move your code under one of the allowed roots.

### Container can't reach Ollama (`connection refused`)
With `MNEMOS_OLLAMA_URL` empty, the server tries the bundled `ollama` service,
then `host.docker.internal:11434`, then `localhost:11434`, and keeps the first
one with a model pulled. An Ollama installed on a Linux host is not reachable
as `host.docker.internal` by default: set `MNEMOS_OLLAMA_URL` in `.env` to the
host gateway IP (e.g. `http://172.17.0.1:11434`) and make Ollama listen on it.

### Slow reranker
Use a smaller checkpoint, in `.env` (or the Configuration page, applied at the
next restart):
```bash
MNEMOS_RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
```
then `docker compose up -d rag-server` (`docker compose restart rag-server`
after a Configuration-page save) — or move to a GPU host.

## Next steps

- Read [`MCP_INTEGRATION.md`](MCP_INTEGRATION.md) for other agent clients.
- Read [`CONFIGURATION.md`](CONFIGURATION.md) for every env var.
- Read [`ARCHITECTURE.md`](ARCHITECTURE.md) to understand the pipeline.
- Read [`EVALUATION.md`](EVALUATION.md) to measure your own setup.
