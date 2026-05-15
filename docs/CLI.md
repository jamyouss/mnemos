# Mnemos — CLI Reference

The `mnemos` CLI is the developer-facing companion to the MCP server. It
wraps the REST API and is the easiest way to drive indexing, evaluation,
and memory management from the terminal.

## Install

```bash
make install                    # creates ./venv + installs all packages
source venv/bin/activate
export MNEMOS_URL=http://localhost:8100
mnemos --help
```

## Environment

| Var | Default | Used by |
|-----|--------|--------|
| `MNEMOS_URL` | `http://localhost:8100` | Every command |
| `MNEMOS_LLM_PROVIDER` | `ollama` | `eval generate` |
| `MNEMOS_LLM_MODEL` | `llama3.1:8b` | `eval generate` |
| `MNEMOS_LLM_API_KEY` | _empty_ | `eval generate` (anthropic / openai) |
| `MNEMOS_LLM_BASE_URL` | _empty_ | `eval generate` |
| `MNEMOS_OLLAMA_URL` | `http://localhost:11434` | `eval generate` (fallback for ollama) |
| `MNEMOS_EVAL_ROOT` | `eval` | Where `mnemos eval *` reads/writes |

---

## Status

```bash
mnemos status
```

Prints server health and per-collection point counts.

---

## Search

### `mnemos search QUERY`

Cross-collection semantic search.

```bash
mnemos search "JWT validation"
mnemos search "JWT validation" --limit 10
mnemos search "JWT validation" --collection mnemos_code_moby
mnemos search "logger init" --file-type go
mnemos search "config" --path-filter /data/codebase/myproject/
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--limit` | int | `5` | Number of results |
| `--collection` | str (repeatable) | _all_ | Restrict to specific collections |
| `--file-type` | str (repeatable) | _any_ | Filter by `language` field |
| `--path-filter` | str | _none_ | Substring filter on `file_path` |

### `mnemos search-code QUERY`

Code-only search with structured filters.

```bash
mnemos search-code "ride cancel" --project moby --language go --symbol-type func
```

| Flag | Description |
|------|-------------|
| `--limit` | Results count (default 5) |
| `--language` | `go`, `vue`, `typescript`, etc. |
| `--symbol-type` | `function`, `type`, `method` (from chunker metadata) |
| `--project` | Maps to `mnemos_code_<project>` |
| `--path-filter` | Substring on `file_path` |

### `mnemos search-skills QUERY`

Find the most relevant agent skill.

```bash
mnemos search-skills "performance debugging"
mnemos search-skills "performance debugging" --limit 5
```

---

## Indexing

### `mnemos reindex`

Trigger a server-side reindex.

```bash
# Initial: drop + recreate with the hybrid schema, full directory scan, 4 workers
mnemos reindex --recreate --full --workers 4 \
       --collection mnemos_code_moby \
       --path /data/codebase/digital-gigafactory/moby

# Subsequent incremental: just re-index a single file
mnemos reindex --collection mnemos_code_moby \
       --path /data/codebase/digital-gigafactory/moby/services/handler.go
```

| Flag | Description |
|------|-------------|
| `--collection` (required) | Collection name |
| `--path` | Container path of a file or directory |
| `--full` | Recursively walk `--path`. Without `--full`, only the path itself is indexed. |
| `--recreate` | Drop + recreate the collection before indexing. Required to migrate to hybrid. |
| `--workers` | Parallel worker threads (use 4 with contextual chunking on) |

The reindex runs as a **background task** server-side. The CLI returns
immediately with `reindex_started`. Watch progress with `mnemos status`.

---

## Memory

### `mnemos memory list`

```bash
mnemos memory list                     # default: status=pending
mnemos memory list --status approved
mnemos memory list --status rejected
```

### `mnemos memory add CONTENT`

Insert a new memory directly (skips extraction, status defaults to `approved`).

```bash
mnemos memory add "Always use flat resource paths in REST routes" \
       --project moby \
       --type convention \
       --tags routing,api
```

### `mnemos memory approve <id>` / `mnemos memory reject <id>`

Move a pending memory to `approved` or `rejected`.

---

## Evaluation

The `mnemos eval` group ships a complete evaluation harness — golden set
generation, run, compare.

### `mnemos eval generate`

Generate candidate Q/A pairs from indexed chunks via your LLM.

```bash
mnemos eval generate --collection mnemos_code_moby --count 10
mnemos eval generate --collection mnemos_skills --count 5 \
       --provider anthropic --model claude-haiku-4-5 \
       --api-key "$ANTHROPIC_API_KEY"
```

| Flag | Description |
|------|-------------|
| `--collection` (required) | Source collection |
| `--count` | Number of candidates to generate (default 10) |
| `--provider` | `ollama` / `anthropic` / `openai` (default $MNEMOS_LLM_PROVIDER) |
| `--model` | Model name (default $MNEMOS_LLM_MODEL) |
| `--api-key` | API key for cloud providers |
| `--base-url` | Override base URL (vLLM, LM Studio, …) |
| `--seed` | Reproducible sampling |

Candidates land in `eval/dataset/_candidates.yaml`. Edit the file, set
`reviewed: true` and `accepted: true` on the ones you want to keep, then:

### `mnemos eval promote`

```bash
mnemos eval promote
```

Moves accepted candidates into `eval/dataset/golden.yaml`. Reviewed but
rejected candidates are discarded; un-reviewed ones stay for next time.

### `mnemos eval run`

```bash
mnemos eval run --tag baseline-2026-05-15
mnemos eval run --tag with-reranker --limit 10
```

Runs the golden set against the live Mnemos server. Computes recall@k,
precision@k, MRR, NDCG@k, hit_rate@k and per-intent breakdown. Saves to
`eval/runs/<tag>.json` and prints a Rich table.

### `mnemos eval compare TAG_A TAG_B`

```bash
mnemos eval compare baseline-2026-05-14 with-reranker-2026-05-15
```

Side-by-side diff of overall metrics with Δ colourised.

### `mnemos eval list`

```bash
mnemos eval list
```

Lists every run currently in `eval/runs/`.

---

## Tips

- **Test a flag toggle quickly**: `MNEMOS_RERANKER_ENABLED=true docker compose
  up -d rag-server` (no rebuild needed for env flips).
- **One-shot search to a specific tenant**: set `MNEMOS_URL` to the tenant's
  endpoint and add the API key in a `~/.mnemos/auth` file (deployed mode).
- **Use `--collection` to avoid the router** when you know exactly which
  collection holds the answer — saves a routing decision.
