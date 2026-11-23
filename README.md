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
  <a href="https://github.com/jamyouss/mnemos/actions/workflows/test.yml"><img src="https://github.com/jamyouss/mnemos/actions/workflows/test.yml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/jamyouss/mnemos/actions/workflows/docker.yml"><img src="https://github.com/jamyouss/mnemos/actions/workflows/docker.yml/badge.svg" alt="Docker"></a>
  <a href="https://hub.docker.com/r/jamyouss/mnemos-server"><img src="https://img.shields.io/docker/v/jamyouss/mnemos-server?label=docker&sort=semver" alt="Docker image"></a>
  <a href="https://hub.docker.com/r/jamyouss/mnemos-server"><img src="https://img.shields.io/docker/pulls/jamyouss/mnemos-server" alt="Docker pulls"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-blue.svg" alt="Python 3.12+">
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
  patterns, and lessons from every diff via an LLM, dedupes them, and stores
  them, searchable right away; you reject the wrong ones. The agent recalls
  them on demand.
- 🔍 **Production-grade retrieval.** Hybrid BM25 + dense fusion is always on;
  the cross-encoder reranker, CRAG corrective loop and semantic cache are
  feature flags you switch on once measured.
- 🌳 **Code-aware chunking.** Tree-sitter for Go, SFC sections for Vue,
  heading splits for Markdown — your symbols stay intact across embeddings.
- 🪡 **MCP-native.** Point Claude Code at `http://localhost:8100/mcp/` and
  your agent gets 9 new tools immediately.
- 🏠 **100% self-hosted.** Qdrant + sentence-transformers + your choice of
  LLM (Ollama / Anthropic / any OpenAI-compatible endpoint). Nothing leaves
  your machine unless you point it at a cloud LLM.

> **Measured:** with the cross-encoder reranker on (it is off by default),
> Mnemos scored **+47% MRR** and **+37% NDCG@5** over plain dense retrieval
> on the same golden set. See [`docs/EVAL.md`](docs/EVAL.md) for the
> methodology and the per-phase numbers.

---

## 🎯 What makes Mnemos different

There are plenty of code-retrieval and AI-coding products out there. Mnemos
isn't trying to replace your editor or your code search — it sits behind
them and gives any MCP-compatible agent a sharper memory of your code, your
docs and your work history.

What's distinctive about the bundle Mnemos ships:

- **Memories extracted from your git history.** A pre-push hook reads each
  diff through an LLM, structures it into decisions / patterns / lessons,
  dedupes against what's already known, and stores it. The agent recalls
  those memories on demand. This pipeline — git → LLM → dedup → searchable,
  reviewable memory — isn't something we've seen in other coding tools.

- **Every memory stays reviewable.** A memory is searchable as soon as it is
  written, and keeps a status: reject a wrong one from the dashboard's
  Memories page or `mnemos memory reject`, and it leaves search. You get
  automatic capture without trusting an LLM blindly.

- **Agent-skill and agent-doc indexing as first-class citizens.** Alongside
  your code, Mnemos indexes your `~/.claude/skills/` and `~/.claude/docs/`
  collections, so an agent can ask "is there a skill for X?" or "what's our
  internal convention on Y?" against the same retrieval pipeline.

- **MCP-native server, not a wrapper.** Drop the URL in your agent config
  and you get 9 tools immediately: cross-collection search, code-only
  search, skill search, memory search, memory mutations, reindex, status.

- **Production-grade retrieval, switched on once measured.** Hybrid BM25 +
  dense fusion is always on. Cross-encoder reranker, contextual chunking,
  CRAG corrective loop (grader + rewriter), MMR and semantic cache each sit
  behind a flag, editable live from the dashboard, so you change behaviour
  without breaking reproducibility.

- **Self-hosted, LLM-agnostic.** Qdrant + sentence-transformers + your
  choice of LLM (Ollama / Anthropic / any OpenAI-compatible endpoint,
  including vLLM, LM Studio, Groq, Together, OpenRouter). Nothing leaves
  your machine unless you point it at a cloud LLM.

- **Open eval harness with measured numbers.** Bench your own retrieval
  changes: `mnemos eval run` against a golden set, MRR / NDCG / R@k /
  P@k reported, runs diffable across configurations. The +47 % MRR
  headline comes from this same harness on the first (25-question) golden set.

---

## ✨ Features at a glance

