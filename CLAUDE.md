# Mnemos — Claude Code Instructions

> Self-hosted memory layer for AI coding agents. Indexes codebase + docs + skills, extracts memories from git, exposes everything via MCP.

## Mission

Mnemos est un **RAG dev-centric self-hosted** qui sert de couche mémoire pour les agents AI (Claude Code, Claude Desktop). Il combine :
- **Code retrieval** : indexing AST-aware (Go via tree-sitter, Vue SFC, Markdown)
- **Memory pipeline** : extraction automatique depuis git, dedup LLM, workflow d'approbation
- **Agent context** : indexing des skills Claude et docs d'architecture

Exposé comme MCP server (Streamable HTTP) sur `localhost:8100/mcp`.

## Architecture

### Pipeline RAG actuel

```
INDEXING (watcher / push API / CLI):
  file → chunker (par extension) → embedder → Qdrant upsert
         AST Go (tree-sitter) | Vue SFC | MD headings | Fallback
         all-MiniLM-L6-v2 (384 dims, cosine, normalized)

RETRIEVAL (MCP / REST):
  query → embed → query_points(N collections séquentiel) → sort by score → top-K
         ⚠️ Pas de reranker, pas de hybrid, pas de query router

MEMORY (git hook / API):
  git commit → Ollama extract decisions/patterns/lessons →
  dedup (cos sim ≥ 0.85, merge ou replace) → pending → review → approved
```

### Stack technique

| Couche | Composant |
|---|---|
| Server | FastAPI + MCP Streamable HTTP, Python 3.12+ |
| Vector DB | Qdrant (cosine, 384 dims) |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` |
| LLM | Pluggable provider (ollama / anthropic / openai-compatible) — extraction + dedup merge, contextual chunking, grader, rewriter |
| Chunkers | tree-sitter (Go), regex SFC (Vue), heading-based (MD) |
| Watcher | `watchdog`, debounce 2s |
| CLI | `click` + `rich` |

### Structure dossiers

```
mnemos/
  packages/core/      # Lib partagée (chunkers, embeddings, indexer, dedup, memory_extractor)
  server/                 # FastAPI + MCP server (port 8100)
  watcher/                # File watcher (watchdog)
  cli/                    # Click CLI client
  config/                 # Tenant configuration
  scripts/hooks/          # Global git hooks (post-commit, pre-push)
  tests/                  # pytest suite (16 fichiers)
  eval/                   # Eval harness (Phase 1.2, en cours)
  docs/                   # ROADMAP.md, EVAL.md, ARCHITECTURE.md
  docker-compose.yml      # Local dev stack
  docker-compose.prod.yml # Production overrides multi-tenant
```

### Collections Qdrant

| Collection | Source | Path prefixes |
|---|---|---|
| `mnemos_skills` | `~/.claude/skills/` | `skills/` |
| `mnemos_docs` | `~/.claude/docs/` | `docs/` |
| `mnemos_memory` | API / git hooks | _(aucun, scoped par projet)_ |
| `mnemos_code_moby` | codebase | `moby/` |
| `mnemos_code_trevio` | codebase | `trevio/` |
| `mnemos_code_infra` | codebase | `infra/`, `github-cicd/` |

### MCP Tools exposés (9)

`mnemos_search`, `mnemos_search_code`, `mnemos_search_skills`, `mnemos_search_memory`, `mnemos_memory`, `mnemos_memory_list`, `mnemos_memory_review`, `mnemos_reindex`, `mnemos_status`

## Manques vs SOTA RAG 2026

Voir [`docs/ROADMAP.md`](docs/ROADMAP.md) pour le plan d'amélioration complet.

**TL;DR — Mnemos est aujourd'hui dense-only.** Manquent : reranker (cross-encoder), hybrid retrieval (BM25 + RRF), contextual chunking (Anthropic), CRAG corrective loop (grader + rewriter), query router, semantic cache, eval harness.

**Différenciateurs solides à préserver** :
- Memory pipeline from git (extraction + dedup + approval) — unique sur le marché
- Skills indexing (écosystème Claude) — unique
- Self-hosted complet zéro SaaS — rare

## Workflow de développement

### Setup local

```bash
# Démarrer la stack
docker compose up -d                       # qdrant + rag-server + watcher
docker compose --profile llm up -d         # + ollama

