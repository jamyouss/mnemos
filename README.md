<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/logo-light.svg">
    <img alt="Mnemos" src="docs/logo-light.svg" width="400">
  </picture>
</p>

<p align="center">
  <b>The self-hosted memory layer for AI coding agents.</b>
</p>

<p align="center">
  <a href="#why-mnemos">Why</a> &middot;
  <a href="#whats-inside">What's inside</a> &middot;
  <a href="#quick-start">Quick start</a> &middot;
  <a href="#mcp-integration">MCP</a> &middot;
  <a href="#configuration">Configuration</a> &middot;
  <a href="#architecture">Architecture</a>
</p>

---

Mnemos turns your codebase, your docs, your skills, and your **git history** into a
queryable memory layer that any MCP-compatible agent (Claude Code, Claude Desktop,
Continue, Cursor via wrapper) can call.

- **Runs entirely on your machine.** No SaaS, no cloud calls — Qdrant + Ollama + sentence-transformers.
- **Code-aware indexing.** Tree-sitter chunking for Go, SFC sections for Vue, heading splits for Markdown.
- **Memories from git.** Pre-push hooks extract decisions, patterns, and lessons from every commit via an LLM, dedupe them, and surface them once you approve.
- **State-of-the-art retrieval.** Hybrid BM25 + dense + RRF fusion, cross-encoder reranker, CRAG corrective loop, semantic cache. All toggleable.
- **MCP-native.** Drop the endpoint into your Claude Code config and you're done.

## Why Mnemos

| | Mnemos | Cursor / Copilot | Continue.dev | Sourcegraph Cody | mem0 |
|---|---|---|---|---|---|
| **Self-hosted, zero SaaS** | ✅ | ❌ | ✅ | ⚠️ enterprise | ✅ |
| **MCP server (native)** | ✅ | ❌ | client only | ❌ | partial |
| **Memories from git history** | ✅ unique | ❌ | ❌ | ❌ | ❌ |
| **Approval workflow on memories** | ✅ unique | ❌ | ❌ | ❌ | ❌ |
| **Hybrid retrieval (BM25 + dense)** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Cross-encoder reranker** | ✅ optional | ✅ | ✅ | ✅ | partial |
| **CRAG corrective loop** | ✅ optional | ❌ | ❌ | ❌ | ❌ |
| **Code-aware AST chunking** | ✅ Go/Vue | ?? | partial | ✅ | n/a |
| **Skill indexing for agents** | ✅ unique | ❌ | ❌ | ❌ | ❌ |

If you ship code with an AI agent, want full data control, and care about the
agent _remembering_ the decisions you've already made — Mnemos is the only tool
that does all of that in one place.

## What's inside

```
Indexing pipeline
    file change  →  AST chunker  →  [contextual preamble*]  →  dense embed
                                                            →  BM25 sparse
                                                            →  Qdrant upsert
Retrieval pipeline
    query  →  [semantic router*]  →  hybrid query (dense + BM25, RRF k=60)
           →  [cross-encoder reranker*]  →  [MMR diversification*]
           →  [CRAG grader → query rewriter retry*]
           →  results
                ↑
           [semantic cache shortcut*]

Memory pipeline
    git commit  →  pre-push hook  →  LLM extraction  →  LLM dedup
                                  →  pending  →  human review  →  approved
                                  →  searchable via MCP

(* = togglable feature flag)
```

| Layer | Tech |
|-------|------|
| Server | FastAPI + MCP Streamable HTTP, Python 3.12+ |
| Vector DB | Qdrant (cosine, hybrid named vectors with `Modifier.IDF`) |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (384 dims, normalised) |
| Sparse | Lightweight BM25 encoder (camelCase / snake_case aware) |
| Reranker | `BAAI/bge-reranker-base` by default — swap to `bge-reranker-v2-m3` for max quality |
| LLM | Pluggable provider — Ollama (local), Anthropic API, or any OpenAI-compatible endpoint |
| Watcher | `watchdog`, debounced batching |
| CLI | Click + Rich |

