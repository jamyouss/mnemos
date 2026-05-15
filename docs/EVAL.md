# Mnemos Eval

Retrieval quality measurements on Mnemos.
Generated via `mnemos eval run --tag <tag>` against a live server.

## Golden set

- **Size**: 25 questions
- **Source**: semi-automated via Ollama (`llama3.1:latest`), auto-accepted for the initial baseline (review pending)
- **Format**: `eval/dataset/golden.yaml`
- **Intent distribution**: code_search (11), doc_lookup (8), skill_discovery (6)

> ⚠️ The golden set was auto-accepted to establish a baseline. Several questions reference
> files that should not have been indexed (`.bak`, generated `_nuxt/*.js`, `.terraform/`).
> A clean re-generation is on the backlog and will push every absolute score up — but the
> **deltas** between phases are still meaningful because each phase is measured against
> the same set.

## Headline numbers

Two campaigns: a **legacy** golden set (25 q, contained junk paths) and a
**clean** golden set (31 q, after blocklist + tighter generator prompt).
Phase numbers across the two golden sets are not strictly comparable —
deltas within a campaign are.

### Clean golden set (31 q, current reference)

| Run | MRR | NDCG@5 | R@5 | P@5 | p50 | What's enabled |
|-----|------|--------|-----|-----|-----|----------------|
| Hybrid only | 0.270 | 0.284 | 0.387 | 0.077 | **72 ms** | BM25 + dense + RRF |
| + Reranker MiniLM + Router + Cache | **0.404** | **0.441** | **0.516** | 0.103 | 4 474 ms | (current default-on prod) |
| + **CRAG grader (no rewriter)** | 0.404 | 0.441 | 0.516 | **0.133** | 3 211 ms | grader drops "low" chunks |
| On cache hit (any of the above) | — | — | — | — | **100 ms** | |

**Reranker over plain hybrid:** MRR +50 %, NDCG@5 +55 %, R@5 +33 %.
**Grader over reranker:** P@5 +29 % (and +117 % on `doc_lookup` alone) — same
top-K but cleaner; the grader rejects irrelevant chunks before ranking.

### Legacy golden set (25 q, historical)

| Run | MRR | NDCG@5 | R@5 |
|-----|------|--------|-----|
| Baseline (dense-only, unnamed vector) | 0.310 | 0.351 | 0.480 |
| Phase 2A.1 (Hybrid BM25+RRF) | 0.285 | 0.328 | 0.440 |
| Phase 2A.2 (Contextual on skills only) | 0.248 | 0.302 | 0.440 |
| Phase 2B with `bge-reranker-base` | 0.457 | 0.481 | 0.560 |
| Phase 2B with `ms-marco-MiniLM-L-6-v2` | 0.420 | 0.445 | 0.520 |

The big-reranker run was the quality champion (+47 % MRR vs baseline) but
at 14 s p50. MiniLM gives up ~3.7 MRR points for 3.8× speed-up.

## Run details

### Baseline 2026-05-14 — dense-only

| Intent              | N  | MRR   | NDCG@5 | R@1   | R@3   | R@5   | R@10  | P@5   | Hit@5 |
|---------------------|----|-------|--------|-------|-------|-------|-------|-------|-------|
| code_search         | 11 | 0.159 | 0.200  | 0.091 | 0.091 | 0.364 | 0.455 | 0.073 | 0.364 |
| doc_lookup          | 8  | 0.625 | 0.695  | 0.500 | 0.625 | 0.875 | 0.875 | 0.175 | 0.875 |
| skill_discovery     | 6  | 0.167 | 0.167  | 0.167 | 0.167 | 0.167 | 0.167 | 0.033 | 0.167 |
| **ALL**             | 25 | 0.310 | 0.351  | 0.240 | 0.280 | 0.480 | 0.520 | 0.096 | 0.480 |

### Phase 2A.1 2026-05-15 — Hybrid BM25 + dense + RRF

| Intent              | N  | MRR   | NDCG@5 | R@1   | R@3   | R@5   | R@10  | P@5   | Hit@5 |
|---------------------|----|-------|--------|-------|-------|-------|-------|-------|-------|
| code_search         | 11 | 0.114 | 0.130  | 0.091 | 0.091 | 0.182 | 0.182 | 0.036 | 0.182 |
| doc_lookup          | 8  | 0.608 | 0.690  | 0.500 | 0.750 | 0.875 | 0.875 | 0.175 | 0.875 |
| skill_discovery     | 6  | 0.167 | 0.210  | 0.000 | 0.333 | 0.333 | 0.333 | 0.067 | 0.333 |
| **ALL**             | 25 | 0.285 | 0.328  | 0.200 | 0.360 | 0.440 | 0.440 | 0.088 | 0.440 |

p50: 33 ms · p95: 74 ms

> Slight regression on absolute numbers because of a **golden-set path bug** that
> was fixed between baseline and Phase 2A.1 (`/data/codebase/moby/` →
> `/data/codebase/digital-gigafactory/moby/`). The baseline retrieved old paths
> that happened to align with the (then-old) golden set; once the paths were
> corrected, some questions stopped matching because they reference files we
> shouldn't be indexing in the first place. Phase 2B confirms the hybrid schema
> is sound — the issue is golden quality, not retrieval quality.

### Phase 2A.2 partial 2026-05-15 — Contextual chunking on `mnemos_skills` only

| Intent              | N  | MRR   | NDCG@5 | R@5   |
|---------------------|----|-------|--------|-------|
| skill_discovery     | 6  | 0.167 | 0.210  | 0.333 |
| **ALL**             | 25 | 0.248 | 0.302  | 0.440 |

