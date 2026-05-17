<p align="center">
  <img alt="Mnemos" src="https://raw.githubusercontent.com/digital-gigafactory/mnemos/main/docs/logo-light.svg" width="360">
</p>

<h3 align="center">The self-hosted memory layer for AI coding agents.</h3>

<p align="center">
  Code-aware indexing · State-of-the-art retrieval · Memories from your git history · MCP-native · Zero SaaS
</p>

<p align="center">
  <a href="https://github.com/digital-gigafactory/mnemos">GitHub</a> ·
  <a href="https://github.com/digital-gigafactory/mnemos/blob/main/docs/QUICKSTART.md">Quickstart</a> ·
  <a href="https://github.com/digital-gigafactory/mnemos/blob/main/docs/ARCHITECTURE.md">Architecture</a> ·
  <a href="https://github.com/digital-gigafactory/mnemos/blob/main/docs/EVAL.md">Benchmarks</a>
</p>

---

## What is this image

Mnemos is a self-hosted MCP server that gives AI coding agents (Claude Code,
Claude Desktop, any MCP client) a real memory of your codebase, your docs,
and your past decisions.

- 🧠 **Memories from your commits** — a git hook extracts decisions, patterns,
  and lessons via an LLM, dedupes them, and queues them for your approval.
- 🔍 **Production-grade retrieval** — hybrid BM25 + dense + RRF fusion,
  cross-encoder reranker, CRAG corrective loop, semantic router & cache.
- 🌳 **Code-aware chunking** — tree-sitter for Go, SFC sections for Vue,
  heading splits for Markdown. Symbols stay intact across embeddings.
- 🪡 **MCP-native** — `http://localhost:8100/mcp` exposes 9 tools to any
  MCP-compatible agent.
- 🏠 **100% self-hosted** — Qdrant + sentence-transformers + your choice of
  LLM (Ollama / Anthropic / any OpenAI-compatible endpoint).

> **Measured:** +47% MRR and +37% NDCG@5 over plain dense retrieval on the
> same golden set.

## Tags

| Tag | Meaning |
|-----|---------|
| `edge` | Latest commit on `main` (rebuilt on every push) |
| `1.2.3`, `1.2`, `1` | Semver tags from git releases |
| `latest` | Most recent semver release |

All tags are multi-arch (`linux/amd64` + `linux/arm64`).

## Quick start

Mnemos needs Qdrant beside it. Pull the project's compose file rather than
running this image standalone:

```bash
git clone https://github.com/digital-gigafactory/mnemos.git
cd mnemos
cp .env.minimal.example .env
docker compose up -d
```

Then point your MCP-compatible agent at `http://localhost:8100/mcp`.

### Run standalone (for debugging)

```bash
docker run --rm -p 8100:8100 \
  -e QDRANT_HOST=host.docker.internal \
  -e QDRANT_PORT=6333 \
  -e MNEMOS_AUTH_ENABLED=false \
  -v "$PWD:/data/codebase:ro" \
  jamyouss/mnemos-server:edge
```

## Key environment variables

| Variable | Default | What it does |
|----------|---------|--------------|
| `QDRANT_HOST` | `qdrant` | Qdrant service hostname |
| `QDRANT_PORT` | `6333` | Qdrant HTTP port |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Pre-baked in the image, instant cold start |
| `CODEBASE_PATH` | `/data/codebase` | Where the watcher reads source code |
| `MNEMOS_LLM_PROVIDER` | `ollama` | `ollama` / `anthropic` / `openai` |
| `MNEMOS_LLM_MODEL` | `llama3.1:8b` | Model for memory extraction & dedup |
| `MNEMOS_RERANKER_ENABLED` | `false` | Enable cross-encoder reranker (~280 MB downloaded on first use) |
| `MNEMOS_CONTEXTUAL_ENABLED` | `false` | Anthropic-style contextual chunking |
| `MNEMOS_AUTH_ENABLED` | `true` | Disable for local dev, enable in multi-tenant prod |

Full reference: [.env.example](https://github.com/digital-gigafactory/mnemos/blob/main/.env.example).

## Image facts

- **Size:** ~1.5 GB (CPU-only PyTorch, no CUDA, with the default embedding model pre-baked)
- **Base:** `python:3.12-slim`
- **Healthcheck:** `GET /health` every 30 s
- **License:** MIT

## Related image

- [`jamyouss/mnemos-watcher`](https://hub.docker.com/r/jamyouss/mnemos-watcher) —
  filesystem watcher that pushes incremental indexing requests to this server.

## Links

- **Source & issues:** https://github.com/digital-gigafactory/mnemos
- **Architecture:** [`docs/ARCHITECTURE.md`](https://github.com/digital-gigafactory/mnemos/blob/main/docs/ARCHITECTURE.md)
- **Retrieval pipeline:** [`docs/RETRIEVAL_PIPELINE.md`](https://github.com/digital-gigafactory/mnemos/blob/main/docs/RETRIEVAL_PIPELINE.md)
- **Memory pipeline:** [`docs/MEMORY_PIPELINE.md`](https://github.com/digital-gigafactory/mnemos/blob/main/docs/MEMORY_PIPELINE.md)
- **MCP integration:** [`docs/MCP_INTEGRATION.md`](https://github.com/digital-gigafactory/mnemos/blob/main/docs/MCP_INTEGRATION.md)
- **Benchmarks:** [`docs/EVAL.md`](https://github.com/digital-gigafactory/mnemos/blob/main/docs/EVAL.md)
