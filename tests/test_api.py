from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from server.main import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_qdrant():
    mock_client = MagicMock()
    mock_client.collection_exists.return_value = False
    mock_client.scroll.return_value = ([], None)
    mock_client.search.return_value = []
    return mock_client


def _make_mock_embeddings():
    mock_embeddings = MagicMock()
    mock_embeddings.dimension = 384
    mock_embeddings.embed_query.return_value = [0.1] * 384
    mock_embeddings.embed_documents.side_effect = lambda texts, titles=None: [[0.1] * 384 for _ in texts]
    return mock_embeddings


@pytest.fixture
def app():
    """Create app with mocked Qdrant and EmbeddingService, state pre-populated."""
    with patch("server.main.QdrantClient") as mock_qdrant_cls, \
         patch("server.main.EmbeddingService") as mock_embed_cls:

        mock_qdrant = _make_mock_qdrant()
        mock_embeddings = _make_mock_embeddings()
        mock_qdrant_cls.return_value = mock_qdrant
        mock_embed_cls.return_value = mock_embeddings

        application = create_app()

        # Manually populate app.state so endpoints can use it
        # (lifespan doesn't run in ASGITransport tests)
        from core.indexer import Indexer
        from server.search import SearchService

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

        mock_deduplicator = MagicMock()
        from core.models import DeduplicationResult
        mock_deduplicator.deduplicate_and_store.return_value = DeduplicationResult(
            action="inserted", memory_id="mock-dedup-id"
        )
        application.state.deduplicator = mock_deduplicator

        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = []
        application.state.memory_extractor = mock_extractor

        yield application


# ---------------------------------------------------------------------------
# /api/status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_api_status(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "collections" in data


# ---------------------------------------------------------------------------
# /api/search
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_api_search(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/search", json={"query": "hello world"})
    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert isinstance(data["results"], list)


@pytest.mark.anyio
async def test_api_search_with_options(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/search",
            json={
                "query": "auth middleware",
                "collections": ["mnemos_code"],
                "file_types": ["go"],
                "path_filter": "/src/auth.go",
                "limit": 3,
            },
        )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# /api/search-code
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_api_search_code(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/search-code",
            json={"query": "func handler", "language": "go", "limit": 5},
        )
    assert response.status_code == 200
    data = response.json()
    assert "results" in data


@pytest.mark.anyio
async def test_search_code_forwards_tags_any(app):
    """POST /api/search-code with tags_any should forward the list to SearchService."""
    spy = MagicMock(return_value=[])
    app.state.search_service.search_code = spy

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/search-code",
            json={"query": "func handler", "tags_any": ["a", "b"]},
        )
    assert response.status_code == 200
    spy.assert_called_once()
    kwargs = spy.call_args.kwargs
    assert kwargs["tags_any"] == ["a", "b"]
    assert kwargs["tags_all"] is None


@pytest.mark.anyio
async def test_search_code_forwards_tags_all(app):
    """POST /api/search-code with tags_all should forward the list to SearchService."""
    spy = MagicMock(return_value=[])
    app.state.search_service.search_code = spy

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/search-code",
            json={"query": "func handler", "tags_all": ["x", "y"]},
        )
    assert response.status_code == 200
    spy.assert_called_once()
    kwargs = spy.call_args.kwargs
    assert kwargs["tags_all"] == ["x", "y"]
    assert kwargs["tags_any"] is None


@pytest.mark.anyio
async def test_search_forwards_tags_filters(app):
    """POST /api/search should forward both tags_any and tags_all to SearchService."""
    spy = MagicMock(return_value=[])
    app.state.search_service.search = spy

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/search",
            json={
                "query": "auth",
                "tags_any": ["go", "python"],
                "tags_all": ["service"],
            },
        )
    assert response.status_code == 200
    spy.assert_called_once()
    kwargs = spy.call_args.kwargs
    assert kwargs["tags_any"] == ["go", "python"]
    assert kwargs["tags_all"] == ["service"]


