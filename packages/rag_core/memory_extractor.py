"""Extract structured memories from git commit diffs using a pluggable LLM provider."""
from __future__ import annotations

import json
import logging

from rag_core.llm import LLMError, LLMProvider
from rag_core.models import ExtractedMemory

logger = logging.getLogger("mnemos.extractor")

_MAX_DIFF_BYTES = 32_768

_SYSTEM_PROMPT = """You extract memories from git commits for a software team's
long-term knowledge base. Every memory you produce MUST be grounded in concrete
text from the diff provided below — never invent or paraphrase from your training data.

Memory categories (use exactly one per memory):
- "decision"   : an architectural or design choice the author made
- "pattern"    : a code pattern introduced or reinforced
- "convention" : a naming or structural rule the change establishes
- "lesson"     : a bug fixed, workaround applied, or pitfall the change documents

Hard rules — break any and your output is invalid:
1. Every memory MUST quote, paraphrase, or directly summarise content that
   appears in the diff. If you can't point to specific lines, do not emit it.
2. DO NOT use the placeholder example phrasings shown elsewhere ("flat API
   routes", "middleware chain", "Create/Get/Update/Delete naming",
   "Qdrant scroll", etc.) unless those exact words appear in the diff.
3. Skip trivial / mechanical changes: typo fixes, import re-ordering, version
   bumps, formatting, generated files, vendored code.
4. Each memory is 1–3 sentences and explains the WHY when possible, not the WHAT.
5. If the diff has nothing worth remembering, return an empty array. Empty is
   a valid and frequent answer.

Output format:
- JSON array of objects with keys: content (string), memory_type (one of the
  four above), project (string or null), tags (array of short strings).
- project: if a project name is given in the input, use it verbatim. Otherwise
  infer it from file paths in the diff, or set it to null. Do not guess.
- Return ONLY the JSON array. No markdown fences, no surrounding prose."""

_USER_TEMPLATE = """## Commit Message
{commit_message}

## Diff
{diff}"""


class MemoryExtractor:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    def extract(self, commit_message: str, diff: str) -> list[ExtractedMemory]:
        truncated_diff = diff[:_MAX_DIFF_BYTES]
        if len(diff) > _MAX_DIFF_BYTES:
            truncated_diff += "\n\n[... diff truncated ...]"

        user_content = _USER_TEMPLATE.format(
            commit_message=commit_message,
            diff=truncated_diff,
        )

        try:
            raw = self._llm.complete(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                json_mode=True,
                timeout=120,
            )
        except LLMError:
            logger.exception("Memory extraction failed (provider=%s)", self._llm.name)
            return []

        try:
            parsed = json.loads(raw)
            # Some smaller LLMs (llama3.2, mistral 7B, …) like to wrap the
            # array in a top-level object or emit a single bare memory.
            # Be defensive: accept whatever shape we can salvage.
            if isinstance(parsed, dict):
                if "memories" in parsed:
                    parsed = parsed["memories"]
                elif "content" in parsed and "memory_type" in parsed:
                    parsed = [parsed]
                else:
                    # Last-ditch: any list value inside?
                    list_vals = [v for v in parsed.values() if isinstance(v, list)]
                    parsed = list_vals[0] if list_vals else []
            if not isinstance(parsed, list):
                return []
            return [ExtractedMemory(**item) for item in parsed if isinstance(item, dict)]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.warning("Failed to parse LLM response as memory list: %r", raw[:200])
            return []

    def merge_memories(self, existing: str, new: str) -> str:
        """Merge two similar memories into one consolidated text."""
        prompt = (
            "You are merging two similar memory entries into one concise, consolidated memory.\n\n"
            f"Existing memory:\n{existing}\n\n"
            f"New memory:\n{new}\n\n"
            "Write a single consolidated memory (1-3 sentences) that captures all information from both. "
            "Return ONLY the merged text, no explanation."
        )
        try:
            return self._llm.complete_prompt(prompt, timeout=60).strip()
        except LLMError:
            logger.exception("Memory merge failed (provider=%s)", self._llm.name)
            return f"{existing}\n\n{new}"
