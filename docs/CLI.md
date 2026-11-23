# Mnemos — CLI Reference

The `mnemos` CLI is the developer-facing companion to the MCP server. It
wraps the REST API and is the easiest way to drive indexing, evaluation,
memory management and the knowledge connectors from the terminal.

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
| `MNEMOS_API_KEY` | _empty_ | Every command, when the server has `MNEMOS_AUTH_ENABLED=true` (sent as `Authorization: Bearer`) |
| `MNEMOS_LLM_PROVIDER` | `ollama` | `eval generate` |
| `MNEMOS_LLM_MODEL` | `llama3.1:8b` | `eval generate` |
| `MNEMOS_LLM_API_KEY` | _empty_ | `eval generate` (anthropic / openai) |
| `MNEMOS_LLM_BASE_URL` | _empty_ | `eval generate` |
| `MNEMOS_OLLAMA_URL` | _empty_ | `eval generate`, when `MNEMOS_LLM_BASE_URL` is empty. Both empty with ollama: probes `http://ollama:11434`, `http://host.docker.internal:11434`, then `http://localhost:11434` |
| `MNEMOS_EVAL_ROOT` | `evals` | Where `mnemos eval *` reads/writes |
| `MNEMOS_CONNECTORS_CONFIG` | _empty_ | `ingest run` / `ingest serve`: read the sources from this file instead of the server, see [`CONNECTORS.md`](CONNECTORS.md) |

---

## Status

```bash
mnemos status
mnemos doctor
```

`status` prints server health and, per collection, its vector count, point
count and status. `doctor` runs an end-to-end check — `/health`, the
collections behind `/api/status`, the five core collections present, at least
one with points, a `/api/search` round-trip — and exits 1 if a check fails.

---

## Search

### `mnemos search QUERY`

Cross-collection semantic search (every collection but `mnemos_memory`
unless `--collection` says otherwise).

```bash
mnemos search "JWT validation"
mnemos search "JWT validation" --limit 10
mnemos search "JWT validation" --collection mnemos_code
mnemos search "logger init" --file-type go
mnemos search "rollback" --collection mnemos_knowledge --tags jira:OPS
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--limit` | int | `5` | Number of results |
| `--collection` | str (repeatable) | _all but memory_ | Restrict to specific collections |
| `--file-type` | str (repeatable) | _any_ | Filter on the chunk's `language`, derived from the file name: `go`, `python`, `typescript`, `javascript`, `vue`, `markdown`, `yaml`, `json`, `shell`, `sql`, `csv`, `tsv`… (`core.indexer.language_for`). Pushed content with no recognised extension carries none |
| `--path-filter` | str | _none_ | One file or one directory, as a stored container path: `/data/codebase/myproject/main.go` keeps that file, `/data/codebase/myproject/api` (trailing `/` optional) every file under it. Matches whole path segments: `api` does not match `api-old` |
| `--tags` | comma-separated | _none_ | OR filter on the `tags` payload (sent as `tags_any`) |
| `--tags-all` | comma-separated | _none_ | AND filter: every tag must be present (sent as `tags_all`) |
| `--mode` | `preview` / `full` | `preview` | `preview` truncates each chunk; `full` returns whole chunks |

### `mnemos search-code QUERY`

Code-only search (`mnemos_code`) with structured filters.

```bash
mnemos search-code "ride cancel" --tags myproject --language go --symbol-type function
mnemos search-code "auth"        --tags acme,webshop                  # OR
mnemos search-code "auth"        --tags-all acme,vue3                 # AND
```

| Flag | Description |
|------|-------------|
| `--limit` | Results count (default 3) |
| `--language` | Exact `language` value, as for `--file-type` on `search` |
| `--symbol-type` | Exact `chunk_type` from the chunker: Go `function` (methods included), `type`, `header`; Vue `template`, `script`, `style`; Markdown `section`; CSV/TSV `rows`; fallback `file`, `window` |
| `--tags` | Comma-separated tags (OR filter on the `tags` payload) |
| `--tags-all` | Comma-separated tags (AND filter — every tag must be present) |
| `--path-filter` | One file or a directory, as for `search` |
| `--mode` | `preview` (default) or `full` |

