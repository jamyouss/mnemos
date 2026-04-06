# Mnemos Memory Evolution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename all `rag_*` references to `mnemos_*`, add LLM-powered auto-extraction of memories from git commits, and add deduplication with merge to the memory system.

**Architecture:** Three layers of change: (1) big-bang rename of all identifiers, (2) new `MemoryExtractor` module calling Ollama for extraction, (3) new `Deduplicator` module that detects similar memories and merges them via Ollama. Git hooks trigger extraction on commit/push. All new config via env vars.

**Tech Stack:** Python 3.12+, FastAPI, Qdrant, sentence-transformers, Ollama (llama3.1:8b), httpx, Click/Rich

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `packages/rag_core/memory_extractor.py` | Call Ollama to extract structured memories from commit diffs |
| `packages/rag_core/deduplicator.py` | Detect similar memories and merge/replace via Ollama |
| `tests/test_memory_extractor.py` | Tests for memory extraction |
| `tests/test_deduplicator.py` | Tests for deduplication |
| `tests/test_extract_api.py` | Tests for the `/api/memory/extract` endpoint |
| `scripts/hooks/post-commit` | Git hook: extract memories after each commit |
| `scripts/hooks/pre-push` | Git hook: extract memories before push |
| `scripts/install-hooks.sh` | Install hooks into a target repo |

### Modified Files

| File | Changes |
|------|---------|
| `packages/rag_core/collections.py` | Rename 6 collection names |
| `packages/rag_core/models.py` | Add `ExtractedMemory`, `DeduplicationResult` models |
| `server/config.py` | Rename env vars, add LLM/dedup config |
| `server/mcp_tools.py` | Rename 9 tools, wire extraction |
| `server/api.py` | Rename constants, add `/api/memory/extract` endpoint |
| `server/search.py` | Update collection name references |
| `server/main.py` | Wire `MemoryExtractor` + `Deduplicator`, rename title |
| `server/requirements.txt` | No change needed (httpx already present) |
| `cli/main.py` | Rename CLI group, env var |
| `watcher/main.py` | Rename env var references |
| `docker-compose.yml` | Rename env vars, add ollama service (profile) |
| `docker-compose.prod.yml` | Rename env vars |
| `tests/conftest.py` | Add mock for MemoryExtractor |
| `tests/test_mcp_tools.py` | Update tool names |
| `tests/test_api.py` | Update collection names in payloads |
| `tests/test_collections.py` | Update expected names |
| `tests/test_search.py` | Update collection references |
| `tests/test_watcher.py` | Update env var references |
| `tests/test_cli.py` | Update CLI command name |
| `README.md` | Full rewrite |

---

## Task 1: Rename collections

**Files:**
- Modify: `packages/rag_core/collections.py`
- Modify: `tests/test_collections.py`

- [ ] **Step 1: Update collection names in `collections.py`**

In `packages/rag_core/collections.py`, replace all collection name strings:

```python
COLLECTIONS = [
    CollectionConfig(
        name="mnemos_skills",
        path_prefixes=["skills/"],
        description="Claude Code skills (metadata + instructions)",
    ),
    CollectionConfig(
        name="mnemos_docs",
        path_prefixes=["docs/"],
        description="Architecture and pattern documentation",
    ),
    CollectionConfig(
        name="mnemos_memory",
        path_prefixes=None,
        description="Conversation memory entries",
    ),
    CollectionConfig(
        name="mnemos_code_moby",
        path_prefixes=["moby/"],
        description="Moby application codebase",
    ),
    CollectionConfig(
        name="mnemos_code_trevio",
        path_prefixes=["trevio/"],
        description="Trevio platform codebase",
    ),
    CollectionConfig(
        name="mnemos_code_infra",
        path_prefixes=["infra/", "github-cicd/"],
        description="Infrastructure and CI/CD",
    ),
]
```

- [ ] **Step 2: Update `tests/test_collections.py`**

Read the file and replace all `rag_` prefixed collection names with `mnemos_` equivalents.

- [ ] **Step 3: Run tests**

Run: `cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos && python -m pytest tests/test_collections.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos
git add packages/rag_core/collections.py tests/test_collections.py
git commit -m "rename: rag_* collections to mnemos_*"
```

---

## Task 2: Rename config and env vars

**Files:**
- Modify: `server/config.py`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.prod.yml`
- Modify: `watcher/main.py`

- [ ] **Step 1: Update `server/config.py`**

Replace with:

```python
from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    embedding_model: str = "all-MiniLM-L6-v2"
    codebase_path: str = "/data/codebase"
    claude_config_path: str = "/data/claude-config"
    mnemos_mode: str = "local"
    mnemos_auth_enabled: bool = False
    mnemos_state_dir: str = "/data/state"
    mnemos_llm_provider: str = "ollama"
    mnemos_llm_model: str = "llama3.1:8b"
    mnemos_ollama_url: str = "http://localhost:11434"
    mnemos_hook_trigger: str = "pre-push"
    mnemos_dedup_threshold: float = 0.85
    mnemos_dedup_strategy: str = "merge"

    class Config:
        env_prefix = ""


settings = Settings()
```

- [ ] **Step 2: Update `docker-compose.yml` env vars**

In `docker-compose.yml`, rename all env vars in rag-server and watcher services:

For rag-server:
```yaml
    environment:
      QDRANT_HOST: qdrant
      QDRANT_PORT: 6333
      EMBEDDING_MODEL: all-MiniLM-L6-v2
      CODEBASE_PATH: /data/codebase
      CLAUDE_CONFIG_PATH: /data/claude-config
      MNEMOS_MODE: local
      MNEMOS_AUTH_ENABLED: "false"
      MNEMOS_STATE_DIR: /data/state
      MNEMOS_LLM_PROVIDER: ollama
      MNEMOS_LLM_MODEL: "llama3.1:8b"
      MNEMOS_OLLAMA_URL: http://ollama:11434
      MNEMOS_DEDUP_THRESHOLD: "0.85"
      MNEMOS_DEDUP_STRATEGY: merge
