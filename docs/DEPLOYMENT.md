# Mnemos — Deployment

How to take Mnemos from `docker compose up` on your laptop to a shared
service serving multiple teams.

## Topology

```
                      ┌─────────────────────┐
                      │  Reverse proxy      │   (Caddy / nginx / Traefik)
                      │  - TLS termination  │
                      └──────────┬──────────┘
                                 │  Authorization: Bearer <api-key>
                                 ▼
                       ┌─────────────────────┐
                       │ rag-server (FastAPI)│  exposes :8100
                       │ MNEMOS_AUTH_ENABLED │
                       └──────────┬──────────┘
                                  │
              ┌───────────────────┼─────────────────────┐
              ▼                   ▼                     ▼
         ┌─────────┐         ┌──────────┐         ┌──────────┐
         │ Qdrant  │         │  Ollama   │         │ config + │
         │ persist │         │ (or API) │         │ API keys │
         └─────────┘         └──────────┘         │ (Qdrant) │
                                                  └──────────┘
```

Components:
- **rag-server** — FastAPI + MCP. The only public-facing service.
- **Qdrant** — internal, never expose port `6333` publicly, and protected by
  an API key (`QDRANT_API_KEY`): whoever reaches an open Qdrant reads and
  rewrites every collection without going through the server's API keys.
- **LLM provider** — Ollama on the host, or a cloud API.
- **Configuration and API keys** — stored in Qdrant (`mnemos_config`
  collection): connectors, projects, settings overrides, tenants. Edited from
  the dashboard or the API; every pod applies a change within
  `MNEMOS_CONFIG_POLL_SECONDS` (default 5). Files in `config/` are only
  imported at the first start. See
  [`CONFIGURATION.md`](CONFIGURATION.md#shared-configuration).
- **Hooks** — devs run `scripts/install-hooks.sh` locally (per-developer);
  the server doesn't host them. Four hooks: `pre-push` / `post-commit` for
  memory extraction, `post-merge` / `post-checkout` for incremental indexing.
- **Connectors** — Confluence, Jira and web crawlers that push into
  `mnemos_knowledge`. See [`CONNECTORS.md`](CONNECTORS.md).
- **Periodic reindex** — `scripts/nightly-reindex.sh` catches what the hooks
  cannot see (rebase, reset, edits outside git, repos cloned while the server
  was down). macOS: `scripts/install-nightly-reindex.sh` registers a launchd
  agent. Elsewhere, wire the same script to cron or a systemd timer.

---

## Single-team production

For a personal or single-team box, the default setup is fine. Settings go in
`.env` (`MNEMOS_RERANKER_ENABLED=true`, …); only the port bindings need an
override:

```yaml
# docker-compose.override.yml
services:
  rag-server:
    ports: !override
      - "127.0.0.1:8100:8100"     # localhost only — let the reverse proxy expose it
```

Then put Caddy in front:

```caddyfile
mnemos.internal {
  reverse_proxy 127.0.0.1:8100
}
```

---

## Authentication

Authentication is set in `.env`; `docker-compose.prod.yml` adds the
`config/tenants.yaml` mount (bootstrap source for the first tenants) and
disables the watcher:

```bash
cp config/tenants.example.yaml config/tenants.yaml   # then replace CHANGE_ME
openssl rand -hex 32                                 # the Qdrant key
```

```dotenv
# .env
MNEMOS_AUTH_ENABLED=true
QDRANT_API_KEY=<the key>
QDRANT__SERVICE__API_KEY=<the same key>
```

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

`QDRANT__SERVICE__API_KEY` is the key Qdrant requires, `QDRANT_API_KEY` the one
the server sends: they must be equal. Never leave `QDRANT__SERVICE__API_KEY`
empty, it locks every client out. The server refuses to start with auth on and
no Qdrant key. On Kubernetes, put the key in a Secret read by both pods, and
add a NetworkPolicy that only lets the Mnemos pods reach Qdrant.

`config/tenants.yaml` is gitignored on purpose — it holds your real API
keys. The template at `config/tenants.example.yaml` is what ships with
the repo. It is only an import source: at the first start, when the database
holds no tenant, each entry becomes an admin tenant with every scope. Instead
of the file you can set `MNEMOS_BOOTSTRAP_ADMIN_KEY`, which creates one tenant
`admin` with that key. With auth on and neither, the server refuses to start.
After the first start, manage keys from the dashboard's Tenants page (or the
API below) and delete the file and the variable.