### `mnemos search-skills QUERY`

Find the most relevant agent skill.

```bash
mnemos search-skills "performance debugging"
mnemos search-skills "performance debugging" --limit 5
```

`--limit` defaults to 3.

### `mnemos search-memory QUERY`

Search memories. Only `approved` entries come back.

```bash
mnemos search-memory "why flat REST paths"
mnemos search-memory "retry policy" --type decision --tags webshop
```

| Flag | Description |
|------|-------------|
| `--limit` | Results count (default 5) |
| `--type` | `memory_type` filter (`decision`, `pattern`, `lesson`, `convention`, `note`…) |
| `--tags` / `--tags-all` | Comma-separated, OR / AND |
| `--mode` | `preview` (default) or `full` |

---

## Indexing

### `mnemos reindex`

Trigger a server-side reindex.

```bash
# First index of an empty collection: full directory scan, 4 workers
mnemos reindex --full --workers 4 \
       --collection mnemos_code \
       --path /data/codebase/myproject \
       --tags myproject,go

# Re-index a single file (tags from the projects mapping)
mnemos reindex --collection mnemos_code \
       --path /data/codebase/myproject/services/handler.go
```

| Flag | Description |
|------|-------------|
| `--collection` (required) | Collection name (e.g. `mnemos_code`, `mnemos_docs`) |
| `--path` | Container path of a file or directory. Without it, nothing is indexed (`no_path`) |
| `--tags` | Comma-separated tags applied to every file under `--path`. Overrides the tags resolved from the server's projects mapping. First tag is the primary (display label). |
| `--full` | Recursively walk `--path`. Without `--full`, only the path itself is indexed. |
| `--recreate` | Drop + recreate the **whole** collection before indexing — every project in it, git connector files included, not just `--path`. Only for a schema migration (e.g. to hybrid) or a deliberate rebuild |
| `--workers` | Parallel worker threads (default 1; use 4 with contextual chunking on) |
| `--exclude-ext` | Extra extension to skip **for this run**, repeatable, leading dot optional (`--exclude-ext .proto --exclude-ext csv`) |
| `--exclude-dir` | Extra directory name to skip **for this run**, repeatable, matched anywhere in the path (`--exclude-dir mocks`) |

