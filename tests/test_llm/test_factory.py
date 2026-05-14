from __future__ import annotations

import pytest

from rag_core.llm import LLMConfig, LLMError, make_llm_provider


def test_factory_ollama_default_base_url():
    llm = make_llm_provider(LLMConfig(provider="ollama", model="llama3.1:8b"))
    assert llm.name == "ollama"
    assert llm.model == "llama3.1:8b"


def test_factory_ollama_custom_base_url():
    llm = make_llm_provider(
        LLMConfig(
            provider="ollama",
            model="qwen2.5:7b",
            base_url="http://my-ollama:9000",
        )
    )
    assert llm.name == "ollama"
    assert llm.model == "qwen2.5:7b"


def test_factory_rejects_unknown_provider():
    with pytest.raises(LLMError, match="Unknown MNEMOS_LLM_PROVIDER"):
        make_llm_provider(LLMConfig(provider="wat", model="x"))


def test_factory_anthropic_requires_api_key():
    pytest.importorskip("anthropic")
    with pytest.raises(LLMError, match="MNEMOS_LLM_API_KEY"):
        make_llm_provider(
            LLMConfig(provider="anthropic", model="claude-3-5-sonnet-latest", api_key="")
        )


def test_factory_is_case_insensitive():
    llm = make_llm_provider(LLMConfig(provider="OLLAMA", model="llama3.1:8b"))
    assert llm.name == "ollama"


def test_factory_strips_whitespace_in_provider_name():
    llm = make_llm_provider(LLMConfig(provider="  ollama  ", model="llama3.1:8b"))
    assert llm.name == "ollama"