With `MNEMOS_AUTH_ENABLED=true`:
- Every path under `/api`, `/internal` and `/mcp` requires an
  `Authorization: Bearer <api-key>` header. A missing or unknown key gets a
  `401` with `WWW-Authenticate: Bearer`. `/health` and the dashboard's static
  files stay open.
- Keys are stored as SHA-256 hashes only; the server looks up the hash of the
  presented key.
- Each tenant has scopes (next section) that restrict what it reads and writes.

No codebase is mounted on a shared server, so content arrives through the
push API (CI, git hooks, connectors) rather than filesystem reindexing.

### Tenants and scopes

A tenant has an `admin` flag and two tag scopes, `read` and `write`, each
either `"*"` (every tag) or a list of tags. An empty `write` list makes a
read-only key.

| Operation | Rule |
|-----------|------|
| Search, list, facets on `mnemos_code`, `mnemos_knowledge`, `mnemos_memory` | Only items sharing at least one tag with `read` (a parent tag such as `acme` covers every chunk tagged with it) |
| Skills and docs | Readable by every tenant; writable only with `write: "*"` |
| Push with tags resolved by the server (no `tags` in the body) | One resolved tag in `write` is enough |
| Push or memory with tags declared by the client (`tags` override, memories, connector pages) | Every tag must be in `write` |
| `/api/reindex`, `/internal/reindex`, `/api/memory/extract`, MCP `mnemos_reindex` | `write: "*"` |
| Query log, `/api/eval/*`, settings, tenants | `admin` |
| `GET /api/connectors`, `GET /api/projects` | `admin` or `write: "*"` (the ingester's and scripts' key) |

A refused operation answers `403` (an `Error: …` text over MCP).

Tenant API (admin key; writes also need `MNEMOS_UI_CONFIG_WRITABLE=true`):

| Request | Effect |
|---------|--------|
| `GET /api/whoami` | Any key: its name, `admin`, `read`, `write` |
| `GET /api/tenants` | List tenants (never the key or its hash) |
| `POST /api/tenants` `{name, admin, read, write}` | Create; returns `api_key`, shown once |
| `PATCH /api/tenants/{name}` `{admin?, read?, write?}` | Change role or scopes |
| `POST /api/tenants/{name}/rotate` | New key, returned once; the old one stops working |
| `DELETE /api/tenants/{name}` | Revoke |

Mutations accept an optional `revision` (`409` if the tenants changed since).
Removing or demoting the last admin answers `409`. A revoked or rotated key
stops working on every pod within `MNEMOS_CONFIG_POLL_SECONDS`.

### Client configuration

| Client | Where the key goes |
|--------|--------------------|
| CLI (`mnemos …`, including `mnemos eval run`) | `MNEMOS_API_KEY` env var |
| Watcher container | `MNEMOS_API_KEY` in its environment |
| `scripts/reindex-all.py` | `MNEMOS_API_KEY` env var |
| Git hooks | `MNEMOS_API_KEY` env var or `~/.config/mnemos/config` (env wins). Passed to curl through a config on fd 3, so it never shows up in `ps` |
| Dashboard | Prompts for the key on the first `401` and keeps it in the browser's localStorage (`mnemos.apiKey`) |
| MCP clients | Must send the header themselves (below) |

Claude Code:

```bash
claude mcp add --transport http mnemos https://mnemos.example.com/mcp \
  --header "Authorization: Bearer <api-key>"
```

Or in a JSON MCP config:

```json
{
  "mcpServers": {
    "mnemos": {
      "type": "http",
      "url": "https://mnemos.example.com/mcp",
      "headers": { "Authorization": "Bearer <api-key>" }
    }
  }
}
```

Keys travel in a plain header: keep TLS in front of `rag-server`, which only
speaks HTTP.

### Push API

CI jobs and connectors push files directly — no shared filesystem needed:

