# Mnemos — Memory Pipeline

How a commit becomes a searchable memory.

## Overview

```
git commit + push
  │
  ▼
┌──────────────────────────┐
│ pre-push hook            │  scripts/hooks/pre-push
│ (or post-commit)         │
└──────────┬───────────────┘
           │ POST /api/memory/extract
           │   { commit_message, diff }
           ▼
┌──────────────────────────┐
│ MemoryExtractor          │  packages/core/memory_extractor.py
│ – LLM JSON extraction    │
│ – classifies into:       │
│     decision             │
│     pattern              │
│     convention           │
│     lesson               │
└──────────┬───────────────┘
           │ list[ExtractedMemory]
           ▼
┌──────────────────────────┐
│ Deduplicator             │  packages/core/deduplicator.py
│ – cosine ≥ threshold?    │
│   ├─ merge (LLM)         │
│   └─ replace             │
│ – else: insert           │
└──────────┬───────────────┘
           │ Qdrant upsert (named hybrid vectors)
           ▼
       mnemos_memory  (status=approved → searchable)
           │
           │   reject  ──▶ out of search
           ▼
       searchable
```

## Hook installation

```bash
./scripts/install-hooks.sh --global --watch ~/code/your-org
```

This:

1. Copies hooks to `~/.config/git/hooks/`.
2. Sets `git config --global core.hooksPath` so every repo uses them.
3. Adds your watched path to `~/.config/mnemos/repos` — only repos under
   this path trigger memory extraction.