<table>
<tr>
<td width="50%" valign="top">

### 🔍 Retrieval

- **Hybrid search** — Dense (`EMBEDDING_MODEL`, MiniLM by default) + Sparse (BM25)
  fused server-side by Qdrant's Reciprocal Rank Fusion, dense weighted 3:1
  (`MNEMOS_FUSION_DENSE_WEIGHT`)
- **Cross-encoder reranker** — `BAAI/bge-reranker-base` by default, swap to
  `bge-reranker-v2-m3` for max quality
- **MMR diversification** — Avoid near-duplicate results in your top-K
- **CRAG corrective loop** — Document grader + query rewriter retry when
  retrieval fails
- **Caller-chosen collections** — narrow a search to `mnemos_code`, `mnemos_docs`,
  `mnemos_skills`, `mnemos_knowledge` or `mnemos_memory`, or let it fan out
- **Semantic cache** — Cosine-similarity cache, wiped on every index write

</td>
<td width="50%" valign="top">

### 🧠 Memory

- **Auto-extraction** — Pre-push git hook → LLM analyses commit diffs →
  structured memories (decisions, patterns, conventions, lessons)
- **LLM-powered dedup** — Cosine ≥ `MNEMOS_DEDUP_THRESHOLD` (0.80 in
  `.env.example`) triggers merge or replace strategies
- **Reviewable** — searchable once written; a rejected memory leaves search
- **Tag-based scoping** — Multi-tag payload (`tags_any` / `tags_all`) for
  targeted recall across projects, technos, teams
- **Timestamped** — every memory carries its creation time

</td>
</tr>
<tr>
<td valign="top">

### 🌳 Indexing

