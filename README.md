<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/logo-light.svg">
    <img alt="Mnemos" src="docs/logo-light.svg" width="420">
  </picture>
</p>

<h3 align="center">The self-hosted memory layer for AI coding agents.</h3>

<p align="center">
  Code-aware indexing &middot; State-of-the-art retrieval &middot; Memories from your git history &middot; MCP-native &middot; Zero SaaS
</p>

<p align="center">
  <a href="#-quick-start">Quick start</a> •
  <a href="#-what-makes-mnemos-different">Why Mnemos</a> •
  <a href="#-features-at-a-glance">Features</a> •
  <a href="docs/ARCHITECTURE.md">Architecture</a> •
  <a href="docs/EVAL.md">Benchmarks</a> •
  <a href="docs/ROADMAP.md">Roadmap</a>
</p>

---

## The problem

Your AI coding agent is brilliant in the moment and amnesic the next day.

It re-discovers the same conventions, re-explores the same modules, re-suggests
patterns you've already rejected. Every session starts from zero because the
agent has no memory of your codebase, your decisions, or your skills.

You can throw context at it — paste files, write CLAUDE.md, retry with a longer
window — but at scale you need something better: a **retrieval layer** that
turns your code, your docs, and your work history into something the agent can
search.

## The solution

**Mnemos** is that layer, self-hosted, and built specifically for coding agents.

- 🧠 **Memories from your commits.** A pre-push hook extracts decisions,
  patterns, and lessons from every diff via an LLM, dedupes them, and queues
  them for your approval. The agent recalls them on demand.
- 🔍 **Production-grade retrieval.** Hybrid BM25 + dense + RRF fusion,
  cross-encoder reranker, CRAG corrective loop, semantic router & cache.
  Every component is feature-flagged so you upgrade safely.
- 🌳 **Code-aware chunking.** Tree-sitter for Go, SFC sections for Vue,
  heading splits for Markdown — your symbols stay intact across embeddings.
- 🪡 **MCP-native.** Drop `http://localhost:8100/mcp` into your Claude Code
  config and your agent gets 9 new tools immediately.
- 🏠 **100% self-hosted.** Qdrant + sentence-transformers + your choice of
  LLM (Ollama / Anthropic / any OpenAI-compatible endpoint). Nothing leaves
  your machine.

> **Measured:** Mnemos full pipeline scores **+47% MRR** and **+37% NDCG@5**
> over plain dense retrieval on the same golden set. See [`docs/EVAL.md`](docs/EVAL.md)
> for the methodology and the per-phase numbers.

---

## 🎯 What makes Mnemos different

| | Mnemos | Cursor | GitHub Copilot | Continue.dev | Sourcegraph Cody | mem0 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Self-hosted (no SaaS)** | ✅ | ❌ | ❌ | ✅ | ⚠️ ent. | ✅ |
| **MCP server (native)** | ✅ | ❌ | ❌ | client | ❌ | partial |
| **Memories from git history** | ✅ **unique** | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Approval workflow on memories** | ✅ **unique** | ❌ | ❌ | ❌ | ❌ | ❌ |
| **AST-based code chunking** | ✅ | ?? | ?? | partial | ✅ | n/a |
| **Skill indexing for agents** | ✅ **unique** | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Hybrid BM25 + dense + RRF** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Cross-encoder reranker** | ✅ | ✅ | ✅ | ✅ | ✅ | partial |
| **CRAG corrective loop** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **LLM-pluggable** (Ollama/Anthropic/OpenAI) | ✅ | ❌ | ❌ | ✅ | ⚠️ | ✅ |
| **Multi-tenant deployable** | ✅ | n/a | n/a | ❌ | enterprise | ✅ |
| **Open eval harness shipped** | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ |

The combination is what's unique: **memory pipeline from git + agent-first
design + SOTA retrieval, all self-hosted**. Nobody else ships that bundle.

---

## ✨ Features at a glance

<table>
<tr>
<td width="50%" valign="top">

### 🔍 Retrieval

- **Hybrid search** — Dense (`all-MiniLM-L6-v2`) + Sparse (BM25) fused with
  Reciprocal Rank Fusion `k=60`
