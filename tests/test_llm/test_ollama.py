from __future__ import annotations

import httpx
import pytest

from rag_core.llm import LLMError
from rag_core.llm.ollama import OllamaProvider


def _make_transport(handler):
    return httpx.MockTransport(handler)


def test_ollama_complete_builds_correct_payload(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        import json as _json
        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": "ok"}})

    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, json, timeout: httpx.Client(transport=_make_transport(handler)).post(
            url, json=json, timeout=timeout
        ),
    )

    llm = OllamaProvider(base_url="http://localhost:11434", model="qwen2.5:7b")
    result = llm.complete(
        [{"role": "user", "content": "hi"}],
        json_mode=True,
        max_tokens=64,
        temperature=0.2,
    )

    assert result == "ok"
    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["body"]["model"] == "qwen2.5:7b"
    assert captured["body"]["format"] == "json"
    assert captured["body"]["options"]["temperature"] == 0.2
    assert captured["body"]["options"]["num_predict"] == 64
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]


def test_ollama_complete_prompt_adds_system(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": "x"}})

    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, json, timeout: httpx.Client(transport=_make_transport(handler)).post(
            url, json=json, timeout=timeout
        ),
    )

    llm = OllamaProvider(base_url="http://localhost:11434", model="m")
    llm.complete_prompt("hello", system="you are helpful")

    assert captured["body"]["messages"] == [
        {"role": "system", "content": "you are helpful"},
        {"role": "user", "content": "hello"},
    ]


def test_ollama_raises_on_http_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, json, timeout: httpx.Client(transport=_make_transport(handler)).post(
            url, json=json, timeout=timeout
        ),
    )

    llm = OllamaProvider(base_url="http://localhost:11434", model="m")
    with pytest.raises(LLMError, match="ollama request failed"):
        llm.complete([{"role": "user", "content": "x"}])


def test_ollama_raises_on_malformed_response(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, json, timeout: httpx.Client(transport=_make_transport(handler)).post(
            url, json=json, timeout=timeout
        ),
    )

    llm = OllamaProvider(base_url="http://localhost:11434", model="m")
    with pytest.raises(LLMError, match="malformed payload"):
        llm.complete([{"role": "user", "content": "x"}])