```

For watcher:
```yaml
    environment:
      MNEMOS_SERVER_URL: http://rag-server:8100
      CODEBASE_PATH: /data/codebase
      CLAUDE_CONFIG_PATH: /data/claude-config
      WATCHER_DEBOUNCE_MS: 2000
```

Add ollama service with profile:
```yaml
  ollama:
    image: ollama/ollama:latest
    volumes:
      - ollama_data:/root/.ollama
    ports:
      - "11434:11434"
    profiles:
      - llm
```

Add to volumes:
```yaml
  ollama_data:
```

- [ ] **Step 3: Update `docker-compose.prod.yml`**

```yaml
services:
  rag-server:
    environment:
      MNEMOS_MODE: deployed
      MNEMOS_AUTH_ENABLED: "true"
    volumes:
      - ./config/tenants.yaml:/data/config/tenants.yaml:ro

  watcher:
    profiles:
      - disabled
```

- [ ] **Step 4: Update `watcher/main.py` env var**

Change line 66:
```python
RAG_SERVER_URL = os.getenv("MNEMOS_SERVER_URL", "http://rag-server:8100")
```

- [ ] **Step 5: Commit**

```bash
cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos
git add server/config.py docker-compose.yml docker-compose.prod.yml watcher/main.py
git commit -m "rename: env vars RAG_* to MNEMOS_*, add LLM config"
```

---

## Task 3: Rename MCP tools

**Files:**
- Modify: `server/mcp_tools.py`
- Modify: `tests/test_mcp_tools.py`

- [ ] **Step 1: Rename all tool definitions in `server/mcp_tools.py`**

Replace the entire `TOOL_DEFINITIONS` list. Key renames:
- `rag_search` → `mnemos_search`
- `rag_search_code` → `mnemos_search_code`
- `rag_search_skills` → `mnemos_search_skills`
- `rag_search_memory` → `mnemos_search_memory`
- `rag_index_memory` → `mnemos_memory`
- `rag_memory_list` → `mnemos_memory_list`
- `rag_memory_review` → `mnemos_memory_review`
- `rag_reindex` → `mnemos_reindex`
- `rag_status` → `mnemos_status`

Update the `name` field of each `types.Tool(...)` in the list. Also update descriptions to say "Mnemos" instead of "RAG" where appropriate.

- [ ] **Step 2: Rename all `if name ==` dispatches in `_dispatch_tool`**

Update every `if name == "rag_..."` to `if name == "mnemos_..."`. The `rag_index_memory` handler becomes `mnemos_memory`. Also update the `_store_memory` function to use `"mnemos_memory"` collection name:

```python
    qdrant_client.upsert(collection_name="mnemos_memory", points=[point])
```

- [ ] **Step 3: Update `server/mcp_tools.py` — `_list_memory` and `_review_memory`**

Replace `"rag_memory"` with `"mnemos_memory"` in both functions.

- [ ] **Step 4: Update `server/mcp_tools.py` — `_get_status`**

The `_get_status` function iterates `COLLECTIONS` which already uses `mnemos_*` from Task 1. No change needed.

- [ ] **Step 5: Rename MCP server name**

In `create_mcp_server`:
```python
    server = Server("mnemos-mcp")
```

- [ ] **Step 6: Update `tests/test_mcp_tools.py`**

Replace the `expected` set:
```python
    expected = {
        "mnemos_search",
        "mnemos_search_code",
        "mnemos_search_skills",
        "mnemos_search_memory",
        "mnemos_memory",
        "mnemos_memory_list",
        "mnemos_memory_review",
        "mnemos_reindex",
        "mnemos_status",
    }
```

Update `test_required_tools_have_required_fields`:
```python
    query_tools = {
        "mnemos_search", "mnemos_search_code", "mnemos_search_skills", "mnemos_search_memory"
    }
    # ...
    review_tool = tool_map["mnemos_memory_review"]
```

- [ ] **Step 7: Run tests**

Run: `cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos && python -m pytest tests/test_mcp_tools.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos
git add server/mcp_tools.py tests/test_mcp_tools.py
git commit -m "rename: MCP tools rag_* to mnemos_*"
```

---

## Task 4: Rename API constants and search service references

**Files:**
- Modify: `server/api.py`
- Modify: `server/search.py`
- Modify: `server/main.py`
- Modify: `tests/test_api.py`
- Modify: `tests/test_search.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Update `server/api.py`**

Replace:
```python
_MEMORY_COLLECTION = "mnemos_memory"
```

- [ ] **Step 2: Update `server/search.py`**

In `search()` method, line 35 — the exclusion filter already uses `c.name` from COLLECTIONS, so it automatically picks up the new name. But the `search_memory()` method hardcodes `"rag_memory"`. Replace:
```python
        hits = self._qdrant.query_points(
            collection_name="mnemos_memory",
```

In `search_code()`, the prefix check uses `c.name.startswith("rag_code_")`. Replace:
```python
            collections = [c.name for c in COLLECTIONS if c.name.startswith("mnemos_code_")]
```

In `search()`, the exclusion of memory from default collections uses `c.name != "rag_memory"`. Replace:
```python
        target_collections = collections or [
            c.name for c in COLLECTIONS if c.name != "mnemos_memory"
        ]
```

- [ ] **Step 3: Update `server/main.py`**

Replace the FastAPI title:
```python
    app = FastAPI(title="Mnemos MCP Server", lifespan=lifespan)
```

- [ ] **Step 4: Update `tests/test_api.py`**

Replace all instances of `rag_code_moby` with `mnemos_code_moby` and `rag_memory` with `mnemos_memory` in test payloads. Specifically:

- `test_api_search_with_options`: `"collections": ["mnemos_code_moby"]`
- `test_api_push_index`: `"collection": "mnemos_code_moby"`
- `test_api_delete_index`: URL path `/api/index/mnemos_code_moby/src/main.go` and assert `data["collection"] == "mnemos_code_moby"`
- `test_api_reindex`: `"collection": "mnemos_code_moby"`
- `test_internal_reindex_deleted_event`: `"collection": "mnemos_code_moby"`

