# Mnemos Eval

Retrieval quality measurements on Mnemos.
Generated via `mnemos eval run --tag <tag>` against a live server.

## Golden set

- **Size**: 25 questions
- **Source**: semi-automated via Ollama (`llama3.1:latest`), auto-accepted for the initial baseline (review pending)
- **Format**: `eval/dataset/golden.yaml`
- **Intent distribution**: code_search (11), doc_lookup (8), skill_discovery (6)

> ⚠️ The golden set was auto-accepted to establish a baseline. Several questions reference
> files that should not have been indexed (e.g. `.bak`, generated `_nuxt/*.js`, Capacitor
> build artifacts, `.terraform/modules/aks/README.md`). Pruning + re-generation is planned
> before drawing strong conclusions on retrieval improvements.

## Runs

### Baseline 2026-05-14 — dense-only, single unnamed vector

| Intent              | N  | MRR   | NDCG@5 | R@1   | R@3   | R@5   | R@10  | P@5   | Hit@5 |
|---------------------|----|-------|--------|-------|-------|-------|-------|-------|-------|
| code_search         | 11 | 0.159 | 0.200  | 0.091 | 0.091 | 0.364 | 0.455 | 0.073 | 0.364 |
| doc_lookup          | 8  | 0.625 | 0.695  | 0.500 | 0.625 | 0.875 | 0.875 | 0.175 | 0.875 |
| skill_discovery     | 6  | 0.167 | 0.167  | 0.167 | 0.167 | 0.167 | 0.167 | 0.033 | 0.167 |
| **ALL**             | 25 | 0.310 | 0.351  | 0.240 | 0.280 | 0.480 | 0.520 | 0.096 | 0.480 |

p50: 35 ms · p95: 56 ms

### Phase 2A.1 (2026-05-15) — Hybrid BM25 + dense + RRF fusion

| Intent              | N  | MRR   | NDCG@5 | R@1   | R@3   | R@5   | R@10  | P@5   | Hit@5 |
|---------------------|----|-------|--------|-------|-------|-------|-------|-------|-------|
| code_search         | 11 | 0.114 | 0.130  | 0.091 | 0.091 | 0.182 | 0.182 | 0.036 | 0.182 |
| doc_lookup          | 8  | 0.608 | 0.690  | 0.500 | 0.750 | 0.875 | 0.875 | 0.175 | 0.875 |
| skill_discovery     | 6  | 0.167 | 0.210  | 0.000 | 0.333 | 0.333 | 0.333 | 0.067 | 0.333 |
| **ALL**             | 25 | 0.285 | 0.328  | 0.200 | 0.360 | 0.440 | 0.440 | 0.088 | 0.440 |

p50: 33 ms · p95: 74 ms

**Comparison vs baseline:** MRR -0.025 · NDCG@5 -0.022 · R@5 -0.040

> **Note:** Phase 2A.1 also fixed a path bug in the golden set (`/data/codebase/moby/`
> → `/data/codebase/digital-gigafactory/moby/`). The baseline was measured against the
> old paths, so the comparison is **not strictly apples-to-apples** — some of the
> regression is path-mapping artefact, not a real hybrid retrieval drop. Once a clean
> golden set is built, a re-measurement of dense-only will give a fair reference.

### Phase 2A.2 partial (2026-05-15) — Contextual chunking on skills only

Only `mnemos_skills` (737 chunks) was reindexed with Anthropic-style contextual preamble
via Ollama (`llama3.1:latest`, 4 parallel workers). 9 min 52 s wall-clock for 737 chunks
(~1.24 chunks/s).

| Intent              | N  | MRR   | NDCG@5 | R@5   |
|---------------------|----|-------|--------|-------|
| skill_discovery     | 6  | 0.167 | 0.210  | 0.333 |
| **ALL**             | 25 | 0.248 | 0.302  | 0.440 |

**No measurable gain on skills**, and a small global regression
(-0.037 MRR, +19 ms latency p50) because the query embedding stays
non-contextualised while the chunks now carry an LLM-prefixed preamble.

**Decision:** stopped the contextual reindex on moby/trevio (~2 h 30 m more wall-clock)
and skipped Phase 2A.2 in favour of Phase 2B. Contextual retrieval will be revisited
after the golden set is cleaned and we can measure the real signal vs noise.

## Comparing runs

```bash
mnemos eval run --tag <new-tag>
mnemos eval compare baseline-2026-05-14 <new-tag>
```

## Target metrics per phase

| Phase                          | Acceptance criterion                   | Status |
|--------------------------------|----------------------------------------|--------|
| Phase 2A.1 — Hybrid + RRF      | No regression vs baseline              | ⚠️ Marginal regression — golden set quality issue |
| Phase 2A.2 — Contextual        | NDCG@5 ≥ Phase 2A.1 + 5 pts            | ❌ No gain on partial test, skipped |
| Phase 2B — Reranker + MMR      | NDCG@5 ≥ Phase 2A.1 + 10 pts           | ⏳ In progress |
| Phase 3 — CRAG grader/rewriter | precision@5 ≥ Phase 2B + 5 pts         | — |
| Phase 4D — Router              | latency p50 ≤ baseline (35 ms)         | — |
| Phase 4E — Cache               | latency p50 (cached) ≤ 10 ms           | — |

## Runs index

| Tag                                  | Date       | Notes                                       |
|--------------------------------------|------------|---------------------------------------------|
| `baseline-2026-05-14`                | 2026-05-14 | Dense-only, single unnamed vector           |
| `phase2a1-hybrid-2026-05-15`         | 2026-05-15 | Hybrid BM25 + dense + RRF, named vectors    |
| `phase2a2-partial-skills-2026-05-15` | 2026-05-15 | Skills contextualized only (partial)        |
