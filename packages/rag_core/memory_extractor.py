"""Extract structured memories from git commit diffs using Ollama."""
from __future__ import annotations

import json
import logging

import httpx

from rag_core.models import ExtractedMemory

logger = logging.getLogger("mnemos.extractor")

_MAX_DIFF_BYTES = 32_768

_SYSTEM_PROMPT = """You are a memory extraction assistant for a software development team.
Given a git commit message and diff, extract actionable memories worth remembering for future work.

Types of memories to extract:
- "decision": Architectural or design decisions made (e.g., "Chose flat API routes over nested")
- "pattern": Code patterns introduced or established (e.g., "All handlers follow middleware chain pattern")
- "convention": Naming or structural conventions (e.g., "Services use Create/Get/Update/Delete naming")
- "lesson": Bugs fixed or workarounds applied (e.g., "Qdrant scroll requires with_vectors=True for updates")

Rules:
- Return a JSON array of objects with keys: content, memory_type, project, tags
- project should be inferred from file paths in the diff (e.g., "moby", "trevio", "infra")
- If nothing worth remembering, return []
- Be concise: each memory should be 1-2 sentences
- Focus on WHY decisions were made, not WHAT code was written
- Do NOT extract trivial changes (typos, formatting, import ordering)

Return ONLY the JSON array, no markdown fences, no explanation."""

_USER_TEMPLATE = """## Commit Message
{commit_message}

## Diff
{diff}"""


class MemoryExtractor:
    def __init__(self, ollama_url: str, model: str) -> None:
        self._ollama_url = ollama_url.rstrip("/")
        self._model = model

    def extract(self, commit_message: str, diff: str) -> list[ExtractedMemory]:
        truncated_diff = diff[:_MAX_DIFF_BYTES]
        if len(diff) > _MAX_DIFF_BYTES:
            truncated_diff += "\n\n[... diff truncated ...]"

        user_content = _USER_TEMPLATE.format(
            commit_message=commit_message,
            diff=truncated_diff,
        )

        try:
            response = httpx.post(
                f"{self._ollama_url}/api/chat",
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    "stream": False,
                    "format": "json",
                },
                timeout=120,
            )
            response.raise_for_status()
        except Exception:
            logger.exception("Failed to call Ollama for memory extraction")
            return []

        try:
            raw = response.json()["message"]["content"]
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "memories" in parsed:
                parsed = parsed["memories"]
            if not isinstance(parsed, list):
                return []
            return [ExtractedMemory(**item) for item in parsed]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.warning("Failed to parse Ollama response as memory list")
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
            response = httpx.post(
                f"{self._ollama_url}/api/chat",
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
                timeout=60,
            )
            response.raise_for_status()
            return response.json()["message"]["content"].strip()
        except Exception:
            logger.exception("Failed to merge memories via Ollama")
            return f"{existing}\n\n{new}"
