# Mnemos Memory Evolution — Design Spec

**Date:** 2026-04-06
**Status:** Approved

## Summary

Three improvements to Mnemos:

1. **Rename `rag_*` to `mnemos_*`** — collections, MCP tools, CLI, env vars
2. **Auto-extraction** — extract memories from git commits/pushes via Ollama
3. **Deduplication with merge** — detect similar memories and consolidate them

Plus a README refresh to reflect the new branding and features.

---

## 1. Renaming `rag_*` → `mnemos_*`

### Strategy

Big bang rename. No backward compatibility aliases.

### Collections (Qdrant)

| Old | New |
|-----|-----|
| `rag_skills` | `mnemos_skills` |
| `rag_docs` | `mnemos_docs` |
| `rag_memory` | `mnemos_memory` |
| `rag_code_moby` | `mnemos_code_moby` |
| `rag_code_trevio` | `mnemos_code_trevio` |
| `rag_code_infra` | `mnemos_code_infra` |

### MCP Tools

| Old | New |
|-----|-----|
| `rag_search` | `mnemos_search` |
| `rag_search_code` | `mnemos_search_code` |
| `rag_search_skills` | `mnemos_search_skills` |
| `rag_search_memory` | `mnemos_search_memory` |
| `rag_index_memory` | `mnemos_memory` |
| `rag_memory_list` | `mnemos_memory_list` |
| `rag_memory_review` | `mnemos_memory_review` |
| `rag_reindex` | `mnemos_reindex` |
| `rag_status` | `mnemos_status` |

### CLI

- Root command: `rag` → `mnemos`
- All subcommands remain the same (`search`, `search-code`, `search-skills`, `reindex`, `memory`, `status`)

### Environment Variables

| Old | New |
|-----|-----|
| `RAG_URL` | `MNEMOS_URL` |
| `RAG_MODE` | `MNEMOS_MODE` |
| `RAG_AUTH_ENABLED` | `MNEMOS_AUTH_ENABLED` |
| `RAG_STATE_DIR` | `MNEMOS_STATE_DIR` |
| `RAG_SERVER_URL` | `MNEMOS_SERVER_URL` |

### API Endpoints

Routes stay the same (`/api/search`, `/api/memory`, etc.). The `mnemos_` prefix is in collection and tool names, not in HTTP paths.

### Files Affected

- `packages/rag_core/collections.py` — collection names
- `server/mcp_tools.py` — tool names and descriptions
- `server/api.py` — collection name constants
- `server/search.py` — collection name references
- `server/config.py` — env var names
- `server/main.py` — any references
- `cli/main.py` — CLI group name, env var
- `watcher/main.py` — env var, collection references
- `docker-compose.yml` — env vars
- `docker-compose.prod.yml` — env vars
- `config/tenants.yaml` — if references exist
- `tests/` — all test files

---

## 2. Auto-Extraction from Git Commits

### Architecture

```
git hook (shell) → POST /api/memory/extract → MemoryExtractor (Ollama) → Deduplicator → Qdrant
```

### New Module: `packages/rag_core/memory_extractor.py`

**Class: `MemoryExtractor`**

```python
class MemoryExtractor:
    def __init__(self, ollama_url: str, model: str):
        ...

    def extract(self, commit_message: str, diff: str) -> list[ExtractedMemory]:
        """Send commit + diff to Ollama, return structured memories."""
        ...
```

**Data model: `ExtractedMemory`**

```python
@dataclass
class ExtractedMemory:
    content: str
    memory_type: str      # decision, pattern, lesson, convention
    project: str | None   # inferred from diff paths
    tags: list[str]
```

**Prompt strategy:**

The LLM receives a system prompt instructing it to extract actionable memories from a commit. It returns a JSON array. The prompt asks for:

- Architectural decisions made
- Patterns introduced or established
- Conventions followed or created
- Lessons learned (bugs fixed, workarounds)

The prompt explicitly tells the LLM to return `[]` if nothing worth remembering.

**Diff truncation:** If the diff exceeds 8000 tokens (~32KB), truncate to the first 32KB with a note. This prevents overwhelming the LLM context window.

### New Endpoint: `POST /api/memory/extract`

**Request:**
```json
{
    "commit_message": "string",
    "diff": "string",
    "author": "string (optional)"
}
```

**Response:**
```json
{
    "extracted": 3,
    "memories": [
        {"id": "uuid", "content": "...", "status": "pending"}
    ]
}
```

**Flow:**
1. Call `MemoryExtractor.extract()` with commit message + diff
2. For each extracted memory, run through `Deduplicator`
3. Store in `mnemos_memory` collection with status `pending`

### Global Git Hooks

Hooks are installed globally via `git config --global core.hooksPath ~/.config/git/hooks/`. No per-repo configuration needed.

**`scripts/hooks/mnemos-common.sh`** — shared logic sourced by both hooks:
- `mnemos_is_watched_repo()` — checks `~/.config/mnemos/repos` config to see if current repo is watched
- `mnemos_chain_local()` — chains to repo-local `.git/hooks/<hook>` if it exists (preserves existing hooks)
- `mnemos_extract()` — fire-and-forget POST to `/api/memory/extract`

**`scripts/hooks/post-commit`**: chains to repo-local hook, checks watched repos, extracts from `HEAD~1..HEAD`

**`scripts/hooks/pre-push`**: chains to repo-local hook, checks watched repos, extracts from all commits being pushed

**`scripts/install-hooks.sh --global --watch <path>`**:
- Copies hooks to `~/.config/git/hooks/`
- Sets `git config --global core.hooksPath`
- Creates/updates `~/.config/mnemos/repos` with watched paths

**Watched repos config** (`~/.config/mnemos/repos`):
```
# One path per line. Hooks only fire for repos under these paths.
~/Developments/Projects/digital-gigafactory
```

**Hook behavior:**
- Non-blocking: fire-and-forget `curl ... &` — never blocks git operations
- Fail-safe: if mnemos server is unreachable, hook silently succeeds
- Repo-aware: only triggers for repos in watched paths
- Chain-safe: repo-local hooks in `.git/hooks/` still execute first
- Configurable via `MNEMOS_URL` and `MNEMOS_HOOK_TRIGGER` env vars

### Configuration

New env vars:

| Variable | Default | Description |
|----------|---------|-------------|
| `MNEMOS_LLM_PROVIDER` | `ollama` | LLM provider (extensible) |
| `MNEMOS_LLM_MODEL` | `llama3.1:8b` | Ollama model name |
| `MNEMOS_OLLAMA_URL` | `http://localhost:11434` | Ollama API URL |
| `MNEMOS_HOOK_TRIGGER` | `pre-push` | When to extract (`pre-push`, `post-commit`, `both`) |

### Docker Compose Addition

Optional `ollama` service for users without Ollama installed locally:

```yaml
ollama:
  image: ollama/ollama:latest
  volumes:
    - ollama_data:/root/.ollama
  ports:
    - "11434:11434"
```

With a profile so it's opt-in: `docker compose --profile llm up -d`

---

## 3. Deduplication with Merge

### New Module: `packages/rag_core/deduplicator.py`

**Class: `Deduplicator`**

```python
class Deduplicator:
    def __init__(
        self,
        qdrant_client: QdrantClient,
        embedding_service: EmbeddingService,
        memory_extractor: MemoryExtractor,
        threshold: float = 0.85,
        strategy: str = "merge",
    ):
        ...

    def deduplicate_and_store(self, memory: ExtractedMemory) -> DeduplicationResult:
        """Check for similar memories and either merge or insert."""
        ...
```

**Flow:**
1. Embed the new memory content
2. Search `mnemos_memory` for existing memories with cosine similarity > threshold
3. If match found:
   - **merge** (default): call Ollama to consolidate old + new into a single memory, update the existing point
   - **replace**: delete old point, insert new one
4. If no match: insert as new point

**Data model:**
```python
@dataclass
class DeduplicationResult:
    action: str           # "inserted", "merged", "replaced"
    memory_id: str
    merged_with: str | None  # ID of the memory it was merged with
```

**Configuration:**

| Variable | Default | Description |
|----------|---------|-------------|
| `MNEMOS_DEDUP_THRESHOLD` | `0.85` | Cosine similarity threshold |
| `MNEMOS_DEDUP_STRATEGY` | `merge` | `merge` or `replace` |

### Integration Points

The deduplicator is used in:
- `POST /api/memory/extract` — for auto-extracted memories
- `POST /api/memory` — for manually created memories (existing endpoint)
- `mnemos_memory` MCP tool — for memories created via MCP

---

## 4. README Refresh

Complete rewrite of `README.md` to:

- Lead with Mnemos branding (not "RAG server")
- Highlight the three pillars: Semantic Search, Smart Memory, Auto-Extraction
- Update all code examples with `mnemos_*` names
- Add section on git hooks setup
- Add section on Ollama configuration
- Update env var table
- Update CLI reference with `mnemos` command

---

## Files to Create

| File | Purpose |
|------|---------|
| `packages/rag_core/memory_extractor.py` | LLM-based memory extraction from diffs |
| `packages/rag_core/deduplicator.py` | Similarity detection + merge logic |
| `scripts/hooks/mnemos-common.sh` | Shared hook logic: repo filtering, chaining, extraction |
| `scripts/hooks/post-commit` | Global git post-commit hook |
| `scripts/hooks/pre-push` | Global git pre-push hook |
| `scripts/install-hooks.sh` | Global hook installer (`core.hooksPath` + watched repos config) |

## Files to Modify

| File | Changes |
|------|---------|
| `packages/rag_core/collections.py` | Rename collections |
| `server/mcp_tools.py` | Rename tools, add extraction |
| `server/api.py` | Rename constants, add `/api/memory/extract` |
| `server/search.py` | Update collection references |
| `server/config.py` | Rename + add new env vars |
| `server/main.py` | Wire new services |
| `cli/main.py` | Rename CLI + env vars |
| `watcher/main.py` | Rename env vars |
| `docker-compose.yml` | Rename env vars, add ollama profile |
| `README.md` | Full rewrite |

## Out of Scope

- Decay/relevance scoring (future improvement)
- Graph-based memory relationships
- Multi-user memory isolation (beyond existing project scoping)
