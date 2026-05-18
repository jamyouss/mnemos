from __future__ import annotations

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
