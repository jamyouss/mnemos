from unittest.mock import MagicMock

import pytest

from server.search import SearchService
from rag_core.models import SearchResult, CodeSearchResult, SkillResult, MemoryResult


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


def test_search_returns_search_results(search_service, mock_qdrant):
    _mock_query_points(mock_qdrant, [
        MagicMock(
            score=0.87,
            payload={
                "content": "func Create()",
                "file_path": "moby/services/core/app.go",
                "chunk_type": "function",
                "language": "go",
            },
        )
    ])
    results = search_service.search(
        query="company creation",
        collections=["mnemos_code_moby"],
        limit=5,
    )
    assert len(results) == 1
    assert isinstance(results[0], SearchResult)
    assert results[0].score == 0.87


def test_search_code_returns_code_results(search_service, mock_qdrant):
    _mock_query_points(mock_qdrant, [
        MagicMock(
            score=0.9,
            payload={
                "content": "func Create()",
                "file_path": "moby/services/core/app.go",
                "chunk_type": "function",
                "language": "go",
                "symbol_name": "Create",
                "package": "application",
            },
        )
    ])
    results = search_service.search_code(
        query="company creation",
        language="go",
        project="moby",
        limit=5,
    )
    assert len(results) == 1
    assert isinstance(results[0], CodeSearchResult)
    assert results[0].symbol_name == "Create"


def test_search_skills(search_service, mock_qdrant):
    _mock_query_points(mock_qdrant, [
        MagicMock(
            score=0.92,
            payload={
                "content": "Expert on Moby project...",
                "skill_name": "moby-expert",
                "description": "Expert on Moby project",
                "chunk_type": "skill",
            },
        )
    ])
    results = search_service.search_skills(query="moby service", limit=3)
    assert len(results) == 1
    assert isinstance(results[0], SkillResult)
    assert results[0].skill_name == "moby-expert"


def test_search_memory(search_service, mock_qdrant):
    _mock_query_points(mock_qdrant, [
        MagicMock(
            score=0.85,
            payload={
                "content": "Fixed NATS reconnection",
                "id": "mem_123",
                "project": "moby",
                "topic": "nats",
                "memory_type": "bug-fix",
                "tags": ["nats"],
                "status": "approved",
                "created_at": "2026-03-12T14:30:00Z",
            },
        )
    ])
    results = search_service.search_memory(
        query="NATS issue", project="moby", limit=5
    )
    assert len(results) == 1
    assert isinstance(results[0], MemoryResult)
    assert results[0].memory_type == "bug-fix"


def test_search_memory_filters_approved(search_service, mock_qdrant):
    """search_memory should only return approved entries by default."""
    _mock_query_points(mock_qdrant, [])
    search_service.search_memory(query="test", limit=5)
    call_args = mock_qdrant.query_points.call_args
    query_filter = call_args.kwargs.get("query_filter")
    assert query_filter is not None
