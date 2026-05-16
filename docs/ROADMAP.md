# Mnemos Roadmap — CRAG & Production RAG

> Plan d'amélioration inspiré du pattern CRAG (Corrective RAG) et de l'état de l'art RAG 2026.
> Démarré le 2026-05-13.

## Contexte

Mnemos est aujourd'hui un RAG self-hosted **dense-only** (embeddings + cosine similarity sur Qdrant), avec extraction mémoire depuis git via Ollama. Ce plan vise à amener Mnemos au niveau de l'état de l'art en intégrant : reranking, hybrid retrieval, contextual chunking, et la boucle corrective CRAG.

## Contraintes

- Self-hosted, zéro dépendance SaaS lourde
- Stack Python/Go existante
- Ragas OK en local si besoin
- Eval harness maison (recall/precision/MRR/NDCG/hit-rate)

---

## Phase 1 — Fondations (étapes 2-4)

### 1.1 Cartographie technique
Documenter dans `CLAUDE.md` :
- Pipeline RAG actuel (chunker → embedder → retrieval → ⌀ reranker)
- Volumétrie collections (via `mnemos status`)
- Manques explicites vs SOTA

### 1.2 Eval harness maison
Structure cible :
```
eval/
  dataset/golden.yaml          # 20-30 Q/R YAML
  harness/
    schema.py                  # GoldenItem, EvalRun, MetricsReport
    loader.py
    generator.py               # Génération semi-auto Ollama
    runner.py
    metrics.py                 # recall@k, precision@k, MRR, NDCG@k, hit_rate@k
    reporter.py                # Console Rich + JSON
  runs/YYYY-MM-DD-<tag>.json
```

Schéma `GoldenItem` :
```yaml
- id: q001
  query: "comment fonctionne l'extraction de mémoire depuis git ?"
  intent: code_search              # code_search | skill_discovery | doc_lookup | memory_recall
  expected_collections: [mnemos_code_mnemos]
  expected_files:
    - packages/core/memory_extractor.py
  expected_chunks: []              # optionnel : chunk_id précis
  relevance_grades: {}             # optionnel : {file: 1|2|3} pour NDCG gradué
  k_relevant: 2
```

CLI :
| Commande | Action |
|---|---|
| `mnemos eval generate --collection X --count N` | Génère candidats Q via Ollama |
| `mnemos eval promote` | Valide candidats reviewés → `golden.yaml` |
| `mnemos eval run --tag baseline` | Exécute eval, sauve dans `runs/` |
| `mnemos eval compare A B` | Diff métriques entre 2 runs |
| `mnemos eval list` | Liste runs |

### 1.3 Baseline
Capturer dans `docs/EVAL.md` :
```
| Intent | k_avg | MRR | NDCG@5 | recall@5 | precision@5 | hit_rate@5 |
```

---

## Phase 2 — Améliorations qualité

### Phase A — Indexing refactor (1.5 j)

**Contextual chunking (Anthropic)**
- Préfixer chaque chunk avec un contexte généré par Ollama (titre fichier, package, section parente, résumé)
- Modifier chunkers `go_chunker.py`, `vue_chunker.py`, `markdown_chunker.py`, `fallback_chunker.py`
- Impact attendu : -35% failure rate (source Anthropic)

**Hybrid retrieval (BM25 + dense + RRF)**
- Activer **sparse vectors** Qdrant pour BM25 (ou SPLADE)
- Implémenter Reciprocal Rank Fusion (k=60 par défaut)
- Modifier `SearchService.search()` pour fusion sparse + dense
- Impact attendu (combiné contextual) : -49% failure rate

Re-index complet nécessaire (one-time).

### Phase B — Retrieval refactor (0.5 j)

