# Mnemos — Evaluation

How to measure Mnemos quality on **your own** data, what each metric means,
and how to compare retrieval upgrades.

For Mnemos' published numbers, see [`EVAL.md`](EVAL.md). This file is the
HOWTO; that one is the changelog.

---

## Why evaluate

Every retrieval upgrade promises gains. Reranker says +47 % MRR.
Contextual says –49 % failure rate. Hybrid says –27 % miss rate.

In practice the gain depends on **your data, your queries, your domain**.
The same flag can lift one team's metrics and regress another's. The only
way to know is to measure, and the only fair way to measure is against a
**golden set** drawn from your own corpus.

Mnemos ships the harness for this and lets you replay any phase comparison
locally.

---

## The harness

```
eval/
  dataset/
    golden.yaml          # the questions + ground truth
    _candidates.yaml     # LLM-generated drafts, awaiting review
  runs/
    <tag>.json           # one file per `mnemos eval run --tag X`
  …
packages/eval/    # the Python library backing the CLI
  schema.py              # Pydantic models
  generator.py           # LLM-backed candidate generation
  loader.py              # YAML I/O
  metrics.py             # recall@k, precision@k, MRR, NDCG@k, hit_rate@k
  runner.py              # HTTP runner against a live server
  reporter.py            # Rich tables + JSON dumps
```

---

## Step 1 — Build a golden set

A golden set is a list of `(query, expected_files)` pairs. Mnemos lets you
generate candidates semi-automatically by asking your LLM "given this code
chunk, what natural-language question would a developer ask such that this
chunk is the best answer?"

```bash
mnemos eval generate --collection mnemos_code_myproject --count 10
mnemos eval generate --collection mnemos_skills    --count 6
mnemos eval generate --collection mnemos_docs      --count 5
```

The candidates land in `evals/dataset/_candidates.yaml`:

```yaml
- id: q-f4d2e7d2
  query: How do I cancel an in-progress ride?
  intent: code_search
  suggested_files:
    - /data/codebase/.../ride/application/cancel.go
  source_collection: mnemos_code_myproject
  source_chunk_preview: |
    func (s *Service) Cancel(ctx context.Context, id RideID) error { ... }
  reviewed: false
  accepted: false
```

### Step 2 — Review

Open `_candidates.yaml`. For each candidate:
- **Keep it**: set `reviewed: true` and `accepted: true`.
- **Drop it**: set `reviewed: true` and `accepted: false`.
- **Postpone**: leave both `false`.

Review takes ~30 seconds per question. The LLM proposes — you decide.
A clean golden set is the difference between meaningful and noisy numbers.

### Step 3 — Promote

```bash
mnemos eval promote
```

This moves every `(reviewed=true, accepted=true)` candidate into
`golden.yaml`. Reviewed-and-rejected ones are deleted. Un-reviewed ones
stay for later.

Your final `golden.yaml` looks like:

```yaml
- id: q-f4d2e7d2
  query: How do I cancel an in-progress ride?
  intent: code_search
  expected_collections: [mnemos_code_myproject]
  expected_files:
    - /data/codebase/.../ride/application/cancel.go
  expected_chunks: []         # optional, finer-grained
  relevance_grades: {}        # optional, for NDCG with graded relevance
  k_relevant: 1
```

---

## Step 4 — Measure

```bash
mnemos eval run --tag baseline
```

That single command:
1. Loads `golden.yaml`.
2. For each question, calls the appropriate REST endpoint
   (`/api/search`, `/api/search-code`, `/api/search-skills`,
   `/api/search-memory`) based on `intent`.
3. Records the top-K results, scores, and latency.
4. Computes the metrics below.
5. Writes `evals/runs/baseline.json` and prints a Rich table.

### Metrics

For each value of `k ∈ {1, 3, 5, 10}` and each intent group:

- **Recall@k** — proportion of expected files retrieved in the top-k.
  Answers "did we find it at all?"
- **Precision@k** — proportion of top-k that's in the expected set.
  Answers "how clean is what we returned?"
