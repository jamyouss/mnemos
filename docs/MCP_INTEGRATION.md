# Mnemos — MCP Integration

Mnemos speaks **Streamable HTTP MCP** natively at `http://<host>:8100/mcp`.
Any MCP-compatible client can connect.

## Tools exposed

Once an MCP client is connected, the agent sees these 9 tools:

| Tool | Use it for | Returns |
|------|------------|---------|
| `mnemos_search` | Semantic search across all collections | List of `{file_path, score, content, collection}` |
| `mnemos_search_code` | Code-only search with `language` / `symbol_type` / `project` filters | List of code chunks with `symbol_name`, `package` |
| `mnemos_search_skills` | Find the most relevant skill by description | List of `{skill_name, description, instructions_preview}` |
| `mnemos_search_memory` | Recall past decisions and patterns | List of `{id, content, project, memory_type, tags}` |
| `mnemos_memory` | Store a new memory (goes to `pending`) | The new memory id |
| `mnemos_memory_list` | List memories by project / status | Paged list |
| `mnemos_memory_review` | Approve or reject a `pending` memory | Updated status |
| `mnemos_reindex` | Trigger a reindex of a collection | Job acknowledgment |
| `mnemos_status` | Health + collection counts | Stats |

## Claude Code

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

Restart Claude Code. Type `/mcp` to confirm Mnemos shows up as connected.

### Make Claude prefer Mnemos over Grep / Glob

Drop this in your `~/.claude/CLAUDE.md`:

```markdown
## Mnemos MCP — Search Priority

ALWAYS try Mnemos MCP tools before Grep / Glob / Read. Mnemos has language-aware
chunking, hybrid retrieval, and your indexed memory; it is faster and more
relevant than text search for most questions.

### Search order

1. **First**: pick the right Mnemos tool by intent:
   - `mnemos_search_code` — functions, types, implementations
   - `mnemos_search` — general cross-collection (docs + code + skills)
   - `mnemos_search_skills` — find the right agent skill
   - `mnemos_search_memory` — past decisions, conventions, lessons learned
   - `mnemos_status` — sanity check before assuming Mnemos is down
2. **Fallback to Grep/Glob/Read** only when:
   - Mnemos returns no results
   - Scores are below 0.5
   - `mnemos_status` shows an empty / unhealthy state
3. **Persist insights**: after resolving a non-trivial question,
   `mnemos_memory(content, project, memory_type)` to save it.

### When to skip Mnemos

- Reading a specific known file path → Read directly
- Listing directory contents → Glob directly
- Checking git state → use git
- Searching files just created in this session (not yet indexed)
```

## Claude Desktop

Add to `claude_desktop_config.json` (location varies by OS):

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

Restart Claude Desktop. The tools appear under the 🔌 plug icon.

## Continue.dev

Continue supports MCP context providers natively. In `~/.continue/config.json`:

```json
{
  "experimental": {
    "modelContextProtocolServers": [
      {
        "transport": {
          "type": "http",
          "url": "http://localhost:8100/mcp"
        }
      }
    ]
  }
}
```

## Cursor

Cursor doesn't speak MCP natively (as of writing). Workaround: run a thin
shim like [`mcp-bridge`](https://github.com/SecretiveShell/MCP-Bridge) and
point Cursor at the bridge's HTTP API.

## Codex / OpenAI Agents SDK

Both speak MCP via the `mcp` Python SDK. Inside an Agent:

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client("http://localhost:8100/mcp") as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool(
            "mnemos_search",
            {"query": "JWT validation", "limit": 5},
        )
        print(result.content)
```

## Generic HTTP fallback

If your agent has no MCP support at all, hit the REST API directly:

```bash
curl -X POST http://localhost:8100/api/search \
  -H "Content-Type: application/json" \
  -d '{"query":"JWT validation","limit":5}'
```

The REST endpoints mirror the MCP tools 1:1 — see [`CLI.md`](CLI.md) for the
full list.

## Multi-tenant deployments

When `MNEMOS_AUTH_ENABLED=true`, every MCP request must carry an
`Authorization: Bearer <api-key>` header. API keys are managed in
`config/tenants.yaml`; each tenant sees a prefixed view of the collections.
See [`DEPLOYMENT.md`](DEPLOYMENT.md).

## Debugging the MCP connection

```bash
# Is the server up?
curl http://localhost:8100/health

# Does the MCP endpoint respond?
curl -i http://localhost:8100/mcp
# Streamable HTTP MCP returns a 405 to bare GET — that's expected.
# A 503 means the server is up but the MCP session manager hasn't
# initialised yet (race during startup).

# Tail the server logs
docker compose logs -f rag-server
```

Common issues:

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| MCP tools never appear in Claude Code | Wrong URL / no restart | Restart the IDE after editing settings.json |
| `MCP server not initialised (503)` | Hit during a startup race | Wait ~5s and retry |
| `connection refused` from container | Trying to reach a host service from inside the container | Use `host.docker.internal` on Mac/Win, or set `MNEMOS_OLLAMA_URL` to the host IP |
| Tools listed but every call times out | Embedding / reranker cold-starting | First call loads the models (~30-60 s). Subsequent calls are fast. |
