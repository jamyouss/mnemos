from unittest.mock import MagicMock, patch

import pytest

from core.indexer import Indexer


@pytest.fixture
def mock_qdrant():
    client = MagicMock()
    client.get_collections.return_value.collections = []
    return client


@pytest.fixture
def mock_embeddings():
    service = MagicMock()
    service.embed.return_value = [0.1] * 384
    service.embed_batch.return_value = [[0.1] * 384, [0.2] * 384]
    return service


@pytest.fixture
def indexer(mock_qdrant, mock_embeddings):
    return Indexer(qdrant_client=mock_qdrant, embedding_service=mock_embeddings)


def test_select_chunker_for_go(indexer):
    from core.chunkers.go_chunker import GoChunker
    chunker = indexer._select_chunker("service.go")
    assert isinstance(chunker, GoChunker)


def test_select_chunker_for_vue(indexer):
    from core.chunkers.vue_chunker import VueChunker
    chunker = indexer._select_chunker("Component.vue")
    assert isinstance(chunker, VueChunker)


def test_select_chunker_for_markdown(indexer):
    from core.chunkers.markdown_chunker import MarkdownChunker
    chunker = indexer._select_chunker("README.md")
    assert isinstance(chunker, MarkdownChunker)


def test_select_chunker_fallback(indexer):
    from core.chunkers.fallback_chunker import FallbackChunker
    chunker = indexer._select_chunker("config.yaml")
    assert isinstance(chunker, FallbackChunker)


def test_index_file_writes_project_payload(indexer, mock_qdrant, sample_go_code):
    """For mnemos_code, the project tag should be auto-derived from the path."""
    indexer.index_file(
        content=sample_go_code,
        file_path="/data/codebase/myproject/services/core/app.go",
        collection="mnemos_code",
    )
    mock_qdrant.upsert.assert_called_once()
    call_args = mock_qdrant.upsert.call_args
    assert call_args.kwargs["collection_name"] == "mnemos_code"
    points = call_args.kwargs["points"]
    assert len(points) > 0
    # Every point gets the project tag
    for p in points:
        assert p.payload.get("project") == "myproject"


def test_index_file_respects_explicit_project_override(indexer, mock_qdrant, sample_go_code):
    indexer.index_file(
        content=sample_go_code,
        file_path="/data/codebase/myproject/services/core/app.go",
        collection="mnemos_code",
        project="custom-name",
    )
    points = mock_qdrant.upsert.call_args.kwargs["points"]
    for p in points:
        assert p.payload.get("project") == "custom-name"


def test_index_file_skips_project_for_skills_and_docs(indexer, mock_qdrant, sample_go_code):
    indexer.index_file(
        content=sample_go_code,
        file_path="skills/some-skill/instructions.md",
        collection="mnemos_skills",
    )
    points = mock_qdrant.upsert.call_args.kwargs["points"]
    for p in points:
        assert "project" not in p.payload


def test_delete_file(indexer, mock_qdrant):
    indexer.delete_file(
        file_path="myproject/services/core/old.go",
        collection="mnemos_code",
    )
    mock_qdrant.delete.assert_called_once()
