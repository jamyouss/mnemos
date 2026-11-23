# Mnemos Roadmap

> Where Mnemos is, and where it's going next.
> The original CRAG / Production-RAG plan from 2026-05-13 is preserved at the
> bottom for historical context — most of it has shipped.

---

## What ships today

Default setup: hybrid retrieval only, every other stage behind a flag. On
the 92-question golden set the live server scores MRR 0.834 with
EmbeddingGemma 2 and dense-weighted fusion; the reranker gave +47 % MRR over
plain dense retrieval on the first, 25-question set. See [`EVAL.md`](EVAL.md)
for per-phase numbers.

### Retrieval pipeline
- ✅ **Hybrid retrieval** — Qdrant named vectors (dense + sparse BM25) fused
  server-side with Reciprocal Rank Fusion, the dense side weighted 3:1
  (`MNEMOS_FUSION_DENSE_WEIGHT`).
- ✅ **Cross-encoder reranker** — `BAAI/bge-reranker-base` by default,
  pluggable via `MNEMOS_RERANKER_MODEL`, pairs capped at 256 tokens.
  Feature-flagged.
- ✅ **MMR diversification** — `MNEMOS_MMR_ENABLED`, on top of the reranker.
- ✅ **Contextual chunking** — Anthropic-style LLM-generated preambles
  per chunk, gated by `MNEMOS_CONTEXTUAL_ENABLED`.
- ✅ **CRAG corrective loop** — Document grader (`MNEMOS_GRADER_ENABLED`) +
  query rewriter (`MNEMOS_REWRITER_ENABLED`), both feature-flagged.
- ✅ **Configurable embedding model** — `EMBEDDING_MODEL` (default
  `all-MiniLM-L6-v2`; `google/embeddinggemma-2` measured best), migrated
  without downtime by `python -m core.embedding_migration sync|switch`.
- ❌ **Semantic router** — built, measured, removed (2026-09-07).

> **Why it is gone.** Routing compared the query
> against each collection's `description` — a short category label — and the
> signal is too weak to act on. Across 10 real queries the top score peaked at
> 0.33 against a 0.40 threshold, so it never once trimmed anything while
> costing an embedding per search.
>
> Lowering the threshold would be worse, not better. A pure code query
> (`func NewCompanyService in the go handler`) ranks `mnemos_code` **last**,
> at 0.00: an active router would exclude the collection the query is about.
> Rewriting the descriptions to read like their contents helps but does not
> fix it — 4/6 correct, with code queries still landing near zero.
>
> The approach is the limit, not the tuning: a viable router needs a real
> signal — a classifier or an LLM call — not cosine against a label.
>
> And it was solving a problem this deployment does not have. Fanning out to
> every collection measures at ~100 ms; outside the code collection they hold
> a few thousand points between them. Spending an LLM call to save nothing is
> not a trade worth making.
>
> The caller already knows the intent. `mnemos_search` takes a `collections`
> list and the dedicated tools each pin one — that is the routing, and it is
> free. Removed rather than left off, so nobody rediscovers the flag and
> turns it on.
- ✅ **Semantic cache** (`MNEMOS_CACHE_ENABLED`) — Qdrant-backed,
  cosine ≥ `MNEMOS_CACHE_THRESHOLD`, TTL via `MNEMOS_CACHE_TTL_SECONDS`,
  wiped on every index write (push, delete, memory, reindex).

### Indexing
- ✅ **Code-aware chunkers** — tree-sitter (Go), regex SFC (Vue),
  heading-based (Markdown), CSV/TSV rows, fallback windowed.
- ✅ **Single `mnemos_code` collection** with tag-based scoping (2026-05-18,
  replacing the single `project` filter) — every chunk carries
  `tags: list[str]`, search filters via `tags_any` (OR) and `tags_all`
  (AND). Mapping `path → tags` declared in the projects configuration (stored
  in Qdrant, edited from the dashboard), with a cumulative-segment fallback.
  `--tags` flag on `mnemos reindex` overrides per-run. See
  [CONFIGURATION.md](CONFIGURATION.md) for the projects mapping schema.