## Quick start

### Prerequisites
- Docker & Docker Compose
- [Ollama](https://ollama.ai) (bundled) or an Anthropic / OpenAI-compatible API key

### Boot the stack
```bash
docker compose up -d                     # qdrant + rag-server + watcher
docker compose --profile llm up -d       # adds bundled ollama
ollama pull llama3.1:8b                  # default model for extraction / grading
```

Three containers start:
- `qdrant` — vector DB on `:6333`
- `mnemos-rag-server` — FastAPI + MCP on `:8100`
- `watcher` — incremental indexer

MCP endpoint: `http://localhost:8100/mcp`

### CLI
```bash
make install                             # creates ./venv + installs the CLI
source venv/bin/activate
export MNEMOS_URL=http://localhost:8100

mnemos status                            # collection counts
mnemos search "JWT validation"
mnemos search-code "ride cancel" --project moby --language go
mnemos memory list
```

### Initial reindex
The watcher only catches changes — the first time, you need to ingest:
```bash
mnemos reindex --recreate --full --collection mnemos_skills        --path /data/claude-config/skills
mnemos reindex --recreate --full --collection mnemos_docs          --path /data/claude-config/docs
mnemos reindex --recreate --full --collection mnemos_code_moby     --path /data/codebase/digital-gigafactory/moby
```

(`--recreate` migrates a legacy unnamed-dense collection to the hybrid schema; drop it after the first run.)

## Git memory hooks
Memories from your commits, with a review gate.

```bash
./scripts/install-hooks.sh --global --watch ~/Developments/Projects/your-org
```

What you get:
- **pre-push** hook (default): every `git push` triggers an async LLM extraction over the commits being pushed.
- Memories land in `mnemos_memory` with `status=pending`. Until you approve them, they're invisible to search.
- LLM dedup (cosine ≥ 0.85): similar memories are merged or replaced, your call (`MNEMOS_DEDUP_STRATEGY`).
- Hooks are **non-blocking** and **fail-safe**: if Mnemos is down, git never slows down.

Approve / reject:
```bash
mnemos memory list
mnemos memory approve <id>
mnemos memory reject <id>
```

## MCP integration

`~/.claude/settings.json` (Claude Code) or `claude_desktop_config.json` (Claude Desktop):

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

Once connected, every agent gets these tools:

| Tool | Use it for |
|------|------------|
| `mnemos_search` | Semantic search across all collections |
| `mnemos_search_code` | Code-only search with language / symbol / project filters |
| `mnemos_search_skills` | Find the right skill by description |
| `mnemos_search_memory` | Recall past decisions and patterns |
| `mnemos_memory` | Store a new memory (goes to `pending`) |
| `mnemos_memory_list` | List memories by project / status |
| `mnemos_memory_review` | Approve or reject |
| `mnemos_reindex` | Trigger a reindex |
| `mnemos_status` | Health + collection counts |

### Recommended agent prompt
Add this to your `~/.claude/CLAUDE.md` so the agent uses Mnemos first:

```markdown
## Mnemos MCP — Search Priority

ALWAYS try Mnemos MCP tools before falling back to Grep / Glob / Read. Mnemos has
language-aware chunking, hybrid retrieval, and your indexed memory; it's faster
and more relevant than text search for most questions.

1. mnemos_search_code  — functions, types, implementations
2. mnemos_search       — cross-collection (docs + code + skills)
3. mnemos_search_skills— find the right agent skill
4. mnemos_search_memory— past decisions, patterns, lessons learned

Fallback to Grep / Glob only when Mnemos returns nothing or scores < 0.5.
After resolving a non-trivial question, use `mnemos_memory` to persist the insight.
```

## Configuration

Every retrieval upgrade is **toggleable** so you can ship features one at a time
and roll them back instantly.

| Variable | Default | Phase | What it does |
|---|---|---|---|
| `MNEMOS_LLM_PROVIDER` | `ollama` | 1.5 | `ollama` / `anthropic` / `openai` |
| `MNEMOS_LLM_MODEL` | `llama3.1:8b` | 1.5 | Provider-specific model |
| `MNEMOS_LLM_API_KEY` | _empty_ | 1.5 | Required for `anthropic` / `openai` |
| `MNEMOS_LLM_BASE_URL` | _empty_ | 1.5 | Override URL — works with vLLM, LM Studio, Groq, Together, OpenRouter… |
| `MNEMOS_CONTEXTUAL_ENABLED` | `false` | 2A.2 | Prepend LLM preamble to every chunk (Anthropic-style) |
| `MNEMOS_RERANKER_ENABLED` | `false` | 2B | Apply a cross-encoder rerank on top-20 → top-K |
| `MNEMOS_RERANKER_MODEL` | `BAAI/bge-reranker-base` | 2B | Reranker checkpoint |
| `MNEMOS_MMR_ENABLED` | `false` | 2B | MMR diversification post-rerank |
| `MNEMOS_GRADER_ENABLED` | `false` | 3 | CRAG document grader (LLM judges each chunk) |
| `MNEMOS_REWRITER_ENABLED` | `false` | 3 | When grader fails, rewrite the query and retry |
| `MNEMOS_ROUTER_ENABLED` | `false` | 4D | Pick top-K relevant collections per query |
| `MNEMOS_CACHE_ENABLED` | `false` | 4E | Cosine-similarity result cache (invalidated on reindex) |
| `MNEMOS_QUERY_LOG_ENABLED` | `false` | 9 | Append every retrieval to a JSONL log |
| `MNEMOS_DEDUP_THRESHOLD` | `0.85` | 1 | Memory dedup cosine threshold |
| `MNEMOS_DEDUP_STRATEGY` | `merge` | 1 | `merge` or `replace` |

Full reference: [`.env.example`](.env.example). All flags are also surfaced via `docker-compose.yml`.

## Deploying for a team

```bash
cp docker-compose.prod.yml docker-compose.override.yml
docker compose up -d
```

Multi-tenant mode (per-team API keys, push-only indexing, GitHub Actions
workflow for CI sync) is configured in `config/tenants.yaml`. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the deployment model.

## Architecture

```
mnemos/
  packages/
    rag_core/                          shared library: indexing + retrieval
      chunkers/                          AST chunking per language
      llm/                               provider abstraction (ollama/anthropic/openai)
      sparse.py                          BM25 encoder
      contextual.py                      Anthropic-style preamble enricher
      reranker.py                        cross-encoder + MMR
      grader.py / rewriter.py            CRAG corrective loop
      router.py                          semantic query router
      cache.py                           cosine-similarity result cache
      observability.py                   JSONL query log
      memory_extractor.py / deduplicator.py
    mnemos_eval/                       evaluation harness (recall@k, MRR, NDCG@k, hit-rate)
  server/                              FastAPI + MCP Streamable HTTP
  watcher/                             filesystem watchdog
  cli/                                 Click + Rich
  scripts/hooks/                       global git hooks
  eval/                                golden set + run artefacts
  docs/
    ROADMAP.md                         CRAG/SOTA improvement plan
    EVAL.md                            measured retrieval metrics
```

## Evaluating

Mnemos ships its own harness so you can prove every change improves quality
before merging.

```bash
# Generate candidate questions from your indexed collections (semi-auto via LLM)
mnemos eval generate --collection mnemos_code_moby --count 10

# Review eval/dataset/_candidates.yaml, then:
mnemos eval promote

# Measure
mnemos eval run --tag baseline
mnemos eval run --tag with-reranker

# Diff
mnemos eval compare baseline with-reranker
```

Metrics: recall@k, precision@k, MRR, **NDCG@k**, hit-rate@k, p50/p95 latency.
Reports land in `eval/runs/<tag>.json` and a summary table in the terminal.

Current numbers and history live in [`docs/EVAL.md`](docs/EVAL.md).

## License

MIT
