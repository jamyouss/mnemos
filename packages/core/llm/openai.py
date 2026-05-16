from __future__ import annotations

import logging

from core.llm.base import LLMError, Message

logger = logging.getLogger("mnemos.llm.openai")


class OpenAIProvider:
    """OpenAI-compatible chat completion provider.

    Works with:
    - OpenAI API (chatgpt.com)
    - Any OpenAI-compatible endpoint: vLLM, LM Studio, LocalAI, Together, OpenRouter, Groq, etc.
      via the `base_url` parameter.
    """

    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
    ) -> None:
        try:
            import openai  # noqa: F401
        except ImportError as exc:
            raise LLMError(
                "openai SDK is required for the 'openai' provider. "
                "Install with: pip install openai"
            ) from exc

        from openai import OpenAI

        kwargs: dict = {"api_key": api_key or "not-needed-for-local"}
        if base_url:
            kwargs["base_url"] = base_url

        self._client = OpenAI(**kwargs)
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
        kwargs: dict = {
            "model": self.model,
            "messages": [dict(m) for m in messages],
            "temperature": temperature,
            "timeout": timeout,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            logger.exception("OpenAI call failed")
            raise LLMError(f"openai-compatible request failed: {exc}") from exc

        try:
            return response.choices[0].message.content or ""
        except (AttributeError, IndexError, TypeError) as exc:
            raise LLMError(f"openai returned malformed payload: {response!r}") from exc

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