```bash
curl -X POST https://mnemos.example.com/api/index \
  -H "Authorization: Bearer $MNEMOS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "services/core/handler.go",
    "collection": "mnemos_code",
    "content": "package core\nfunc Handle() { ... }",
    "tags": ["myproject", "go", "services"]
  }'

# External knowledge: source_url comes back as metadata.source_url in search results
curl -X POST https://mnemos.example.com/api/index \
  -H "Authorization: Bearer $MNEMOS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "confluence/ENG/12345.md",
    "collection": "mnemos_knowledge",
    "content": "# Release process\n…",
    "tags": ["confluence", "confluence:ENG"],
    "source_url": "https://wiki.example.com/pages/12345",
    "file_mtime": 1767225600
  }'

# Delete a file from the index when it's removed from the repo
curl -X DELETE \
  https://mnemos.example.com/api/index/mnemos_code/services/core/handler.go \
  -H "Authorization: Bearer $MNEMOS_API_KEY"

# List what is indexed under a tag, as {file_path: file_mtime} — a connector's sync state
curl "https://mnemos.example.com/api/index/mnemos_knowledge/files?tag=confluence:ENG" \
  -H "Authorization: Bearer $MNEMOS_API_KEY"
```

The optional `tags` array overrides the tags resolved from the projects
mapping, and then every tag must be in the key's `write` scope. When omitted,
the server resolves tags from the file path. See [`CONFIGURATION.md`](CONFIGURATION.md#tags--scoping-chunks-across-projects).
`mnemos_knowledge` uses `<source>` and `<source>:<scope>` tags
(`confluence:ENG`, `jira:OPS`, `web:docs.example.com`); the connectors in
[`CONNECTORS.md`](CONNECTORS.md) set them for you.

### GitHub Actions workflow

Sample CI step (one per repo):

```yaml
# .github/workflows/mnemos-sync.yml
on:
  push:
    branches: [main]

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Push changed files to Mnemos
        env:
          MNEMOS_URL:       ${{ secrets.MNEMOS_URL }}
          MNEMOS_API_KEY:   ${{ secrets.MNEMOS_API_KEY }}
        run: |
          git diff --name-status HEAD~1 HEAD | while read status path; do
            if [ "$status" = "D" ]; then
              curl -X DELETE \
                "$MNEMOS_URL/api/index/mnemos_code/$path" \
                -H "Authorization: Bearer $MNEMOS_API_KEY"
            else
              jq -n --arg fp "$path" \
                     --arg coll "mnemos_code" \
                     --rawfile content "$path" \
                     '{file_path:$fp, collection:$coll, content:$content}' \
              | curl -X POST "$MNEMOS_URL/api/index" \
                  -H "Authorization: Bearer $MNEMOS_API_KEY" \
                  -H "Content-Type: application/json" \
                  --data-binary @-
            fi
          done
```

---

## Persistence and backups

Two stateful volumes:
- `qdrant_data` — the vector store (every collection, every chunk).
- `rag_state` — Mnemos' state directory (query log, anything you add).

Back them up with `docker run --rm -v qdrant_data:/data:ro -v $(pwd):/backup \
busybox tar -czf /backup/qdrant-$(date +%F).tgz /data` or your container
backup tooling of choice.

Restore by stopping the stack, replacing the volume contents, and restarting.

---

## Scaling

Mnemos is not (yet) horizontally sharded, but `rag-server` runs as several
replicas: all configuration lives in Qdrant and each pod polls it every
`MNEMOS_CONFIG_POLL_SECONDS`, so a dashboard save on one pod reaches the
others within seconds. Two saves of the same page race on a `revision`
check (`409`, the dashboard reloads); the check and the write are not atomic
in Qdrant, which only matters for two admins saving in the same millisecond.

The ingester (`mnemos ingest serve`) must run **once**, not per server pod:
a single-replica Deployment with `strategy: Recreate`, or a CronJob running
`mnemos ingest run` with `concurrencyPolicy: Forbid`. Never a sidecar of a
multi-replica `rag-server`: each replica would sync every source. It reads
its sources from `GET /api/connectors`, so no ConfigMap is needed.

To scale up:

| Bottleneck | Scaling lever |
|------------|--------------|
| Embedding throughput | Pre-warm at startup (load model on lifespan), bump uvicorn workers. The model is shared across workers via `_model_cache`. |
| Reranker latency | GPU host, or use a smaller checkpoint. Latency drops 10-100× on GPU. |
| Qdrant size | Move to a dedicated Qdrant instance; mnemos-rag-server stays stateless apart from the LLM. |
| Memory extraction load | Route `POST /api/memory/extract` to a faster LLM provider (Groq, Anthropic Haiku). |
| LLM calls in CRAG | Disable grader / rewriter for low-stakes traffic; keep them on for `code_search` only via a thin wrapper. |

---

## Observability in prod

Turn on the query log:

```yaml
MNEMOS_QUERY_LOG_ENABLED: "true"
MNEMOS_QUERY_LOG_PATH: "/data/state/query-log.jsonl"
```

The file is JSONL — easy to feed to Loki, Datadog, BigQuery, or just
`jq` on the box. Example queries:

```bash
# p95 latency per intent over the last hour
jq -c 'select(.ts > (now - 3600))' /data/state/query-log.jsonl \
  | jq -s 'group_by(.intent)
           | map({intent: .[0].intent,
                  p95: (map(.latency_ms) | sort | .[(length*0.95|floor)])})'

# Cache hit rate per day
jq -c '. | select(.cache_hit != null)' /data/state/query-log.jsonl \
  | jq -s 'group_by(.ts / 86400 | floor)
           | map({day: .[0].ts | strftime("%Y-%m-%d"),
                  hits: map(select(.cache_hit==true)) | length,
                  total: length})'
```

---

## Security checklist

- [ ] `MNEMOS_AUTH_ENABLED=true` in production.
- [ ] Qdrant port `6333` not exposed publicly (only via the rag-server), and
      `QDRANT_API_KEY` set on both Qdrant and the server.
- [ ] API keys stored in a secret manager (HashiCorp Vault, GitHub Secrets,
      AWS Secrets Manager), **never** committed to git (`config/tenants.yaml`
      is gitignored; delete it after the first start); no `CHANGE_ME` key left
      (the server refuses to start).
- [ ] TLS in front (Caddy, nginx, ALB, …) — the rag-server only does
      plain HTTP, and API keys travel in the `Authorization` header.
- [ ] One key per client (team, CI, connector), scoped to its tags, so a
      leaked key can be revoked alone from the Tenants page.
- [ ] `MNEMOS_LLM_API_KEY` rotated periodically.
- [ ] Read-only mounts for `CODEBASE_PATH` and `CLAUDE_CONFIG_PATH` (already
      `:ro` in the default compose file).

---

## Upgrading

1. Read [`CHANGELOG`](CHANGELOG) — no, joke, there isn't one yet. Read git log.
2. `git pull` on the deployment server (or pull a new image tag).
3. `docker compose up -d --build rag-server watcher` — Qdrant doesn't need
   rebuild.
4. If the upgrade introduces a new collection schema, run
   `mnemos reindex --recreate --full --collection <name>` for each affected
   collection (Mnemos 0.x → 1.x did this for hybrid).
5. Run your eval suite: `mnemos eval run --tag post-upgrade` and `mnemos eval
   compare pre-upgrade post-upgrade`.

---

## Disaster recovery

| Disaster | Recovery |
|----------|---------|
| Qdrant disk corruption | Restore `qdrant_data` from backup or wipe + reindex (full re-extraction from `mnemos_memory` won't recover — see "Memories" below). |
| Lost collections | Re-trigger CI sync and connectors, or re-run `mnemos reindex --recreate --full` for code/docs, then ask devs to push commits to re-populate memories. |
| **Memories** | This is the one corpus that **can't be regenerated** from sources, only from git diffs. Keep regular backups of `qdrant_data`. |
| Lost LLM API key | Rotate; no data impact — only future calls fail. |

---

## Cost (rough order of magnitude)

For a 5-developer team, mid-size monorepo, US-east region:

| Component | Cost / month |
|-----------|--------------|
| 1× t3.large (rag-server + Qdrant + Ollama small) | ~$70 |
| 1× t3.medium (load balancer / proxy) | ~$30 |
| 50 GB EBS gp3 | ~$5 |
| Anthropic Haiku (memory extraction, ~30 commits/day × 5 devs) | ~$5 |
| **Total** | **~$110** |

A GPU host (e.g. g5.xlarge) for the reranker bumps this to ~$300/month
but makes the reranker latency negligible. Most teams don't need this
until they hit > 200 queries/hour.