- **MRR** — Mean Reciprocal Rank. Average of `1 / rank` of the first
  expected file. Sensitive to position: a hit at rank 1 is twice as good
  as a hit at rank 2.
- **NDCG@k** — Normalized Discounted Cumulative Gain. Like MRR but
  rewards relevance across the whole top-k, with logarithmic position
  decay. Supports graded relevance (set `relevance_grades` in the golden
  item).
- **Hit_rate@k** — binary: did **any** expected file land in the top-k?
  Useful as a quick sanity metric.

Plus:
- **Latency p50 / p95** — wall-clock end-to-end (your client → server → server response).

### Per-intent vs overall

The golden set declares an `intent` on every question
(`code_search`, `doc_lookup`, `skill_discovery`, `memory_recall`,
`general`). The report shows one row per intent **plus** a global row.
Per-intent numbers usually tell the real story — for example, the
reranker doubled `code_search` MRR while leaving `doc_lookup` mostly
unchanged.

---

## Step 5 — Compare

Toggle a feature, restart the server, run again with a new tag:

```bash
MNEMOS_RERANKER_ENABLED=true docker compose up -d rag-server
# wait ~30s for the model to warm up (first call is slow)
mnemos eval run --tag with-reranker

mnemos eval compare baseline with-reranker
```

The compare output:

```
Comparing baseline vs with-reranker  (25 vs 25 questions)

| Metric           | baseline | with-reranker | Δ      |
| MRR              | 0.310    | 0.457         | +0.147 |
| NDCG@5           | 0.351    | 0.481         | +0.130 |
| Recall@5         | 0.480    | 0.560         | +0.080 |
| …                                                  |
```

Green = improvement, red = regression. **Always run the comparison after
every flag flip** — a feature that helps one corpus can hurt another.

---

## Quality of your golden set

The harness is only as honest as the golden set behind it. Smell-test
yours regularly:

| Smell | What it means | Fix |
|------|---------------|-----|
| Many `expected_files` point at `.bak`, generated files, or build artefacts | Your sampler picked chunks from junk paths | Improve `_should_skip` (see `server/api.py`) and re-generate |
| Questions look like the chunk verbatim | The LLM copied the answer | Strengthen the generator prompt (rare with `llama3.1+`) |
| All questions are too literal | The LLM didn't generalise | Use a stronger LLM for generation (Anthropic Sonnet, OpenAI GPT-4o) |
| You can answer every question by Ctrl-F | Mnemos won't shine here; it's good for **conceptual** questions | Mix in higher-level questions ("what's the convention for X?") |
| Numbers stay flat across all phases | The golden set queries land in already-easy regions of the index | Add deliberately hard questions: typos, partial names, multi-file answers |

A few useful patterns for hard questions:
- "Where is the validation logic for ride cancellation?" (instead of `func Cancel`)
- "How are tenant API keys stored?" (instead of `tenant_api_key`)
- "What does the config say about Qdrant?" (instead of `qdrant_host`)

---

## Running the eval in CI

`mnemos eval run` exits 0 even if metrics regress. To enforce thresholds in CI,
post-process `evals/runs/<tag>.json`:

```python
import json, sys
report = json.load(open(f"evals/runs/{sys.argv[1]}.json"))["report"]
if report["overall"]["mrr"] < 0.40:
    print(f"MRR dropped below threshold: {report['overall']['mrr']}")
    sys.exit(1)
```

Compare two runs programmatically:

```python
import json
a = json.load(open("evals/runs/baseline.json"))["report"]["overall"]
b = json.load(open("evals/runs/new.json"))["report"]["overall"]
delta_mrr = b["mrr"] - a["mrr"]
assert delta_mrr >= -0.01, f"MRR regression: {delta_mrr}"
```

---

## When in doubt

The full conceptual walk-through (every stage, every flag) lives in
[`RETRIEVAL_PIPELINE.md`](RETRIEVAL_PIPELINE.md). If a metric moves in a
way you don't understand, that's the place to start.