- [ ] **Step 5: Update remaining test files**

Read and update `tests/test_search.py`, `tests/test_server.py`, `tests/test_watcher.py`, `tests/test_cli.py` for any `rag_*` references.

- [ ] **Step 6: Run all tests**

Run: `cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos && python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos
git add server/api.py server/search.py server/main.py tests/
git commit -m "rename: update API, search, and tests to mnemos_*"
```

---

## Task 5: Rename CLI

**Files:**
- Modify: `cli/main.py`

- [ ] **Step 1: Rename CLI group and env var**

In `cli/main.py`:

Change the base URL function:
```python
def _base_url() -> str:
    return os.environ.get("MNEMOS_URL", DEFAULT_RAG_URL).rstrip("/")
```

Change the CLI group:
```python
@click.group()
def cli() -> None:
    """Mnemos CLI — search, reindex, and manage memory."""
```

- [ ] **Step 2: Update CLI entry point if needed**

Check `cli/setup.py` or `cli/pyproject.toml` for console_scripts entry and rename `rag` → `mnemos`.

- [ ] **Step 3: Run CLI tests**

Run: `cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos && python -m pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos
git add cli/
git commit -m "rename: CLI rag to mnemos"
```

---

## Task 6: Add models for extraction and deduplication

**Files:**
- Modify: `packages/rag_core/models.py`

- [ ] **Step 1: Add new models**

Append to `packages/rag_core/models.py`:

```python
class ExtractedMemory(BaseModel):
    content: str
    memory_type: str = "note"  # decision, pattern, lesson, convention, note
    project: str | None = None
    tags: list[str] = []


class DeduplicationResult(BaseModel):
    action: str  # "inserted", "merged", "replaced"
    memory_id: str
    merged_with: str | None = None
```

- [ ] **Step 2: Run model tests**

Run: `cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos && python -m pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos
git add packages/rag_core/models.py
git commit -m "feat: add ExtractedMemory and DeduplicationResult models"
```

---

## Task 7: Implement MemoryExtractor

**Files:**
- Create: `packages/rag_core/memory_extractor.py`
- Create: `tests/test_memory_extractor.py`

- [ ] **Step 1: Write failing tests for MemoryExtractor**

Create `tests/test_memory_extractor.py`:

```python
"""Tests for MemoryExtractor."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from rag_core.memory_extractor import MemoryExtractor


@pytest.fixture
def extractor():
    return MemoryExtractor(
        ollama_url="http://localhost:11434",
        model="llama3.1:8b",
    )


def test_extract_returns_list(extractor):
    """extract() must return a list of ExtractedMemory."""
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "message": {
            "content": '[{"content": "Decided to use flat routes for API", "memory_type": "decision", "project": "moby", "tags": ["routing"]}]'
        }
    }

    with patch("rag_core.memory_extractor.httpx.post", return_value=fake_response):
        results = extractor.extract(
            commit_message="feat: flatten API routes",
            diff="diff --git a/routes.go ...",
        )

    assert len(results) == 1
    assert results[0].content == "Decided to use flat routes for API"
    assert results[0].memory_type == "decision"
    assert results[0].project == "moby"


def test_extract_returns_empty_for_trivial_commit(extractor):
    """Trivial commits should produce no memories."""
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "message": {"content": "[]"}
    }

    with patch("rag_core.memory_extractor.httpx.post", return_value=fake_response):
        results = extractor.extract(
            commit_message="fix: typo in comment",
            diff="- // helo\n+ // hello",
        )

    assert results == []


def test_extract_truncates_large_diffs(extractor):
    """Diffs larger than 32KB should be truncated."""
    large_diff = "+" * 50_000  # 50KB

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "message": {"content": "[]"}
    }

    with patch("rag_core.memory_extractor.httpx.post", return_value=fake_response) as mock_post:
        extractor.extract(commit_message="big change", diff=large_diff)

    # Verify the diff sent to Ollama was truncated
    call_args = mock_post.call_args
    body = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
    prompt_content = str(body)
    # The actual diff in the prompt should be <= 32KB + overhead
    assert len(large_diff) > 32_768


def test_extract_handles_ollama_error(extractor):
    """If Ollama returns an error, extract() returns empty list."""
    with patch("rag_core.memory_extractor.httpx.post", side_effect=Exception("connection refused")):
        results = extractor.extract(
            commit_message="feat: something",
            diff="some diff",
        )

    assert results == []


def test_extract_handles_malformed_json(extractor):
    """If Ollama returns non-JSON, extract() returns empty list."""
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "message": {"content": "This is not JSON at all"}
    }

    with patch("rag_core.memory_extractor.httpx.post", return_value=fake_response):
        results = extractor.extract(
            commit_message="feat: something",
            diff="some diff",
        )

    assert results == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos && python -m pytest tests/test_memory_extractor.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement MemoryExtractor**

Create `packages/rag_core/memory_extractor.py`:

```python
"""Extract structured memories from git commit diffs using Ollama."""
from __future__ import annotations

import json
import logging

import httpx

from rag_core.models import ExtractedMemory

logger = logging.getLogger("mnemos.extractor")

_MAX_DIFF_BYTES = 32_768

_SYSTEM_PROMPT = """You are a memory extraction assistant for a software development team.
Given a git commit message and diff, extract actionable memories worth remembering for future work.

Types of memories to extract:
- "decision": Architectural or design decisions made (e.g., "Chose flat API routes over nested")
- "pattern": Code patterns introduced or established (e.g., "All handlers follow middleware chain pattern")
- "convention": Naming or structural conventions (e.g., "Services use Create/Get/Update/Delete naming")
- "lesson": Bugs fixed or workarounds applied (e.g., "Qdrant scroll requires with_vectors=True for updates")

Rules:
- Return a JSON array of objects with keys: content, memory_type, project, tags
- project should be inferred from file paths in the diff (e.g., "moby", "trevio", "infra")
- If nothing worth remembering, return []
- Be concise: each memory should be 1-2 sentences
- Focus on WHY decisions were made, not WHAT code was written
- Do NOT extract trivial changes (typos, formatting, import ordering)

Return ONLY the JSON array, no markdown fences, no explanation."""

