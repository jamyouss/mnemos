from __future__ import annotations

import logging

from rag_core.llm.base import LLMError, Message

logger = logging.getLogger("mnemos.llm.anthropic")


class AnthropicProvider:
    """Anthropic API provider (claude.ai).

    Supports prompt caching (5-min TTL) — well-suited for Contextual Retrieval
    where the same document is re-used as context for many chunk extractions.
    """

    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
    ) -> None:
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:
            raise LLMError(
                "anthropic SDK is required for the 'anthropic' provider. "
                "Install with: pip install anthropic"
            ) from exc

        if not api_key:
            raise LLMError("MNEMOS_LLM_API_KEY is required for the 'anthropic' provider")

        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key, base_url=base_url) if base_url else Anthropic(api_key=api_key)
        self.model = model

    def complete(
        self,
        messages: list[Message],
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float = 0.4,
        timeout: float = 60.0,
    ) -> str:
        system_chunks: list[str] = []
        clean_messages: list[dict] = []
        for m in messages:
            if m["role"] == "system":
                system_chunks.append(m["content"])
            else:
                clean_messages.append({"role": m["role"], "content": m["content"]})

        system_prompt = "\n\n".join(system_chunks) if system_chunks else None
        if json_mode and system_prompt is None:
            system_prompt = "Respond with strict JSON only, no markdown fences and no explanation."
        elif json_mode:
            system_prompt = system_prompt + "\n\nRespond with strict JSON only, no markdown fences."

        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens or 1024,
                temperature=temperature,
                system=system_prompt,
                messages=clean_messages,
                timeout=timeout,
            )
        except Exception as exc:  # anthropic SDK raises a few different types
            logger.exception("Anthropic call failed")
            raise LLMError(f"anthropic request failed: {exc}") from exc

        try:
            blocks = response.content
            return "".join(block.text for block in blocks if getattr(block, "type", "") == "text")
        except Exception as exc:
            raise LLMError(f"anthropic returned malformed payload: {response!r}") from exc

    def complete_prompt(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float = 0.4,
        timeout: float = 60.0,
    ) -> str:
        messages: list[Message] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.complete(
            messages,
            json_mode=json_mode,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )
