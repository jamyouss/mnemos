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
           │   { commit_message, diff, author }
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
│ – cosine ≥ 0.85?         │
│   ├─ merge (LLM)         │
│   └─ replace             │
│ – else: insert           │
└──────────┬───────────────┘
           │ Qdrant upsert (named hybrid vectors)
           ▼
       mnemos_memory  (status=pending)
           │
           │   approve  ──┐
           │   reject   ──┤
           ▼              ▼
       searchable   discarded
```

## Hook installation

```bash
./scripts/install-hooks.sh --global --watch ~/Developments/Projects/your-org
```

This:

1. Copies hooks to `~/.config/git/hooks/`.
2. Sets `git config --global core.hooksPath` so every repo uses them.
3. Adds your watched path to `~/.config/mnemos/repos` — only repos under
   this path trigger the hook.

### Two trigger modes

| Mode | When it fires | Default? |
|------|---------------|---------|
| `pre-push` | Once per `git push`, analyses **all commits** about to be pushed | ✅ |
| `post-commit` | After every commit, analyses **one commit** | – |
| `both` | Both of the above | – |

Set with `export MNEMOS_HOOK_TRIGGER=pre-push|post-commit|both`.

### Hook semantics

- **Non-blocking**: hooks run in the background with `&`. Git never waits.
- **Fail-safe**: if Mnemos is unreachable, the hook exits 0 and `git push`
  succeeds normally.
- **Chained**: if you had an existing local hook in `.git/hooks/`, it runs
  too. Mnemos hooks never replace yours.

## Extraction prompt

`MemoryExtractor` ships a system prompt that's deliberately strict:

> Extract actionable memories worth remembering for future work.
>
> Types:
> - `decision` — architectural / design choices
> - `pattern` — code patterns introduced or established
> - `convention` — naming or structural conventions
> - `lesson` — bugs fixed or workarounds applied
>
> Return a JSON array. Be concise. Focus on **why**, not what. Skip trivial
> changes (typos, formatting, import ordering).

Output is forced through JSON mode (`response_format` for OpenAI,
`format: "json"` for Ollama, system-prompt instruction for Anthropic).

## Deduplication

Every new memory is embedded, and the existing closest memory is fetched
with `query_points(limit=1)`. If the cosine similarity is **≥
`MNEMOS_DEDUP_THRESHOLD`** (0.85 default), one of two strategies runs:

### `merge` (default)

The LLM is given both memory texts and asked to write a single
consolidated text. The existing point is **upserted in place** with:
- new content = LLM merge
- new vector = embedding of the merged content
- tags = union of both tag lists
- timestamp = now

This keeps the memory count low and the text up to date.

### `replace`

The existing point is deleted, and the new memory is inserted fresh.
Use this when you want history (audit log of replacements), since the
old point id is recorded in `DeduplicationResult.merged_with`.

Set with `MNEMOS_DEDUP_STRATEGY=merge|replace`.

## Approval workflow

Newly extracted memories land in `status=pending`. They are **invisible to
search** until you approve them — `SearchService.search_memory` filters on
`status=approved`.

```bash
mnemos memory list                    # see pending memories
mnemos memory list --status approved  # see what's already live
mnemos memory approve <id>            # surface it in search
mnemos memory reject <id>             # discard
```

You can also approve/reject via the MCP tool `mnemos_memory_review` so your
agent can curate its own memory pool.

## Memory schema

```python
MemoryEntry {
  id: str (uuid),
  content: str,
  project: str | None,
  topic: str | None,
  memory_type: "decision" | "pattern" | "convention" | "lesson" | "general",
  tags: list[str],
  status: "pending" | "approved" | "rejected",
  created_at: ISO-8601,
  # Internal indexing fields:
  file_path: f"memory/{id}",
  chunk_type: "memory",
  last_indexed_at, file_mtime,
}
```

The `file_path` value (`memory/<id>`) is synthetic — it gives `search_memory`
a stable identifier compatible with the rest of the retrieval stack.

## Manual memory entry

You don't have to wait for a commit:

```bash
mnemos memory add "Always use flat resource paths in REST routes" \
  --project moby \
  --type convention \
  --tags routing,api
```

Manually-added memories go straight to `approved` (since you wrote them).

## Searching memories

Via CLI:
```bash
mnemos search-memory "API routing decisions" --project moby
```

Via MCP (agent):
```python
mnemos_search_memory(
  query="API routing decisions",
  project="moby",
  memory_type="decision",
  limit=5,
)
```

Memories participate in the full hybrid retrieval stack — dense + BM25 +
RRF + optional reranker. They're a first-class collection.

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

To turn off dedup, set `MNEMOS_DEDUP_THRESHOLD=1.01` — every memory gets
inserted as fresh.
