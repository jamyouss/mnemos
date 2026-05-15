# Mnemos — Deployment

How to take Mnemos from `docker compose up` on your laptop to a shared
service serving multiple teams.

## Topology

```
                      ┌─────────────────────┐
                      │  Reverse proxy      │   (Caddy / nginx / Traefik)
                      │  - TLS termination  │
                      │  - Auth gateway     │  (optional, on top of MNEMOS_AUTH_ENABLED)
                      └──────────┬──────────┘
                                 │
                                 ▼
                       ┌─────────────────────┐
                       │ rag-server (FastAPI)│  exposes :8100
                       │ MNEMOS_MODE=deployed│
                       └──────────┬──────────┘
                                  │
              ┌───────────────────┼─────────────────────┐
              ▼                   ▼                     ▼
         ┌─────────┐         ┌──────────┐         ┌──────────┐
         │ Qdrant  │         │  Ollama   │         │  Tenants │
         │ persist │         │ (or API) │         │ + Hooks  │
         └─────────┘         └──────────┘         └──────────┘
```

Components:
- **rag-server** — FastAPI + MCP. The only public-facing service.
- **Qdrant** — internal, never expose port `6333` publicly.
- **LLM provider** — Ollama on the host, or a cloud API. Configurable
  per-tenant.
- **Tenants config** — `config/tenants.yaml`, hot-reloaded.
- **Hooks** — devs install `scripts/hooks/pre-push` locally (per-developer);
  the server doesn't host them.

---

## Single-tenant production

For a personal or single-team box, the local-mode setup is fine.
Just lock it down:

```yaml
# docker-compose.override.yml
services:
  rag-server:
    environment:
      MNEMOS_MODE: local
      MNEMOS_RERANKER_ENABLED: "true"
      MNEMOS_CACHE_ENABLED: "true"
      MNEMOS_ROUTER_ENABLED: "true"
      MNEMOS_QUERY_LOG_ENABLED: "true"
    ports:
      - "127.0.0.1:8100:8100"     # localhost only — let the reverse proxy expose it
  qdrant:
    ports: []                     # remove from compose-up exposure
```

Then put Caddy in front:

```caddyfile
mnemos.internal {
  reverse_proxy 127.0.0.1:8100
}
```

---

## Multi-tenant deployed mode

The bundled `docker-compose.prod.yml` flips Mnemos into deployed mode:

```bash
cp docker-compose.prod.yml docker-compose.override.yml
docker compose up -d
```

Deployed mode:
- Requires an `Authorization: Bearer <api-key>` header on every request.
- Looks up the tenant by API key in `config/tenants.yaml`.
- Prefixes every collection with the tenant's namespace.
- Skips filesystem reindexing — only the push API (`POST /api/index`) and
  CI-driven sync are accepted (filesystem mounts are read-only or absent).

### `config/tenants.yaml`

```yaml
tenants:
  tenant_acme:
    api_key: "sk-acme-…"
    collections_prefix: "acme_"          # → acme_code_moby, acme_docs, …
    max_documents: 0                     # 0 = unlimited
  tenant_brand:
    api_key: "sk-brand-…"
    collections_prefix: "brand_"
    max_documents: 100000
```

Rotate keys by editing the file; restart `rag-server` to pick up changes.

### Push API

CI jobs push files directly — no shared filesystem needed:

```bash
curl -X POST https://mnemos.example.com/api/index \
  -H "Authorization: Bearer sk-acme-…" \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "services/core/handler.go",
    "collection": "acme_code_moby",
    "content": "package core\nfunc Handle() { ... }"
  }'

# Delete a file from the index when it's removed from the repo
curl -X DELETE \
  https://mnemos.example.com/api/index/acme_code_moby/services/core/handler.go \
  -H "Authorization: Bearer sk-acme-…"
```

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
                "$MNEMOS_URL/api/index/acme_code_moby/$path" \
                -H "Authorization: Bearer $MNEMOS_API_KEY"
            else
              jq -n --arg fp "$path" \
                     --arg coll "acme_code_moby" \
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

Mnemos is not (yet) horizontally sharded. To scale up:

| Bottleneck | Scaling lever |
|------------|--------------|
| Embedding throughput | Pre-warm at startup (load model on lifespan), bump uvicorn workers. The model is shared across workers via `_model_cache`. |
| Reranker latency | GPU host, or use a smaller checkpoint. Latency drops 10-100× on GPU. |
| Qdrant size | Move to a dedicated Qdrant instance; mnemos-rag-server stays stateless apart from the LLM. |
| Memory extraction load | Per-tenant: route `POST /api/memory/extract` to a faster LLM provider (Groq, Anthropic Haiku). |
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
- [ ] Qdrant port `6333` not exposed publicly (only via the rag-server).
- [ ] API keys stored in a secret manager (HashiCorp Vault, GitHub Secrets,
      AWS Secrets Manager), **never** committed to `tenants.yaml` in git.
- [ ] TLS in front (Caddy, nginx, ALB, …) — the rag-server only does
      plain HTTP.
- [ ] Per-tenant `max_documents` limit set to a sane value.
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
| Lost tenant's collections | Re-trigger their CI sync, or re-run `mnemos reindex --recreate --full` for code/docs, then ask devs to push commits to re-populate memories. |
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
