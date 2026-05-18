# Tags-Based Project Tagging — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-value `project: str` filter with a multi-value `tags: list[str]` filter on `mnemos_code` and `mnemos_memory`, exposed as a new `paths:` schema in `config/projects.yaml` (key = path-prefix, value = list of tags).

**Architecture:**
- Each chunk gets a `tags: list[str]` payload in Qdrant alongside the existing `project` field (= `tags[0]`, kept for backward compat and display).
- Filtering at query time uses Qdrant `MatchAny` (OR) and an `AND` combinator built from multiple `must` conditions (AND).
- The legacy `projects:` YAML schema (`name → path_prefixes`) is auto-converted to the new `paths:` schema (`path → [name]`) at load time, so existing configs keep working with a deprecation warning.
- Default fallback (no YAML match) emits hierarchical path segments as tags (`foo/bar/baz/x.go` → `[foo, foo/bar, foo/bar/baz]`), giving zero-config users automatic hierarchy.

**Tech Stack:** Python 3.12, Qdrant (`MatchAny`, `MatchValue`, payload index), FastAPI, Pydantic, Click, MCP SDK, pytest.

---

## File Structure

**Modified:**
- `packages/core/projects.py` — `detect_project` → `detect_tags` (returns `list[str]`); add new YAML schema loader.
- `packages/core/indexer.py` — write `tags` to payload, keep `project = tags[0]`.
- `packages/core/collections.py` — add `TAGS_PAYLOAD_FIELD` constant.
- `server/search.py` — replace single-value project filter with multi-value tags filter (any/all).
- `server/api.py` — add `tags_any` / `tags_all` to DTOs.
- `server/mcp_tools.py` — expose `tags_any` / `tags_all` on relevant tools.
- `cli/main.py` — add `--tags` / `--tags-all` flags.
- `config/projects.yaml` — migrate to new `paths:` schema.
- `config/projects.example.yaml` — rewrite as new schema with both formats documented.
- `README.md` — update project tagging section.
- `CLAUDE.md` — update project-detection section.
- `docs/CONFIGURATION.md` — rewrite `projects.yaml` section.
- `docs/ARCHITECTURE.md` — update payload schema reference.
- `docs/ROADMAP.md` — mark feature done.

**Created:**
- `tests/test_projects_tags.py` — new tests for `detect_tags` and the new loader.

**Tests touched:**
- `tests/test_projects.py` — keep legacy tests as compatibility regression suite.

---

## Task 1: Add `TAGS_PAYLOAD_FIELD` constant

**Files:**
- Modify: `packages/core/collections.py`

- [ ] **Step 1: Add the constant next to `PROJECT_PAYLOAD_FIELD`**

In `packages/core/collections.py` after the `PROJECT_PAYLOAD_FIELD` declaration (line 13), add:

```python
# Multi-value scoping field. Each chunk carries a list of tags (project name +
# any parent / cross-cutting labels declared in config/projects.yaml). Filtered
# at query time with MatchAny (OR) or AND-combined conditions.
TAGS_PAYLOAD_FIELD = "tags"
```

- [ ] **Step 2: Commit**

```bash
git add packages/core/collections.py
git commit -m "feat(core): add TAGS_PAYLOAD_FIELD constant"
```

---

## Task 2: Implement `detect_tags()` returning a list

**Files:**
- Modify: `packages/core/projects.py`
- Create: `tests/test_projects_tags.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_projects_tags.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from core.projects import detect_tags, load_path_tags


# ---------------------------------------------------------------------------
# detect_tags — default (no overrides)
# ---------------------------------------------------------------------------


def test_detect_tags_default_emits_hierarchical_segments():
    # Zero-config users get cumulative path prefixes as tags, so they can
    # filter by any level of the directory hierarchy out of the box.
    assert detect_tags("foo/bar/baz/file.go") == ["foo", "foo/bar", "foo/bar/baz"]


def test_detect_tags_default_single_segment():
    assert detect_tags("topdir/file.go") == ["topdir"]


def test_detect_tags_default_empty_or_absolute_returns_empty():
    assert detect_tags("") == []
    assert detect_tags("/abs/path") == []


def test_detect_tags_default_skips_dot_segments():
    # './file.go' normalises to 'file.go' — single bare filename, no parent.
    assert detect_tags("./file.go") == ["file.go"]
    assert detect_tags("../file.go") == []


# ---------------------------------------------------------------------------
# detect_tags — explicit overrides
# ---------------------------------------------------------------------------


def test_detect_tags_override_emits_explicit_list():
    overrides = {
        "myproject/services/": ["my-service", "myproject", "go"],
    }
    assert detect_tags(
        "myproject/services/handler.go", overrides
    ) == ["my-service", "myproject", "go"]


def test_detect_tags_longest_prefix_wins():
    overrides = {
        "wrapper/":     ["outer"],
        "wrapper/sub/": ["inner", "outer"],
    }
    assert detect_tags("wrapper/sub/file.go", overrides) == ["inner", "outer"]
    assert detect_tags("wrapper/other/file.go", overrides) == ["outer"]


def test_detect_tags_falls_back_to_default_when_no_override_matches():
    overrides = {"unrelated/": ["x"]}
    assert detect_tags("myproject/file.go", overrides) == ["myproject"]


def test_detect_tags_empty_overrides_uses_default():
    assert detect_tags("project-x/file.go", {}) == ["project-x"]


def test_detect_tags_normalises_missing_trailing_slash():
    overrides = {"myproject/services": ["alias"]}
    assert detect_tags("myproject/services/handler.go", overrides) == ["alias"]
    assert detect_tags("myproject/services_v2/handler.go", overrides) == ["myproject"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
source venv/bin/activate
pytest tests/test_projects_tags.py -v
```

Expected: All FAIL with `ImportError: cannot import name 'detect_tags' from 'core.projects'`.

- [ ] **Step 3: Implement `detect_tags()` in `packages/core/projects.py`**

Append to `packages/core/projects.py` (keep the existing `detect_project` and `load_project_overrides` for back-compat — we will deprecate them in Task 9):

```python
# ---------------------------------------------------------------------------
# Tags model (new) — replaces the single-value `project` over time.
# ---------------------------------------------------------------------------

PathTags = dict[str, list[str]]
"""Mapping path-prefix (relative to codebase root) → list of tags to apply
to every chunk whose file lives under that prefix. The first tag is the
'primary' (mirrored into the legacy `project` payload field for display)."""


def detect_tags(
    rel_path: str,
    overrides: PathTags | None = None,
) -> list[str]:
    """Resolve the list of tags for a path relative to the codebase mount.

    Resolution order:
        1. Explicit ``overrides`` (longest matching prefix wins).
        2. Default convention: cumulative path-segments
           (``foo/bar/baz`` → ``["foo", "foo/bar", "foo/bar/baz"]``)
           so users with zero configuration still get a usable hierarchy.

    Returns an empty list for empty / absolute / dot-only paths.
    """
    if not rel_path or rel_path.startswith("/"):
        return []

    # 1. Explicit overrides — longest matching prefix wins.
    if overrides:
        best_tags: list[str] | None = None
        best_len = 0
        for prefix, tags in overrides.items():
            if not prefix:
                continue
            normalised = prefix if prefix.endswith("/") else prefix + "/"
            if rel_path.startswith(normalised) and len(normalised) > best_len:
                best_tags = list(tags)
                best_len = len(normalised)
        if best_tags:
            return best_tags

    # 2. Default: cumulative segment hierarchy.
    parts = Path(rel_path).parts
    if not parts or parts[0] in (".", ".."):
        # './file.go' becomes ('file.go',); '..' is skipped.
        if parts and parts[0] == "..":
            return []
        return [parts[0]] if parts else []

    cumulative: list[str] = []
    acc = ""
    # Drop the filename: tags should reflect directories, not the file itself.
    dir_parts = parts[:-1] if len(parts) > 1 else parts
    for segment in dir_parts:
        if not segment:
            continue
        acc = f"{acc}/{segment}" if acc else segment
        cumulative.append(acc)
    return cumulative or [parts[0]]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_projects_tags.py -v
```