_USER_TEMPLATE = """## Commit Message
{commit_message}

## Diff
{diff}"""


class MemoryExtractor:
    def __init__(self, ollama_url: str, model: str) -> None:
        self._ollama_url = ollama_url.rstrip("/")
        self._model = model

    def extract(self, commit_message: str, diff: str) -> list[ExtractedMemory]:
        truncated_diff = diff[:_MAX_DIFF_BYTES]
        if len(diff) > _MAX_DIFF_BYTES:
            truncated_diff += "\n\n[... diff truncated ...]"

        user_content = _USER_TEMPLATE.format(
            commit_message=commit_message,
            diff=truncated_diff,
        )

        try:
            response = httpx.post(
                f"{self._ollama_url}/api/chat",
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    "stream": False,
                    "format": "json",
                },
                timeout=120,
            )
            response.raise_for_status()
        except Exception:
            logger.exception("Failed to call Ollama for memory extraction")
            return []

        try:
            raw = response.json()["message"]["content"]
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "memories" in parsed:
                parsed = parsed["memories"]
            if not isinstance(parsed, list):
                return []
            return [ExtractedMemory(**item) for item in parsed]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.warning("Failed to parse Ollama response as memory list")
            return []
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos && python -m pytest tests/test_memory_extractor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos
git add packages/rag_core/memory_extractor.py tests/test_memory_extractor.py
git commit -m "feat: add MemoryExtractor for LLM-based commit analysis"
```

---

## Task 8: Implement Deduplicator

**Files:**
- Create: `packages/rag_core/deduplicator.py`
- Create: `tests/test_deduplicator.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_deduplicator.py`:

```python
"""Tests for Deduplicator."""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from rag_core.deduplicator import Deduplicator
from rag_core.models import ExtractedMemory


@pytest.fixture
def mock_qdrant():
    client = MagicMock()
    client.get_collections.return_value.collections = []
    return client


@pytest.fixture
def mock_embeddings():
    service = MagicMock()
    service.embed.return_value = [0.1] * 384
    return service


@pytest.fixture
def mock_extractor():
    return MagicMock()


@pytest.fixture
def deduplicator(mock_qdrant, mock_embeddings, mock_extractor):
    return Deduplicator(
        qdrant_client=mock_qdrant,
        embedding_service=mock_embeddings,
        memory_extractor=mock_extractor,
        threshold=0.85,
        strategy="merge",
    )


def test_insert_new_memory(deduplicator, mock_qdrant):
    """When no similar memory exists, insert as new."""
    mock_qdrant.query_points.return_value.points = []

    memory = ExtractedMemory(
        content="Use flat API routes",
        memory_type="decision",
        project="moby",
        tags=["routing"],
    )

    result = deduplicator.deduplicate_and_store(memory)

    assert result.action == "inserted"
    assert result.merged_with is None
    mock_qdrant.upsert.assert_called_once()


def test_merge_similar_memory(deduplicator, mock_qdrant, mock_extractor):
    """When a similar memory exists with score > threshold, merge them."""
    existing_point = MagicMock()
    existing_point.id = "existing-point-id"
    existing_point.score = 0.92
    existing_point.payload = {
        "id": "existing-mem-id",
        "content": "API routes should be flat",
        "memory_type": "decision",
        "project": "moby",
        "tags": ["routing"],
        "status": "approved",
        "created_at": "2026-04-01T00:00:00Z",
    }
    mock_qdrant.query_points.return_value.points = [existing_point]

    # Mock the merge call to Ollama
    mock_extractor.merge_memories.return_value = "API routes must always be flat, never nested. Confirmed across multiple commits."

    memory = ExtractedMemory(
        content="Confirmed: flat routes are the standard",
        memory_type="decision",
        project="moby",
        tags=["routing"],
    )

    result = deduplicator.deduplicate_and_store(memory)

    assert result.action == "merged"
    assert result.merged_with == "existing-mem-id"
    mock_extractor.merge_memories.assert_called_once()


def test_replace_similar_memory(mock_qdrant, mock_embeddings, mock_extractor):
    """When strategy is 'replace', delete old and insert new."""
    dedup = Deduplicator(
        qdrant_client=mock_qdrant,
        embedding_service=mock_embeddings,
        memory_extractor=mock_extractor,
        threshold=0.85,
        strategy="replace",
    )

    existing_point = MagicMock()
    existing_point.id = "existing-point-id"
    existing_point.score = 0.90
    existing_point.payload = {
        "id": "old-mem-id",
        "content": "Old memory",
        "status": "approved",
        "created_at": "2026-04-01T00:00:00Z",
    }
    mock_qdrant.query_points.return_value.points = [existing_point]

    memory = ExtractedMemory(content="New memory", memory_type="decision")

    result = dedup.deduplicate_and_store(memory)

    assert result.action == "replaced"
    assert result.merged_with == "old-mem-id"


def test_no_merge_below_threshold(deduplicator, mock_qdrant):
    """Memories with similarity below threshold should be inserted as new."""
    existing_point = MagicMock()
    existing_point.id = "point-id"
    existing_point.score = 0.75  # Below 0.85 threshold
    existing_point.payload = {"id": "mem-id", "content": "something related"}
    mock_qdrant.query_points.return_value.points = [existing_point]

    memory = ExtractedMemory(content="Something different enough")

    result = deduplicator.deduplicate_and_store(memory)

    assert result.action == "inserted"
    assert result.merged_with is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos && python -m pytest tests/test_deduplicator.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement Deduplicator**

