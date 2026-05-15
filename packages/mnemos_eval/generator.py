from __future__ import annotations

import json
import random
import uuid
from typing import Iterable

from rag_core.llm import LLMError, LLMProvider

from mnemos_eval.schema import GoldenCandidate, Intent


_DEFAULT_PROMPT = """You generate evaluation questions for a code/docs RAG system.

Given the file path, language, and a content excerpt below, write ONE concise
natural-language question that a developer might realistically ask such that this
chunk is the BEST answer in the codebase.

Hard rules — break any and you fail:
- Be self-contained: a reader who hasn't seen the chunk must understand the question.
- Avoid demonstratives ("this", "the above", "the following", "here").
- Do NOT quote or paraphrase the answer verbatim. Don't name the exact function /
  type / file you're pointing to.
- Focus on intent / behaviour / "how do I do X?" or "where is X handled?".
  Avoid trivia ("what variable is on line 3").
- If the chunk is too short, generic, or auto-generated (boilerplate, getters,
  data dumps), respond with {{"question": "", "intent": "code_search"}} to skip it.

Output strictly JSON with keys: "question" (string, empty to skip), "intent"
(one of: code_search, skill_discovery, doc_lookup, memory_recall).

File: {file_path}
Language: {language}
Chunk type: {chunk_type}
Content:
{content}

JSON:"""


_COLLECTION_TO_INTENT: dict[str, Intent] = {
    "mnemos_skills": "skill_discovery",
    "mnemos_docs": "doc_lookup",
    "mnemos_memory": "memory_recall",
}


class GoldenGenerator:
    """Generates candidate Q/A pairs from indexed chunks via a pluggable LLM provider."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    def generate(
        self,
        chunks: Iterable[dict],
        count: int,
        rng: random.Random | None = None,
    ) -> list[GoldenCandidate]:
        rng = rng or random.Random()
        chunks_list = list(chunks)
        if not chunks_list:
            return []
        sample = rng.sample(chunks_list, min(count, len(chunks_list)))

        candidates: list[GoldenCandidate] = []
        for chunk in sample:
            cand = self._gen_one(chunk)
            if cand is not None:
                candidates.append(cand)
        return candidates

    _MIN_USEFUL_CONTENT_CHARS = 80

    def _gen_one(self, chunk: dict) -> GoldenCandidate | None:
        content = (chunk.get("content", "") or "").strip()
        # Skip chunks too short to generate a meaningful question against.
        if len(content) < self._MIN_USEFUL_CONTENT_CHARS:
            return None
        content = content[:1500]

        prompt = _DEFAULT_PROMPT.format(
            file_path=chunk.get("file_path", "<unknown>"),
            language=chunk.get("language", "text"),
            chunk_type=chunk.get("chunk_type", "block"),
            content=content,
        )
        try:
            raw = self._llm.complete_prompt(prompt, json_mode=True, timeout=60)
        except LLMError:
            return None

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None

        question = (payload.get("question") or "").strip()
        if not question:
            return None

        intent_raw = (payload.get("intent") or "").strip()
        collection = chunk.get("collection") or chunk.get("source_collection") or ""
        intent: Intent
        if intent_raw in {"code_search", "skill_discovery", "doc_lookup", "memory_recall"}:
            intent = intent_raw  # type: ignore[assignment]
        else:
            intent = _COLLECTION_TO_INTENT.get(collection, "code_search")

        return GoldenCandidate(
            id=f"q-{uuid.uuid4().hex[:8]}",
            query=question,
            intent=intent,
            suggested_files=[chunk.get("file_path", "")] if chunk.get("file_path") else [],
            source_collection=collection,
            source_chunk_preview=content[:200],
        )
