from __future__ import annotations

import json
import random
import uuid
from typing import Iterable

import httpx

from mnemos_eval.schema import GoldenCandidate, Intent


_DEFAULT_PROMPT = """You generate evaluation questions for a code/docs RAG system.

Given the file path, language, and a content excerpt below, write ONE concise
natural-language question that a developer might ask such that this chunk is
the BEST answer. The question must:
- be self-contained (no "this", "the above")
- not quote the answer
- focus on intent/behaviour, not surface syntax

Output strictly JSON with keys: "question" (string), "intent" (one of:
code_search, skill_discovery, doc_lookup, memory_recall).

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
    """Generates candidate Q/A pairs via Ollama from random chunks in a collection."""

    def __init__(
        self,
        ollama_url: str,
        model: str,
        timeout: float = 60.0,
    ) -> None:
        self._ollama_url = ollama_url.rstrip("/")
        self._model = model
        self._timeout = timeout

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
        with httpx.Client(timeout=self._timeout) as client:
            for chunk in sample:
                cand = self._gen_one(client, chunk)
                if cand is not None:
                    candidates.append(cand)
        return candidates

    def _gen_one(self, client: httpx.Client, chunk: dict) -> GoldenCandidate | None:
        content = chunk.get("content", "")[:1500]
        prompt = _DEFAULT_PROMPT.format(
            file_path=chunk.get("file_path", "<unknown>"),
            language=chunk.get("language", "text"),
            chunk_type=chunk.get("chunk_type", "block"),
            content=content,
        )
        try:
            response = client.post(
                f"{self._ollama_url}/api/generate",
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.4},
                },
            )
            response.raise_for_status()
            data = response.json()
            payload = json.loads(data.get("response", "{}"))
        except (httpx.HTTPError, json.JSONDecodeError):
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
