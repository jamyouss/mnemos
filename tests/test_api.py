from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from server.main import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_qdrant():
    mock_client = MagicMock()
    mock_client.get_collections.return_value.collections = []
    mock_client.scroll.return_value = ([], None)
    mock_client.search.return_value = []
    return mock_client


def _make_mock_embeddings():
    mock_embeddings = MagicMock()
    mock_embeddings.embed.return_value = [0.1] * 384
    mock_embeddings.embed_batch.return_value = [[0.1] * 384]
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
