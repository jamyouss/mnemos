"""Tests for MemoryExtractor with mocked LLM provider."""
from __future__ import annotations

import pytest

from rag_core.llm import LLMError
from rag_core.memory_extractor import MemoryExtractor


class FakeLLM:
    name = "fake"
    model = "fake-1"

    def __init__(self, responses=None, raise_error=False):
        self.responses = list(responses or [])
        self.raise_error = raise_error
        self.calls: list[dict] = []

    def complete(self, messages, *, json_mode=False, max_tokens=None, temperature=0.4, timeout=60.0):
        self.calls.append({"messages": messages, "json_mode": json_mode})
        if self.raise_error:
            raise LLMError("simulated failure")
        return self.responses.pop(0) if self.responses else "[]"

    def complete_prompt(self, prompt, *, system=None, json_mode=False, max_tokens=None, temperature=0.4, timeout=60.0):
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.complete(messages, json_mode=json_mode, max_tokens=max_tokens, temperature=temperature, timeout=timeout)


@pytest.fixture
def extractor():
    return MemoryExtractor(llm=FakeLLM())


def test_extract_returns_list():
    llm = FakeLLM(responses=['[{"content": "Decided to use flat routes for API", "memory_type": "decision", "project": "moby", "tags": ["routing"]}]'])
    extractor = MemoryExtractor(llm=llm)
    results = extractor.extract(
        commit_message="feat: flatten API routes",
        diff="diff --git a/routes.go ...",
    )
    assert len(results) == 1
    assert results[0].content == "Decided to use flat routes for API"
    assert results[0].memory_type == "decision"
    assert results[0].project == "moby"
    # Verify the LLM was called with json_mode and a system message
    assert llm.calls[0]["json_mode"] is True
    assert llm.calls[0]["messages"][0]["role"] == "system"


def test_extract_returns_empty_for_trivial_commit():
    llm = FakeLLM(responses=["[]"])
    extractor = MemoryExtractor(llm=llm)
    assert extractor.extract(commit_message="fix: typo", diff="- helo\n+ hello") == []


def test_extract_handles_llm_error():
    extractor = MemoryExtractor(llm=FakeLLM(raise_error=True))
    assert extractor.extract(commit_message="feat: something", diff="some diff") == []


def test_extract_handles_malformed_json():
    llm = FakeLLM(responses=["This is not JSON"])
    extractor = MemoryExtractor(llm=llm)
    assert extractor.extract(commit_message="feat: something", diff="some diff") == []


def test_merge_memories():
    llm = FakeLLM(responses=["Merged memory text"])
    extractor = MemoryExtractor(llm=llm)
    assert extractor.merge_memories("old memory", "new memory") == "Merged memory text"


def test_merge_memories_fallback_on_error():
    extractor = MemoryExtractor(llm=FakeLLM(raise_error=True))
    result = extractor.merge_memories("old memory", "new memory")
    assert result == "old memory\n\nnew memory"
