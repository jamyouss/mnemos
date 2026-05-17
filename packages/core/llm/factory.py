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

        base_url = config.base_url or _autodetect_ollama_url()
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


# In-container Ollama is reachable via three common URLs depending on how
# the user set up Ollama: bundled service, host-installed (Mac/Win), or
# host-installed (Linux). We probe them in order and pick the first one
# that responds. Nothing is fatal: if all three fail we fall back to the
# bundled service URL and let the actual LLM call surface the error.
_OLLAMA_URL_CANDIDATES = (
    "http://ollama:11434",                  # docker compose --profile llm
    "http://host.docker.internal:11434",    # host-installed Ollama on Mac/Win
    "http://localhost:11434",               # running CLI on the host directly
)


def _autodetect_ollama_url() -> str:
    """Probe the common Ollama URLs and return the first reachable one.

    Falls back to the bundled-service URL when nothing responds — that way
    the caller still gets a deterministic, documented base_url to debug.
    """
    import logging
    import httpx

    logger = logging.getLogger("mnemos.llm")
    for url in _OLLAMA_URL_CANDIDATES:
        try:
            r = httpx.get(f"{url}/api/tags", timeout=1.5)
            if r.status_code == 200:
                logger.info("Auto-detected Ollama at %s", url)
                return url
        except httpx.HTTPError:
            continue
    logger.warning(
        "No reachable Ollama at any of %s — defaulting to %s",
        ", ".join(_OLLAMA_URL_CANDIDATES),
        _OLLAMA_URL_CANDIDATES[0],
    )
    return _OLLAMA_URL_CANDIDATES[0]
