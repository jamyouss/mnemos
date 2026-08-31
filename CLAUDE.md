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
  query → cache → router (trim collections) → hybrid dense+BM25 sparse (RRF)
        → cross-encoder rerank → MMR → CRAG grader (+rewriter) → cache + query log
         Tout est câblé (server/main.py + server/search.py). Sauf l'hybrid,
         chaque étage est derrière un flag MNEMOS_*_ENABLED, off par défaut.

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
  packages/core/      # Lib partagée (chunkers, embeddings, indexer, path_filter, dedup, memory_extractor)
  server/                 # FastAPI + MCP server (port 8100)
  watcher/                # File watcher (watchdog)
  cli/                    # Click CLI client
  config/                 # Tenant configuration
  scripts/hooks/          # Global git hooks (memory extraction + incremental indexing)
  tests/                  # pytest suite
  eval/                   # Eval harness (Phase 1.2, en cours)
  docs/                   # ROADMAP.md, EVAL.md, ARCHITECTURE.md
  docker-compose.yml      # Local dev stack
  docker-compose.prod.yml # Production overrides multi-tenant
```

### Collections Qdrant

| Collection | Source | Path prefixes | Scoping |
|---|---|---|---|
| `mnemos_skills` | `~/.claude/skills/` | `skills/` | — |
| `mnemos_docs` | `~/.claude/docs/` | `docs/` | — |
| `mnemos_memory` | API / git hooks | _(aucun)_ | via `tags` payload |
| `mnemos_code` | codebase | _(tout le reste)_ | via `tags` payload (filtres `tags_any` / `tags_all`) |

Une seule collection `mnemos_code` héberge **tous** les projets. Chaque chunk
porte un champ `tags: list[str]` — le premier tag sert de label primaire
(display), les suivants sont des labels transverses (parent, techno, équipe).

Le mapping `path → tags` est défini dans `config/projects.yaml` (template
`config/projects.example.yaml`). Sans ce fichier, Mnemos émet par défaut les
segments cumulatifs du path (`foo/bar/baz/file.go` →
`["foo", "foo/bar", "foo/bar/baz"]`).

Côté requête, deux filtres sont exposés partout (CLI, REST, MCP) :
- `tags_any` (CLI `--tags`) — match si l'un des tags est présent (OR).
- `tags_all` (CLI `--tags-all`) — match si **tous** les tags sont présents (AND).

```bash
mnemos search-code "auth"  --tags acme,webshop           # OR
mnemos search-code "auth"  --tags-all acme,vue3       # AND
```

```python
mnemos_search_code(query="auth", tags_any=["acme-front-app-ecommerce", "acme-front-lib-auth"])
mnemos_search_code(query="auth", tags_all=["acme", "vue3"])
```

### MCP Tools exposés (9)

`mnemos_search`, `mnemos_search_code`, `mnemos_search_skills`, `mnemos_search_memory`, `mnemos_memory`, `mnemos_memory_list`, `mnemos_memory_review`, `mnemos_reindex`, `mnemos_status`

## Manques vs SOTA RAG 2026

Voir [`docs/ROADMAP.md`](docs/ROADMAP.md) pour le plan d'amélioration complet.

**TL;DR — l'essentiel du plan est implémenté**, pas seulement planifié :
hybrid BM25+RRF (toujours actif), reranker cross-encoder, contextual chunking,
CRAG (grader + rewriter), query router, cache sémantique, MMR, query log, et un
harness d'eval (`packages/eval/`, `evals/`). Tous sauf l'hybrid sont **off par
défaut** — un gain non mesuré reste un gain non acquis : activer d'abord
`MNEMOS_QUERY_LOG_ENABLED`, mesurer, puis lever les flags un par un.

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
mnemos reindex --collection mnemos_code --path /data/codebase/myproject --tags myproject,go --full
mnemos reindex --collection mnemos_code --path /data/codebase/otherproject --tags otherproject,vue3 --full
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
- `tags: list[str]` — scoping multi-label (project, techno, équipe). Filtrable
  via `tags_any` (OR) et `tags_all` (AND).
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
4. Un chunker spécialisé doit **déléguer au `FallbackChunker`**, pas rendre
   `[]`, quand le contenu ne correspond pas à ce qu'annonce l'extension —
   `index_file` traite `[]` comme « rien à indexer », pas comme un repli.
   Voir `TabularChunker._delegate`.

### Modifications du filtrage par projet
`core.path_filter` porte la politique **globale** — ce qui n'est jamais du
source, nulle part. Le bruit propre à un projet (`exports/`, un `.csv` généré)
va dans `config/projects.yaml`, en `exclude_dirs` / `exclude_exts` par préfixe.

1. `should_skip_path` reste une **fonction pure**. Ne jamais y charger de
   config : la résolution par préfixe vit dans `Indexer._resolve_excludes`,
   calquée sur `_resolve_tags`.
2. Tout call site qui décide « faut-il ignorer ce fichier ? » passe par
   `Indexer.should_skip`, jamais par `should_skip_path` en direct — c'est le
   seul endroit qui unit built-in + config + extras.
3. Les trois sources **s'additionnent**. Rien ne peut ré-autoriser ce qu'une
   règle built-in refuse.
4. Le watcher pré-filtre sur les built-ins seuls : `config/` n'est pas monté
   dans son conteneur. La correction est garantie côté serveur par
   `index_file`, le coût est un aller-retour HTTP gâché.

### Modifications du filtre d'indexation (`core.path_filter`)
1. **Single source of truth** : ajouter le pattern dans
   `packages/core/path_filter.py` (`IGNORE_DIRS`, `IGNORE_EXTS`,
   `IGNORE_FILENAMES`, `IGNORE_PATH_SUBSTRINGS`, `IGNORE_BASENAME_SUBSTRINGS`).
   Ne jamais re-déclarer une liste locale dans `watcher/`, `server/` ou
   ailleurs — les call sites importent `should_skip_path`.
2. Ajouter un test dans `tests/test_path_filter.py` :
   - un cas positif (le pattern skip)
   - un cas négatif non-régression (un path légitime proche n'est PAS skippé,
     ex. `my_node_modules_helper/` ne doit pas matcher `node_modules`)
3. Le filtre est appliqué à 3 endroits, **automatiquement** : watcher (early),
   bulk reindex walker (`_should_skip`), et `Indexer.index_file`
   (defense-in-depth pour la push API). Aucun wiring supplémentaire à faire.
4. Pour qu'un nouveau pattern s'applique aux chunks déjà indexés, lancer un
   `./scripts/reindex-all.py --recreate`.

### Modifications des git hooks (`scripts/hooks/`)

Quatre hooks, deux rôles, **deux listes de repos distinctes** :

| Hook | Rôle | Gate | Coût |
|---|---|---|---|
| `post-commit` / `pre-push` | extraction mémoire | `mnemos_is_watched_repo` → `~/.config/mnemos/repos` | 1 appel LLM par déclenchement |
| `post-merge` / `post-checkout` | indexation incrémentale | `mnemos_is_indexed_repo` → `~/.config/mnemos/index-repos` | 1 POST par fichier changé |

`index-repos` retombe sur `repos` s'il n'existe pas — les installs existantes
continuent de fonctionner sans changement.

L'indexation incrémentale pousse **uniquement le diff** (`git diff --name-status
--no-renames`) vers `POST /api/index`, et bascule sur un `POST /api/reindex`
global au-delà de `MNEMOS_MAX_INCREMENTAL_FILES` (défaut 200) — au-delà, N
allers-retours HTTP coûtent plus qu'un walk serveur. Un clone frais
(`post-checkout` avec le sha nul) part directement en bulk.

Règles :
1. Les hooks sont **non bloquants et fail-safe** : `exit 0` systématique, curl
   détaché, aucune dépendance au serveur (`/health` jamais requis).
2. Ne **jamais** ré-implémenter `core.path_filter` en shell. Les hooks poussent,
   le serveur filtre (`Indexer.index_file` est le chokepoint défensif). Seules
   les limites de *transport* vivent côté hook (taille max, fichier binaire).
3. Les tags ne sont **pas** envoyés par le hook : le serveur les résout depuis
   `config/projects.yaml` via le chemin container.
4. Tout changement se teste dans `tests/test_hooks_incremental.py` (vrai repo
   git temporaire + stub HTTP, les hooks shell sont exécutés pour de vrai).

### Réindexation périodique

`scripts/nightly-reindex.sh` (agent launchd, voir
`scripts/install-nightly-reindex.sh`) rattrape ce que les hooks ne voient pas :
rebase, reset, stash, édition hors git, repo cloné serveur éteint. Lock via
`mkdir`, skip silencieux si le serveur est injoignable.

⚠️ `reindex-all.py` ne saute **pas** les fichiers inchangés — `Indexer.index_file`
n'a aucun test de fraîcheur sur `file_mtime`. Un full reindex ré-embedde tout.
C'est pour ça que le chemin nominal est l'incrémental, pas le cron.

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

| # | Composant | Phase | Statut | Flag |
|---|---|---|---|---|
| 1 | Reranker (cross-encoder) | 2B | fait | `MNEMOS_RERANKER_ENABLED` |
| 2 | Hybrid retrieval (BM25 + RRF) | 2A | fait | toujours actif |
| 3 | Contextual chunking (Anthropic) | 2A | fait | `MNEMOS_CONTEXTUAL_ENABLED` |
| 4 | Document Grader | 3 | fait | `MNEMOS_GRADER_ENABLED` |
| 5 | Query Router | 4D | fait | `MNEMOS_ROUTER_ENABLED` |
| 6 | Query Rewriter | 3 | fait | `MNEMOS_REWRITER_ENABLED` |
| 7 | MMR diversification | 2B | fait | `MNEMOS_MMR_ENABLED` |
| 8 | Semantic Cache | 4E | fait | `MNEMOS_CACHE_ENABLED` |
| 9 | Observability / Query logging | 4 | fait | `MNEMOS_QUERY_LOG_ENABLED` |
| 10 | A/B Testing infra | 4 | TODO | — |

⚠️ Ce tableau et `docs/ROADMAP.md` avaient dérivé du code : vérifier
`server/main.py` avant de croire un statut.

Détails : [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Liens utiles

- [`README.md`](README.md) — Quick start utilisateur
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — Plan d'amélioration CRAG/SOTA
- [`docs/EVAL.md`](docs/EVAL.md) — Métriques baseline + runs (à venir Phase 1.3)
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — Détails techniques (à venir)