# CLI
make install                               # crée venv + installe mnemos CLI
source venv/bin/activate
export MNEMOS_URL=http://localhost:8100
mnemos status
```

### Re-indexing initial

```bash
mnemos reindex --collection mnemos_skills --path /data/claude-config/skills --full
mnemos reindex --collection mnemos_docs --path /data/claude-config/docs --full
mnemos reindex --collection mnemos_code_moby --path /data/codebase/moby --full
```

Note : paths sont les **chemins container** (mount via `docker-compose.yml`).

### Tests

```bash
pip install pytest pytest-asyncio
pip install -e packages/core/
pip install -r server/requirements.txt
pytest tests/
```

Conventions tests :
- Un fichier par module testé (`test_<module>.py`)
- Fixtures partagées dans `tests/conftest.py`
- Tests d'intégration séparés (`test_mcp_integration.py`)

## Conventions code

### Python (server, core, watcher, cli)
- Python 3.12+, type hints partout (`from __future__ import annotations`)
- Dataclasses ou Pydantic pour les modèles
- Pas d'effets de bord dans les imports
- Logging via `logging` standard (pas de `print`)
- Async pour les handlers FastAPI, sync ailleurs sauf justification

### Architecture
- `core/` est **pure logique** — pas d'I/O server ni HTTP
- `server/` est **transport-only** — wrappe `core` derrière FastAPI/MCP
- `watcher/` et `cli/` consomment `server/` via HTTP, jamais directement `core`
- Collections sont déclarées dans `core/collections.py` — single source of truth

### Memory entries
- `id` toujours UUID
- `status` ∈ {`pending`, `approved`, `rejected`}
- `memory_type` ∈ {`decision`, `pattern`, `lesson`, `convention`}
- `project` optionnel, scope multi-tenant
- Search ne retourne **que** les `approved`

## Quand intervenir dans ce repo

### Modifications côté retrieval qualité
1. Lire [`docs/ROADMAP.md`](docs/ROADMAP.md) pour comprendre la priorité
2. Ne pas régresser les métriques baseline (voir `docs/EVAL.md`)
3. Toute amélioration doit être A/B-testable via feature flag (Phase 4)

### Modifications chunker
1. Toujours ajouter un test dans `tests/test_chunkers/`
2. Préserver les métadonnées `chunk_type`, `symbol_name`, `language`, `chunk_index`
3. Tester avec un fichier réel du codebase indexé

### Modifications memory pipeline
1. Toute extraction passe par `MemoryExtractor` (LLM provider injecté)
2. Toute écriture passe par `Deduplicator` (jamais d'upsert direct sur `mnemos_memory`)
3. Préserver le workflow `pending → approved`

### LLM provider — abstraction (`core.llm`)
Toute composante qui appelle un LLM (extractor, dedup merge, futur contextual chunker, grader, rewriter, eval generator) **doit** :
1. Recevoir un `LLMProvider` par constructeur (jamais instancier en interne)
2. Utiliser uniquement `complete()` ou `complete_prompt()` de l'interface
3. Gérer `LLMError` (fallback ou propagation)

Providers supportés :
- `ollama` (par défaut, local, self-hosted)
- `anthropic` (cloud, prompt caching, idéal pour contextual chunking)
- `openai` (cloud OU endpoint compatible : vLLM, LM Studio, Together, Groq, OpenRouter…)

Switch via `MNEMOS_LLM_PROVIDER` env var (voir `.env.example`).

### Ajout d'une collection
1. Déclarer dans `core/collections.py` (avec `path_prefixes` et `description`)
2. Le startup `server/main.py:lifespan` crée la collection automatiquement
3. Ajouter au tableau dans `README.md` et `CLAUDE.md`

## Roadmap visible (résumé)

| # | Composant | Phase | Statut |
|---|---|---|---|
| 1 | Reranker (cross-encoder) | 2B | TODO |
| 2 | Hybrid retrieval (BM25 + RRF) | 2A | TODO |
| 3 | Contextual chunking (Anthropic) | 2A | TODO |
| 4 | Document Grader | 3 | TODO |
| 5 | Query Router | 4D | TODO |
| 6 | Query Rewriter | 3 | TODO |
| 7 | MMR diversification | 2B | TODO |
| 8 | Semantic Cache | 4E | TODO |
| 9 | Observability / Query logging | 4 | TODO |
| 10 | A/B Testing infra | 4 | TODO |

Détails : [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Liens utiles

- [`README.md`](README.md) — Quick start utilisateur
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — Plan d'amélioration CRAG/SOTA
- [`docs/EVAL.md`](docs/EVAL.md) — Métriques baseline + runs (à venir Phase 1.3)
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — Détails techniques (à venir)