@pytest.mark.anyio
async def test_search_request_ignores_unknown_project_field(app):
    """The legacy `project` field has been removed. Pydantic's default config
    silently drops unknown fields, so the request still succeeds (200) but the
    search service is never called with a `project=` kwarg."""
    spy = MagicMock(return_value=[])
    app.state.search_service.search = spy

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/search",
            json={"query": "hello", "project": "legacy-project"},
        )
    assert response.status_code == 200
    spy.assert_called_once()
    kwargs = spy.call_args.kwargs
    assert "project" not in kwargs


@pytest.mark.anyio
async def test_search_returns_a_trace_only_when_asked(app):
    spy = MagicMock(return_value=[])
    app.state.search_service.search_code = spy

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        plain = (await client.post("/api/search-code", json={"query": "q"})).json()
        traced = (await client.post("/api/search-code", json={"query": "q", "trace": True})).json()

    assert "trace" not in plain
    assert set(traced["trace"]) == {"total_ms", "stages", "candidates"}
    assert spy.call_args_list[0].kwargs["trace"] is None
    assert spy.call_args_list[1].kwargs["trace"] is not None


@pytest.mark.anyio
async def test_search_source_is_a_closed_set(app):
    """Free text here would let any client write arbitrary labels into the log."""
    spy = MagicMock(return_value=[])
    app.state.search_service.search = spy

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        ok = await client.post("/api/search", json={"query": "q", "source": "playground", "use_cache": False})
        bad = await client.post("/api/search", json={"query": "q", "source": "anything"})

    assert ok.status_code == 200
    assert spy.call_args.kwargs["source"] == "playground"
    assert spy.call_args.kwargs["use_cache"] is False
    assert bad.status_code == 422


# ---------------------------------------------------------------------------
# /api/search-memory
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_memory_forwards_tags(app):
    """POST /api/search-memory should forward tags_any/tags_all and never pass project."""
    spy = MagicMock(return_value=[])
    app.state.search_service.search_memory = spy

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/search-memory",
            json={
                "query": "convention",
                "tags_any": ["auth"],
                "tags_all": ["service"],
            },
        )
    assert response.status_code == 200
    spy.assert_called_once()
    kwargs = spy.call_args.kwargs
    assert kwargs["tags_any"] == ["auth"]
    assert kwargs["tags_all"] == ["service"]
    assert "project" not in kwargs


@pytest.mark.anyio
async def test_search_memory_drops_project_from_response(app):
    """The response payload must not leak the legacy `project` field."""
    from core.models import MemoryResult

    spy = MagicMock(
        return_value=[
            MemoryResult(
                id="m1",
                content="never use /admin",
                project="legacy-proj",  # still on the model, must be omitted in response
                memory_type="convention",
                tags=["routing"],
                score=0.9,
                created_at="2026-05-18T00:00:00Z",
            )
        ]
    )
    app.state.search_service.search_memory = spy

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/search-memory", json={"query": "anything"}
        )
    assert response.status_code == 200
    results = response.json()["results"]
    assert results, "expected at least one result"
    for r in results:
        assert "project" not in r
        assert "tags" in r


# ---------------------------------------------------------------------------
# /api/search-skills
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_api_search_skills(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/search-skills", json={"query": "code review", "limit": 3}
        )
    assert response.status_code == 200
    data = response.json()
    assert "results" in data