- ✅ **Watcher** — `watchdog` with 2 s debounce, incremental push to the
  server.
> ⚠️ The watcher only survives a large codebase thanks to the inotify prune
> patch in `watcher/main.py` (`_install_inotify_prune`). Without it, the
> recursive walk watches every directory — `node_modules` included — and the
> process dies at startup with `OSError: [Errno 28] inotify watch limit
> reached`. Published images up to `0.3.0` predate the patch; `0.3.1` and
> later include it.

- ✅ **Git hooks for incremental indexing** — `post-merge` / `post-checkout`
  push the files a pull changed; `scripts/nightly-reindex.sh` catches the rest.
- ✅ **Ignore rules** — built-in policy in `core.path_filter` (`.bak`,
  `_nuxt/`, `.terraform/`, lockfiles…) plus per-project `exclude_dirs` /
  `exclude_exts`, enforced at every ingestion path; `scripts/purge-filtered.py`
  removes what was indexed before a rule existed.

### Memory pipeline
- ✅ **Auto-extraction from git** — pre-push (or post-commit) hook runs the
  diff through the configured LLM, produces structured
  decisions/patterns/conventions/lessons.
- ✅ **LLM-powered deduplication** — cosine ≥ `MNEMOS_DEDUP_THRESHOLD`
  (0.85 server default, 0.80 in `.env.example`) triggers `merge` or
  `replace`, configurable via `MNEMOS_DEDUP_STRATEGY`.
- ✅ **Write-first memories** — memories are stored `approved`, searchable at
  once (2026-09-07: a review gate nobody opened left the pool empty). Search
  only returns approved memories, so rejecting one prunes it.
- ✅ **Tag-based scoping on memories** — same `tags` payload field as code,
  filtered via `tags_any` / `tags_all`.

### Platform
- ✅ **Pluggable LLM** — Ollama, Anthropic, any OpenAI-compatible endpoint
  (vLLM, LM Studio, Groq, Together, OpenRouter). One interface, swap via
  `MNEMOS_LLM_PROVIDER`.
- ✅ **MCP Streamable HTTP server** with 9 tools.
- ✅ **API-key authentication** — `MNEMOS_AUTH_ENABLED=true` requires a
  bearer key on `/api`, `/internal` and `/mcp`. Tenants carry an admin role
  and per-tag `read` / `write` scopes; no quotas.
- ✅ **Configuration in Qdrant** (`mnemos_config`) — connectors, projects,
  settings overrides and tenants shared by every server pod, propagated
  within `MNEMOS_CONFIG_POLL_SECONDS`; `config/` files are first-start
  import sources only.
- ✅ **`mnemos_knowledge` collection + connectors** — Confluence, Jira, web
  and git pushed through the API, tagged `<source>:<scope>`, with
  `source_url`; `mnemos ingest run|serve` reads them from the server.
- ✅ **Dashboard** — Nuxt SPA served at `/`: overview, search log,
  playground, memories, connectors, projects, tenants and configuration
  (retrieval flags editable live), behind a sign-in page when auth is on.
- ✅ **Observability** (`MNEMOS_QUERY_LOG_ENABLED`) — JSONL query log with
  top-K, scores, latencies and which stages ran.
- ✅ **Eval harness** — `mnemos eval generate|promote|run|compare|list`,
  golden set in YAML, metrics (MRR, NDCG@5, R@k, P@5, hit@5).
- ✅ **Multi-arch Docker images** (`linux/amd64`, `linux/arm64`) at
  ~500 MB compressed, CPU-only PyTorch, default embedding pre-baked.

---

## Backlog

### Retrieval quality
- **MMR diversification — mesure** — Shippé wired, off par défaut
  (`MNEMOS_MMR_ENABLED`). Reste à mesurer : l'activer par défaut seulement
  s'il réduit les near-duplicates du top-K sans dégrader NDCG@5.
- **Contextual chunking — retry with the cleaned golden** — Le premier
  passage (golden bruité) ne montrait pas de gain. Le golden est désormais
  propre, à mesurer via Anthropic API (prompt caching).