- **Tree-sitter Go** — Functions, methods, types as discrete chunks
- **Vue SFC** — `<script>`, `<template>`, `<style>` sections separated
- **Markdown** — Heading-based splits preserve document structure
- **Unified ignore policy** — Vendored bundles (`.yarn/`, `.cjs`), build
  outputs (`dist/`, `.nuxt/`), generated reports — single source of truth
  enforced at every ingestion path ([see ARCHITECTURE](docs/ARCHITECTURE.md#ignored-paths))
- **Watcher** — Incremental indexing on file change (`watchdog`, debounced)
- **Fresh on `git pull`** — `post-merge` / `post-checkout` hooks push just the
  diff, so a pull never leaves the index quoting code you deleted
- **Push API** — CI/CD-friendly REST endpoint for headless ingestion
- **Contextual chunking** — Optional Anthropic-style preambles via LLM

</td>
<td valign="top">

### 🤖 Agent integration

- **MCP Streamable HTTP** — Native protocol, no shim
- **9 MCP tools** — `mnemos_search`, `mnemos_search_code`, `mnemos_memory`,
  `mnemos_reindex`, …
- **API-key auth with per-tag scopes** — Bearer keys (`MNEMOS_AUTH_ENABLED`)
  scoped by tag for read and write, push API, GitHub Actions workflow,
  connectors for Confluence, Jira, web pages and remote git repositories
- **Shared configuration** — connectors, projects, settings and API keys
  stored in Qdrant and edited from the dashboard; every server replica picks
  a change up within seconds
- **Pluggable LLM** — Ollama, Anthropic API (prompt caching!), or any
  OpenAI-compatible endpoint (vLLM, LM Studio, Groq, Together, OpenRouter…)
- **Observability** — Optional JSONL query log for debug + A/B analysis

</td>
</tr>
</table>

---

## 🚀 Quick start

```bash
# 1. Configure, then boot the stack — every setting lives in .env
cp .env.example .env                       # set MNEMOS_CODEBASE_HOST_PATH
cp config/projects.example.yaml config/projects.yaml   # mounted as a file
docker compose up -d
docker compose --profile llm up -d        # adds bundled Ollama
docker compose exec ollama ollama pull llama3.1:8b

# 2. Install the CLI
make install
source venv/bin/activate
export MNEMOS_URL=http://localhost:8100

# 3. Index your code into the single mnemos_code collection, tagged for scoping
mnemos reindex --full --collection mnemos_code \
                --path /data/codebase/myproject --tags myproject,go

# 4. Search it (optionally scope by tags — OR with --tags, AND with --tags-all)
mnemos search "JWT validation middleware"
mnemos search-code "JWT validation middleware" --tags myproject
mnemos search-code "JWT validation middleware" --tags-all myproject,go

# 5. Keep it fresh — index on pull, extract memories on push
./scripts/install-hooks.sh --global --codebase-root ~/code \
    --watch-index ~/code --watch ~/code/your-org

# 6. Plug it into Claude Code
claude mcp add --transport http --scope user mnemos http://localhost:8100/mcp/
```

🪤 **First reranker query takes longer** — the embedding model is baked into
the image, but if you enable the optional cross-encoder reranker
(`MNEMOS_RERANKER_ENABLED=true` in `.env`, or from the dashboard's
Configuration page once `MNEMOS_UI_CONFIG_WRITABLE=true`) it downloads
~1.1 GB the first time it's used.

Then open **http://localhost:8100/** for the dashboard — what is indexed, what
is being searched, what has been remembered, and how retrieval scored. See
**[docs/DASHBOARD.md](docs/DASHBOARD.md)**.

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
| **Deployment** | [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Authentication, push API, CI/CD |
| **Dashboard** | [`docs/DASHBOARD.md`](docs/DASHBOARD.md) | Sign-in, every screen, what each page can edit |
| **Connectors** | [`docs/CONNECTORS.md`](docs/CONNECTORS.md) | Confluence, Jira and web → `mnemos_knowledge`; git repositories → `mnemos_code` |
| **Agent guide** | [`CLAUDE.md`](CLAUDE.md) | Instructions for Claude Code when touching this repo |

---

## 🛠 Tech stack

| Layer | What | Why |
|-------|------|-----|
| Server | FastAPI + MCP Streamable HTTP | Async, type-safe, native MCP |
| Vector DB | Qdrant (named hybrid vectors) | Single store for dense + sparse, server-side RRF + IDF |
| Embeddings | `EMBEDDING_MODEL`: `all-MiniLM-L6-v2` (default, 384d, fast on CPU) or `google/embeddinggemma-2` (768d, ~2× code-search MRR, slower to index) | Vector size read from the model; switching migrates every collection (`python -m core.embedding_migration`) |
| Sparse | In-house BM25 encoder | No model download; camelCase/snake-aware tokenizer |
| Reranker | `BAAI/bge-reranker-base` (default) | Open-weight, SOTA on MS-MARCO |
| LLM | Pluggable (Ollama / Anthropic / OpenAI-compatible) | Use local for dev, cloud for prod |
| CLI | Click + Rich | Polished terminal UX |
| Tests | pytest (~950 tests) + Vitest for the dashboard | High-confidence refactors |

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

PRs welcome. The codebase is small and well-tested (~950 tests). Start
with the architecture doc, then poke around `packages/core/` — every
retrieval feature is a single file with its own tests.

For local dev:

```bash
pip install -e packages/core -e packages/eval -e packages/connectors -e cli/
pip install -r server/requirements.txt -r watcher/requirements.txt pytest pytest-asyncio
pytest tests/
(cd ui && npm ci && npm test)   # dashboard
```

---

## 🛣️ Next milestone

Only hybrid retrieval (dense + BM25) is on by default. Every other stage —
reranker, MMR, cache, grader, rewriter, contextual chunking — ships wired but
off, behind a flag you can flip from the dashboard. The cross-encoder
reranker was the biggest gain of the first campaign (**+47 % MRR** over plain
dense) at ~14 s p50 on CPU; capped at 256 tokens per pair and on top of
EmbeddingGemma 2, it now adds ~+0.10 MRR for ~0.8 s per query on a Mac CPU.
The next iteration turns measurements into defaults:

| Item | Why it matters | Status |
|------|---------------|--------|
| **Contextual chunking, second measurement** | The first run, on a noisy golden set, showed no gain. Worth measuring again on the cleaned set, through the Anthropic API with prompt caching. | 🔜 |
| **MMR, measured** | Ships wired but off. Turn it on by default only if it cuts near-duplicates in the top 5 without hurting NDCG@5. | 🔜 |
| **A/B-testing infra** | Sampling on variants per tenant, so future retrieval changes ship with data, not vibes. | 🔜 |

Track this list and the rolling design notes in
[`docs/ROADMAP.md`](docs/ROADMAP.md).

## License

MIT — see [LICENSE](LICENSE) for the boring legal bits.

<p align="center">
  <sub>Built for developers who want their AI to remember.</sub>
</p>