- **Cross-encoder reranker** — `BAAI/bge-reranker-base` by default, swap to
  `bge-reranker-v2-m3` for max quality
- **MMR diversification** — Avoid near-duplicate results in your top-K
- **CRAG corrective loop** — Document grader + query rewriter retry when
  retrieval fails
- **Semantic router** — Trim collections per query to cut latency
- **Semantic cache** — Cosine-similarity cache with automatic invalidation
  on reindex

</td>
<td width="50%" valign="top">

### 🧠 Memory

- **Auto-extraction** — Pre-push git hook → LLM analyses commit diffs →
  structured memories (decisions, patterns, conventions, lessons)
- **LLM-powered dedup** — Cosine ≥ 0.85 triggers merge or replace strategies
- **Approval workflow** — `pending → approved` gate keeps the search
  surface clean
- **Project scoping** — Tag memories per project for targeted recall
- **Temporal context** — Every memory carries its origin commit + timestamp

</td>
</tr>
<tr>
<td valign="top">

### 🌳 Indexing

- **Tree-sitter Go** — Functions, methods, types as discrete chunks
- **Vue SFC** — `<script>`, `<template>`, `<style>` sections separated
- **Markdown** — Heading-based splits preserve document structure
- **Watcher** — Incremental indexing on file change (`watchdog`, debounced)
- **Push API** — CI/CD-friendly REST endpoint for headless ingestion
- **Contextual chunking** — Optional Anthropic-style preambles via LLM

</td>
<td valign="top">

### 🤖 Agent integration

- **MCP Streamable HTTP** — Native protocol, no shim
- **9 MCP tools** — `mnemos_search`, `mnemos_search_code`, `mnemos_memory`,
  `mnemos_reindex`, …
- **Multi-tenant ready** — Per-team API keys, push-only mode, GitHub
  Actions workflow
- **Pluggable LLM** — Ollama, Anthropic API (prompt caching!), or any
  OpenAI-compatible endpoint (vLLM, LM Studio, Groq, Together, OpenRouter…)
- **Observability** — Optional JSONL query log for debug + A/B analysis

</td>
</tr>
</table>

---

## 🚀 Quick start

```bash
# 1. Boot the stack — pulls jamyouss/mnemos-server + mnemos-watcher from Docker Hub
docker compose up -d
docker compose --profile llm up -d        # adds bundled Ollama
ollama pull llama3.1:8b

# 2. Install the CLI
make install
source venv/bin/activate
export MNEMOS_URL=http://localhost:8100

# 3. Index your code
mnemos reindex --recreate --full --collection mnemos_code_myproject \
                --path /data/codebase/myproject

# 4. Search it
mnemos search "JWT validation middleware"

# 5. Plug it into Claude Code
# → add { "type": "url", "url": "http://localhost:8100/mcp" }
#   to your ~/.claude/settings.json mcpServers
```

🪤 **First run takes longer** — sentence-transformers downloads the embedding
model (~90 MB), and the optional reranker pulls another ~280 MB the first
time it's used.

→ Full setup guide: **[docs/QUICKSTART.md](docs/QUICKSTART.md)**

---

## 📚 Documentation

