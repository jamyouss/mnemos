# Mnemos Eval

Retrieval quality measurements on Mnemos.
Generated via `mnemos eval run --tag <tag>` against a live server.

## Golden set

- **Size**: 25 questions
- **Source**: semi-automated via Ollama (`llama3.1:latest`), auto-accepted for the initial baseline (review pending)
- **Format**: `eval/dataset/golden.yaml`
- **Intent distribution**: code_search (11), doc_lookup (8), skill_discovery (6)

> ⚠️ The golden set was auto-accepted to establish a baseline. Some questions reference
> files that should not have been indexed (e.g. `.bak` files, generated `_nuxt/*.js`).
> Pruning + re-generation is planned before Phase 2A so the baseline reflects real
> developer intent.

## Baseline — 2026-05-14

Dense-only retrieval (current Mnemos). No reranker, no hybrid, no contextual chunking.

| Intent              | N  | MRR   | NDCG@5 | R@1   | R@3   | R@5   | R@10  | P@5   | Hit@5 |
|---------------------|----|-------|--------|-------|-------|-------|-------|-------|-------|
| code_search         | 11 | 0.159 | 0.200  | 0.091 | 0.091 | 0.364 | 0.455 | 0.073 | 0.364 |
| doc_lookup          | 8  | 0.625 | 0.695  | 0.500 | 0.625 | 0.875 | 0.875 | 0.175 | 0.875 |
| skill_discovery     | 6  | 0.167 | 0.167  | 0.167 | 0.167 | 0.167 | 0.167 | 0.033 | 0.167 |
| **ALL**             | 25 | 0.310 | 0.351  | 0.240 | 0.280 | 0.480 | 0.520 | 0.096 | 0.480 |

**Latency** — p50: 35 ms · p95: 56 ms

### Reading the numbers

- **code_search is the weak spot**: R@5 = 36 %. The dense embedding alone misses exact-keyword matches (function names, types). Hybrid retrieval (Phase 2A) targets this directly.
- **doc_lookup is solid**: R@5 = 88 %. Markdown chunks are well separated, dense embeddings capture topical similarity well.
- **skill_discovery is artificially low**: 5 of 6 questions reference paths that shouldn't be in the golden set (`.bak`, `examples.md` instead of `SKILL.md`). After golden-set pruning, expect a real number closer to doc_lookup.

### Known harness limitations (to address)

1. Golden set was generated against chunks that include files we'd normally skip (e.g. `.terraform/modules/aks/README.md`, compiled `_nuxt/*.js`). The watcher's ignore rules should be applied to the eval sampler too.
2. NDCG dedupes retrieved files (multi-chunk hits count once). This is intentional but worth flagging.
3. `skill_discovery` is routed through `/api/search` with `collections=[mnemos_skills]` since `/api/search-skills` returns a different schema (`skill_name` instead of `file_path`).

## Comparing runs

```bash
mnemos eval run --tag <new-tag>
mnemos eval compare baseline-2026-05-14 <new-tag>
```

## Target metrics per phase

| Phase                          | Acceptance criterion                   |
|--------------------------------|----------------------------------------|
| Phase 2A — Hybrid + Contextual | NDCG@5 ≥ baseline + 15 pts → ≥ 0.50    |
| Phase 2B — Reranker + MMR      | NDCG@5 ≥ Phase 2A + 5 pts → ≥ 0.55     |
| Phase 3 — CRAG grader/rewriter | precision@5 ≥ Phase 2B + 5 pts         |
| Phase 4D — Router              | latency p50 ≤ baseline (35 ms)         |
| Phase 4E — Cache               | latency p50 (cached) ≤ 10 ms           |

Any phase that regresses metrics vs baseline must be revisited before shipping.

## Runs index

| Tag                       | Date       | Notes                       |
|---------------------------|------------|-----------------------------|
| `baseline-2026-05-14`     | 2026-05-14 | Dense-only baseline (this)  |
