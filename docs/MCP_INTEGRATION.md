# Mnemos — MCP Integration

Mnemos speaks **Streamable HTTP MCP** natively at `http://<host>:8100/mcp/`.
Any MCP-compatible client can connect.

## Tools exposed

Once an MCP client is connected, the agent sees these 9 tools
(`server/mcp_tools.py`):

| Tool | Use it for | Returns |
|------|------------|---------|
| `mnemos_search` | Semantic search across every collection except `mnemos_memory` unless `collections` says otherwise. Filters: `collections`, `file_types`, `path_filter`, `tags_any` / `tags_all`; `limit` (5), `mode` (`preview` / `full`) | `{results, kept, dropped}`; each result `{content, file_path, score, chunk_type, collection, metadata}`, `mnemos_knowledge` hits carrying `metadata.source_url` |
| `mnemos_search_code` | Code-only search (`mnemos_code`) with `language` / `symbol_type` / `path_filter` / `tags_any` / `tags_all`; `limit` (3), `mode` | Same envelope; results add `language`, `symbol_name`, `package` |
| `mnemos_search_skills` | Find the most relevant skill by description; `limit` (3) | Same envelope; results `{skill_name, description, score, instructions_preview}` |
| `mnemos_search_memory` | Recall past decisions and patterns (`approved` only); `memory_type`, `tags_any` / `tags_all`, `limit` (5), `mode` | Same envelope; results `{id, content, project, topic, memory_type, tags, score, created_at}` |
| `mnemos_memory` | Store a memory (`content`; optional `memory_type` — default `note` —, `tags`, `project`), deduplicated against existing ones. Written `approved`: searchable at once | `{id, status, action}` (`inserted` / `merged` / `replaced`) |
| `mnemos_memory_list` | List memories, filtered by `project` / `status`; `limit` (20) | The raw entries, one call, no paging |
| `mnemos_memory_review` | `approve` or `reject` a memory by `memory_id`, whatever its current status | `{memory_id, action}` |
| `mnemos_reindex` | Re-index a file or a directory tree of `mnemos_code`, `mnemos_skills` or `mnemos_docs` (`collection`, optional server-side `path`, default the collection's root: the codebase mount, `skills/` or `docs/`). Runs in the background; needs a key that may write every tag; the path must sit under the codebase or Claude config mount. Clears the semantic cache when the run starts and ends | `{status: reindex_started, collection, path}` |
| `mnemos_status` | Qdrant health + per-collection counts | `{qdrant_healthy, collections: {name: {document_count, vector_size}}}` |

The search tools' responses are capped at `MNEMOS_RESPONSE_BUDGET_CHARS`
(32 000 characters): trailing results past it are dropped and counted in
`dropped`. `path_filter` takes one file or one directory, as a stored
container path: `/data/codebase/myproject/main.go` re-fetches that file,
`/data/codebase/myproject/api` keeps every file under that directory (whole
path segments: `api` does not match `api-old`).

## Claude Code

```bash
claude mcp add --transport http --scope user mnemos http://localhost:8100/mcp/
```

Or per project, in `.mcp.json` at the repository root:

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

Type `/mcp` in Claude Code to confirm Mnemos shows up as connected.

### Make Claude prefer Mnemos over Grep / Glob

Drop this in your `~/.claude/CLAUDE.md`:

```markdown
## Mnemos MCP — Search Priority

**ALWAYS try Mnemos MCP tools before falling back to traditional search (Grep, Glob, Read).** Mnemos provides semantic search across your indexed codebase, docs, skills, and memory — it is faster and more relevant than text-based search for most questions.

### Search Order

1. **First**: Use Mnemos MCP tools based on intent:
   - `mnemos_search_code` — looking for functions, types, patterns, implementations
   - `mnemos_search` — general cross-collection search (docs + code + skills + external knowledge)
   - `mnemos_search_skills` — finding relevant agent skills
   - `mnemos_search_memory` — past decisions, conventions, lessons learned
   - `mnemos_status` — check if Mnemos is available and collections are populated

2. **Fallback only if Mnemos returns no useful results**:
   - No results at all, OR
   - `mnemos_status` shows the collection is empty or Mnemos is down, OR
   - The results are about something else entirely — judge the content, not the number

   Then use Grep / Glob / Read as usual.

   **On scores:** they are rank-fusion sums, not similarities. With the default
   3:1 dense/BM25 fusion a normal top-1 sits around **0.75–1.25**; with the
   reranker on, scores are cross-encoder logits and can be negative. There is
   no absolute threshold that separates good from bad, so do not treat one.
   Compare results against each other and read what came back.

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

A stored memory is searchable immediately (status `approved`). A near
duplicate of an existing one is merged into it (or replaces it, with
`MNEMOS_DEDUP_STRATEGY=replace`) rather than added. A wrong memory is taken
back out with `mnemos_memory_review` (`reject`).

### Mnemos memory vs. built-in agent memory

They are not interchangeable, and the split matters:

| | Built-in memory | Mnemos |
|---|---|---|
| Scope | one working directory | global, sliced at query time by tags |
| Cost | loaded into context every session | nothing until you search |
| Retrieval | you read an index of titles | semantic search over full content |
| Curation | edited by hand | deduplicated on write, wrong entries rejected |

Built-in memory is **siloed per project directory**: what you learn in one repo
is invisible from another. It also grows the context of every session, so it
does not scale past a few dozen entries.

- **Built-in memory** → what must be present without being asked: the user's
  preferences, how they want you to work, standing constraints.
- **Mnemos** → what must be findable when the need arises: technical decisions,
  patterns, incident lessons — especially anything worth reaching for from a
  *different* project.

### Rationale

Mnemos is indexed with language-aware chunking (tree-sitter AST for Go, SFC
blocks for Vue, headings for Markdown, row groups for CSV/TSV), hybrid
dense + BM25 retrieval, and memories auto-extracted from git history. A
single `mnemos_search_code` call often replaces 5-10 Grep invocations. Use it.
```

## Claude Desktop

`claude_desktop_config.json` launches local (stdio) servers; reach the HTTP
endpoint through the `mcp-remote` bridge:

```json
{
  "mcpServers": {
    "mnemos": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://localhost:8100/mcp/"]
    }
  }
}
```

Restart Claude Desktop; the tools appear in the tools menu of the chat input.

## Continue.dev

In `~/.continue/config.yaml`:

```yaml
mcpServers:
  - name: mnemos
    type: streamable-http
    url: http://localhost:8100/mcp/
```

## Cursor

In `~/.cursor/mcp.json` (or `.cursor/mcp.json` in a project):

```json
{
  "mcpServers": {
    "mnemos": {
      "url": "http://localhost:8100/mcp/"
    }
  }
}
```

## Python (mcp SDK, OpenAI Agents SDK…)

Any Python agent can use the `mcp` SDK's Streamable HTTP client:

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client("http://localhost:8100/mcp/") as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool(
            "mnemos_search",
            {"query": "JWT validation", "limit": 5},
        )
        print(result.content)
```

Pass `headers={"Authorization": "Bearer <api-key>"}` to
`streamablehttp_client` on an authenticated server.

## Generic HTTP fallback

If your agent has no MCP support at all, hit the REST API directly:

```bash
curl -X POST http://localhost:8100/api/search \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $MNEMOS_API_KEY" \
  -d '{"query":"JWT validation","limit":5}'
```

The search tools have REST twins taking the same arguments:
`/api/search`, `/api/search-code`, `/api/search-skills`, `/api/search-memory`.
They answer `{"results": [...]}`, without the MCP response cap. Drop the
`Authorization` header on a server without auth.

## Authenticated servers

When `MNEMOS_AUTH_ENABLED=true`, every MCP request must carry an
`Authorization: Bearer <api-key>` header, which the MCP client sends itself.
Claude Code:

```bash
claude mcp add --transport http mnemos https://mnemos.example.com/mcp/ \
  --header "Authorization: Bearer <api-key>"
```

In a JSON config, add `"headers": {"Authorization": "Bearer <api-key>"}` to
the server entry. Keys are tenants managed from the dashboard's Tenants page
(or `/api/tenants`); each key reads only the tags in its `read` scope. See
[`DEPLOYMENT.md`](DEPLOYMENT.md#tenants-and-scopes).

## Debugging the MCP connection

```bash
# Is the server up?
curl http://localhost:8100/health

# Does the MCP endpoint respond?
curl -i http://localhost:8100/mcp/
# 406 "Client must accept text/event-stream": the endpoint is up — a bare
#     GET is not an MCP request, so that answer is expected.
# 401: auth is on, send the key (-H "Authorization: Bearer …").
# 404 / 405 from a server image older than this release: use …/mcp/ (with the slash).

# Tail the server logs
docker compose logs -f rag-server
```

Common issues:

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| MCP tools never appear | Wrong URL, or the client was not restarted | Check `…/mcp/`, then restart the client |
| `405 Method Not Allowed` / `404` on `…/mcp` | Server image older than this release: its dashboard caught `/mcp` without the slash | Use `…/mcp/`, or update the image |
| `401 Missing or invalid API key` | Server runs with `MNEMOS_AUTH_ENABLED=true` | Send `Authorization: Bearer <api-key>` |
| `MCP server not initialised (503)` | A request reached the app before its start-up finished | Wait a few seconds and retry |
| `connection refused` from container | Trying to reach a host service from inside the container | Use `host.docker.internal` on Mac/Win, or set `MNEMOS_OLLAMA_URL` to the host IP |
| First search with the reranker takes long | The cross-encoder loads lazily on its first use (~1.1 GB download the first time) | Subsequent calls are fast; the embedding model itself loads at start-up |
