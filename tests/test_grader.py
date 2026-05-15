from __future__ import annotations

from rag_core.grader import DocumentGrader, GradedResult
from rag_core.llm import LLMError


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
        return self.responses.pop(0) if self.responses else '{"grade": "medium"}'


def test_grader_disabled_returns_uniform_high():
    grader = DocumentGrader(llm=FakeLLM(), enabled=False)
    out = grader.grade("q", [("text a", "a"), ("text b", "b")])
    assert all(g.grade == "high" and g.score == 1.0 for g in out)
    assert [g.payload for g in out] == ["a", "b"]


def test_grader_parses_grades():
    llm = FakeLLM(responses=['{"grade": "high"}', '{"grade": "low"}'])
    grader = DocumentGrader(llm=llm, enabled=True, workers=1)
    out = grader.grade("q", [("a", "x"), ("b", "y")])
    assert [g.grade for g in out] == ["high", "low"]
    assert [g.score for g in out] == [1.0, 0.0]
    assert llm.calls == 2


def test_grader_normalises_unknown_grade_to_medium():
    llm = FakeLLM(responses=['{"grade": "VERY_HIGH"}'])
    grader = DocumentGrader(llm=llm, enabled=True, workers=1)
    out = grader.grade("q", [("a", "x")])
    assert out[0].grade == "medium"


def test_grader_falls_back_on_llm_error():
    grader = DocumentGrader(llm=FakeLLM(raise_error=True), enabled=True, workers=1)
    out = grader.grade("q", [("a", "x"), ("b", "y")])
    assert all(g.grade == "medium" for g in out)


def test_grader_falls_back_on_invalid_json():
    llm = FakeLLM(responses=["not json at all"])
    grader = DocumentGrader(llm=llm, enabled=True, workers=1)
    out = grader.grade("q", [("a", "x")])
    assert out[0].grade == "medium"


def test_all_low_helper():
    low = [GradedResult(grade="low", score=0.0, payload=None) for _ in range(3)]
    mixed = low + [GradedResult(grade="high", score=1.0, payload=None)]
    assert DocumentGrader.all_low(low) is True
    assert DocumentGrader.all_low(mixed) is False
    assert DocumentGrader.all_low([]) is True


def test_grader_empty_input():
    grader = DocumentGrader(llm=FakeLLM(), enabled=True)
    assert grader.grade("q", []) == []