Create `packages/rag_core/deduplicator.py`:

```python
"""Deduplicate memories by detecting similar entries and merging or replacing."""
from __future__ import annotations

import time
import uuid
import logging
from datetime import datetime, timezone

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, Filter, FieldCondition, MatchValue

from rag_core.embeddings import EmbeddingService
from rag_core.models import DeduplicationResult, ExtractedMemory

logger = logging.getLogger("mnemos.deduplicator")

_MEMORY_COLLECTION = "mnemos_memory"


class Deduplicator:
    def __init__(
        self,
        qdrant_client: QdrantClient,
        embedding_service: EmbeddingService,
        memory_extractor,
        threshold: float = 0.85,
        strategy: str = "merge",
    ) -> None:
        self._qdrant = qdrant_client
        self._embeddings = embedding_service
        self._extractor = memory_extractor
        self._threshold = threshold
        self._strategy = strategy

    def deduplicate_and_store(self, memory: ExtractedMemory, status: str = "pending") -> DeduplicationResult:
        vector = self._embeddings.embed(memory.content)

        # Search for similar existing memories
        hits = self._qdrant.query_points(
            collection_name=_MEMORY_COLLECTION,
            query=vector,
            limit=1,
        ).points

        # Check if top hit exceeds threshold
        if hits and hits[0].score >= self._threshold:
            existing = hits[0]
            existing_id = existing.payload.get("id", "")

            if self._strategy == "merge":
                return self._merge(existing, memory, vector, status)
            else:
                return self._replace(existing, memory, vector, status)

        # No similar memory — insert as new
        return self._insert(memory, vector, status)

    def _insert(self, memory: ExtractedMemory, vector: list[float], status: str) -> DeduplicationResult:
        mem_id = str(uuid.uuid4())
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, mem_id))
        now = datetime.now(timezone.utc).isoformat()

        payload = {
            "id": mem_id,
            "content": memory.content,
            "project": memory.project,
            "memory_type": memory.memory_type,
            "tags": memory.tags,
            "status": status,
            "created_at": now,
            "file_path": f"memory/{mem_id}",
            "chunk_type": "memory",
            "last_indexed_at": now,
            "file_mtime": time.time(),
        }

        self._qdrant.upsert(
            collection_name=_MEMORY_COLLECTION,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )
        return DeduplicationResult(action="inserted", memory_id=mem_id)

    def _merge(self, existing, memory: ExtractedMemory, vector: list[float], status: str) -> DeduplicationResult:
        existing_id = existing.payload.get("id", "")
        existing_content = existing.payload.get("content", "")

        merged_content = self._extractor.merge_memories(existing_content, memory.content)

        # Re-embed the merged content
        merged_vector = self._embeddings.embed(merged_content)
        now = datetime.now(timezone.utc).isoformat()

        updated_payload = {
            **existing.payload,
            "content": merged_content,
            "tags": list(set(existing.payload.get("tags", []) + memory.tags)),
            "last_indexed_at": now,
            "file_mtime": time.time(),
        }

        self._qdrant.upsert(
            collection_name=_MEMORY_COLLECTION,
            points=[PointStruct(id=existing.id, vector=merged_vector, payload=updated_payload)],
        )
        return DeduplicationResult(action="merged", memory_id=existing_id, merged_with=existing_id)

    def _replace(self, existing, memory: ExtractedMemory, vector: list[float], status: str) -> DeduplicationResult:
        existing_id = existing.payload.get("id", "")

        # Delete old point
        self._qdrant.delete(
            collection_name=_MEMORY_COLLECTION,
            points_selector=[existing.id],
        )

        # Insert new
        result = self._insert(memory, vector, status)
        return DeduplicationResult(
            action="replaced",
            memory_id=result.memory_id,
            merged_with=existing_id,
        )
```

- [ ] **Step 4: Add `merge_memories` method to MemoryExtractor**

Append to `packages/rag_core/memory_extractor.py`:

```python
    def merge_memories(self, existing: str, new: str) -> str:
        """Merge two similar memories into one consolidated text."""
        prompt = (
            "You are merging two similar memory entries into one concise, consolidated memory.\n\n"
            f"Existing memory:\n{existing}\n\n"
            f"New memory:\n{new}\n\n"
            "Write a single consolidated memory (1-3 sentences) that captures all information from both. "
            "Return ONLY the merged text, no explanation."
        )

        try:
            response = httpx.post(
                f"{self._ollama_url}/api/chat",
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
                timeout=60,
            )
            response.raise_for_status()
            return response.json()["message"]["content"].strip()
        except Exception:
            logger.exception("Failed to merge memories via Ollama")
            return f"{existing}\n\n{new}"
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos && python -m pytest tests/test_deduplicator.py tests/test_memory_extractor.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos
git add packages/rag_core/deduplicator.py packages/rag_core/memory_extractor.py tests/test_deduplicator.py
git commit -m "feat: add Deduplicator with merge/replace strategies"
```

---

## Task 9: Add extraction API endpoint

**Files:**
- Modify: `server/api.py`
- Modify: `server/main.py`
- Create: `tests/test_extract_api.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_extract_api.py`:

