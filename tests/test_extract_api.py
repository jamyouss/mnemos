"""Tests for the memory extraction endpoint."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from core.models import ExtractedMemory, DeduplicationResult


@pytest.fixture
def app():
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
        from core.indexer import Indexer
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

        mock_extractor = MagicMock()
        mock_deduplicator = MagicMock()
        application.state.memory_extractor = mock_extractor
        application.state.deduplicator = mock_deduplicator

        yield application, mock_extractor, mock_deduplicator


@pytest.mark.anyio
async def test_extract_memories_from_commit(app):
    application, mock_extractor, mock_deduplicator = app
    mock_extractor.extract.return_value = [
        ExtractedMemory(content="Decided to use flat routes", memory_type="decision", project="myproject", tags=["routing"])
    ]
    mock_deduplicator.deduplicate_and_store.return_value = DeduplicationResult(action="inserted", memory_id="test-id-123")

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/memory/extract", json={"commit_message": "feat: flatten API routes", "diff": "diff --git a/routes.go ..."})
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
        response = await client.post("/api/memory/extract", json={"commit_message": "fix: typo", "diff": "- helo\n+ hello"})
    assert response.status_code == 200
    data = response.json()
    assert data["extracted"] == 0
    assert data["memories"] == []


@pytest.mark.anyio
async def test_llm_failure_is_not_reported_as_an_empty_result(app):
    """A 200 with `extracted: 0` is indistinguishable from "this diff held
    nothing worth keeping". That ambiguity hid an unreachable LLM endpoint for
    months: mnemos_memory stayed empty and every caller saw success."""
    from core.llm import LLMError

    application, mock_extractor, _ = app
    mock_extractor.extract.side_effect = LLMError("ollama request failed: name or service not known")

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/memory/extract",
            json={"commit_message": "feat: x", "diff": "diff --git a/x.go ..."},
        )

    assert response.status_code == 502
    assert "unavailable" in response.json()["detail"]
    assert "name or service not known" in response.json()["detail"]


@pytest.mark.anyio
async def test_an_empty_result_still_means_nothing_to_extract(app):
    """The other half of the distinction: a working LLM that found nothing
    must still be a plain 200."""
    application, mock_extractor, _ = app
    mock_extractor.extract.return_value = []

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/memory/extract",
            json={"commit_message": "chore: typo", "diff": "diff --git a/README.md ..."},
        )

    assert response.status_code == 200
    assert response.json()["extracted"] == 0