Expected: 9 PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/projects.py tests/test_projects_tags.py
git commit -m "feat(core): add detect_tags() returning hierarchical tag list"
```

---

## Task 3: Implement `load_path_tags()` for the new YAML schema

**Files:**
- Modify: `packages/core/projects.py`
- Modify: `tests/test_projects_tags.py`

- [ ] **Step 1: Append loader tests**

Append to `tests/test_projects_tags.py`:

```python
# ---------------------------------------------------------------------------
# load_path_tags — new schema
# ---------------------------------------------------------------------------


def test_load_path_tags_missing_file(tmp_path: Path):
    assert load_path_tags(tmp_path / "nope.yaml") == {}


def test_load_path_tags_new_schema(tmp_path: Path):
    p = tmp_path / "projects.yaml"
    p.write_text(
        """
paths:
  Projects/acme-corp/acme/front/applications/ecommerce/:
    - acme-front-app-ecommerce
    - acme
    - acme-front
    - acme-corp

  Projects/digital-gigafactory/moby/services/:
    - moby-services
    - moby
    - dgf
""",
        encoding="utf-8",
    )
    out = load_path_tags(p)
    assert out == {
        "Projects/acme-corp/acme/front/applications/ecommerce/": [
            "acme-front-app-ecommerce", "acme", "acme-front", "acme-corp",
        ],
        "Projects/digital-gigafactory/moby/services/": [
            "moby-services", "moby", "dgf",
        ],
    }


def test_load_path_tags_legacy_schema_auto_converts(tmp_path: Path, caplog):
    """The legacy `projects:` schema must keep working — we convert it
    in-memory to the new path → [project_name] shape and emit a warning."""
    p = tmp_path / "projects.yaml"
    p.write_text(
        """
projects:
  myproject:
    path_prefixes:
      - myproject/
  monorepo-shared:
    path_prefixes:
      - apps/billing/shared/
      - libs/shared/
""",
        encoding="utf-8",
    )
    with caplog.at_level("WARNING"):
        out = load_path_tags(p)
    assert out == {
        "myproject/":            ["myproject"],
        "apps/billing/shared/":  ["monorepo-shared"],
        "libs/shared/":          ["monorepo-shared"],
    }
    assert any("legacy" in rec.message.lower() for rec in caplog.records)


def test_load_path_tags_empty_file_returns_empty(tmp_path: Path):
    p = tmp_path / "projects.yaml"
    p.write_text("", encoding="utf-8")
    assert load_path_tags(p) == {}


def test_load_path_tags_drops_malformed_entries(tmp_path: Path, caplog):
    p = tmp_path / "projects.yaml"
    p.write_text(
        """
paths:
  good/path/:
    - tag-a
    - tag-b
  bad-not-a-list/: "just a string"
  empty/path/: []
""",
        encoding="utf-8",
    )
    with caplog.at_level("WARNING"):
        out = load_path_tags(p)
    assert out == {"good/path/": ["tag-a", "tag-b"]}


def test_load_path_tags_strips_blank_tags(tmp_path: Path):
    p = tmp_path / "projects.yaml"
    p.write_text(
        """
paths:
  x/:
    - ""
    - "  "
    - real-tag
""",
        encoding="utf-8",
    )
    assert load_path_tags(p) == {"x/": ["real-tag"]}
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
pytest tests/test_projects_tags.py::test_load_path_tags_new_schema -v
```

Expected: FAIL with `ImportError: cannot import name 'load_path_tags'`.

- [ ] **Step 3: Implement `load_path_tags()`**

Append to `packages/core/projects.py`:

```python
def load_path_tags(config_path: Path | str) -> PathTags:
    """Load `config/projects.yaml` and return path → tags mapping.

    Two supported schemas:

    1. New (preferred) — keys are path-prefixes:

        paths:
          myproject/services/:
            - my-service
            - myproject

    2. Legacy — keys are project names with `path_prefixes:` lists:

        projects:
          my-service:
            path_prefixes: [myproject/services/]

    Legacy configs are converted to the new shape with a single-element
    tag list (`[project_name]`) and a one-time WARNING is logged. The
    function returns an empty dict on error rather than raising — callers
    fall back to the default first-segment behaviour in that case.
    """
    path = Path(config_path)
    if not path.exists():
        return {}

    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed; cannot load %s", path)
        return {}

    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return {}

    if not isinstance(raw, dict):
        return {}

    # --- Path A: new schema ---
    if "paths" in raw and isinstance(raw["paths"], dict):
        return _parse_new_schema(raw["paths"], path)

    # --- Path B: legacy schema, auto-converted ---
    if "projects" in raw and isinstance(raw["projects"], dict):
        logger.warning(
            "%s: detected legacy `projects:` schema. It still works but the "
            "new `paths:` schema is recommended (see CONFIGURATION.md).",
            path,
        )
        return _convert_legacy_schema(raw["projects"])

    return {}


def _parse_new_schema(paths_block: dict, source: Path) -> PathTags:
    out: PathTags = {}
    for prefix, value in paths_block.items():
        if not isinstance(value, list):
            logger.warning(
                "%s: entry for %r is not a list of tags; skipping.", source, prefix,
            )
            continue
        cleaned = [t.strip() for t in value if isinstance(t, str) and t.strip()]
        if cleaned:
            out[str(prefix)] = cleaned
    return out


def _convert_legacy_schema(projects_block: dict) -> PathTags:
    out: PathTags = {}
    for project_name, cfg in projects_block.items():
        if not isinstance(cfg, dict):
            continue
        prefixes = cfg.get("path_prefixes") or []
        if not isinstance(prefixes, list):
            continue
        for prefix in prefixes:
            if isinstance(prefix, str) and prefix.strip():
                out[prefix] = [str(project_name)]
    return out
```

- [ ] **Step 4: Run all project tests**

```bash
pytest tests/test_projects_tags.py tests/test_projects.py -v
```

Expected: All PASS (the legacy `test_projects.py` keeps passing since `detect_project` and `load_project_overrides` are still exported unchanged).

- [ ] **Step 5: Commit**

```bash
git add packages/core/projects.py tests/test_projects_tags.py
git commit -m "feat(core): add load_path_tags() with legacy-schema auto-conversion"
```

---

## Task 4: Write tags to chunk payload in `Indexer`

**Files:**
- Modify: `packages/core/indexer.py`
- Modify: `tests/test_indexer.py`

- [ ] **Step 1: Inspect the existing indexer test for the patterns it uses**

```bash
grep -n "project" tests/test_indexer.py | head -20
```

This shows you how the existing tests stub Qdrant + check payloads. Reuse the same fixtures.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_indexer.py` (use the same `qdrant_stub` / `embeddings_stub` fixtures already present in that file — adapt if names differ):

