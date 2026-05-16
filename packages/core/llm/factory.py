from __future__ import annotations

from dataclasses import dataclass

from core.llm.base import LLMError, LLMProvider


@dataclass(frozen=True)
class LLMConfig:
    """Provider-agnostic LLM configuration.

    Populate from environment / Settings via make_llm_provider().
    """

    provider: str          # "ollama" | "anthropic" | "openai"
    model: str
    api_key: str = ""
    base_url: str = ""     # for ollama: http://localhost:11434
                           # for openai: any OpenAI-compatible endpoint (vLLM, Groq, etc.)
                           # for anthropic: optional override (mock/proxy)


def make_llm_provider(config: LLMConfig) -> LLMProvider:
    provider = (config.provider or "").strip().lower()

    if provider == "ollama":
        from core.llm.ollama import OllamaProvider

        base_url = config.base_url or "http://localhost:11434"
        return OllamaProvider(base_url=base_url, model=config.model)

    if provider == "anthropic":
        from core.llm.anthropic import AnthropicProvider

        return AnthropicProvider(
            api_key=config.api_key,
            model=config.model,
            base_url=config.base_url or None,
        )

    if provider == "openai":
        from core.llm.openai import OpenAIProvider

        return OpenAIProvider(
            api_key=config.api_key,
            model=config.model,
            base_url=config.base_url or None,
        )

    raise LLMError(
        f"Unknown MNEMOS_LLM_PROVIDER: {config.provider!r}. "
        "Supported: 'ollama', 'anthropic', 'openai'."
    )
