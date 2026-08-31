from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from core.llm import LLMConfig, LLMError, make_llm_provider


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


# ---------------------------------------------------------------------------
# Ollama auto-detection: reachable is not the same as usable
# ---------------------------------------------------------------------------


def _fake_get(responses):
    """Build an httpx.get stub: {url: (status, json) or Exception}."""
    import httpx

    def _get(url, timeout=None):
        key = url.replace("/api/tags", "")
        outcome = responses.get(key, httpx.ConnectError("refused"))
        if isinstance(outcome, Exception):
            raise outcome
        status, payload = outcome
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = payload
        return resp

    return _get


def test_autodetect_skips_a_reachable_but_empty_ollama(monkeypatch):
    """The bundled container answers 200 with no models and is probed first.
    Picking it sends every LLM call to a 404 — silently, since each feature
    swallows its own error. Prefer an instance that actually has a model."""
    import httpx
    from core.llm import factory

    monkeypatch.setattr(httpx, "get", _fake_get({
        "http://ollama:11434": (200, {"models": []}),
        "http://host.docker.internal:11434": (200, {"models": [{"name": "llama3.1"}]}),
    }))
    assert factory._autodetect_ollama_url() == "http://host.docker.internal:11434"


def test_autodetect_prefers_the_first_url_that_has_models(monkeypatch):
    import httpx
    from core.llm import factory

    monkeypatch.setattr(httpx, "get", _fake_get({
        "http://ollama:11434": (200, {"models": [{"name": "llama3.1"}]}),
        "http://host.docker.internal:11434": (200, {"models": [{"name": "other"}]}),
    }))
    assert factory._autodetect_ollama_url() == "http://ollama:11434"


def test_autodetect_falls_back_to_a_reachable_empty_instance(monkeypatch):
    """Nothing has models anywhere: still return a deterministic URL to debug."""
    import httpx
    from core.llm import factory

    monkeypatch.setattr(httpx, "get", _fake_get({
        "http://localhost:11434": (200, {"models": []}),
    }))
    assert factory._autodetect_ollama_url() == "http://localhost:11434"


def test_autodetect_falls_back_to_the_bundled_url_when_nothing_answers(monkeypatch):
    import httpx
    from core.llm import factory

    monkeypatch.setattr(httpx, "get", _fake_get({}))
    assert factory._autodetect_ollama_url() == "http://ollama:11434"


def test_autodetect_tolerates_a_malformed_body(monkeypatch):
    import httpx
    from core.llm import factory

    monkeypatch.setattr(httpx, "get", _fake_get({
        "http://ollama:11434": (200, None),
        "http://host.docker.internal:11434": (200, {"models": [{"name": "llama3.1"}]}),
    }))
    assert factory._autodetect_ollama_url() == "http://host.docker.internal:11434"
