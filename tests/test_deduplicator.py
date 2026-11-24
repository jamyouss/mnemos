"""Tests for Deduplicator."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.deduplicator import Deduplicator
from core.models import ExtractedMemory


@pytest.fixture
def mock_qdrant():
    client = MagicMock()
    client.collection_exists.return_value = False
    return client


@pytest.fixture
def mock_embeddings():
    service = MagicMock()
    service.embed_documents.return_value = [[0.1] * 384]
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


def test_new_memories_are_searchable_by_default(deduplicator, mock_qdrant):
    """Writes land approved. Holding them for review made the empty state the
    default one: six days of extraction, fifteen entries, zero approvals, and
    nothing retrievable — search only returns approved."""
    mock_qdrant.query_points.return_value.points = []

    result = deduplicator.deduplicate_and_store(
        ExtractedMemory(content="A decision.", memory_type="decision", project=None, tags=[])
    )

    assert result.status == "approved"
    assert mock_qdrant.upsert.call_args.kwargs["points"][0].payload["status"] == "approved"


def test_an_explicit_status_still_wins(deduplicator, mock_qdrant):
    """A caller that wants review can still ask for it."""
    mock_qdrant.query_points.return_value.points = []

    result = deduplicator.deduplicate_and_store(
        ExtractedMemory(content="Unsure.", memory_type="note", project=None, tags=[]),
        status="pending",
    )

    assert result.status == "pending"
    assert mock_qdrant.upsert.call_args.kwargs["points"][0].payload["status"] == "pending"


@pytest.fixture
def live():
    """A deduplicator over a real in-memory Qdrant holding one memory."""
    from qdrant_client import QdrantClient

    from core.indexer import Indexer

    embeddings = MagicMock(dimension=4)
    embeddings.embed_documents.side_effect = lambda texts, titles=None: [[float(len(t)), 0.1, 0.2, 0.3] for t in texts]
    qdrant = QdrantClient(":memory:")
    Indexer(qdrant_client=qdrant, embedding_service=embeddings).ensure_collection("mnemos_memory")
    dedup = Deduplicator(qdrant_client=qdrant, embedding_service=embeddings, memory_extractor=MagicMock())
    result = dedup.deduplicate_and_store(ExtractedMemory(content="Use RRF", memory_type="decision", tags=["acme"]))
    return dedup, qdrant, result.memory_id


def test_an_edit_rewrites_the_text_and_its_vectors(live):
    dedup, qdrant, mem_id = live
    before = qdrant.scroll("mnemos_memory", with_vectors=True)[0][0].vector["dense"]
    updated = dedup.update(mem_id, content="Use weighted RRF, dense 0.7", tags=["acme", "search"])

    assert updated["content"] == "Use weighted RRF, dense 0.7" and updated["tags"] == ["acme", "search"]
    assert updated["memory_type"] == "decision"  # untouched fields stay
    point = qdrant.scroll("mnemos_memory", with_vectors=True)[0][0]
    assert point.payload["content"] == "Use weighted RRF, dense 0.7"
    assert point.vector["dense"] != before  # re-embedded
    assert dedup.update("missing-id", content="x") is None


def test_delete_removes_the_memory(live):
    dedup, qdrant, mem_id = live
    assert dedup.delete(mem_id) is True
    assert qdrant.count("mnemos_memory").count == 0
    assert dedup.delete(mem_id) is False
