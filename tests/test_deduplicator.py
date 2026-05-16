"""Tests for Deduplicator."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.deduplicator import Deduplicator
from core.models import ExtractedMemory


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


def _mock_query_points(mock_qdrant, hits):
    result = MagicMock()
    result.points = hits
    mock_qdrant.query_points.return_value = result


def test_insert_new_memory(deduplicator, mock_qdrant):
    _mock_query_points(mock_qdrant, [])
    memory = ExtractedMemory(content="Use flat API routes", memory_type="decision", project="myproject", tags=["routing"])
    result = deduplicator.deduplicate_and_store(memory)
    assert result.action == "inserted"
    assert result.merged_with is None
    mock_qdrant.upsert.assert_called_once()


def test_merge_similar_memory(deduplicator, mock_qdrant, mock_extractor):
    existing_point = MagicMock()
    existing_point.id = "existing-point-id"
    existing_point.score = 0.92
    existing_point.payload = {
        "id": "existing-mem-id",
        "content": "API routes should be flat",
        "memory_type": "decision",
        "project": "myproject",
        "tags": ["routing"],
        "status": "approved",
        "created_at": "2026-04-01T00:00:00Z",
    }
    _mock_query_points(mock_qdrant, [existing_point])
    mock_extractor.merge_memories.return_value = "API routes must always be flat."

    memory = ExtractedMemory(content="Confirmed: flat routes", memory_type="decision", project="myproject", tags=["routing"])
    result = deduplicator.deduplicate_and_store(memory)
    assert result.action == "merged"
    assert result.merged_with == "existing-mem-id"
    mock_extractor.merge_memories.assert_called_once()


def test_replace_similar_memory(mock_qdrant, mock_embeddings, mock_extractor):
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
    existing_point.payload = {"id": "old-mem-id", "content": "Old memory", "status": "approved", "created_at": "2026-04-01T00:00:00Z"}
    _mock_query_points(mock_qdrant, [existing_point])

    memory = ExtractedMemory(content="New memory", memory_type="decision")
    result = dedup.deduplicate_and_store(memory)
    assert result.action == "replaced"
    assert result.merged_with == "old-mem-id"


def test_no_merge_below_threshold(deduplicator, mock_qdrant):
    existing_point = MagicMock()
    existing_point.id = "point-id"
    existing_point.score = 0.75
    existing_point.payload = {"id": "mem-id", "content": "something related"}
    _mock_query_points(mock_qdrant, [existing_point])

    memory = ExtractedMemory(content="Something different enough")
    result = deduplicator.deduplicate_and_store(memory)
    assert result.action == "inserted"
    assert result.merged_with is None
