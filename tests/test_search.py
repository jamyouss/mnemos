from unittest.mock import MagicMock

import pytest
from qdrant_client.models import MatchAny

from server.search import SearchService
from core.models import SearchResult, CodeSearchResult, SkillResult, MemoryResult


@pytest.fixture
def mock_qdrant():
    client = MagicMock()
    return client


@pytest.fixture
def mock_embeddings():
    service = MagicMock()
    service.embed.return_value = [0.1] * 384
    return service


@pytest.fixture
def search_service(mock_qdrant, mock_embeddings):
    return SearchService(qdrant_client=mock_qdrant, embedding_service=mock_embeddings)


def _mock_query_points(mock_qdrant, hits):
    """Helper to set up query_points mock with given hits."""
    result = MagicMock()
    result.points = hits
    mock_qdrant.query_points.return_value = result


def _last_query_filter(mock_qdrant):
    """Pull the Filter used by the most recent query_points call.

    Under the hybrid schema, the filter lives on each Prefetch leg
    (dense + sparse) rather than at the top level. We assert both legs
    carry the same filter and return it.
    """
    call_args = mock_qdrant.query_points.call_args
    prefetch = call_args.kwargs.get("prefetch") or []
    if not prefetch:
        # Legacy dense-only path
        return call_args.kwargs.get("query_filter")
    filters = [p.filter for p in prefetch]
    # All legs share the same filter
    assert all(f == filters[0] for f in filters)
    return filters[0]


def test_search_returns_search_results(search_service, mock_qdrant):
    _mock_query_points(mock_qdrant, [
        MagicMock(
            score=0.87,
            payload={
                "content": "func Create()",
                "file_path": "myproject/services/core/app.go",
                "chunk_type": "function",
                "language": "go",
                "tags": ["myproject"],
            },
        )
    ])
    results = search_service.search(
        query="company creation",
        collections=["mnemos_code"],
        limit=5,
    )
    assert len(results) == 1
    assert isinstance(results[0], SearchResult)
    assert results[0].score == 0.87


def _big_content(lines: int = 80) -> str:
    """A chunk content long enough to overflow preview budgets."""
    return "\n".join(f"line_{i}_with_some_filler_text_for_chars" for i in range(lines))


def test_search_preview_mode_truncates_long_content(search_service, mock_qdrant):
    big = _big_content(80)
    _mock_query_points(mock_qdrant, [
        MagicMock(
            score=0.9,
            payload={
                "content": big,
                "file_path": "x.go",
                "chunk_type": "function",
                "language": "go",
                "symbol_name": "Big",
            },
        )
    ])
    results = search_service.search(
        query="anything", collections=["mnemos_code"], limit=5, mode="preview"
    )
    assert len(results) == 1
    out = results[0].content
    assert len(out) < len(big), "preview must shrink the content"
    assert "[truncated" in out, "preview must signal truncation inside content"
    assert results[0].metadata.get("truncated") is True


def test_search_full_mode_returns_full_content(search_service, mock_qdrant):
    big = _big_content(80)
    _mock_query_points(mock_qdrant, [
        MagicMock(
            score=0.9,
            payload={
                "content": big,
                "file_path": "x.go",
                "chunk_type": "function",
            },
        )
    ])
    results = search_service.search(
        query="anything", collections=["mnemos_code"], limit=5, mode="full"
    )
    assert len(results) == 1
    assert results[0].content == big
    assert "truncated" not in results[0].metadata


def test_search_preview_mode_leaves_short_content_intact(search_service, mock_qdrant):
    """Chunks already under the budget must not be touched (no marker appended)."""
    short = "func Tiny() {}"
    _mock_query_points(mock_qdrant, [
        MagicMock(
            score=0.9,
            payload={"content": short, "file_path": "x.go", "chunk_type": "function"},
        )
    ])
    results = search_service.search(
        query="anything", collections=["mnemos_code"], limit=5, mode="preview"
    )
    assert results[0].content == short
    assert "truncated" not in results[0].metadata


def test_search_code_respects_mode(search_service, mock_qdrant):
    big = _big_content(80)
    _mock_query_points(mock_qdrant, [
        MagicMock(
            score=0.9,
            payload={
                "content": big,
                "file_path": "x.go",
                "chunk_type": "function",
                "language": "go",
                "symbol_name": "Big",
                "tags": ["proj"],
            },
        )
    ])
    preview = search_service.search_code(query="x", limit=3, mode="preview")
    full = search_service.search_code(query="x", limit=3, mode="full")
    assert preview[0].content != big
    assert full[0].content == big


