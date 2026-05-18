# Mnemos Roadmap

> Where Mnemos is, and where it's going next.
> The original CRAG / Production-RAG plan from 2026-05-13 is preserved at the
> bottom for historical context — most of it has shipped.

---

## What ships in v0.1.0

Default-on production setup measured at **+47 % MRR** and **+37 % NDCG@5**
over plain dense retrieval on the clean golden set. See [`EVAL.md`](EVAL.md)
for per-phase numbers.

### Retrieval pipeline
- ✅ **Hybrid retrieval** — Qdrant named vectors (dense + sparse BM25) fused
  server-side with Reciprocal Rank Fusion (k=60).
- ✅ **Cross-encoder reranker** — `BAAI/bge-reranker-base` by default,
  pluggable via `MNEMOS_RERANKER_MODEL`. Feature-flagged.
- ✅ **Contextual chunking** — Anthropic-style LLM-generated preambles
  per chunk, gated by `MNEMOS_CONTEXTUAL_ENABLED`.
- ✅ **CRAG corrective loop** — Document grader (`MNEMOS_GRADER_ENABLED`) +
  query rewriter (`MNEMOS_REWRITER_ENABLED`), both feature-flagged.
- ✅ **Semantic router** (`MNEMOS_ROUTER_ENABLED`) — Trim collections per
  query based on cosine similarity to each collection's description.
- ✅ **Semantic cache** (`MNEMOS_CACHE_ENABLED`) — Qdrant-backed,
  cosine ≥ `MNEMOS_CACHE_THRESHOLD`, TTL via `MNEMOS_CACHE_TTL_SECONDS`,
  invalidated on `reindex`.

### Indexing
- ✅ **Code-aware chunkers** — tree-sitter (Go), regex SFC (Vue),
  heading-based (Markdown), fallback windowed.
- ✅ **Single `mnemos_code` collection** with tag-based scoping — every chunk
  carries `tags: list[str]`, search filters via `tags_any` (OR) and `tags_all`
  (AND). Mapping `path → tags` declared in `config/projects.yaml`, with a
  cumulative-segment fallback when absent. `--tags` flag on `mnemos reindex`
  overrides per-run.
- ✅ **Tags-based scoping (replaces single `project` filter)** — ✅ shipped 2026-05-18.
  Chunks carry `tags: list[str]`; search filters with `tags_any` (OR) and `tags_all` (AND).
  See [CONFIGURATION.md](CONFIGURATION.md) for the `projects.yaml` schema.
- ✅ **Watcher** — `watchdog` with 2 s debounce, incremental push to the
  server.
- ✅ **`_should_skip` rules** — `.bak`, `_nuxt/*.js`, `.terraform/`, etc.
  filtered out at index time.

### Memory pipeline
- ✅ **Auto-extraction from git** — pre-push hook runs the diff through the
  configured LLM, produces structured decisions/patterns/lessons.
- ✅ **LLM-powered deduplication** — cosine ≥ 0.85 triggers `merge` or
  `replace`, configurable via `MNEMOS_DEDUP_STRATEGY`.
- ✅ **Approval workflow** — `pending → approved`. Search only returns
  approved memories.
- ✅ **Tag-based scoping on memories** — same `tags` payload field as code,
  filtered via `tags_any` / `tags_all`.

### Platform
- ✅ **Pluggable LLM** — Ollama, Anthropic, any OpenAI-compatible endpoint
  (vLLM, LM Studio, Groq, Together, OpenRouter). One interface, swap via
  `MNEMOS_LLM_PROVIDER`.
- ✅ **MCP Streamable HTTP server** with 9 tools.
- ✅ **Multi-tenant deployed mode** — `MNEMOS_AUTH_ENABLED=true`,
  `config/tenants.yaml`, collection prefixing, quota field.
- ✅ **Observability** (`MNEMOS_QUERY_LOG_ENABLED`) — JSONL query log with
  top-K, scores, latencies, grader hits.
- ✅ **Eval harness** — `mnemos eval generate|promote|run|compare`,
  golden set in YAML, metrics (MRR, NDCG@k, R@k, P@k, hit@k).
- ✅ **Multi-arch Docker images** (`linux/amd64`, `linux/arm64`) at
  ~500 MB compressed, CPU-only PyTorch, default embedding pre-baked.

---

## Backlog

### 🔜 Retrieval quality
- **MMR diversification** — Implementer dans le tracé original mais jamais
  shippé. ~20 lignes, lambda configurable, après le reranker. Aide quand
  plusieurs near-duplicates dominent le top-K.
- **Contextual chunking — retry with the cleaned golden** — Le premier
  passage (golden bruité) ne montrait pas de gain. Le golden est désormais
  propre, à mesurer via Anthropic API (prompt caching).
- **CRAG rewriter on a fast LLM** (Anthropic Haiku, Groq) — Le rewriter
  ship wired mais reste off : sur Ollama local le combo grader+rewriter
  pousse la latence p50 au-delà de 100 s. Avec un LLM cloud rapide,
  estimé +5–10 % de recall sur les queries vagues.

### 🔜 Performance
- **GPU host for the reranker** — `bge-reranker-base` sur CPU = ~14 s p50,
  sur GPU ~250 ms. Bloque l'activation par défaut du reranker.
- **Sparse model upgrade** — Investiguer SPLADE-v3 ou ColBERT en remplacement
  du BM25 in-house.

### 🔜 Production-ready
- **A/B testing infra** — Feature flags par tenant, 10 % de sampling sur les
  variantes, log dédié pour mesurer l'impact des changements retrieval en
  conditions réelles.
- **Authn richer than API key** — OAuth/OIDC, scoping fin par projet,
  rotation automatique.
- **Helm chart** — Pour les déploiements Kubernetes multi-tenant.

### 🔜 Écosystème
- **Plus de chunkers** — TypeScript/TSX (AST), Python (AST), Rust, Java.
- **Webhook outputs** — Notifier Slack / Discord quand une mémoire est
  proposée à la review.

---

## Métriques cibles pour les prochaines phases

Critères de done quand un item du backlog ship (vs baseline v0.1.0) :

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
> implémentée et figure désormais dans la section "What ships in v0.1.0".
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
| 2B | MMR diversification | 🔜 backlog |
| 3 | Document Grader (CRAG) | ✅ shipped |
| 3 | Query Rewriter (CRAG) | ✅ shipped wired, off-default |
| 4D | Query Router | ✅ shipped |
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