```python
def test_indexer_writes_tags_alongside_project(qdrant_stub, embeddings_stub, tmp_path):
    from core.indexer import Indexer
    from core.collections import PROJECT_PAYLOAD_FIELD, TAGS_PAYLOAD_FIELD

    indexer = Indexer(
        qdrant_client=qdrant_stub,
        embedding_service=embeddings_stub,
        path_tags={
            "Projects/acme-corp/acme/front/applications/ecommerce/": [
                "acme-front-app-ecommerce", "acme", "acme-front", "acme-corp",
            ],
        },
        codebase_root="/data/codebase",
    )

    file_path = "/data/codebase/Projects/acme-corp/acme/front/applications/ecommerce/main.go"
    n = indexer.index_file(
        content="package main\nfunc main() {}\n",
        file_path=file_path,
        collection="mnemos_code",
    )

    assert n >= 1
    upserted = qdrant_stub.upserts[-1].points  # adapt to actual stub shape
    payload = upserted[0].payload
    assert payload[PROJECT_PAYLOAD_FIELD] == "acme-front-app-ecommerce"
    assert payload[TAGS_PAYLOAD_FIELD] == [
        "acme-front-app-ecommerce", "acme", "acme-front", "acme-corp",
    ]


def test_indexer_default_emits_hierarchical_tags(qdrant_stub, embeddings_stub):
    from core.indexer import Indexer
    from core.collections import PROJECT_PAYLOAD_FIELD, TAGS_PAYLOAD_FIELD

    indexer = Indexer(
        qdrant_client=qdrant_stub,
        embedding_service=embeddings_stub,
        path_tags=None,
        codebase_root="/data/codebase",
    )
    indexer.index_file(
        content="x = 1\n",
        file_path="/data/codebase/foo/bar/baz/x.py",
        collection="mnemos_code",
    )
    payload = qdrant_stub.upserts[-1].points[0].payload
    assert payload[TAGS_PAYLOAD_FIELD] == ["foo", "foo/bar", "foo/bar/baz"]
    assert payload[PROJECT_PAYLOAD_FIELD] == "foo"  # tags[0]
```

- [ ] **Step 3: Run the new tests to confirm they fail**

```bash
pytest tests/test_indexer.py::test_indexer_writes_tags_alongside_project tests/test_indexer.py::test_indexer_default_emits_hierarchical_tags -v
```

Expected: FAIL — `Indexer.__init__()` got an unexpected keyword argument 'path_tags'.

- [ ] **Step 4: Update `packages/core/indexer.py`**

Replace the constructor and `_resolve_project` section. Patch:

```python
# At top of file: update the import line
from core.collections import (
    DENSE_VECTOR_NAME,
    PROJECT_PAYLOAD_FIELD,
    SPARSE_VECTOR_NAME,
    TAGS_PAYLOAD_FIELD,        # NEW
)
from core.projects import PathTags, detect_tags
```

Update the `__init__` signature:

```python
def __init__(
    self,
    qdrant_client: QdrantClient,
    embedding_service: EmbeddingService,
    contextual_enricher: ContextualEnricher | None = None,
    path_tags: PathTags | None = None,                 # NEW (replaces project_overrides)
    codebase_root: str = "/data/codebase",
) -> None:
    self._qdrant = qdrant_client
    self._embeddings = embedding_service
    self._contextual = contextual_enricher
    self._path_tags = path_tags or {}
    self._codebase_root = codebase_root.rstrip("/")
    self._go_chunker = GoChunker()
    self._vue_chunker = VueChunker()
    self._md_chunker = MarkdownChunker()
    self._fallback_chunker = FallbackChunker()
```

Replace `_resolve_project` with `_resolve_tags`:

```python
def _resolve_tags(self, file_path: str, override: str | None) -> list[str]:
    """Decide which tag list to write into a chunk's payload.

    Order:
        1. Explicit `override` (CLI flag / push API field) — emits [override].
        2. YAML / segment-based detection on the path relative to the
           codebase mount root.
    """
    if override:
        return [override]
    rel = file_path
    if file_path.startswith(self._codebase_root + "/"):
        rel = file_path[len(self._codebase_root) + 1:]
    return detect_tags(rel, overrides=self._path_tags)
```

Update `index_file`. The relevant block (around line 145-164) becomes:

```python
# Resolve tags once per file. Only relevant for the code/memory
# collections; skills/docs leave the fields unset.
resolved_tags: list[str] = (
    self._resolve_tags(file_path, project)
    if collection in _PROJECT_INDEXED_COLLECTIONS
    else []
)
resolved_project = resolved_tags[0] if resolved_tags else None
```

Then in the chunk-payload construction (around line 158-164):

```python
payload = {
    **chunk,
    "last_indexed_at": now,
    "file_mtime": file_mtime or time.time(),
}
if resolved_project is not None:
    payload[PROJECT_PAYLOAD_FIELD] = resolved_project
if resolved_tags:
    payload[TAGS_PAYLOAD_FIELD] = resolved_tags
```

Update `_ensure_project_payload_index` to also index `tags`:

```python
def _ensure_payload_indexes(self, collection_name: str) -> None:
    """Idempotently create keyword payload indexes on `project` and `tags`.
    `tags` is indexed as keyword so MatchAny on the array filters fast."""
    for field in (PROJECT_PAYLOAD_FIELD, TAGS_PAYLOAD_FIELD):
        try:
            self._qdrant.create_payload_index(
                collection_name=collection_name,
                field_name=field,
                field_schema=KeywordIndexParams(type=PayloadSchemaType.KEYWORD),
            )
        except Exception:
            # Qdrant raises if the index already exists; that's fine.
            pass
```

And rename the call site:

```python
if collection_name in _PROJECT_INDEXED_COLLECTIONS:
    self._ensure_payload_indexes(collection_name)
```

- [ ] **Step 5: Run the indexer tests**

```bash
pytest tests/test_indexer.py -v
```

Expected: PASS (including the 2 new tests).

- [ ] **Step 6: Commit**

```bash
git add packages/core/indexer.py tests/test_indexer.py
git commit -m "feat(indexer): write tags array to chunk payload (project = tags[0])"
```

---

## Task 5: Wire `Indexer` construction at startup with the new loader

**Files:**
- Modify: `server/main.py`
- Modify: `server/config.py` (only if path setting is renamed; otherwise no-op)

- [ ] **Step 1: Locate the indexer construction**

```bash
grep -n "Indexer(" server/main.py
grep -n "load_project_overrides\|load_path_tags\|projects.yaml" server/main.py server/config.py
```

- [ ] **Step 2: Replace `load_project_overrides` with `load_path_tags`**

In `server/main.py`, find the `lifespan` block (around line 60-90) and update the import + call:

```python
# Before:
#   from core.projects import load_project_overrides
#   overrides = load_project_overrides(_Path(settings.mnemos_projects_config_path))
#   indexer = Indexer(..., project_overrides=overrides, ...)
#
# After:

from core.projects import load_path_tags
path_tags = load_path_tags(_Path(settings.mnemos_projects_config_path))
indexer = Indexer(
    qdrant_client=qdrant_client,
    embedding_service=embedding_service,
    contextual_enricher=contextual_enricher,
    path_tags=path_tags,
    codebase_root=settings.codebase_path,
)
```

Keep the setting name (`mnemos_projects_config_path`) — the file name `projects.yaml` does not change; only its content schema does.

- [ ] **Step 3: Smoke-test that the server boots**

```bash
make up-dev
sleep 5
curl -s http://localhost:8100/health
docker compose logs rag-server | tail -40
```