> **`repos` is not the indexing list.** The same installer also lays down
> `post-merge` / `post-checkout`, which index code on `git pull` and are gated
> on a separate list (`~/.config/mnemos/index-repos`, populated by
> `--watch-index`). Two lists because the costs differ by orders of magnitude:
> indexing is an HTTP POST per changed file, extraction is an LLM call. Keep
> `repos` to the repos where real decisions get made. See
> [QUICKSTART](QUICKSTART.md#7-turn-on-the-git-hooks-recommended).

### Two trigger modes

| Mode | When it fires | Default? |
|------|---------------|---------|
| `pre-push` | Once per `git push`, analyses **all commits** about to be pushed | ✅ |
| `post-commit` | After every commit, analyses **one commit** | – |
| `both` | Both of the above | – |

Set `MNEMOS_HOOK_TRIGGER=pre-push|post-commit|both` in your shell profile or
in `~/.config/mnemos/config` (read by hooks launched from GUI clients and
IDEs, which never see a shell profile). The environment wins over the file.

### Hook semantics

- **Non-blocking**: the request runs in the background with `&`. Git never waits.
- **Fail-safe**: if Mnemos is unreachable, the hook exits 0 and `git push`
  succeeds normally.
- **Chained**: the repo's own hook still runs — the versioned
  `.githooks/<name>` if the repo ships one, else `.git/hooks/<name>`. Mnemos
  hooks never replace yours.
- **Authenticated**: with `MNEMOS_API_KEY` set, the hook sends it as a bearer
  key. Extraction needs a tenant allowed to write every tag (an admin key, or
  `write: ["*"]`), since the LLM picks the tags.

## Extraction prompt

`MemoryExtractor` ships a system prompt that's deliberately strict. In
substance:

> Every memory MUST be grounded in concrete text from the diff — never
> invented from training data.
>
> Types (exactly one per memory):
> - `decision` — an architectural or design choice
> - `pattern` — a code pattern introduced or reinforced
> - `convention` — a naming or structural rule the change establishes
> - `lesson` — a bug fixed, workaround applied, or pitfall documented
>
> Skip trivial / mechanical changes (typos, import ordering, version bumps,
> formatting, generated or vendored code). Each memory is 1–3 sentences and
> explains the **why**. An empty array is a valid and frequent answer.
>
> Return only a JSON array of `{content, memory_type, project, tags}`.

The diff is truncated at 32 KB. Output goes through JSON mode
(`response_format` for OpenAI, `format: "json"` for Ollama, system-prompt
instruction for Anthropic). An LLM failure is not swallowed:
`/api/memory/extract` answers 502, so a broken endpoint does not pass for "no
memory worth keeping".

## Deduplication

Every new memory is embedded, and the existing closest memory is fetched
with `query_points(limit=1)`. If the cosine similarity is **≥
`MNEMOS_DEDUP_THRESHOLD`**, one of two strategies runs. The server's default
is 0.85; `.env.example` ships 0.80, calibrated on a real corpus where the one
genuine near-duplicate pair scored 0.844 and the next closest pair 0.646.
Both the threshold and the strategy can be changed live from the dashboard.

### `merge` (default)

The LLM is given both memory texts and asked to write a single
consolidated text. The existing point is **upserted in place** with:
- new content = LLM merge
- new vector = embedding of the merged content
- tags = union of both tag lists
- `last_indexed_at` = now (`id`, `created_at` and `status` are kept)

If the merge call fails, the two texts are concatenated instead.

This keeps the memory count low and the text up to date.

### `replace`

The existing point is deleted, and the new memory is inserted fresh under a
new id: newer wins. Nothing of the old entry is kept.

Set with `MNEMOS_DEDUP_STRATEGY=merge|replace`.

## Status and review

Memories are written **`approved`**, so they are searchable as soon as they
are stored. Gating writes behind a human review made the empty state the
default one: six days of extraction produced fifteen memories and zero
approvals. Search still returns only `approved` entries
(`SearchService.search_memory` filters on it), so `status` now serves
**pruning**: reject a bad memory to take it out of search.

`pending` only happens when a caller asks for it (`status: "pending"` on
`POST /api/memory`).

```bash
mnemos memory list --status approved  # what's live (the default lists pending)
mnemos memory reject <id>             # take a memory out of search
mnemos memory approve <id>            # put it back / surface a pending one
mnemos memory review                  # walk pending entries one by one
```

You can also approve/reject via the MCP tool `mnemos_memory_review` so your
agent can curate its own memory pool, or reject from the dashboard's Memories
page.

## Memory schema

```python
MemoryEntry {
  id: str (uuid),
  content: str,
  project: str | None,
  memory_type: "decision" | "pattern" | "convention" | "lesson"
               | "general" | "note",
  tags: list[str],
  status: "pending" | "approved" | "rejected",
  created_at: ISO-8601,
  # Internal indexing fields:
  file_path: f"memory/{id}",
  chunk_type: "memory",
  last_indexed_at, file_mtime,
}
```

`memory_type` is free text: the four types above come from extraction,
`general` is the default of `mnemos memory add` and `POST /api/memory`, `note`
the default of the `mnemos_memory` MCP tool and of an extracted memory the
LLM left untyped.

The `file_path` value (`memory/<id>`) is synthetic — it gives `search_memory`
a stable identifier compatible with the rest of the retrieval stack. The REST
search endpoint returns it as `memory://<id>`.

## Manual memory entry

You don't have to wait for a commit:

```bash
mnemos memory add "Always use flat resource paths in REST routes" \
  --project myproject \
  --type convention \
  --tags routing --tags api
```

Manual entries go through the same deduplication and are stored `approved`.

## Searching memories

Via CLI:
```bash
mnemos search-memory "API routing decisions" --tags myproject                  # OR
mnemos search-memory "API routing decisions" --tags-all myproject,api --type decision
```

Via MCP (agent):
```python
mnemos_search_memory(
  query="API routing decisions",
  tags_any=["myproject"],
  memory_type="decision",
  limit=5,
)
```

Memories participate in the hybrid retrieval stack — dense + BM25 +
RRF + optional reranker. They're a first-class collection, left out of
`mnemos_search` unless you name `mnemos_memory` in `collections`.

## What the LLM costs you

For a typical commit (one diff hunk, one extraction call):

| Provider | Approximate cost per commit |
|----------|----------------------------|
| Ollama (local) | $0, ~2-5 s wall time |
| Anthropic Haiku | < $0.001 |
| Anthropic Sonnet | ~$0.003 |
| OpenAI GPT-4o-mini | ~$0.0005 |
| Groq Llama 3.1 70B | ~$0.0002 |

Multiply by typical commit frequency (10-50/day per developer) — even with
Sonnet, you're under a dollar a day per developer.

For the **merge** strategy, every duplicate hit also costs one merge call.
That's usually cheap because merges happen at most a few times per day per
project.

## Disabling the pipeline

Memory extraction is **off** if no git hooks are installed. The
`MemoryExtractor` is only invoked through the hooks (or manual calls to
`POST /api/memory/extract`). Nothing runs automatically at indexing time.

To turn off dedup, set `MNEMOS_DEDUP_THRESHOLD=1.01` in `.env` — every memory
gets inserted as fresh. (The dashboard caps the field at 1.0.)