```python
"""Tests for the memory extraction endpoint."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from rag_core.models import ExtractedMemory, DeduplicationResult


@pytest.fixture
def app():
    """Create app with mocked dependencies."""
    with patch("server.main.QdrantClient") as mock_qdrant_cls, \
         patch("server.main.EmbeddingService") as mock_embed_cls:

        mock_qdrant = MagicMock()
        mock_qdrant.get_collections.return_value.collections = []
        mock_qdrant.scroll.return_value = ([], None)
        mock_embeddings = MagicMock()
        mock_embeddings.embed.return_value = [0.1] * 384
        mock_embeddings.embed_batch.return_value = [[0.1] * 384]
        mock_qdrant_cls.return_value = mock_qdrant
        mock_embed_cls.return_value = mock_embeddings

        from server.main import create_app
        from rag_core.indexer import Indexer
        from server.search import SearchService

        application = create_app()
        application.state.qdrant = mock_qdrant
        application.state.embeddings = mock_embeddings
        application.state.indexer = Indexer(
            qdrant_client=mock_qdrant,
            embedding_service=mock_embeddings,
        )
        application.state.search_service = SearchService(
            qdrant_client=mock_qdrant,
            embedding_service=mock_embeddings,
        )

        # Mock extractor and deduplicator
        mock_extractor = MagicMock()
        mock_deduplicator = MagicMock()
        application.state.memory_extractor = mock_extractor
        application.state.deduplicator = mock_deduplicator

        yield application, mock_extractor, mock_deduplicator


@pytest.mark.anyio
async def test_extract_memories_from_commit(app):
    application, mock_extractor, mock_deduplicator = app

    mock_extractor.extract.return_value = [
        ExtractedMemory(
            content="Decided to use flat routes",
            memory_type="decision",
            project="moby",
            tags=["routing"],
        )
    ]
    mock_deduplicator.deduplicate_and_store.return_value = DeduplicationResult(
        action="inserted", memory_id="test-id-123"
    )

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/memory/extract",
            json={
                "commit_message": "feat: flatten API routes",
                "diff": "diff --git a/routes.go ...",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["extracted"] == 1
    assert len(data["memories"]) == 1
    assert data["memories"][0]["id"] == "test-id-123"


@pytest.mark.anyio
async def test_extract_no_memories(app):
    application, mock_extractor, mock_deduplicator = app
    mock_extractor.extract.return_value = []

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/memory/extract",
            json={
                "commit_message": "fix: typo",
                "diff": "- helo\n+ hello",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["extracted"] == 0
    assert data["memories"] == []
```

- [ ] **Step 2: Add request model and endpoint to `server/api.py`**

Add request model after `MemoryReviewRequest`:

```python
class MemoryExtractRequest(BaseModel):
    commit_message: str
    diff: str
    author: Optional[str] = None
```

Add endpoint after the memory review endpoint:

```python
@api_router.post("/api/memory/extract")
async def extract_memories(body: MemoryExtractRequest, request: Request):
    extractor = request.app.state.memory_extractor
    deduplicator = request.app.state.deduplicator

    extracted = extractor.extract(
        commit_message=body.commit_message,
        diff=body.diff,
    )

    results = []
    for memory in extracted:
        dedup_result = deduplicator.deduplicate_and_store(memory)
        results.append({
            "id": dedup_result.memory_id,
            "content": memory.content,
            "action": dedup_result.action,
            "status": "pending",
        })

    return {"extracted": len(results), "memories": results}
```

- [ ] **Step 3: Wire MemoryExtractor and Deduplicator in `server/main.py`**

Add imports at the top of `create_app` lifespan, after the existing service creation:

```python
        from rag_core.memory_extractor import MemoryExtractor
        from rag_core.deduplicator import Deduplicator

        app.state.memory_extractor = MemoryExtractor(
            ollama_url=settings.mnemos_ollama_url,
            model=settings.mnemos_llm_model,
        )
        app.state.deduplicator = Deduplicator(
            qdrant_client=app.state.qdrant,
            embedding_service=app.state.embeddings,
            memory_extractor=app.state.memory_extractor,
            threshold=settings.mnemos_dedup_threshold,
            strategy=settings.mnemos_dedup_strategy,
        )
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos && python -m pytest tests/test_extract_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos
git add server/api.py server/main.py tests/test_extract_api.py
git commit -m "feat: add /api/memory/extract endpoint for commit analysis"
```

---

## Task 10: Wire deduplication into existing memory creation paths

**Files:**
- Modify: `server/api.py`
- Modify: `server/mcp_tools.py`

- [ ] **Step 1: Update `POST /api/memory` to use deduplicator**

In `server/api.py`, replace the `create_memory` function body:

```python
@api_router.post("/api/memory")
async def create_memory(body: MemoryCreateRequest, request: Request):
    deduplicator = request.app.state.deduplicator

    from rag_core.models import ExtractedMemory
    memory = ExtractedMemory(
        content=body.content,
        memory_type=body.memory_type,
        project=body.project,
        tags=body.tags,
    )

    result = deduplicator.deduplicate_and_store(memory, status=body.status)
    return {"status": "created", "id": result.memory_id, "action": result.action}
```

- [ ] **Step 2: Update `mnemos_memory` MCP tool to use deduplicator**

In `server/mcp_tools.py`, update the `mnemos_memory` handler in `_dispatch_tool` and the `_store_memory` function. Pass `deduplicator` to `create_mcp_server` and use it:

Update `create_mcp_server` signature:
```python
def create_mcp_server(
    search_service: SearchService,
    indexer: Indexer,
    qdrant_client: QdrantClient | None = None,
    embedding_service: EmbeddingService | None = None,
    deduplicator=None,
) -> Server:
```

Update `_dispatch_tool` signature to accept `deduplicator` and pass it through.

Update the `mnemos_memory` handler:
```python
    if name == "mnemos_memory":
        if deduplicator is None:
            raise RuntimeError("deduplicator required for mnemos_memory")
        from rag_core.models import ExtractedMemory
        memory = ExtractedMemory(
            content=args["content"],
            memory_type=args.get("memory_type", "note"),
            project=args.get("project"),
            tags=args.get("tags", []),
        )
        result = deduplicator.deduplicate_and_store(memory)
        return {"id": result.memory_id, "status": "pending", "action": result.action}
```

- [ ] **Step 3: Update `server/main.py` to pass deduplicator to MCP server**

```python
        mcp_server = create_mcp_server(
            search_service=app.state.search_service,
            indexer=app.state.indexer,
            qdrant_client=app.state.qdrant,
            embedding_service=app.state.embeddings,
            deduplicator=app.state.deduplicator,
        )
```

- [ ] **Step 4: Run all tests**

Run: `cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos && python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos
git add server/api.py server/mcp_tools.py server/main.py
git commit -m "feat: wire deduplication into all memory creation paths"
```

