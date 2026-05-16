from __future__ import annotations

from core.llm import LLMError
from core.rewriter import QueryRewriter


class FakeLLM:
    name = "fake"
    model = "fake-1"

    def __init__(self, responses=None, raise_error=False):
        self.responses = list(responses or [])
        self.raise_error = raise_error
        self.calls = 0

    def complete(self, *a, **kw):
        raise NotImplementedError

    def complete_prompt(self, prompt, **kw):
        self.calls += 1
        if self.raise_error:
            raise LLMError("boom")
        return self.responses.pop(0) if self.responses else '{"queries": ["alt"]}'


def test_rewriter_disabled_returns_original_only():
    rw = QueryRewriter(llm=FakeLLM(), enabled=False)
    assert rw.rewrite("how to cancel a ride") == ["how to cancel a ride"]


def test_rewriter_expansion_includes_original_first():
    llm = FakeLLM(responses=['{"queries": ["abort trip", "cancel booking"]}'])
    rw = QueryRewriter(llm=llm, enabled=True, strategy="expansion", max_variants=3)
    out = rw.rewrite("cancel ride")
    assert out[0] == "cancel ride"
    assert "abort trip" in out
    assert "cancel booking" in out
    assert len(out) <= 4  # original + up to 3 variants


def test_rewriter_falls_back_on_llm_error():
    rw = QueryRewriter(llm=FakeLLM(raise_error=True), enabled=True)
    assert rw.rewrite("anything") == ["anything"]


def test_rewriter_falls_back_on_invalid_json():
    rw = QueryRewriter(llm=FakeLLM(responses=["this is not json"]), enabled=True)
    assert rw.rewrite("anything") == ["anything"]


def test_rewriter_dedupes_variants():
    llm = FakeLLM(responses=['{"queries": ["abort", "abort", "abort"]}'])
    rw = QueryRewriter(llm=llm, enabled=True)
    out = rw.rewrite("cancel")
    assert len(out) == len(set(out))


def test_rewriter_caps_max_variants():
    llm = FakeLLM(responses=['{"queries": ["v1", "v2", "v3", "v4", "v5"]}'])
    rw = QueryRewriter(llm=llm, enabled=True, max_variants=2)
    out = rw.rewrite("original")
    # original + 2 variants
    assert len(out) <= 3


def test_rewriter_strategy_falls_back_to_expansion_for_unknown():
    rw = QueryRewriter(llm=FakeLLM(), enabled=True, strategy="some-unknown-strategy")  # type: ignore[arg-type]
    assert rw.strategy == "expansion"


def test_rewriter_empty_query_returns_original():
    rw = QueryRewriter(llm=FakeLLM(), enabled=True)
    assert rw.rewrite("") == [""]