- **CRAG rewriter on a fast LLM** (Anthropic Haiku, Groq) — Le rewriter
  ship wired mais reste off : sur Ollama local le combo grader+rewriter
  pousse la latence p50 au-delà de 100 s. Avec un LLM cloud rapide,
  estimé +5–10 % de recall sur les queries vagues.

### Performance
- **GPU host for the reranker** — `bge-reranker-base` sur CPU ≈ 1,1 s pour
  20 candidats (moitié moins tronqué à 256 tokens, le défaut) ; ~14 s p50
  mesurés en mai. Avec EmbeddingGemma 2 le gain du reranker se réduit
  (MRR 0.556 → 0.658) : à remesurer avant tout default-on.
- **Sparse model upgrade** — Investiguer SPLADE-v3 ou ColBERT en remplacement
  du BM25 in-house.

### Production-ready
- **A/B testing infra** — Feature flags par tenant, 10 % de sampling sur les
  variantes, log dédié pour mesurer l'impact des changements retrieval en
  conditions réelles.
- **Authn richer than API key** — OAuth/OIDC, rotation automatique des clés
  (les scopes par tag, la rotation manuelle et la clé root existent déjà).
- **Quotas par tenant** — Les tenants ont des scopes de lecture/écriture par
  tag, mais aucune limite de volume ni de débit.
- **Helm chart** — Pour les déploiements Kubernetes.

### Écosystème
- **Plus de chunkers** — TypeScript/TSX (AST), Python (AST), Rust, Java.
- **Webhook outputs** — Notifier Slack / Discord quand une mémoire est
  écrite.

---

## Métriques cibles pour les prochaines phases

Critères de done quand un item du backlog ship (vs la référence courante,
hybrid seul — voir [EVAL](EVAL.md)) :

| Composant | Métrique seuil |
|---|---|
| MMR | duplicate@5 < 0.20 sans dégrader NDCG@5 |
| Contextual chunking (retry) | NDCG@5 ≥ baseline + 5pts |
| Rewriter + grader on cloud LLM | recall@5 sur intent vague +10 %, p50 < 2 s |
| GPU reranker | p50 < 500 ms, prêt pour default-on |

Aucun item ne ship si une métrique régresse.

---

## Historique — plan initial (2026-05-13)

> Le plan ci-dessous était le brief de démarrage. La quasi-totalité a été
> implémentée et figure désormais dans la section "What ships today".
> Conservé tel quel pour référence et pour l'archéologie des choix de design.

**Contraintes initiales :**
- Self-hosted, zéro dépendance SaaS lourde
- Stack Python/Go existante
- Ragas OK en local si besoin
- Eval harness maison (recall/precision/MRR/NDCG/hit-rate)

**Phases prévues vs livré :**

| Phase | Composant | Statut |
|---|---|---|
| 1 | Cartographie + eval harness + baseline | ✅ shipped |
| 2A | Contextual chunking (wired, off-default) | ✅ shipped |
| 2A | Hybrid BM25 + dense + RRF | ✅ shipped |
| 2B | Cross-encoder reranker | ✅ shipped |
| 2B | MMR diversification | ✅ shipped wired, off-default |
| 3 | Document Grader (CRAG) | ✅ shipped |
| 3 | Query Rewriter (CRAG) | ✅ shipped wired, off-default |
| 4D | Query Router | ❌ removed — see above |
| 4E | Semantic Cache | ✅ shipped |
| 4 | Observability (query log JSONL) | ✅ shipped |
| 4 | A/B Testing infra | 🔜 backlog |

**Sources utilisées pendant le design :**

- [Anthropic — Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval) — -49 % à -67 % failure rate.
- [BAAI/bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3) — SOTA local reranker.
- [AnswerDotAI/rerankers](https://github.com/AnswerDotAI/rerankers) — API unifiée.
- [RAG Production Guide 2026](https://lushbinary.com/blog/rag-retrieval-augmented-generation-production-guide/) — hybrid + RRF par défaut.
- [RAG Evaluation 2026 — PremAI](https://blog.premai.io/rag-evaluation-metrics-frameworks-testing-2026/) — corrélation NDCG.
- [Reranker Benchmark — AIMultiple](https://aimultiple.com/rerankers) — 8 models comparés.