---

## Task 11: Create global git hooks

**Files:**
- Create: `scripts/hooks/post-commit`
- Create: `scripts/hooks/pre-push`
- Create: `scripts/hooks/mnemos-common.sh`
- Create: `scripts/install-hooks.sh`

Global hooks via `core.hooksPath`. Each hook: (1) checks if repo is in watched paths, (2) chains to repo-local hook if it exists, (3) runs mnemos extraction.

- [ ] **Step 1: Create shared helper `scripts/hooks/mnemos-common.sh`**

```bash
#!/bin/sh
# Shared logic for mnemos git hooks.
# Sourced by post-commit and pre-push hooks.

MNEMOS_URL="${MNEMOS_URL:-http://localhost:8100}"
MNEMOS_HOOK_TRIGGER="${MNEMOS_HOOK_TRIGGER:-pre-push}"
MNEMOS_REPOS_CONFIG="$HOME/.config/mnemos/repos"

# Check if the current repo is in the watched paths list.
# Returns 0 if watched, 1 if not.
mnemos_is_watched_repo() {
    if [ ! -f "$MNEMOS_REPOS_CONFIG" ]; then
        return 1
    fi

    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "")"
    if [ -z "$REPO_ROOT" ]; then
        return 1
    fi

    while IFS= read -r watched || [ -n "$watched" ]; do
        # Skip comments and empty lines
        case "$watched" in
            "#"*|"") continue ;;
        esac
        # Expand ~
        watched=$(eval echo "$watched")
        case "$REPO_ROOT" in
            "$watched"*) return 0 ;;
        esac
    done < "$MNEMOS_REPOS_CONFIG"

    return 1
}

# Chain to repo-local hook if it exists.
# Usage: mnemos_chain_local "pre-push" "$@"
mnemos_chain_local() {
    HOOK_NAME="$1"
    shift
    REPO_HOOK="$(git rev-parse --git-dir)/hooks/$HOOK_NAME"
    if [ -x "$REPO_HOOK" ]; then
        "$REPO_HOOK" "$@" || exit $?
    fi
}

# Send extraction request to mnemos (fire and forget).
mnemos_extract() {
    COMMIT_MSG="$1"
    DIFF="$2"

    if [ -z "$DIFF" ]; then
        return
    fi

    curl -s -X POST "$MNEMOS_URL/api/memory/extract" \
        -H "Content-Type: application/json" \
        -d "$(jq -n \
            --arg msg "$COMMIT_MSG" \
            --arg diff "$DIFF" \
            '{commit_message: $msg, diff: $diff}')" \
        >/dev/null 2>&1 &
}
```

- [ ] **Step 2: Create `scripts/hooks/post-commit`**

```bash
#!/bin/sh
# Mnemos global post-commit hook.
# Extracts memories from the last commit.
# Non-blocking and fail-safe — never blocks git operations.

HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$HOOK_DIR/mnemos-common.sh"

# 1. Chain to repo-local hook
mnemos_chain_local "post-commit" "$@"

# 2. Check if this repo is watched
mnemos_is_watched_repo || exit 0

# 3. Check trigger config
case "$MNEMOS_HOOK_TRIGGER" in
    post-commit|both) ;;
    *) exit 0 ;;
esac

# 4. Extract
COMMIT_MSG=$(git log -1 --pretty=%B)
DIFF=$(git diff HEAD~1..HEAD 2>/dev/null || echo "")
mnemos_extract "$COMMIT_MSG" "$DIFF"

exit 0
```

- [ ] **Step 3: Create `scripts/hooks/pre-push`**

```bash
#!/bin/sh
# Mnemos global pre-push hook.
# Extracts memories from all commits being pushed.
# Non-blocking and fail-safe — never blocks git operations.

HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$HOOK_DIR/mnemos-common.sh"

# 1. Chain to repo-local hook
mnemos_chain_local "pre-push" "$@"

# 2. Check if this repo is watched
mnemos_is_watched_repo || exit 0

# 3. Check trigger config
case "$MNEMOS_HOOK_TRIGGER" in
    pre-push|both) ;;
    *) exit 0 ;;
esac

# 4. Extract from all commits being pushed
while read local_ref local_sha remote_ref remote_sha; do
    if [ "$remote_sha" = "0000000000000000000000000000000000000000" ]; then
        # New branch — diff against main/master
        BASE=$(git rev-parse --verify origin/main 2>/dev/null \
            || git rev-parse --verify origin/master 2>/dev/null \
            || echo "")
    else
        BASE="$remote_sha"
    fi

    if [ -z "$BASE" ]; then
        continue
    fi

    COMMIT_MSGS=$(git log --pretty=%B "$BASE".."$local_sha" 2>/dev/null || echo "")
    DIFF=$(git diff "$BASE".."$local_sha" 2>/dev/null || echo "")
    mnemos_extract "$COMMIT_MSGS" "$DIFF"
done

exit 0
```

- [ ] **Step 4: Create `scripts/install-hooks.sh`**

