"""Query rewriter — expands the original query into one or more reformulations.

Used inside the CRAG corrective loop: when the document grader reports that
no `high` candidates survived retrieval, the rewriter produces alternative
phrasings that we re-issue against the retrieval stack.

Strategies (selectable via env):
    expansion  — synonyms + technical terms (default, cheap)
    decompose  — split a multi-hop question into sub-questions
    hyde       — generate a hypothetical answer document for embedding
"""
from __future__ import annotations

import json
import logging
from typing import Literal

from core.llm import LLMError, LLMProvider

logger = logging.getLogger("mnemos.rewriter")

Strategy = Literal["expansion", "decompose", "hyde"]

_MAX_OUTPUT_TOKENS = 200
_TEMPERATURE = 0.4

_EXPANSION_PROMPT = """You rewrite developer search queries to improve retrieval recall.

Original query: {query}

Produce up to 3 alternative phrasings that:
- Surface synonymous technical terms (e.g. "ride cancellation" → "cancel ride", "abort trip")
- Spell out abbreviations and acronyms
- Mention likely code identifiers (function names, types) that would appear in matching code

Reply with strict JSON: {{"queries": ["...", "...", "..."]}}.

JSON:"""

_DECOMPOSE_PROMPT = """You decompose a developer search query into atomic sub-questions.

Original query: {query}

If the query asks about multiple things, split it into separate concise sub-questions.
If it is already atomic, return a single-element list with the original query.

Reply with strict JSON: {{"queries": ["...", "..."]}}.

JSON:"""

_HYDE_PROMPT = """You generate a hypothetical short answer to a developer query, so the
answer can be embedded and used for retrieval.

Query: {query}

Write 2-4 sentences that would plausibly answer the query, using realistic technical
vocabulary that you would expect to find in matching code or documentation.

Reply with strict JSON: {{"queries": ["..."]}} where the single string is your answer."""


_PROMPTS = {
    "expansion": _EXPANSION_PROMPT,
    "decompose": _DECOMPOSE_PROMPT,
    "hyde": _HYDE_PROMPT,
}


class QueryRewriter:
    """Generate alternative phrasings of a query via a pluggable LLM."""

    def __init__(
        self,
        llm: LLMProvider,
        enabled: bool = True,
        strategy: Strategy = "expansion",
        max_variants: int = 3,
    ) -> None:
        self._llm = llm
        self._enabled = enabled
        self._strategy = strategy if strategy in _PROMPTS else "expansion"
        self._max_variants = max(1, int(max_variants))

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def strategy(self) -> Strategy:
        return self._strategy

    def rewrite(self, query: str) -> list[str]:
        """Return up to `max_variants` alternative queries.

        Always returns at least the original query so callers can iterate uniformly.
        On LLM error: returns [query] (graceful no-op).
        """
        if not self._enabled or not query.strip():
            return [query]

        prompt = _PROMPTS[self._strategy].format(query=query)
        try:
            raw = self._llm.complete_prompt(
                prompt,
                json_mode=True,
                max_tokens=_MAX_OUTPUT_TOKENS,
                temperature=_TEMPERATURE,
                timeout=30,
            )
            data = json.loads(raw)
            variants = data.get("queries") or []
        except (LLMError, json.JSONDecodeError):
            return [query]

        cleaned = [q.strip() for q in variants if isinstance(q, str) and q.strip()]
        # Always include the original at position 0 so we never *replace* it.
        if not cleaned or cleaned[0] != query:
            cleaned = [query, *cleaned]
        # Cap and dedupe while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for q in cleaned:
            if q not in seen:
                seen.add(q)
                deduped.append(q)
            if len(deduped) >= self._max_variants + 1:  # +1 for the original
                break
        return deduped
