from __future__ import annotations

import logging

import httpx

from rag_core.llm.base import LLMError, Message

logger = logging.getLogger("mnemos.llm.ollama")


class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
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
        payload: dict = {
            "model": self.model,
            "messages": [dict(m) for m in messages],
            "stream": False,
            "options": {"temperature": temperature},
        }
        if json_mode:
            payload["format"] = "json"
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens

        try:
            response = httpx.post(
                f"{self._base_url}/api/chat",
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            logger.exception("Ollama call failed")
            raise LLMError(f"ollama request failed: {exc}") from exc

        try:
            return data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LLMError(f"ollama returned malformed payload: {data!r}") from exc

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