**Cross-encoder reranker**
- Modèle local : `BAAI/bge-reranker-v2-m3` ou `mixedbread-ai/mxbai-rerank-large-v2`
- Lib : [`rerankers`](https://github.com/AnswerDotAI/rerankers) (API unifiée)
- Pipeline : retrieve top-20 (hybrid) → rerank top-20 → return top-5
- Impact attendu : +9pts MRR, +25-40% precision

**MMR (Maximum Marginal Relevance)**
- Appliqué après reranker pour diversifier top-K
- ~20 lignes, lambda=0.5 par défaut
- Évite doublons sémantiques

---

## Phase 3 — Améliorations corrective (1 j)

### Phase C — CRAG loop

**Document Grader (priorité 4)**
- LLM-based via Ollama : `(query, chunk) → {high, medium, low}`
- Batch les chunks (latence ~500ms)
- Si zéro chunks "high" → fallback
- Wire dans `SearchService.search()` avec option `enable_grader: bool`

**Query Rewriter (priorité 6)**
- Triggered par grader low score
- Stratégie initiale : expansion via Ollama (synonymes + termes techniques)
- Optionnel future : HyDE, decomposition multi-hop

---

## Phase 4 — Production (1 j)

### Phase D — Query Router (0.5 j)
- Pré-compute embedding de chaque `CollectionConfig.description`
- À chaque query : cos sim query↔descriptions → top-K collections
- Fallback : si top score < 0.4 → balaie tout
- Wire au début de `SearchService.search()`

### Phase E — Semantic Cache (0.5 j)
- Collection Qdrant dédiée `mnemos_cache`
- Clé : embedding query
- Valeur : résultats sérialisés + TTL (1h par défaut)
- Invalidation : sur `reindex` complet (vider la collection)

### 9. Observability (0.25 j)
- Query logging JSONL : `(tenant, query, top-K, scores, latencies, grader_hits)`
- Permet :
  - Détection queries "ratées" en prod
  - Enrichissement du golden set avec cas réels
  - A/B testing propre

### 10. A/B Testing (0.5 j)
- Feature flags par tenant (env var ou config)
- Variantes : reranker on/off, BM25 weight, contextual on/off
- Sampling : 10% des queries en variante par défaut

---

## Métriques cibles

Critères de done par phase (vs baseline) :

| Phase | Métrique seuil |
|---|---|
| Phase 2 (A+B) | **NDCG@5 ≥ baseline + 15pts** |
| Phase 3 (C) | **precision@5 ≥ Phase 2 + 5pts** |
| Phase 4 | **latence p50 ≤ baseline / 2** (grâce au cache) |

Aucune phase ne ship si métriques régressent.

---

## Ordre d'exécution final

| # | Composant | Phase | Effort | Impact attendu |
|---|---|---|---|---|
| 1 | Reranker (cross-encoder) | B | 0.5 j | +9pts MRR, +25-40% precision |
| 2 | Hybrid retrieval (BM25 + RRF) | A | 1 j | -49% failure rate |
| 3 | Contextual chunking | A | 0.5 j | -35% failure rate standalone |
| 4 | Document Grader | C | 0.5 j | ↑ precision edge cases |
| 5 | Query Router | D | 0.5 j | -50ms latence |
| 6 | Query Rewriter | C | 0.5 j | +5% recall queries vagues |
| 7 | MMR | B | 1h | ↑ couverture top-K |
| 8 | Semantic Cache | E | 0.5 j | latence p50 cached < 50ms |
| 9 | Observability | prod | 0.25 j | infra debug |
| 10 | A/B Testing | prod | 0.5 j | comparaison variantes prod |

**Total estimé** : ~5 jours dev + 0.5 j eval/doc par phase.

---

## Sources

- [Anthropic — Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval) — -49% to -67% failure rate
- [BAAI/bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3) — SOTA local reranker
- [AnswerDotAI/rerankers](https://github.com/AnswerDotAI/rerankers) — unified rerankers API
- [RAG Production Guide 2026](https://lushbinary.com/blog/rag-retrieval-augmented-generation-production-guide/) — hybrid + RRF default
- [RAG Evaluation 2026 — PremAI](https://blog.premai.io/rag-evaluation-metrics-frameworks-testing-2026/) — NDCG correlation
- [Reranker Benchmark — AIMultiple](https://aimultiple.com/rerankers) — 8 models compared