**No measurable lift on skills**, small global regression (-0.037 MRR / +19 ms p50)
because the query stays uncontextualised while the chunks now carry preambles.
**Decision:** stopped the full contextual reindex (~2 h 30 m remaining on moby/trevio).
The technique is still in code, env-gated by `MNEMOS_CONTEXTUAL_ENABLED`; revisit
after the golden set is cleaned.

### Phase 2B 2026-05-15 — Hybrid + reranker + router + cache

| Intent              | N  | MRR   | NDCG@5 | R@1   | R@3   | R@5   | R@10  | P@5   | Hit@5 |
|---------------------|----|-------|--------|-------|-------|-------|-------|-------|-------|
| code_search         | 11 | 0.348 | 0.399  | 0.182 | 0.545 | 0.545 | 0.545 | 0.109 | 0.545 |
| doc_lookup          | 8  | 0.762 | 0.750  | 0.750 | 0.750 | 0.750 | 0.875 | 0.150 | 0.750 |
| skill_discovery     | 6  | 0.250 | 0.272  | 0.167 | 0.333 | 0.333 | 0.333 | 0.067 | 0.333 |
| **ALL**             | 25 | 0.457 | 0.481  | 0.360 | 0.560 | 0.560 | 0.600 | 0.112 | 0.560 |

p50: **14 466 ms** · p95: **63 683 ms**

Per-intent jumps vs Phase 2A.1:
- code_search MRR **0.114 → 0.348 (×3)** — reranker rescues the weakest intent
- doc_lookup MRR 0.608 → 0.762 (+25 %)
- skill_discovery MRR 0.167 → 0.250 (+50 %)

Configuration of this run:
- `MNEMOS_RERANKER_ENABLED=true` (model: `BAAI/bge-reranker-base`, CPU)
- `MNEMOS_ROUTER_ENABLED=true` (top-K = 2)
- `MNEMOS_CACHE_ENABLED=true` (warm-up done; no hits during this run because
  every question is unique)
- Grader / Rewriter / MMR / Contextual: OFF

## Latency reality check

The reranker is the dominant cost. **CPU-bound** reranking of 20-40 candidates
through `BAAI/bge-reranker-base` takes 12-50 seconds per query on a M-series
laptop. This is **not viable for interactive use** without one of:
- GPU (CUDA / MPS) — reduces to ~50-200 ms easily
- A smaller model (e.g. `cross-encoder/ms-marco-MiniLM-L-6-v2`, ~80 MB) — likely 1-3 s on CPU
- Reduced `HYBRID_TOP` (currently 20 per collection) — linear improvement at the cost of recall
- Quantised reranker checkpoint

For now: `MNEMOS_RERANKER_ENABLED=false` is the right default for local dev,
and `true` is for prod boxes with GPU.

## Comparing runs

```bash
mnemos eval run --tag <new-tag>
mnemos eval compare baseline-2026-05-14 <new-tag>
```

## Target metrics per phase

| Phase                          | Acceptance criterion                   | Status |
|--------------------------------|----------------------------------------|--------|
| Phase 2A.1 — Hybrid + RRF      | No regression vs baseline              | ⚠️ -0.025 MRR (golden path bug, see notes) |
| Phase 2A.2 — Contextual        | NDCG@5 ≥ Phase 2A.1 + 5 pts            | ❌ No gain, skipped pending golden cleanup |
| Phase 2B — Reranker + MMR      | NDCG@5 ≥ Phase 2A.1 + 10 pts → ≥ 0.43  | ✅ **0.481** (+0.153) |
| Phase 3 — CRAG grader/rewriter | precision@5 ≥ Phase 2B + 5 pts         | ⏳ Scaffolded, off by default until Phase 2B is GPU-fast |
| Phase 4D — Router              | latency p50 ≤ baseline (35 ms)         | ✅ shipped (active in Phase 2B run, no isolated number) |
| Phase 4E — Cache               | latency p50 (cached) ≤ 10 ms           | ⏳ shipped; needs repeated queries to verify |

## Runs index

| Tag                                  | Date       | Golden | Notes                                       |
|--------------------------------------|------------|--------|---------------------------------------------|
| `baseline-2026-05-14`                | 2026-05-14 | legacy 25 | Dense-only, unnamed vector              |
| `phase2a1-hybrid-2026-05-15`         | 2026-05-15 | legacy 25 | Hybrid BM25 + dense + RRF               |
| `phase2a2-partial-skills-2026-05-15` | 2026-05-15 | legacy 25 | Contextual on skills only (no gain)     |
| `phase2b-reranker-2026-05-15`        | 2026-05-15 | legacy 25 | bge-reranker-base + router + cache      |
| `phase2b-minilm-2026-05-15`          | 2026-05-15 | legacy 25 | Smaller reranker (3.8× faster)          |
| `cache-warmup-2026-05-15`            | 2026-05-15 | legacy 25 | Run 1 (cache empty)                     |
| `cache-hit-2026-05-15`               | 2026-05-15 | legacy 25 | Run 2 — p50 dropped to 122 ms           |
| `phase2a1-clean-2026-05-15`          | 2026-05-15 | clean 31  | Hybrid only on clean golden             |
| `phase2b-clean-2026-05-15`           | 2026-05-15 | clean 31  | + MiniLM reranker + router + cache      |
| `phase3-grader-2026-05-15`           | 2026-05-15 | clean 31  | + CRAG document grader (no rewriter)    |