Expected: server boots, no errors, the legacy `projects.yaml` (if still in place) triggers exactly one "legacy schema" warning in the logs.

- [ ] **Step 4: Commit**

```bash
git add server/main.py
git commit -m "feat(server): wire Indexer to new load_path_tags loader"
```

---

## Task 6: Multi-value `tags_any` / `tags_all` filter in `SearchService`

**Files:**
- Modify: `server/search.py`
- Modify: `tests/test_search.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_search.py` (adapt fixtures to the file's existing style):

```python
def test_search_code_tags_any_matches_or(search_service, qdrant_stub):
    qdrant_stub.queue_hits([
        _stub_code_hit(tags=["acme", "acme-front-app-ecommerce"], score=0.9),
        _stub_code_hit(tags=["moby", "moby-services"], score=0.5),
    ])
    results = search_service.search_code(
        query="auth",
        tags_any=["acme", "moby-services"],
    )
    # Filter is enforced server-side, but here we assert the Qdrant call shape:
    filt = qdrant_stub.last_query_filter
    assert any(
        cond.key == "tags" and set(cond.match.any) == {"acme", "moby-services"}
        for cond in filt.must
    )


def test_search_code_tags_all_combines_with_and(search_service, qdrant_stub):
    search_service.search_code(query="auth", tags_all=["acme", "vue3"])
    filt = qdrant_stub.last_query_filter
    # tags_all → multiple `must` FieldConditions, each with MatchAny([tag])
    matched = [c for c in filt.must if c.key == "tags"]
    assert len(matched) == 2
    assert {tuple(c.match.any) for c in matched} == {("acme",), ("vue3",)}


def test_search_code_legacy_project_param_still_filters(search_service, qdrant_stub):
    """Back-compat: passing `project=...` must still produce a single-value
    filter on the legacy `project` payload field."""
    search_service.search_code(query="auth", project="moby-services")
    filt = qdrant_stub.last_query_filter
    assert any(
        c.key == "project" and c.match.value == "moby-services"
        for c in filt.must
    )
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
pytest tests/test_search.py -v -k "tags_any or tags_all or legacy_project_param"
```

Expected: 3 FAIL.

- [ ] **Step 3: Update `server/search.py`**

Patch the imports if needed (`MatchAny` is already imported). Update `search_code`:

```python
def search_code(
    self,
    query: str,
    language: str | None = None,
    symbol_type: str | None = None,
    project: str | None = None,
    tags_any: list[str] | None = None,            # NEW
    tags_all: list[str] | None = None,            # NEW
    path_filter: str | None = None,
    limit: int = 5,
) -> list[CodeSearchResult]:
    """Search the single `mnemos_code` collection; scope by `tags_any`
    (OR), `tags_all` (AND), or the legacy `project` (single-value)."""
    dense_vec = self._embeddings.embed(query)
    sparse_vec = bm25_sparse(query)

    must_conditions: list = []
    if tags_any:
        must_conditions.append(FieldCondition(key="tags", match=MatchAny(any=tags_any)))
    if tags_all:
        for tag in tags_all:
            must_conditions.append(FieldCondition(key="tags", match=MatchAny(any=[tag])))
    if project:
        must_conditions.append(FieldCondition(key="project", match=MatchValue(value=project)))
    if language:
        must_conditions.append(FieldCondition(key="language", match=MatchValue(value=language)))
    if symbol_type:
        must_conditions.append(FieldCondition(key="chunk_type", match=MatchValue(value=symbol_type)))
    if path_filter:
        must_conditions.append(FieldCondition(key="file_path", match=MatchValue(value=path_filter)))
    query_filter = Filter(must=must_conditions) if must_conditions else None

    per_collection = _HYBRID_TOP if (self._reranker and self._reranker.enabled) else limit
    hits = self._hybrid_query("mnemos_code", dense_vec, sparse_vec, query_filter, per_collection)

    all_results: list[CodeSearchResult] = [
        CodeSearchResult(
            content=hit.payload.get("content", ""),
            file_path=hit.payload.get("file_path", ""),
            score=hit.score,
            chunk_type=hit.payload.get("chunk_type", ""),
            collection="mnemos_code",
            metadata={
                "project": hit.payload.get("project"),
                "tags":    hit.payload.get("tags", []),     # NEW
            },
            language=hit.payload.get("language", ""),
            symbol_name=hit.payload.get("symbol_name"),
            package=hit.payload.get("package"),
        )
        for hit in hits
    ]
    return self._rerank_and_select(query, all_results, limit)
```

Apply the same `tags_any` / `tags_all` extension to `search()` (around line 187) and `search_memory()` (around line 359). For `search_memory` add `tags_any` / `tags_all` next to the existing `project` filter; the legacy `project=...` stays for back-compat.

- [ ] **Step 4: Run the search tests**

```bash
pytest tests/test_search.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add server/search.py tests/test_search.py
git commit -m "feat(search): add tags_any (OR) and tags_all (AND) filters"
```

---

## Task 7: Expose `tags_any` / `tags_all` in HTTP API DTOs

**Files:**
- Modify: `server/api.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing API tests**

Append to `tests/test_api.py`:

```python
def test_search_code_accepts_tags_any(api_client, search_service_spy):
    resp = api_client.post(
        "/api/search-code",
        json={"query": "auth", "tags_any": ["acme", "moby"]},
    )
    assert resp.status_code == 200
    assert search_service_spy.last_call["tags_any"] == ["acme", "moby"]


def test_search_code_accepts_tags_all(api_client, search_service_spy):
    resp = api_client.post(
        "/api/search-code",
        json={"query": "auth", "tags_all": ["acme", "vue3"]},
    )
    assert resp.status_code == 200
    assert search_service_spy.last_call["tags_all"] == ["acme", "vue3"]


def test_search_code_legacy_project_param_still_works(api_client, search_service_spy):
    resp = api_client.post(
        "/api/search-code",
        json={"query": "auth", "project": "moby-services"},
    )
    assert resp.status_code == 200
    assert search_service_spy.last_call["project"] == "moby-services"
```

- [ ] **Step 2: Confirm failure**

```bash
pytest tests/test_api.py -v -k "tags_any or tags_all or legacy_project_param"
```

Expected: 3 FAIL (pydantic rejects unknown fields by default in Mnemos).

- [ ] **Step 3: Update DTOs in `server/api.py`**

```python
class SearchCodeRequest(BaseModel):
    query: str
    language: Optional[str] = None
    symbol_type: Optional[str] = None
    project:  Optional[str] = None              # legacy single-value
    tags_any: Optional[List[str]] = None        # NEW — OR filter
    tags_all: Optional[List[str]] = None        # NEW — AND filter
    path_filter: Optional[str] = None
    limit: int = 5


class SearchRequest(BaseModel):
    query: str
    collections: Optional[List[str]] = None
    file_types: Optional[List[str]] = None
    path_filter: Optional[str] = None
    limit: int = 5
    project:  Optional[str] = None
    tags_any: Optional[List[str]] = None        # NEW
    tags_all: Optional[List[str]] = None        # NEW


class SearchMemoryRequest(BaseModel):
    query: str
    project:  Optional[str] = None
    tags_any: Optional[List[str]] = None        # NEW
    tags_all: Optional[List[str]] = None        # NEW
    memory_type: Optional[str] = None
    limit: int = 5
```

Update the corresponding handlers to forward the new fields:

```python
@api_router.post("/api/search-code")
async def search_code(body: SearchCodeRequest, request: Request):
    results = request.app.state.search_service.search_code(
        query=body.query,
        language=body.language,
        symbol_type=body.symbol_type,
        project=body.project,
        tags_any=body.tags_any,
        tags_all=body.tags_all,
        path_filter=body.path_filter,
        limit=body.limit,
    )
    return {"results": [r.model_dump() for r in results]}
```

Same pattern for `/api/search` and `/api/search-memory`.

- [ ] **Step 4: Run API tests**

```bash
pytest tests/test_api.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/api.py tests/test_api.py
git commit -m "feat(api): expose tags_any/tags_all on search DTOs (project kept for back-compat)"
```

---

## Task 8: Expose `tags_any` / `tags_all` in MCP tools

**Files:**
- Modify: `server/mcp_tools.py`
- Modify: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_mcp_tools.py`:

```python
def test_mnemos_search_code_schema_exposes_tags_any():
    from server.mcp_tools import TOOL_DEFINITIONS
    tool = next(t for t in TOOL_DEFINITIONS if t.name == "mnemos_search_code")
    props = tool.inputSchema["properties"]
    assert props["tags_any"]["type"] == "array"
    assert props["tags_any"]["items"]["type"] == "string"
    assert props["tags_all"]["type"] == "array"


async def test_mnemos_search_code_dispatch_forwards_tags(search_service_spy, indexer_stub):
    from server.mcp_tools import _dispatch_tool
    await _dispatch_tool(
        "mnemos_search_code",
        {"query": "auth", "tags_any": ["acme", "moby"], "tags_all": ["vue3"]},
        search_service_spy, indexer_stub, qdrant_client=None,
    )
    call = search_service_spy.last_call
    assert call["tags_any"] == ["acme", "moby"]
    assert call["tags_all"] == ["vue3"]
```

- [ ] **Step 2: Confirm failure**

```bash
pytest tests/test_mcp_tools.py -v -k "tags"
```

Expected: 2 FAIL.

- [ ] **Step 3: Add the properties to the relevant tool schemas**

In `server/mcp_tools.py`, inside the `mnemos_search_code` tool's `inputSchema["properties"]`:

```python
"tags_any": {
    "type": "array",
    "items": {"type": "string"},
    "description": "Filter to chunks tagged with ANY of these (OR). Use for cross-cutting queries that span multiple sub-projects.",
},
"tags_all": {
    "type": "array",
    "items": {"type": "string"},
    "description": "Filter to chunks tagged with ALL of these (AND). Combine with tags_any to narrow further.",
},
```

Add the same two properties to `mnemos_search` and `mnemos_search_memory` tool schemas.

In the dispatch (`_dispatch_tool`), forward the new args:

```python
if name == "mnemos_search_code":
    results = search_service.search_code(
        query=args["query"],
        language=args.get("language"),
        symbol_type=args.get("symbol_type"),
        project=args.get("project"),
        tags_any=args.get("tags_any"),
        tags_all=args.get("tags_all"),
        path_filter=args.get("path_filter"),
        limit=args.get("limit", 5),
    )
    return [r.model_dump() for r in results]
```

Same forwarding for `mnemos_search` and `mnemos_search_memory`.

- [ ] **Step 4: Run MCP tests**

```bash
pytest tests/test_mcp_tools.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/mcp_tools.py tests/test_mcp_tools.py
git commit -m "feat(mcp): expose tags_any/tags_all on search/search_code/search_memory tools"
```

---

## Task 9: CLI flags `--tags` / `--tags-all` (and deprecate-but-keep `--project`)

**Files:**
- Modify: `cli/main.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Append to `tests/test_cli.py`:

```python
def test_cli_search_passes_tags_to_payload(cli_runner, mock_http_post):
    cli_runner.invoke(
        cli,
        ["search", "auth", "--collection", "mnemos_code",
         "--tags", "acme,moby"],
    )
    body = mock_http_post.last_json
    assert body["tags_any"] == ["acme", "moby"]


def test_cli_search_passes_tags_all_to_payload(cli_runner, mock_http_post):
    cli_runner.invoke(
        cli,
        ["search", "auth", "--collection", "mnemos_code",
         "--tags-all", "acme,vue3"],
    )
    body = mock_http_post.last_json
    assert body["tags_all"] == ["acme", "vue3"]
```

- [ ] **Step 2: Confirm failure**

```bash
pytest tests/test_cli.py -v -k "tags"
```

Expected: 2 FAIL (`--tags` is not a known option).

- [ ] **Step 3: Update the CLI `search` command in `cli/main.py`**

Find the `@cli.command("search")` block. Add the two flags + the payload assembly:

```python
@click.option(
    "--tags",
    default=None,
    help="Comma-separated tags (OR filter). Example: --tags acme,moby-services",
)
@click.option(
    "--tags-all",
    "tags_all",
    default=None,
    help="Comma-separated tags (AND filter). Example: --tags-all acme,vue3",
)
@click.option("--project", default=None, help="Legacy single-project filter.")
def search(
    query: str,
    collection: str,
    limit: int,
    tags: str | None,
    tags_all: str | None,
    project: str | None,
    ...,
) -> None:
    ...
    payload: dict = {"query": query, "limit": limit}
    if collection:
        payload["collections"] = [collection]
    if tags:
        payload["tags_any"] = [t.strip() for t in tags.split(",") if t.strip()]
    if tags_all:
        payload["tags_all"] = [t.strip() for t in tags_all.split(",") if t.strip()]
    if project:
        payload["project"] = project
```

Apply the same two flags to `search-memory` (around line 258).

For `reindex` (around line 304-345), keep `--project` as the per-reindex override (it sets the **primary** tag for everything under `--path`). Add a `--tags` flag too for users who want to set the full tag list explicitly:

```python
@click.option(
    "--project",
    default=None,
    help="Override the primary tag (first entry) for every file under --path.",
)
@click.option(
    "--tags",
    default=None,
    help="Override the full tag list (comma-separated) for every file under --path. "
         "Overrides --project if both are provided.",
)
def reindex(..., project: str | None, tags: str | None, ...) -> None:
    payload: dict = {"collection": collection, ...}
    if tags:
        payload["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    elif project:
        payload["project"] = project
```

- [ ] **Step 4: Run CLI tests**

```bash
pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cli/main.py tests/test_cli.py
git commit -m "feat(cli): add --tags / --tags-all flags on search and search-memory"
```

---

## Task 10: Allow `--tags` override in reindex push flow

**Files:**
- Modify: `server/api.py` (the `ReindexRequest` DTO + the indexer call site)
- Modify: `packages/core/indexer.py` (`index_file` accepts `tags_override` list)

- [ ] **Step 1: Write the failing test in `tests/test_indexer.py`**

```python
def test_indexer_tags_override_wins_over_yaml(qdrant_stub, embeddings_stub):
    from core.indexer import Indexer
    from core.collections import PROJECT_PAYLOAD_FIELD, TAGS_PAYLOAD_FIELD

    indexer = Indexer(
        qdrant_client=qdrant_stub,
        embedding_service=embeddings_stub,
        path_tags={"Projects/foo/": ["yaml-tag"]},
        codebase_root="/data/codebase",
    )
    indexer.index_file(
        content="x = 1\n",
        file_path="/data/codebase/Projects/foo/x.py",
        collection="mnemos_code",
        tags=["override-a", "override-b"],
    )
    payload = qdrant_stub.upserts[-1].points[0].payload
    assert payload[TAGS_PAYLOAD_FIELD] == ["override-a", "override-b"]
    assert payload[PROJECT_PAYLOAD_FIELD] == "override-a"
```

- [ ] **Step 2: Run + confirm failure**

```bash
pytest tests/test_indexer.py::test_indexer_tags_override_wins_over_yaml -v
```

Expected: FAIL (`tags` not a known kwarg).

- [ ] **Step 3: Extend `index_file()` to accept `tags`**

```python
def index_file(
    self,
    content: str,
    file_path: str,
    collection: str,
    file_mtime: float | None = None,
    project: str | None = None,
    tags:   list[str] | None = None,         # NEW
) -> int:
    ...
    if collection in _PROJECT_INDEXED_COLLECTIONS:
        if tags:
            resolved_tags = tags
        else:
            resolved_tags = self._resolve_tags(file_path, project)
    else:
        resolved_tags = []
    resolved_project = resolved_tags[0] if resolved_tags else None
    ...
```

- [ ] **Step 4: Extend `IndexPushRequest` and `ReindexRequest` DTOs in `server/api.py`**

```python
class IndexPushRequest(BaseModel):
    file_path: str
    collection: str
    content: str
    project: Optional[str] = None
    tags:    Optional[List[str]] = None     # NEW


class ReindexRequest(BaseModel):
    collection: str
    path: Optional[str] = None
    full: bool = False
    recreate: bool = False
    workers: int = 1
    project: Optional[str] = None
    tags:    Optional[List[str]] = None     # NEW
```

And forward in the handler / `_index_one_file` helper (line 407 area):

```python
def _index_one_file(indexer, collection, fp, project=None, tags=None) -> int:
    ...
    return indexer.index_file(
        content=content,
        file_path=str(fp),
        collection=collection,
        project=project,
        tags=tags,
    )
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_indexer.py tests/test_api.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/core/indexer.py server/api.py tests/test_indexer.py
git commit -m "feat(indexer): accept explicit tags override on index_file + reindex/push DTOs"
```

---

## Task 11: Migrate `config/projects.yaml` to the new schema

**Files:**
- Modify: `config/projects.yaml`

- [ ] **Step 1: Rewrite `config/projects.yaml`**

Replace the entire content of `config/projects.yaml` with:

```yaml
# Mnemos — project tagging.
# Key   = path prefix RELATIVE to the codebase mount root (no leading slash).
# Value = list of tags. First tag is the "primary" (shown in result payloads).
#
# Longest matching prefix wins on conflict. Paths without a match get
# cumulative segment tags by default (foo/bar/baz → [foo, foo/bar, foo/bar/baz]).
paths:

  # ----- ACCOR -----
  Projects/accor/applications/account-vue/:
    [accor-account-vue, accor, accor-apps, vue3]
  Projects/accor/applications/customer-account/:
    [accor-customer-account, accor, accor-apps, vue3]
  Projects/accor/applications/customer-authentication/:
    [accor-customer-authentication, accor, accor-apps, vue3]
  Projects/accor/applications/customer-loyalty-funnel/:
    [accor-customer-loyalty-funnel, accor, accor-apps, vue3]
  Projects/accor/applications/customer-permalink/:
    [accor-customer-permalink, accor, accor-apps, vue3]
  Projects/accor/libraries/accor-core-library/:
    [accor-lib-core, accor, accor-libs]
  Projects/accor/libraries/common-front/:
    [accor-lib-common-front, accor, accor-libs]
  Projects/accor/libraries/core-components-vue3/:
    [accor-lib-core-components-vue3, accor, accor-libs, vue3]
  Projects/accor/libraries/core-services/:
    [accor-lib-core-services, accor, accor-libs]
  Projects/accor/libraries/customer-api/:
    [accor-lib-customer-api, accor, accor-libs]
  Projects/accor/libraries/customer-common-front/:
    [accor-lib-customer-common-front, accor, accor-libs]
  Projects/accor/docs/:
    [accor-docs, accor, docs]

  # ----- LPEL (la poste) — crawler/, insomnia/, assets/, setup/, docs/ NOT indexed -----
  Projects/acme-corp/acme/sentinel/:
    [acme-sentinel, acme, acme-corp]

  # acme-front : applications (22)
  Projects/acme-corp/acme/front/applications/bo-douane21/:
    [acme-front-app-bo-douane21, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/buralistes/:
    [acme-front-app-buralistes, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/cel/:
    [acme-front-app-cel, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/checkout/:
    [acme-front-app-checkout, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/cn23/:
    [acme-front-app-cn23, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/colibri/:
    [acme-front-app-colibri, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/colis/:
    [acme-front-app-colis, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/douane21/:
    [acme-front-app-douane21, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/ecommerce/:
    [acme-front-app-ecommerce, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/ecoscore/:
    [acme-front-app-ecoscore, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/expebal/:
    [acme-front-app-expebal, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/mobile-payment/:
    [acme-front-app-mobile-payment, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/mtel/:
    [acme-front-app-mtel, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/outils-pratiques/:
    [acme-front-app-outils-pratiques, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/poc-av/:
    [acme-front-app-poc-av, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/procuration/:
    [acme-front-app-procuration, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/reex/:
    [acme-front-app-reex, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/smoke-tests/:
    [acme-front-app-smoke-tests, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/smt/:
    [acme-front-app-smt, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/suivi/:
    [acme-front-app-suivi, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/vrel/:
    [acme-front-app-vrel, acme, acme-front, acme-front-apps, acme-corp]
  Projects/acme-corp/acme/front/applications/vsmp/:
    [acme-front-app-vsmp, acme, acme-front, acme-front-apps, acme-corp]

  # acme-front : libraries (15)
  Projects/acme-corp/acme/front/libraries/address/:
    [acme-front-lib-address, acme, acme-front, acme-front-libs, acme-corp]
  Projects/acme-corp/acme/front/libraries/auth/:
    [acme-front-lib-auth, acme, acme-front, acme-front-libs, acme-corp]
  Projects/acme-corp/acme/front/libraries/cache/:
    [acme-front-lib-cache, acme, acme-front, acme-front-libs, acme-corp]
  Projects/acme-corp/acme/front/libraries/dotenv/:
    [acme-front-lib-dotenv, acme, acme-front, acme-front-libs, acme-corp]
  Projects/acme-corp/acme/front/libraries/e2e-utils/:
    [acme-front-lib-e2e-utils, acme, acme-front, acme-front-libs, acme-corp]
  Projects/acme-corp/acme/front/libraries/edito/:
    [acme-front-lib-edito, acme, acme-front, acme-front-libs, acme-corp]
  Projects/acme-corp/acme/front/libraries/hybris-ecommerce/:
    [acme-front-lib-hybris-ecommerce, acme, acme-front, acme-front-libs, acme-corp]
  Projects/acme-corp/acme/front/libraries/hybris-smartedit/:
    [acme-front-lib-hybris-smartedit, acme, acme-front, acme-front-libs, acme-corp]
  Projects/acme-corp/acme/front/libraries/logger/:
    [acme-front-lib-logger, acme, acme-front, acme-front-libs, acme-corp]
  Projects/acme-corp/acme/front/libraries/omnia/:
    [acme-front-lib-omnia, acme, acme-front, acme-front-libs, acme-corp]
  Projects/acme-corp/acme/front/libraries/solaris/:
    [acme-front-lib-solaris, acme, acme-front, acme-front-libs, acme-corp]
  Projects/acme-corp/acme/front/libraries/store-finder/:
    [acme-front-lib-store-finder, acme, acme-front, acme-front-libs, acme-corp]
  Projects/acme-corp/acme/front/libraries/tag-commander/:
    [acme-front-lib-tag-commander, acme, acme-front, acme-front-libs, acme-corp]
  Projects/acme-corp/acme/front/libraries/transverse/:
    [acme-front-lib-transverse, acme, acme-front, acme-front-libs, acme-corp]
  Projects/acme-corp/acme/front/libraries/utils/:
    [acme-front-lib-utils, acme, acme-front, acme-front-libs, acme-corp]

  # acme-front : others (6)
  Projects/acme-corp/acme/front/others/config/:
    [acme-front-other-config, acme, acme-front, acme-front-others, acme-corp]
  Projects/acme-corp/acme/front/others/local/:
    [acme-front-other-local, acme, acme-front, acme-front-others, acme-corp]
  Projects/acme-corp/acme/front/others/omnia-api/:
    [acme-front-other-omnia-api, acme, acme-front, acme-front-others, acme-corp]
  Projects/acme-corp/acme/front/others/starter-kits/:
    [acme-front-other-starter-kits, acme, acme-front, acme-front-others, acme-corp]
  Projects/acme-corp/acme/front/others/varnish/:
    [acme-front-other-varnish, acme, acme-front, acme-front-others, acme-corp]
  Projects/acme-corp/acme/front/others/webdriverio-phoenix/:
    [acme-front-other-webdriverio-phoenix, acme, acme-front, acme-front-others, acme-corp]

  # ----- DENTAFLOW -----
  Projects/jybl-labs/dentaflow/webapp/:
    [dentaflow, jybl-labs]

  # ----- MOBY -----
  Projects/digital-gigafactory/moby/webapp/:
    [moby-webapp, moby, dgf, nuxt, vue3]
  Projects/digital-gigafactory/moby/backoffice/:
    [moby-backoffice, moby, dgf, nuxt, vue3]
  Projects/digital-gigafactory/moby/site/:
    [moby-site, moby, dgf]
  Projects/digital-gigafactory/moby/services/:
    [moby-services, moby, dgf, go]
  Projects/digital-gigafactory/moby/infra/:
    [moby-infra, moby, dgf]
  Projects/digital-gigafactory/moby/docs/:
    [moby-docs, moby, dgf, docs]

  # ----- TREVIO (framework) -----
  Projects/digital-gigafactory/trevio/go-modules/:
    [trevio-go-modules, trevio, dgf, go]
  Projects/digital-gigafactory/trevio/ui-modules/:
    [trevio-ui-modules, trevio, dgf, nuxt, vue3]
  Projects/digital-gigafactory/trevio/service-template/:
    [trevio-service-template, trevio, dgf, go]
  Projects/digital-gigafactory/trevio/service-runner/:
    [trevio-service-runner, trevio, dgf, go]
  Projects/digital-gigafactory/trevio/apisix/:
    [trevio-apisix, trevio, dgf]
  Projects/digital-gigafactory/trevio/apisix-plugins/:
    [trevio-apisix-plugins, trevio, dgf]
  Projects/digital-gigafactory/trevio/infra/:
    [trevio-infra, trevio, dgf]

  # ----- GAMES -----
  Projects/digital-gigafactory/games/grain/:
    [games-grain, games, dgf]
  Projects/digital-gigafactory/games/medusa/:
    [games-medusa, games, dgf]
  Projects/digital-gigafactory/games/root/:
    [games-root, games, dgf]
  Projects/digital-gigafactory/games/snip/:
    [games-snip, games, dgf]
  Projects/digital-gigafactory/games/volt/:
    [games-volt, games, dgf]

  # ----- EXCURSION-MAROC -----
  Projects/excursion-maroc/:
    [excursion-maroc]
```

- [ ] **Step 2: Reload the server and inspect logs**

```bash
make restart
sleep 5
docker compose logs rag-server | tail -20
```

Expected: no "legacy schema" warning anymore. No errors.

- [ ] **Step 3: Commit**

```bash
git add config/projects.yaml
git commit -m "config(projects): migrate to new paths→tags schema"
```

---

## Task 12: Rewrite `config/projects.example.yaml` for both schemas

**Files:**
- Modify: `config/projects.example.yaml`

- [ ] **Step 1: Replace the file content**

```yaml
# Mnemos — project tagging configuration.
#
# Copy this file to `config/projects.yaml` (gitignored) and adapt to your
# codebase layout. Without this file, Mnemos emits cumulative path segments
# as tags by default — usable but coarse-grained.
#
# ---------------------------------------------------------------------------
# Recommended schema: `paths:` — key = path prefix, value = list of tags.
# ---------------------------------------------------------------------------
#
# - Path prefixes are RELATIVE to the codebase mount root (no leading slash).
# - The first tag in each list is the "primary" — it is also written to the
#   legacy `project` payload field, so older tooling keeps working.
# - Longest matching prefix wins on conflict.
# - Tags are searched with `MatchAny` (OR) by default; pass `--tags-all` to
#   require all tags simultaneously.
#
# Examples:

paths:

  # Example 1 — monorepo, services explicitly tagged + cross-cutting "go" tag
  # myorg/services/billing/:
  #   - billing                    # primary (also stored as `project`)
  #   - myorg
  #   - myorg-services
  #   - go
  # myorg/services/payments/:
  #   - payments
  #   - myorg
  #   - myorg-services
  #   - go

  # Example 2 — same project living under two physical paths
  # apps/billing/payments/:
  #   - payments
  #   - billing-team
  # libs/shared/payments/:
  #   - payments                   # same primary → one logical project
  #   - shared

  # Example 3 — cross-cutting techno tags for transverse queries
  # apps/dashboard/:
  #   - dashboard
  #   - vue3                       # searchable with --tags vue3
  #   - typescript

# ---------------------------------------------------------------------------
# Legacy schema (auto-converted) — `projects:` — kept working for migration.
# ---------------------------------------------------------------------------
#
# If your config still uses the old `projects:` block, Mnemos converts it
# in-memory to the new shape with a single primary tag per project. A WARNING
# is logged once at startup; please migrate when convenient.
#
# projects:
#   billing:
#     path_prefixes:
#       - myorg/services/billing/
#   shared-lib:
#     path_prefixes:
#       - apps/billing/payments/
#       - libs/shared/payments/
```

- [ ] **Step 2: Commit**

```bash
git add config/projects.example.yaml
git commit -m "docs(config): rewrite projects.example.yaml for the new paths→tags schema"
```

---

## Task 13: Update `README.md`

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Locate the project-tagging section**

```bash
grep -n "project\|projects.yaml\|tag" README.md | head -30
```

- [ ] **Step 2: Update the relevant block(s)**

Replace any block describing the legacy single-project filter with text like:

```markdown
### Tagging projects

Mnemos stores every code chunk with a list of **tags** (the project name plus any
cross-cutting labels you declare). Tags live in the `tags` payload of
`mnemos_code` and let you filter searches with OR (`--tags`) or AND (`--tags-all`)
semantics.

Edit `config/projects.yaml` to declare your tagging:

```yaml
paths:
  myorg/services/billing/:
    - billing            # primary (stored as `project` for back-compat)
    - myorg
    - go                 # cross-cutting techno tag
```

With **no** `projects.yaml`, Mnemos falls back to cumulative path segments
(`foo/bar/baz/file.go` → tags `[foo, foo/bar, foo/bar/baz]`), so you get a
usable hierarchy out of the box.

### Searching across tags

```bash
mnemos search "auth"  --tags acme,moby           # OR  (chunks tagged acme OR moby)
mnemos search "auth"  --tags-all acme,vue3       # AND (chunks tagged acme AND vue3)
```

From an MCP-connected agent:

```python
mnemos_search_code(query="auth", tags_any=["acme-front-app-ecommerce", "acme-front-lib-auth"])
mnemos_search_code(query="auth", tags_all=["acme", "vue3"])
```
```

Insert it where the current single-project filter is documented (search for
"`--project`" or "project filter"). Update any code snippet that still uses
`--project` to use `--tags` (mention `--project` is kept for back-compat).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): document tags-based project filtering"
```

---

## Task 14: Update `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Replace the "Memory entries" + "project payload" sections**

Find the lines mentioning `project` payload (around the "Collections Qdrant" table and the "Memory entries" section). Replace with:

```markdown
### Collections Qdrant

| Collection     | Source              | Path prefixes  | Multi-tenant scoping        |
|----------------|---------------------|----------------|-----------------------------|
| `mnemos_skills`| `~/.claude/skills/` | `skills/`      | —                           |
| `mnemos_docs`  | `~/.claude/docs/`   | `docs/`        | —                           |
| `mnemos_memory`| API / git hooks     | _(aucun)_      | via `project` + `tags` payload |
| `mnemos_code`  | codebase            | _(tout le reste)_ | via `tags` payload (OR/AND filter) |

Une seule collection `mnemos_code` héberge **tous** les projets, chaque chunk
portant deux champs scoping :

- `project: str` — tag primaire (= `tags[0]`), conservé pour l'affichage et
  la rétro-compat.
- `tags: list[str]` — liste complète (project + labels transverses),
  filtrée à la search avec `tags_any` (OR) ou `tags_all` (AND).

La résolution est portée par `config/projects.yaml`
(template : `config/projects.example.yaml`). Le schéma legacy `projects:` est
auto-converti avec un warning au démarrage.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): document tags payload and updated projects.yaml schema"
```

---

## Task 15: Update `docs/CONFIGURATION.md` and `docs/ARCHITECTURE.md`

**Files:**
- Modify: `docs/CONFIGURATION.md`
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: Update `docs/CONFIGURATION.md`**

Find the `projects.yaml` section. Replace with a complete description of the
new `paths:` schema, the auto-converted legacy schema, the default fallback
behaviour, and 3 worked examples. Reuse the prose from Task 12's
`projects.example.yaml` but expand each example with surrounding context (why
you'd want it, what queries it enables).

Include a `### Migration from the legacy schema` subsection showing the
before/after:

```yaml
# Before
projects:
  billing:
    path_prefixes: [myorg/services/billing/]

# After
paths:
  myorg/services/billing/:
    - billing
```

- [ ] **Step 2: Update `docs/ARCHITECTURE.md`**

Find the payload-schema section (search for `project:`). Add `tags: list[str]`
next to it and explain the duality:

```markdown
### Payload schema (`mnemos_code` chunks)

| Field            | Type        | Source              | Purpose                  |
|------------------|-------------|---------------------|--------------------------|
| `content`        | string      | chunker             | Embedded text            |
| `file_path`      | string      | indexer             | Source location          |
| `chunk_type`     | string      | chunker             | function, type, …        |
| `language`       | string      | chunker             | go, vue, md, …           |
| `project`        | string      | `detect_tags()[0]`  | Legacy single-tag filter |
| `tags`           | list[str]   | `detect_tags()`     | Multi-value scoping (OR/AND) |
| `last_indexed_at`| ISO date    | indexer             | Freshness                |
| `file_mtime`     | float       | indexer             | Diff with FS             |

The `tags` array is keyword-indexed in Qdrant for O(log n) filtering. `project`
remains the single-value back-compat field; agents and CLI should prefer
`tags_any` / `tags_all` going forward.
```

- [ ] **Step 3: Commit**

```bash
git add docs/CONFIGURATION.md docs/ARCHITECTURE.md
git commit -m "docs: update configuration + architecture for tags-based scoping"
```

---

## Task 16: Mark feature done in `docs/ROADMAP.md`

**Files:**
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Open the roadmap and add the entry**

If the roadmap has a "Multi-project scoping" line, mark it ✅. Otherwise, add
a one-line entry in the most relevant section:

```markdown
- **Tags-based scoping (replaces single `project` filter)** — ✅ shipped 2026-05-18.
  Chunks now carry `tags: list[str]`; search filters with `tags_any` (OR) and
  `tags_all` (AND). Legacy `--project` kept for back-compat. See
  [CONFIGURATION.md](CONFIGURATION.md#projectsyaml).
```

- [ ] **Step 2: Commit**

```bash
git add docs/ROADMAP.md
git commit -m "docs(roadmap): mark tags-based scoping shipped"
```

---

## Task 17: End-to-end smoke test

**Files:** _(no edits; verification only)_

- [ ] **Step 1: Run the full test suite**

```bash
make test
```

Expected: all PASS.

- [ ] **Step 2: Rebuild and restart the local stack**

```bash
make rebuild-dev
sleep 8
make status
```

Expected: server healthy, all 4 collections present.

- [ ] **Step 3: Reindex one project to populate the new payload**

```bash
source venv/bin/activate
mnemos reindex --collection mnemos_code \
  --path /data/codebase/Projects/digital-gigafactory/mnemos \
  --tags mnemos,dgf,python --full
```

- [ ] **Step 4: Verify the payload via Qdrant**

```bash
docker compose exec qdrant curl -s 'http://localhost:6333/collections/mnemos_code/points/scroll' \
  -H 'content-type: application/json' \
  -d '{"limit":1,"with_payload":true}' | jq '.result.points[0].payload | {project, tags}'
```

Expected output:

```json
{
  "project": "mnemos",
  "tags": ["mnemos", "dgf", "python"]
}
```

- [ ] **Step 5: Query with `tags_any` and `tags_all`**

```bash
mnemos search "memory pipeline"  --collection mnemos_code  --tags mnemos
mnemos search "memory pipeline"  --collection mnemos_code  --tags dgf,python
mnemos search "memory pipeline"  --collection mnemos_code  --tags-all dgf,python
mnemos search "memory pipeline"  --collection mnemos_code  --project mnemos    # back-compat
```

Expected: all 4 return results; the first 3 should agree, and the back-compat
form (`--project`) returns a subset (only the primary tag matches).

- [ ] **Step 6: Final commit if anything moved**

```bash
git status
# If nothing moved, no commit needed; the smoke test was verification-only.
```

---

## Self-review summary

- **Spec coverage**: every item the user asked for (multi-project filter, prefix-like via shared tags, projects.yaml schema change, projects.example.yaml, README, CLAUDE.md, ARCHITECTURE.md, ROADMAP.md) has a dedicated task.
- **Placeholder scan**: no "TBD" or "implement later". Every code block is concrete.
- **Type consistency**: `tags_any` / `tags_all` are the names used end-to-end (CLI flag `--tags` maps to API `tags_any` for the natural-language default of OR). `tags` (no `_any` / `_all`) is the indexer / payload field name.
- **Back-compat**: the legacy `project: str` field stays in the payload, the legacy `projects:` YAML keeps loading with a warning, the legacy `--project` flag keeps working.
- **No reindex required**: dual-write means old chunks (with only `project`) remain searchable via the legacy filter; reindex is recommended but not blocking.