# ---------------------------------------------------------------------------
# /api/index (push)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_api_push_index(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/index",
            json={
                "file_path": "/data/codebase/src/main.go",
                "collection": "mnemos_code",
                "content": "package main\n\nfunc main() {}\n",
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "indexed"


# ---------------------------------------------------------------------------
# DELETE /api/index/{collection}/{file_path}
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_api_delete_index(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete("/api/index/mnemos_code/src/main.go")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "deleted"
    assert data["collection"] == "mnemos_code"


@pytest.mark.anyio
@pytest.mark.parametrize("method, url, body", [
    ("POST", "/api/index", {"file_path": "/data/codebase/a.go", "collection": "mnemos_code", "content": "package a\n"}),
    ("DELETE", "/api/index/mnemos_code/src/main.go", None),
    ("POST", "/api/memory", {"content": "Use RRF", "tags": ["x"]}),
])
async def test_index_mutations_clear_the_semantic_cache(app, method, url, body):
    """A cached answer must not outlive the chunks it was built from."""
    app.state.cache = MagicMock(enabled=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(method, url, json=body)
    assert response.status_code == 200
    app.state.cache.invalidate.assert_called_once()


# ---------------------------------------------------------------------------
# /api/reindex
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_api_reindex(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/reindex",
            json={"collection": "mnemos_code", "full": False},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "no_path"
    assert data["collection"] == "mnemos_code"


# ---------------------------------------------------------------------------
# /api/memory
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_api_memory_list(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/memory")
    assert response.status_code == 200
    data = response.json()
    assert "entries" in data


@pytest.mark.anyio
async def test_api_memory_list_with_status(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/memory", params={"status": "pending"})
    assert response.status_code == 200
    data = response.json()
    assert "entries" in data


@pytest.mark.anyio
async def test_api_memory_create(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/memory",
            json={
                "content": "Never use /admin in API routes",
                "project": "myproject",
                "memory_type": "convention",
                "tags": ["routing", "api"],
                "status": "pending",
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "created"
    assert "id" in data
    assert "action" in data


@pytest.mark.anyio
async def test_api_memory_review_approve(app):
    fake_id = "test-mem-id-123"
    fake_record = MagicMock()
    fake_record.payload = {"id": fake_id, "content": "some memory", "status": "pending"}
    fake_record.vector = [0.1] * 384
    app.state.qdrant.scroll.return_value = ([fake_record], None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/memory/{fake_id}/review",
            json={"action": "approve"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "approved"
    assert data["id"] == fake_id


@pytest.mark.anyio
async def test_api_memory_review_reject(app):
    fake_id = "test-mem-id-456"
    fake_record = MagicMock()
    fake_record.payload = {"id": fake_id, "content": "some memory", "status": "pending"}
    fake_record.vector = [0.1] * 384
    app.state.qdrant.scroll.return_value = ([fake_record], None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/memory/{fake_id}/review",
            json={"action": "reject"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "rejected"


@pytest.mark.anyio
async def test_api_memory_review_invalid_action(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/memory/some-id/review",
            json={"action": "delete"},
        )
    assert response.status_code == 400


@pytest.mark.anyio
async def test_api_memory_review_not_found(app):
    app.state.qdrant.scroll.return_value = ([], None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/memory/nonexistent-id/review",
            json={"action": "approve"},
        )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# /internal/reindex — path traversal protection
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_internal_reindex_path_traversal_rejected(app):
    """Paths outside allowed base dirs must be rejected with 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/reindex",
            json={
                "file_path": "/etc/passwd",
                "event": "modified",
                "collection": "mnemos_code",
            },
        )
    assert response.status_code == 400
    assert "traversal" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_internal_reindex_deleted_event(app):
    """A 'deleted' event should call delete_file and not check file existence."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/internal/reindex",
            json={
                "file_path": "/data/codebase/src/main.go",
                "event": "deleted",
                "collection": "mnemos_code",
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "deleted"


# ---------------------------------------------------------------------------
# Observability endpoints (dashboard)
# ---------------------------------------------------------------------------


def _write_log(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


LOG_ROWS = [
    {"ts": 1, "intent": "search", "query": "a", "n_results": 3, "latency_ms": 10},
    {"ts": 2, "intent": "search_code", "query": "b", "n_results": 5, "latency_ms": 20},
    {"ts": 3, "intent": "search_code", "query": "c", "n_results": 1, "latency_ms": 30},
]


@pytest.mark.anyio
async def test_query_log_returns_entries_oldest_last(app, tmp_path, monkeypatch):
    from server.api import settings

    log = tmp_path / "query-log.jsonl"
    _write_log(log, LOG_ROWS)
    monkeypatch.setattr(settings, "mnemos_query_log_path", str(log))
    monkeypatch.setattr(settings, "mnemos_query_log_enabled", True)

    application = app[0] if isinstance(app, tuple) else app
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/query-log")

    body = resp.json()
    assert resp.status_code == 200
    assert body["enabled"] is True
    assert [e["query"] for e in body["entries"]] == ["a", "b", "c"]


@pytest.mark.anyio
async def test_query_log_distinguishes_off_from_empty(app, tmp_path, monkeypatch):
    """An empty dashboard must be able to say whether nothing was searched or
    nothing is being recorded. Conflating the two hid a logger that had been
    off for a week."""
    from server.api import settings

    monkeypatch.setattr(settings, "mnemos_query_log_path", str(tmp_path / "absent.jsonl"))
    monkeypatch.setattr(settings, "mnemos_query_log_enabled", False)

    application = app[0] if isinstance(app, tuple) else app
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/query-log")

    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "count": 0, "entries": []}


@pytest.mark.anyio
async def test_query_log_filters_by_intent_and_limit(app, tmp_path, monkeypatch):
    from server.api import settings

    log = tmp_path / "query-log.jsonl"
    _write_log(log, LOG_ROWS)
    monkeypatch.setattr(settings, "mnemos_query_log_path", str(log))
    monkeypatch.setattr(settings, "mnemos_query_log_enabled", True)

    application = app[0] if isinstance(app, tuple) else app
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        by_intent = (await client.get("/api/query-log", params={"intent": "search_code"})).json()
        limited = (await client.get("/api/query-log", params={"limit": 1})).json()

    assert [e["query"] for e in by_intent["entries"]] == ["b", "c"]
    assert [e["query"] for e in limited["entries"]] == ["c"]


@pytest.mark.anyio
async def test_query_log_survives_a_torn_line(app, tmp_path, monkeypatch):
    """The server appends while the dashboard reads; a half-written last line
    is normal and must not fail the request."""
    from server.api import settings

    log = tmp_path / "query-log.jsonl"
    log.write_text(json.dumps(LOG_ROWS[0]) + '\n{"ts": 2, "intent": "sea', encoding="utf-8")
    monkeypatch.setattr(settings, "mnemos_query_log_path", str(log))
    monkeypatch.setattr(settings, "mnemos_query_log_enabled", True)

    application = app[0] if isinstance(app, tuple) else app
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/query-log")

    assert resp.status_code == 200
    assert [e["query"] for e in resp.json()["entries"]] == ["a"]


@pytest.fixture
def eval_runs_dir(tmp_path, monkeypatch):
    """Two runs on disk. Pointed at a tmp dir so the suite does not depend on
    whatever the developer happens to have evaluated."""
    from server.api import settings

    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "a.json").write_text(json.dumps({
        "tag": "run-a", "created_at": "2026-01-01T00:00:00+00:00",
        "report": {"n_questions": 3, "overall": {"mrr": 0.5}},
        "results": [{"query": "q", "retrieved_files": ["/data/codebase/x/main.go"]}],
    }), encoding="utf-8")
    (runs / "b.json").write_text(json.dumps({
        "tag": "run-b", "created_at": "2026-02-01T00:00:00+00:00",
        "report": {"n_questions": 4, "overall": {"mrr": 0.7}},
        "results": [],
    }), encoding="utf-8")
    (runs / "broken.json").write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(settings, "mnemos_eval_runs_path", str(runs))
    return runs


@pytest.mark.anyio
async def test_eval_runs_lists_summaries_newest_first(app, eval_runs_dir):
    """Per-question results carry indexed file paths, so the listing stays a
    summary and only an explicit tag returns the detail."""
    application = app[0] if isinstance(app, tuple) else app
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/eval/runs")

    runs = resp.json()["runs"]
    assert resp.status_code == 200
    assert [r["tag"] for r in runs] == ["run-b", "run-a"]
    for run in runs:
        assert set(run) == {"tag", "created_at", "report"}
        assert "results" not in run


@pytest.mark.anyio
async def test_a_malformed_run_does_not_break_the_listing(app, eval_runs_dir):
    """A half-written or hand-edited run file must not take the page down."""
    application = app[0] if isinstance(app, tuple) else app
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/eval/runs")

    assert resp.status_code == 200
    assert len(resp.json()["runs"]) == 2


@pytest.mark.anyio
async def test_eval_runs_returns_the_detail_for_a_tag(app, eval_runs_dir):
    application = app[0] if isinstance(app, tuple) else app
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/eval/runs", params={"tag": "run-a"})

    body = resp.json()
    assert resp.status_code == 200
    assert body["tag"] == "run-a"
    assert body["results"], "the detail view is where per-question data lives"


@pytest.mark.anyio
async def test_eval_runs_unknown_tag_is_a_404(app, eval_runs_dir):
    application = app[0] if isinstance(app, tuple) else app
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/eval/runs", params={"tag": "does-not-exist"})

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# External sources: push metadata + file listing
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_api_push_index_forwards_source_metadata(app):
    app.state.indexer = MagicMock()
    app.state.indexer.index_file.return_value = 1
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/index",
            json={
                "file_path": "jira/OPS/OPS-12.md",
                "collection": "mnemos_knowledge",
                "content": "# OPS-12\n",
                "tags": ["jira", "jira:OPS"],
                "source_url": "https://tracker.example.com/browse/OPS-12",
                "file_mtime": 1700000000.0,
            },
        )
    assert response.status_code == 200
    kwargs = app.state.indexer.index_file.call_args.kwargs
    assert kwargs["source_url"] == "https://tracker.example.com/browse/OPS-12"
    assert kwargs["file_mtime"] == 1700000000.0


@pytest.mark.anyio
async def test_api_list_files_by_tag(app):
    app.state.indexer = MagicMock()
    app.state.indexer.list_files.return_value = {"jira/OPS/OPS-12.md": 1700000000.0}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/index/mnemos_knowledge/files", params={"tag": "jira:OPS"}
        )
    assert response.status_code == 200
    assert response.json()["files"] == {"jira/OPS/OPS-12.md": 1700000000.0}
    app.state.indexer.list_files.assert_called_once_with("mnemos_knowledge", tag="jira:OPS")


@pytest.mark.anyio
async def test_api_list_files_rejects_an_unknown_collection(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/index/nope/files", params={"tag": "x"})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_slow_extraction_does_not_block_search(app):
    """A git hook's LLM extraction must not freeze agent searches.

    Both calls meet at a barrier: it only opens if they run at the same time.
    A handler blocking the event loop would hold the barrier alone until it
    times out.
    """
    barrier = threading.Barrier(2, timeout=5)

    def slow_extract(**_):
        barrier.wait()
        return []

    def slow_search(**_):
        barrier.wait()
        return []

    app.state.memory_extractor.extract.side_effect = slow_extract
    app.state.search_service = MagicMock()
    app.state.search_service.search.side_effect = slow_search

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        extract_resp, search_resp = await asyncio.gather(
            client.post("/api/memory/extract", json={"commit_message": "feat: x", "diff": "d"}),
            client.post("/api/search", json={"query": "auth"}),
        )

    assert extract_resp.status_code == 200
    assert search_resp.status_code == 200


# ---------------------------------------------------------------------------
# Dashboard: memory stats, pagination, facets, dependency health
# ---------------------------------------------------------------------------


def _paged_scroll(payloads: list[dict]):
    """A Qdrant scroll over `payloads`, honouring `limit` and `offset`."""
    calls = []

    def scroll(collection_name, scroll_filter=None, limit=10, offset=None,
               with_payload=True, with_vectors=False):
        calls.append({"limit": limit, "offset": offset, "with_payload": with_payload,
                      "with_vectors": with_vectors})
        start = offset or 0
        page = payloads[start:start + limit]
        nxt = start + limit if start + limit < len(payloads) else None
        return [MagicMock(payload=p) for p in page], nxt

    return scroll, calls


def _memories(n: int) -> list[dict]:
    types = ["decision", "pattern", "lesson"]
    return [
        {
            "id": f"m{i}",
            "status": "approved" if i % 4 else "pending",
            "memory_type": types[i % 3],
            "created_at": f"2026-11-{1 + i % 3:02d}T23:30:00+00:00",
        }
        for i in range(n)
    ]


@pytest.mark.anyio
async def test_memory_stats_aggregates_every_page(app):
    payloads = _memories(1203)
    payloads.append({"id": "x", "status": "rejected", "memory_type": "note",
                     "created_at": "2026-11-05T01:00:00+02:00"})   # 2026-11-04 in UTC
    payloads.append({"id": "y"})                                   # legacy, no fields
    scroll, calls = _paged_scroll(payloads)
    app.state.qdrant.scroll.side_effect = scroll

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get("/api/memory/stats")).json()

    assert body["total"] == 1205
    assert body["by_status"] == {"pending": 301, "approved": 902, "rejected": 1, "unknown": 1}
    assert body["by_type"] == {"decision": 401, "pattern": 401, "lesson": 401, "note": 1, "unknown": 1}
    assert body["by_day"] == {"2026-11-01": 401, "2026-11-02": 401, "2026-11-03": 401, "2026-11-04": 1}
    assert len(calls) > 1
    assert all(c["with_vectors"] is False for c in calls)
    assert all(c["with_payload"] == ["status", "memory_type", "created_at"] for c in calls)


def _dated(n: int) -> list[dict]:
    """`n` memories created one minute apart, stored oldest first."""
    return [
        {
            "id": f"m{i:04d}",
            "status": "approved",
            "memory_type": "lesson",
            "content": f"memory number {i}",
            "tags": ["mnemos"],
            "created_at": f"2026-11-01T{i // 60:02d}:{i % 60:02d}:00+00:00",
        }
        for i in range(n)
    ]


@pytest.mark.anyio
async def test_memory_pages_cover_every_match_newest_first(app):
    scroll, _ = _paged_scroll(_dated(1203))
    app.state.qdrant.scroll.side_effect = scroll

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        pages = [
            (await client.get("/api/memory", params={"offset": o, "limit": 500})).json()
            for o in (0, 500, 1000)
        ]
        too_big = await client.get("/api/memory", params={"limit": 10001})

    # Past the old 1,000 default nothing is lost, and the pages do not overlap.
    ids = [e["id"] for page in pages for e in page["entries"]]
    assert len(ids) == len(set(ids)) == 1203
    assert all(page["total"] == 1203 for page in pages)
    # Newest first across pages, not within each Qdrant batch.
    assert ids[0] == "m1202" and ids[-1] == "m0000"
    assert too_big.status_code == 422


@pytest.mark.anyio
async def test_memory_sort_compares_instants_not_strings(app):
    payloads = [
        {"id": "utc", "created_at": "2026-11-01T10:00:00+00:00"},
        # 09:30 in Paris is 08:30 UTC: older, although it sorts later as a string.
        {"id": "paris", "created_at": "2026-11-01T09:30:00+01:00"},
        {"id": "legacy"},
    ]
    scroll, _ = _paged_scroll(payloads)
    app.state.qdrant.scroll.side_effect = scroll

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get("/api/memory")).json()

    assert [e["id"] for e in body["entries"]] == ["utc", "paris", "legacy"]


@pytest.mark.anyio
async def test_memory_text_filter_counts_matches_not_the_page(app):
    payloads = _dated(120)
    payloads[7]["tags"] = ["Auth"]
    scroll, _ = _paged_scroll(payloads)
    app.state.qdrant.scroll.side_effect = scroll

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        by_content = (await client.get("/api/memory", params={"q": "NUMBER 11", "limit": 5})).json()
        by_tag = (await client.get("/api/memory", params={"q": "auth"})).json()

    # "number 11" also matches 110-119: the total counts all of them.
    assert by_content["total"] == 11
    assert len(by_content["entries"]) == 5
    assert [e["id"] for e in by_tag["entries"]] == ["m0007"]


@pytest.mark.anyio
async def test_memory_list_keeps_status_and_type_filters_on_every_page(app):
    filters = []

    def scroll(collection_name, scroll_filter=None, limit=10, offset=None, **_):
        filters.append(scroll_filter)
        return ([MagicMock(payload={"id": "a"})], 1) if offset is None else ([], None)

    app.state.qdrant.scroll.side_effect = scroll
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/api/memory", params={"status": "pending", "type": "lesson"})

    assert len(filters) == 2
    for f in filters:
        assert {(c.key, c.match.value) for c in f.must} == {("status", "pending"), ("memory_type", "lesson")}


@pytest.mark.anyio
async def test_facets_counts_a_payload_field(app):
    from qdrant_client.models import FacetResponse, FacetValueHit

    app.state.qdrant.facet.return_value = FacetResponse(hits=[
        FacetValueHit(value="acme", count=42), FacetValueHit(value="webshop", count=7),
    ])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/index/facets",
                                params={"collection": "mnemos_code", "field": "tags", "limit": 5})

    assert resp.status_code == 200
    assert resp.json() == {
        "collection": "mnemos_code",
        "field": "tags",
        "counts": [{"value": "acme", "count": 42}, {"value": "webshop", "count": 7}],
    }
    app.state.qdrant.facet.assert_called_once_with(
        collection_name="mnemos_code", key="tags", limit=5, facet_filter=None
    )


@pytest.mark.anyio
async def test_facets_rejects_unknown_collection_and_fields(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unknown = await client.get("/api/index/facets", params={"collection": "nope", "field": "tags"})
        not_allowed = await client.get("/api/index/facets",
                                       params={"collection": "mnemos_code", "field": "file_path"})
        not_indexed = await client.get("/api/index/facets",
                                       params={"collection": "mnemos_docs", "field": "tags"})

    assert unknown.status_code == 404
    assert not_allowed.status_code == 400
    assert not_indexed.status_code == 400
    app.state.qdrant.facet.assert_not_called()


@pytest.mark.anyio
async def test_health_deps_reports_both_dependencies(app):
    llm = MagicMock()
    llm.name, llm.model = "ollama", "qwen2.5:7b"
    app.state.llm = llm

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get("/api/health/deps")).json()
        again = (await client.get("/api/health/deps")).json()

    assert body["qdrant"]["ok"] is True and body["qdrant"]["error"] is None
    assert isinstance(body["qdrant"]["latency_ms"], float)
    llm_body = body["llm"]
    assert llm_body["ok"] is True
    assert (llm_body["provider"], llm_body["model"]) == ("ollama", "qwen2.5:7b")
    assert llm_body["error"] is None and llm_body["checked_at"]
    llm.ping.assert_called_once_with(timeout=3.0)  # second call hit the 30 s cache
    assert again["llm"] == llm_body


@pytest.mark.anyio
async def test_health_deps_failure_never_leaks_the_key(app, monkeypatch):
    from core.llm import LLMError
    from server.api import settings

    secret = "sk-SENTINEL-health-9999"
    monkeypatch.setattr(settings, "mnemos_llm_api_key", secret)
    llm = MagicMock()
    llm.name, llm.model = "openai", "gpt-4o-mini"
    llm.ping.side_effect = LLMError(f"auth failed for {secret}")
    app.state.llm = llm
    app.state.qdrant.get_collections.side_effect = ConnectionError("qdrant down")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health/deps")

    body = resp.json()
    assert resp.status_code == 200
    assert body["qdrant"]["ok"] is False and "qdrant down" in body["qdrant"]["error"]
    assert body["llm"]["ok"] is False and body["llm"]["error"]
    assert isinstance(body["llm"]["latency_ms"], float)
    assert "SENTINEL" not in resp.text
