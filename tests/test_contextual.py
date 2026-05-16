from __future__ import annotations

from core.contextual import ContextualEnricher
from core.llm import LLMError


class FakeLLM:
    name = "fake"
    model = "fake-1"

    def __init__(self, responses=None, raise_error=False):
        self.responses = list(responses or [])
        self.raise_error = raise_error
        self.calls: list[str] = []

    def complete(self, messages, **kwargs):
        raise NotImplementedError

    def complete_prompt(self, prompt, **kwargs):
        self.calls.append(prompt)
        if self.raise_error:
            raise LLMError("simulated")
        return self.responses.pop(0) if self.responses else "default preamble"


def test_enricher_disabled_passes_through():
    llm = FakeLLM(responses=["should not be called"])
    enricher = ContextualEnricher(llm=llm, enabled=False)
    chunks = [{"content": "raw chunk", "chunk_index": 0}]
    result = enricher.enrich("full document", chunks, "file.py")
    assert result[0]["content"] == "raw chunk"
    assert "preamble" not in result[0]
    assert llm.calls == []


def test_enricher_prepends_preamble():
    llm = FakeLLM(responses=["A function that computes tax."])
    enricher = ContextualEnricher(llm=llm, enabled=True)
    chunks = [{"content": "def compute_tax():\n    pass", "chunk_index": 0, "language": "python"}]
    result = enricher.enrich("file content", chunks, "pkg/tax.py")
    assert result[0]["content"].startswith("A function that computes tax.\n\n")
    assert result[0]["preamble"] == "A function that computes tax."
    assert len(llm.calls) == 1


def test_enricher_handles_llm_failure_per_chunk():
    llm = FakeLLM(raise_error=True)
    enricher = ContextualEnricher(llm=llm, enabled=True)
    chunks = [{"content": "raw", "chunk_index": 0}]
    result = enricher.enrich("doc", chunks, "f.py")
    # Original chunk preserved when the LLM call fails
    assert result[0]["content"] == "raw"
    assert "preamble" not in result[0]


def test_enricher_skips_empty_input():
    llm = FakeLLM()
    enricher = ContextualEnricher(llm=llm, enabled=True)
    assert enricher.enrich("doc", [], "f.py") == []
    assert llm.calls == []


def test_enricher_processes_multiple_chunks():
    llm = FakeLLM(responses=["preamble A", "preamble B", "preamble C"])
    enricher = ContextualEnricher(llm=llm, enabled=True)
    chunks = [
        {"content": "chunk 1", "chunk_index": 0},
        {"content": "chunk 2", "chunk_index": 1},
        {"content": "chunk 3", "chunk_index": 2},
    ]
    result = enricher.enrich("doc", chunks, "f.py")
    assert result[0]["preamble"] == "preamble A"
    assert result[1]["preamble"] == "preamble B"
    assert result[2]["preamble"] == "preamble C"


def test_enricher_parallel_workers_preserves_order():
    """With multiple workers, chunk order MUST be preserved."""
    llm = FakeLLM(responses=[f"preamble {i}" for i in range(20)])
    enricher = ContextualEnricher(llm=llm, enabled=True, workers=4)
    chunks = [{"content": f"chunk {i}", "chunk_index": i} for i in range(20)]
    result = enricher.enrich("doc", chunks, "f.py")
    assert len(result) == 20
    # Each chunk preserves its own content (no cross-contamination)
    for i, ch in enumerate(result):
        assert ch["chunk_index"] == i
        assert f"chunk {i}" in ch["content"]