| Topic | File | What you'll find |
|------|------|---|
| **Setup** | [`docs/QUICKSTART.md`](docs/QUICKSTART.md) | First-run, reindex, troubleshooting |
| **Configuration** | [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) | Every env var with examples |
| **MCP integration** | [`docs/MCP_INTEGRATION.md`](docs/MCP_INTEGRATION.md) | Wire Claude Code, Claude Desktop, Continue, Cursor |
| **Retrieval pipeline** | [`docs/RETRIEVAL_PIPELINE.md`](docs/RETRIEVAL_PIPELINE.md) | How a query flows through the system |
| **Memory pipeline** | [`docs/MEMORY_PIPELINE.md`](docs/MEMORY_PIPELINE.md) | Git hooks → LLM → dedup → review |
| **CLI reference** | [`docs/CLI.md`](docs/CLI.md) | Every `mnemos` command + flags |
| **Architecture** | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Modules, data flow, schemas |
| **Evaluation** | [`docs/EVALUATION.md`](docs/EVALUATION.md) | Run your own eval, interpret metrics |
| **Results history** | [`docs/EVAL.md`](docs/EVAL.md) | Measured numbers per phase |
| **Roadmap** | [`docs/ROADMAP.md`](docs/ROADMAP.md) | What's next, design decisions |
| **Deployment** | [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Multi-tenant prod setup, push API, CI/CD |
| **Agent guide** | [`CLAUDE.md`](CLAUDE.md) | Instructions for Claude Code when touching this repo |

---

## 🛠 Tech stack

| Layer | What | Why |
|-------|------|-----|
| Server | FastAPI + MCP Streamable HTTP | Async, type-safe, native MCP |
| Vector DB | Qdrant (named hybrid vectors) | Single store for dense + sparse, server-side RRF + IDF |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` | 384d, normalised, fast on CPU |
| Sparse | In-house BM25 encoder | No model download; camelCase/snake-aware tokenizer |
| Reranker | `BAAI/bge-reranker-base` (default) | Open-weight, SOTA on MS-MARCO |
| LLM | Pluggable (Ollama / Anthropic / OpenAI-compatible) | Use local for dev, cloud for prod |
| CLI | Click + Rich | Polished terminal UX |
| Tests | pytest, 143 unit + integration | High-confidence refactors |

---

## 🧭 Where Mnemos sits

If you ship code with an AI agent, you're combining three things:

1. **Generation** — the model that writes the code (Claude / GPT / local LLM).
2. **Context retrieval** — what gets stuffed into the prompt (what file, what
   doc, what decision).
3. **Memory** — what should persist across sessions.

Most tools own #1 (Cursor, Copilot, Codeium) or part of #2 (Sourcegraph,
Continue). Almost nothing owns #3 outside conversational memory products
like mem0.

**Mnemos owns #2 and #3 specifically for developers**, sits behind any agent
via MCP, and refuses to bind you to any particular LLM provider.

---

## 🤝 Contributing

PRs welcome. The codebase is small and well-tested (143 unit tests). Start
with the architecture doc, then poke around `packages/core/` — every
retrieval feature is a single file with its own tests.

For local dev:

```bash
pip install -e packages/core packages/eval cli/
pytest tests/
```

---

## 🛣️ Next milestone

The current default-on production setup (hybrid + reranker + router + cache)
delivers **+47 % MRR** over plain dense retrieval on our golden set. The next
iteration tightens the corners:

| Item | Why it matters | Status |
|------|---------------|--------|
| **GPU host for the reranker** | `bge-reranker-base` on CPU takes ~14 s p50; on GPU it's ~250 ms. Unlocks the reranker as a default-on feature, not an opt-in. | 🔜 |
| **CRAG rewriter with a fast LLM** (Anthropic Haiku, Groq) | Currently the rewriter is wired but disabled because grader+rewriter on local Ollama pushes per-query latency past 100 s. With a cloud LLM it should land at +5–10 % recall on vague queries. | 🔜 |
| **`project_hint` from git hook → memory extractor** | Stops the LLM from inferring (and occasionally hallucinating) the `project` field. The hook already knows the repo it's running in; pass it through. | 🔜 |
| **Full re-index after the `_should_skip` fix** | All 4 source collections (`mnemos_skills`, `mnemos_docs`, `mnemos_code_myproject`, `mnemos_code_otherproject`) were indexed before the latest skip rules. A full re-index purges accidentally-included junk paths. | 🔜 |
| **`contextual chunking` retry** with cleaner golden | First attempt showed no gain because the golden was noisy. Now that the golden is clean, contextual via Anthropic API (prompt caching) deserves a second measurement. | 🔜 |

Track this list and the rolling design notes in
[`docs/ROADMAP.md`](docs/ROADMAP.md).

## License

MIT — see [LICENSE](LICENSE) for the boring legal bits.

<p align="center">
  <sub>Built for developers who want their AI to remember.</sub>
</p>