```bash
#!/bin/bash
# Install Mnemos global git hooks.
#
# Usage:
#   ./install-hooks.sh --global --watch ~/Developments/Projects/digital-gigafactory
#   ./install-hooks.sh --global --watch /path/to/repo --trigger both
#
# Options:
#   --global              Install hooks globally via core.hooksPath
#   --watch <path>        Add a path to the watched repos list
#   --trigger <mode>      Set MNEMOS_HOOK_TRIGGER (pre-push|post-commit|both, default: pre-push)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_SRC="$SCRIPT_DIR/hooks"
GLOBAL_HOOKS_DIR="$HOME/.config/git/hooks"
MNEMOS_CONFIG_DIR="$HOME/.config/mnemos"
REPOS_CONFIG="$MNEMOS_CONFIG_DIR/repos"
GLOBAL=false
WATCH_PATHS=()
TRIGGER=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --global) GLOBAL=true; shift ;;
        --watch) WATCH_PATHS+=("$2"); shift 2 ;;
        --trigger) TRIGGER="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [ "$GLOBAL" = false ]; then
    echo "Usage: $0 --global --watch <path> [--trigger pre-push|post-commit|both]"
    exit 1
fi

# 1. Copy hooks to global hooks directory
mkdir -p "$GLOBAL_HOOKS_DIR"
cp "$HOOKS_SRC/post-commit" "$GLOBAL_HOOKS_DIR/post-commit"
cp "$HOOKS_SRC/pre-push" "$GLOBAL_HOOKS_DIR/pre-push"
cp "$HOOKS_SRC/mnemos-common.sh" "$GLOBAL_HOOKS_DIR/mnemos-common.sh"
chmod +x "$GLOBAL_HOOKS_DIR/post-commit"
chmod +x "$GLOBAL_HOOKS_DIR/pre-push"
chmod +x "$GLOBAL_HOOKS_DIR/mnemos-common.sh"
echo "Hooks installed to $GLOBAL_HOOKS_DIR"

# 2. Set global core.hooksPath
git config --global core.hooksPath "$GLOBAL_HOOKS_DIR"
echo "Set core.hooksPath to $GLOBAL_HOOKS_DIR"

# 3. Add watched paths to config
mkdir -p "$MNEMOS_CONFIG_DIR"
if [ ! -f "$REPOS_CONFIG" ]; then
    echo "# Mnemos watched repository paths (one per line)" > "$REPOS_CONFIG"
    echo "# Hooks only trigger extraction for repos under these paths." >> "$REPOS_CONFIG"
fi

for wp in "${WATCH_PATHS[@]}"; do
    # Resolve to absolute path
    ABS_PATH="$(cd "$wp" 2>/dev/null && pwd || echo "$wp")"
    # Check if already in config
    if grep -qxF "$ABS_PATH" "$REPOS_CONFIG" 2>/dev/null; then
        echo "Already watched: $ABS_PATH"
    else
        echo "$ABS_PATH" >> "$REPOS_CONFIG"
        echo "Added to watch list: $ABS_PATH"
    fi
done

# 4. Set trigger if specified
if [ -n "$TRIGGER" ]; then
    echo ""
    echo "Add to your shell profile (~/.zshrc or ~/.bashrc):"
    echo "  export MNEMOS_HOOK_TRIGGER=$TRIGGER"
fi

echo ""
echo "Done. Mnemos hooks are now active globally."
echo "Watched repos: $(cat "$REPOS_CONFIG" | grep -v '^#' | grep -v '^$' | tr '\n' ', ')"
```

- [ ] **Step 5: Make all scripts executable**

```bash
chmod +x /Users/jamal/Developments/Projects/digital-gigafactory/mnemos/scripts/hooks/post-commit
chmod +x /Users/jamal/Developments/Projects/digital-gigafactory/mnemos/scripts/hooks/pre-push
chmod +x /Users/jamal/Developments/Projects/digital-gigafactory/mnemos/scripts/hooks/mnemos-common.sh
chmod +x /Users/jamal/Developments/Projects/digital-gigafactory/mnemos/scripts/install-hooks.sh
```

- [ ] **Step 6: Verify hook scripts are valid shell**

Run: `bash -n /Users/jamal/Developments/Projects/digital-gigafactory/mnemos/scripts/hooks/post-commit && bash -n /Users/jamal/Developments/Projects/digital-gigafactory/mnemos/scripts/hooks/pre-push && bash -n /Users/jamal/Developments/Projects/digital-gigafactory/mnemos/scripts/hooks/mnemos-common.sh && bash -n /Users/jamal/Developments/Projects/digital-gigafactory/mnemos/scripts/install-hooks.sh && echo "OK"`
Expected: OK

- [ ] **Step 7: Commit**

```bash
cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos
git add scripts/hooks/ scripts/install-hooks.sh
git commit -m "feat: add global git hooks for automatic memory extraction"
```

---

## Task 12: Rewrite README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Rewrite README.md**

Replace the entire content of `README.md` with updated branding, features, and examples. Key sections:

1. **Header** — Mnemos branding with logo
2. **Tagline** — "Intelligent memory layer for AI coding agents"
3. **Features** — Three pillars:
   - Semantic Search (multi-collection, code-aware, skills, memory)
   - Smart Memory (auto-extraction from commits, deduplication with merge, approval workflow)
   - Automatic Indexing (file watcher, language-aware chunking, push API)
4. **Quick Start** — docker compose up, install hooks
5. **Git Hooks Setup** — `scripts/install-hooks.sh` usage
6. **MCP Integration** — Claude Code / Claude Desktop config
7. **Available MCP Tools** — table with `mnemos_*` names
8. **CLI Reference** — `mnemos` commands
9. **Collections** — table with `mnemos_*` names
10. **Configuration** — env var table with all `MNEMOS_*` vars including LLM config
11. **Architecture** — directory tree
12. **Development** — setup and test commands

All code examples use `mnemos` CLI and `mnemos_*` tool/collection names.

- [ ] **Step 2: Commit**

```bash
cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos
git add README.md
git commit -m "docs: rewrite README with mnemos branding and new features"
```

---

## Task 13: Run full test suite and verify

- [ ] **Step 1: Run all tests**

Run: `cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos && python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 2: Verify docker-compose is valid**

Run: `cd /Users/jamal/Developments/Projects/digital-gigafactory/mnemos && docker compose config --quiet`
Expected: Exit code 0, no errors

- [ ] **Step 3: Verify hook scripts are valid**

Run: `bash -n /Users/jamal/Developments/Projects/digital-gigafactory/mnemos/scripts/hooks/post-commit && bash -n /Users/jamal/Developments/Projects/digital-gigafactory/mnemos/scripts/hooks/pre-push && echo "OK"`
Expected: OK

- [ ] **Step 4: Final commit if any fixes needed**

Only if previous steps revealed issues to fix.
