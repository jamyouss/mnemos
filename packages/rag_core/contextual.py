"""Anthropic-style Contextual Retrieval — prepend an LLM-generated context line
to every chunk before embedding + sparse indexing.

Activated via `MNEMOS_CONTEXTUAL_ENABLED=true`. The preamble is generated from
the chunk plus a header excerpt of the parent document so the LLM has enough
signal to write a useful situating sentence.

Falls back gracefully on LLM errors — the original chunk is preserved.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from rag_core.llm import LLMError, LLMProvider

logger = logging.getLogger("mnemos.contextual")

_DOC_HEADER_CHARS = 1500
_CHUNK_PREVIEW_CHARS = 800
_MAX_PREAMBLE_TOKENS = 80
_TEMPERATURE = 0.2

_PROMPT_TEMPLATE = """You are writing a one-sentence contextual preamble for a chunk \
of code or documentation, so a retrieval system can find it.

File: {file_path}
Language: {language}
Chunk type: {chunk_type}

Document header (first {header_chars} chars):
---
{doc_header}
---

Chunk to contextualize:
---
{chunk}
---

Write ONE sentence (max 30 words) that:
- States what this chunk is about
- Mentions the package/module or parent symbol if relevant
- Surfaces key technical terms a developer might search for
- Does NOT repeat the chunk verbatim
- Does NOT use phrases like "this chunk", "the above"

One sentence only, no quotes, no markdown:"""


class ContextualEnricher:
    """Generate a contextual preamble for chunks via an LLM."""

    def __init__(
        self,
        llm: LLMProvider,
        enabled: bool = True,
        workers: int = 1,
        max_preamble_tokens: int = _MAX_PREAMBLE_TOKENS,
    ) -> None:
        self._llm = llm
        self._enabled = enabled
        self._workers = max(1, int(workers))
        self._max_preamble_tokens = max_preamble_tokens

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enrich(
        self,
        document_content: str,
        chunks: list[dict],
        file_path: str,
    ) -> list[dict]:
        """Mutate-and-return each chunk with a `preamble` field and a prefixed `content`.

        If contextual enrichment is disabled, returns chunks untouched.
        On LLM error per chunk, that chunk is left untouched.
        """
        if not self._enabled or not chunks:
            return chunks

        doc_header = document_content[:_DOC_HEADER_CHARS]

        def build_one(chunk: dict) -> tuple[dict, str]:
            preview = chunk.get("content", "")[:_CHUNK_PREVIEW_CHARS]
            prompt = _PROMPT_TEMPLATE.format(
                file_path=file_path,
                language=chunk.get("language", "text"),
                chunk_type=chunk.get("chunk_type", "block"),
                header_chars=_DOC_HEADER_CHARS,
                doc_header=doc_header,
                chunk=preview,
            )
            try:
                preamble = self._llm.complete_prompt(
                    prompt,
                    max_tokens=self._max_preamble_tokens,
                    temperature=_TEMPERATURE,
                    timeout=30,
                ).strip()
            except LLMError:
                logger.warning("Contextual preamble failed for %s chunk %s",
                               file_path, chunk.get("chunk_index", "?"))
                return chunk, ""
            return chunk, preamble

        # Parallelise per chunk; for a typical file 3-15 chunks → little fanout, but the
        # LLM round-trip dominates the cost so even modest parallelism helps.
        if self._workers == 1 or len(chunks) <= 1:
            results = [build_one(c) for c in chunks]
        else:
            with ThreadPoolExecutor(max_workers=self._workers) as pool:
                results = list(pool.map(build_one, chunks))

        enriched: list[dict] = []
        for chunk, preamble in results:
            if preamble:
                chunk["preamble"] = preamble
                chunk["content"] = f"{preamble}\n\n{chunk['content']}"
            enriched.append(chunk)
        return enriched
