"""Document grader — CRAG-style relevance scoring of retrieved chunks.

For each (query, chunk) pair the grader asks an LLM whether the chunk is
genuinely useful to answer the query. Grades are coarse on purpose:

    high   — strongly relevant, would surface this chunk
    medium — partially relevant or tangentially useful
    low    — irrelevant or noise

When zero `high` candidates remain, the grader signals that the retrieval
likely failed and a downstream fallback should kick in (e.g. query rewriter).
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal

from core.llm import LLMError, LLMProvider

logger = logging.getLogger("mnemos.grader")

Grade = Literal["high", "medium", "low"]
_GRADE_TO_SCORE = {"high": 1.0, "medium": 0.5, "low": 0.0}

_CHUNK_PREVIEW_CHARS = 800
_MAX_OUTPUT_TOKENS = 24
_TEMPERATURE = 0.0

_PROMPT_TEMPLATE = """You are a retrieval relevance judge.

Given a search query and a candidate chunk, decide whether the chunk is a
useful answer or evidence for the query.

Reply with strict JSON: {{"grade": "high" | "medium" | "low"}}.

Query: {query}

Candidate chunk:
---
{chunk}
---

JSON:"""


@dataclass
class GradedResult:
    """A retrieval result + its grader-assigned relevance grade."""

    grade: Grade
    score: float            # numeric mapping of the grade for sorting
    payload: object         # the original search result, opaque to the grader


class DocumentGrader:
    """Relevance grading via a pluggable LLM. Parallel per-chunk."""

    def __init__(
        self,
        llm: LLMProvider,
        enabled: bool = True,
        workers: int = 4,
    ) -> None:
        self._llm = llm
        self._enabled = enabled
        self._workers = max(1, int(workers))

    @property
    def enabled(self) -> bool:
        return self._enabled

    def grade(
        self,
        query: str,
        candidates: list[tuple[str, object]],
    ) -> list[GradedResult]:
        """Grade `candidates` ((text, payload)) for relevance to `query`.

        When disabled, every candidate gets a synthetic `high` grade so the
        caller sees a uniform interface.
        On per-chunk LLM error: that chunk falls back to `medium` (safest
        neutral grade).
        """
        if not self._enabled:
            return [GradedResult(grade="high", score=1.0, payload=p) for _, p in candidates]
        if not candidates:
            return []

        def grade_one(item: tuple[str, object]) -> GradedResult:
            text, payload = item
            preview = (text or "")[:_CHUNK_PREVIEW_CHARS]
            prompt = _PROMPT_TEMPLATE.format(query=query, chunk=preview)
            try:
                raw = self._llm.complete_prompt(
                    prompt,
                    json_mode=True,
                    max_tokens=_MAX_OUTPUT_TOKENS,
                    temperature=_TEMPERATURE,
                    timeout=30,
                )
                data = json.loads(raw)
                g = (data.get("grade") or "").strip().lower()
                if g not in _GRADE_TO_SCORE:
                    g = "medium"
            except (LLMError, json.JSONDecodeError):
                g = "medium"
            return GradedResult(grade=g, score=_GRADE_TO_SCORE[g], payload=payload)

        if self._workers == 1 or len(candidates) <= 1:
            return [grade_one(c) for c in candidates]
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            return list(pool.map(grade_one, candidates))

    @staticmethod
    def all_low(graded: list[GradedResult]) -> bool:
        """Return True when no candidate is graded high — signals retrieval failure."""
        return all(g.grade == "low" for g in graded) if graded else True