Both exclude flags **add to** the built-in deny list and to whatever the
projects mapping declares — they never replace either. They also last
only for the run: the watcher and the push API do not see them, so a file
excluded by a flag comes back on the next change. For a rule that sticks, put
it in the projects mapping (dashboard Projects page) — see
[`CONFIGURATION.md`](CONFIGURATION.md#per-project-ignore-rules).

Without `--tags`, Mnemos resolves tags from the projects mapping stored on
the server (longest path-prefix wins). Without a matching entry, it falls
back to cumulative path segments. See [`CONFIGURATION.md`](CONFIGURATION.md#tags--scoping-chunks-across-projects).

Reindexing a file replaces its chunks, so re-running without `--recreate` is
how to re-tag or re-chunk a project. The reindex runs as a **background
task** server-side: the CLI returns immediately with `reindex_started`.
`mnemos status` shows the point counts moving.

---

## Memory

Every write path — `mnemos memory add`, the `mnemos_memory` MCP tool and
the git-hook extraction — stores memories as `approved`, so they are
searchable at once. Review is for taking a bad one back out, not for letting
good ones in.

### `mnemos memory list`

```bash
mnemos memory list                     # default: status=pending
mnemos memory list --status approved
mnemos memory list --status rejected
```

The default `pending` is usually empty: nothing writes that status unless a
client asks for it through `POST /api/memory`.

### `mnemos memory add CONTENT`

Insert a memory directly, skipping extraction. Lands as `approved`.

```bash
mnemos memory add "Chose flat REST paths over nested ones: nesting encoded a \
hierarchy that changed twice in a year" \
       --project myproject \
       --type decision \
       --tags routing --tags api
```

`--type` defaults to `general`; prefer `decision`, `pattern`, `lesson`,
`convention` or `note`. `--tags` is **repeatable**, one tag per flag (a
comma-separated value would be stored as one tag). Tags are how a memory is
found later from another project — an untagged entry is reachable by wording
alone.

Prefer recording the reasoning over the conclusion: "chose X over Y because Z"
stays useful when the context shifts, "we use X" does not.

### `mnemos memory reject <id>` / `mnemos memory approve <id>`

Act on a single entry. `reject` takes a wrong memory out of search;
`approve` puts it back. The dashboard's Memories page does the same with one
click per entry.

### `mnemos memory review`

Walk the `pending` entries one at a time — full content, then approve,
reject, skip or quit.

```bash
mnemos memory review
mnemos memory review --approve-all    # bulk, asks for confirmation first
```

It dates from when every extracted memory waited as `pending`; with writes
now approved, it only has entries left from that time or written `pending`
on purpose.

---

## Knowledge connectors

`mnemos ingest` syncs Confluence, Jira, web sites and remote git
repositories into Mnemos:

```bash
mnemos ingest run                       # every source of the server's connectors
mnemos ingest serve                     # same, then on a timer (the compose `ingester`)
mnemos ingest confluence --space ENG    # one source, from flags
mnemos ingest jira --project OPS
mnemos ingest web https://docs.example.com/guide/
mnemos ingest git https://github.com/acme/app.git
mnemos ingest -v run                    # log every pushed / deleted document
```

Options, environment and sync rules: [`CONNECTORS.md`](CONNECTORS.md).

---

## Evaluation

The `mnemos eval` group ships a complete evaluation harness — golden set
generation, run, compare.

### `mnemos eval generate`

Generate candidate Q/A pairs from indexed chunks via your LLM.

```bash
mnemos eval generate --collection mnemos_code --count 10
mnemos eval generate --collection mnemos_skills --count 5 \
       --provider anthropic --model claude-haiku-4-5 \
       --api-key "$ANTHROPIC_API_KEY"
```

| Flag | Description |
|------|-------------|
| `--collection` (required) | Source collection |
| `--count` | Number of candidates to generate (default 10) |
| `--provider` | `ollama` / `anthropic` / `openai` (default `$MNEMOS_LLM_PROVIDER`, else `ollama`) |
| `--model` | Model name (default `$MNEMOS_LLM_MODEL`, else `llama3.1:8b`) |
| `--api-key` | API key for cloud providers (default `$MNEMOS_LLM_API_KEY`) |
| `--base-url` | Override base URL (vLLM, LM Studio, …; default `$MNEMOS_LLM_BASE_URL`, else `$MNEMOS_OLLAMA_URL`) |
| `--seed` | Reproducible sampling |

Candidates land in `evals/dataset/_candidates.yaml`. Edit the file, set
`reviewed: true` and `accepted: true` on the ones you want to keep, then:

### `mnemos eval promote`

```bash
mnemos eval promote
```

Moves accepted candidates into `evals/dataset/golden.yaml`. Reviewed but
rejected candidates are discarded; un-reviewed ones stay for next time.

### `mnemos eval run`

```bash
mnemos eval run --tag baseline-2026-05-15
mnemos eval run --tag with-reranker --limit 5
```

Runs the golden set against the live Mnemos server (`--limit`, the top-K per
query, defaults to 10). Computes recall@k, precision@k, MRR, NDCG@k,
hit_rate@k and per-intent breakdown. Saves to `evals/runs/<tag>.json` and
prints a Rich table.

### `mnemos eval compare TAG_A TAG_B`

```bash
mnemos eval compare baseline-2026-05-14 with-reranker-2026-05-15
```

Side-by-side diff of overall metrics with Δ colourised.

### `mnemos eval list`

```bash
mnemos eval list
```

Lists every run currently in `evals/runs/`.

---

## Tips

- **Test a flag toggle quickly**: flip it on the dashboard's Configuration
  page (live for the retrieval switches), or set it in `.env` and run
  `docker compose up -d rag-server` — a variable in front of `docker compose`
  does not reach the container.
- **One-shot search to another server**: set `MNEMOS_URL` to its endpoint and
  `MNEMOS_API_KEY` to your key when it has auth enabled.
- **Pass `--collection`** when you know which collection holds the answer:
  nothing picks it for you, and fewer collections means fewer unrelated hits.
