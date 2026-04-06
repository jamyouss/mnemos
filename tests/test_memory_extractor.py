"""Tests for MemoryExtractor."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from rag_core.memory_extractor import MemoryExtractor


@pytest.fixture
def extractor():
    return MemoryExtractor(
        ollama_url="http://localhost:11434",
        model="llama3.1:8b",
    )


def test_extract_returns_list(extractor):
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "message": {
            "content": '[{"content": "Decided to use flat routes for API", "memory_type": "decision", "project": "moby", "tags": ["routing"]}]'
        }
    }
    with patch("rag_core.memory_extractor.httpx.post", return_value=fake_response):
        results = extractor.extract(
            commit_message="feat: flatten API routes",
            diff="diff --git a/routes.go ...",
        )
    assert len(results) == 1
    assert results[0].content == "Decided to use flat routes for API"
    assert results[0].memory_type == "decision"
    assert results[0].project == "moby"


def test_extract_returns_empty_for_trivial_commit(extractor):
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"message": {"content": "[]"}}
    with patch("rag_core.memory_extractor.httpx.post", return_value=fake_response):
        results = extractor.extract(commit_message="fix: typo", diff="- helo\n+ hello")
    assert results == []


def test_extract_handles_ollama_error(extractor):
    with patch("rag_core.memory_extractor.httpx.post", side_effect=Exception("connection refused")):
        results = extractor.extract(commit_message="feat: something", diff="some diff")
    assert results == []


def test_extract_handles_malformed_json(extractor):
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"message": {"content": "This is not JSON"}}
    with patch("rag_core.memory_extractor.httpx.post", return_value=fake_response):
        results = extractor.extract(commit_message="feat: something", diff="some diff")
    assert results == []


def test_merge_memories(extractor):
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"message": {"content": "Merged memory text"}}
    with patch("rag_core.memory_extractor.httpx.post", return_value=fake_response):
        result = extractor.merge_memories("old memory", "new memory")
    assert result == "Merged memory text"


def test_merge_memories_fallback_on_error(extractor):
    with patch("rag_core.memory_extractor.httpx.post", side_effect=Exception("fail")):
        result = extractor.merge_memories("old memory", "new memory")
    assert result == "old memory\n\nnew memory"