def test_search_metadata_drops_bookkeeping_fields(search_service, mock_qdrant):
    """SearchResult.metadata should not leak indexer bookkeeping fields like
    last_indexed_at / file_mtime / chunk_index — they bloat LLM responses
    for zero retrieval value."""
    _mock_query_points(mock_qdrant, [
        MagicMock(
            score=0.7,
            payload={
                "content": "func F()",
                "file_path": "x.go",
                "chunk_type": "function",
                "language": "go",
                "symbol_name": "F",
                "tags": ["proj"],
                # Bookkeeping fields that MUST be filtered out:
                "last_indexed_at": "2026-05-19T00:00:00Z",
                "file_mtime": 1716000000.0,
                "chunk_index": 3,
            },
        )
    ])
    results = search_service.search(
        query="anything", collections=["mnemos_code"], limit=5
    )
    assert len(results) == 1
    meta = results[0].metadata
    assert "last_indexed_at" not in meta
    assert "file_mtime" not in meta
    assert "chunk_index" not in meta
    # And the useful ones survive
    assert meta.get("symbol_name") == "F"
    assert meta.get("language") == "go"
    assert meta.get("tags") == ["proj"]


def test_search_code_returns_code_results(search_service, mock_qdrant):
    _mock_query_points(mock_qdrant, [
        MagicMock(
            score=0.9,
            payload={
                "content": "func Create()",
                "file_path": "myproject/services/core/app.go",
                "chunk_type": "function",
                "language": "go",
                "symbol_name": "Create",
                "package": "application",
                "tags": ["myproject", "myproject/services"],
            },
        )
    ])
    results = search_service.search_code(
        query="company creation",
        language="go",
        tags_any=["myproject"],
        limit=5,
    )
    assert len(results) == 1
    assert isinstance(results[0], CodeSearchResult)
    assert results[0].symbol_name == "Create"
    # metadata carries tags (not the legacy `project` field)
    assert results[0].metadata.get("tags") == ["myproject", "myproject/services"]
    assert "project" not in results[0].metadata


def test_search_skills(search_service, mock_qdrant):
    _mock_query_points(mock_qdrant, [
        MagicMock(
            score=0.92,
            payload={
                "content": "Expert on Moby project...",
                "skill_name": "project-expert",
                "description": "Expert on Moby project",
                "chunk_type": "skill",
            },
        )
    ])
    results = search_service.search_skills(query="myproject service", limit=3)
    assert len(results) == 1
    assert isinstance(results[0], SkillResult)
    assert results[0].skill_name == "project-expert"


def test_search_memory(search_service, mock_qdrant):
    _mock_query_points(mock_qdrant, [
        MagicMock(
            score=0.85,
            payload={
                "content": "Fixed NATS reconnection",
                "id": "mem_123",
                "project": "myproject",
                "topic": "nats",
                "memory_type": "bug-fix",
                "tags": ["nats"],
                "status": "approved",
                "created_at": "2026-03-12T14:30:00Z",
            },
        )
    ])
    results = search_service.search_memory(
        query="NATS issue", tags_any=["myproject"], limit=5
    )
    assert len(results) == 1
    assert isinstance(results[0], MemoryResult)
    assert results[0].memory_type == "bug-fix"


def test_search_memory_filters_approved(search_service, mock_qdrant):
    """search_memory should only return approved entries by default.

    Under the hybrid schema the filter is applied to each Prefetch leg
    (dense + sparse) rather than at the top level — verify it's present there.
    """
    _mock_query_points(mock_qdrant, [])
    search_service.search_memory(query="test", limit=5)
    call_args = mock_qdrant.query_points.call_args
    prefetch = call_args.kwargs.get("prefetch") or []
    assert prefetch, "expected hybrid prefetch with dense+sparse legs"
    # Both legs must carry the approval filter
    assert all(p.filter is not None for p in prefetch)


# ---------------------------------------------------------------------------
# Tag-based filtering (tags_any / tags_all)
# ---------------------------------------------------------------------------


def _tag_match_anys(query_filter):
    """Return the list[str] payload of every MatchAny condition on key='tags'."""
    if query_filter is None:
        return []
    out: list[list[str]] = []
    for cond in (query_filter.must or []):
        if getattr(cond, "key", None) != "tags":
            continue
        match = getattr(cond, "match", None)
        if isinstance(match, MatchAny):
            out.append(list(match.any))
    return out


def test_search_without_tag_filter_has_no_tags_condition(search_service, mock_qdrant):
    _mock_query_points(mock_qdrant, [])
    search_service.search(query="foo", collections=["mnemos_code"], limit=5)
    qf = _last_query_filter(mock_qdrant)
    # No filter at all OR a filter with no `tags` condition is acceptable.
    if qf is not None:
        assert _tag_match_anys(qf) == []


def test_search_tags_any_emits_single_match_any_on_tags(search_service, mock_qdrant):
    _mock_query_points(mock_qdrant, [])
    search_service.search(
        query="foo",
        collections=["mnemos_code"],
        tags_any=["proj-a", "proj-b"],
        limit=5,
    )
    qf = _last_query_filter(mock_qdrant)
    assert qf is not None
    matches = _tag_match_anys(qf)
    assert len(matches) == 1
    assert set(matches[0]) == {"proj-a", "proj-b"}


def test_search_tags_all_emits_one_match_any_per_tag(search_service, mock_qdrant):
    _mock_query_points(mock_qdrant, [])
    search_service.search(
        query="foo",
        collections=["mnemos_code"],
        tags_all=["proj-a", "team-x"],
        limit=5,
    )
    qf = _last_query_filter(mock_qdrant)
    assert qf is not None
    matches = _tag_match_anys(qf)
    # AND semantics → one condition per tag, each a singleton MatchAny.
    assert len(matches) == 2
    assert ["proj-a"] in matches
    assert ["team-x"] in matches


def test_search_code_tags_any_filter(search_service, mock_qdrant):
    _mock_query_points(mock_qdrant, [])
    search_service.search_code(
        query="foo",
        tags_any=["myproject"],
        limit=5,
    )
    qf = _last_query_filter(mock_qdrant)
    assert qf is not None
    matches = _tag_match_anys(qf)
    assert matches == [["myproject"]]


def test_search_code_tags_all_filter(search_service, mock_qdrant):
    _mock_query_points(mock_qdrant, [])
    search_service.search_code(
        query="foo",
        tags_all=["myproject", "myproject/services"],
        limit=5,
    )
    qf = _last_query_filter(mock_qdrant)
    assert qf is not None
    matches = _tag_match_anys(qf)
    assert len(matches) == 2
    assert ["myproject"] in matches
    assert ["myproject/services"] in matches


def test_search_memory_tags_any_combined_with_approved(search_service, mock_qdrant):
    _mock_query_points(mock_qdrant, [])
    search_service.search_memory(
        query="foo",
        tags_any=["myproject"],
        limit=5,
    )
    qf = _last_query_filter(mock_qdrant)
    assert qf is not None
    matches = _tag_match_anys(qf)
    assert matches == [["myproject"]]
    # The approval filter must still be present alongside the tag filter.
    keys = [getattr(c, "key", None) for c in (qf.must or [])]
    assert "status" in keys


def test_search_cache_namespace_includes_tags(search_service, mock_qdrant):
    """Cached results for one tag scope must NOT be reused for another."""
    captured: dict[str, str] = {}

    cache = MagicMock()
    cache.enabled = True
    cache.lookup.return_value = None

    def _store(query, results, namespace):
        captured["ns"] = namespace
    cache.store.side_effect = _store

    search_service._cache = cache

    _mock_query_points(mock_qdrant, [
        MagicMock(
            score=0.9,
            payload={
                "content": "x",
                "file_path": "f.go",
                "chunk_type": "function",
                "tags": ["a"],
            },
        )
    ])
    search_service.search(
        query="q",
        collections=["mnemos_code"],
        tags_any=["a"],
        limit=5,
    )
    ns_with_a = captured["ns"]
    assert "tagsAny=a" in ns_with_a

    search_service.search(
        query="q",
        collections=["mnemos_code"],
        tags_any=["b"],
        limit=5,
    )
    ns_with_b = captured["ns"]
    assert "tagsAny=b" in ns_with_b
    assert ns_with_a != ns_with_b
