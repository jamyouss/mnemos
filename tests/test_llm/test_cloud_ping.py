"""ping() of the SDK-backed providers: a model listing, never a completion.

The SDK client is mocked: its HTTP layer changes across SDK versions
(anthropic moved to httpx2), the call the provider makes does not.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.llm import LLMError
from core.llm.anthropic import AnthropicProvider
from core.llm.openai import OpenAIProvider

KEY = "sk-SENTINEL-key-0000"


class _FakeAuthError(Exception):
    status_code = 401


def _with_client(provider):
    provider._client = MagicMock()
    return provider, provider._client


def _openai():
    return _with_client(OpenAIProvider(api_key=KEY, model="gpt-4o-mini", base_url="http://llm.test/v1"))


def _anthropic():
    return _with_client(AnthropicProvider(api_key=KEY, model="claude-haiku-4-5"))


def test_openai_ping_lists_models_with_a_short_timeout():
    provider, client = _openai()
    provider.ping(timeout=2.0)
    client.with_options.assert_called_once_with(timeout=2.0, max_retries=0)
    client.with_options.return_value.models.list.assert_called_once_with()
    client.chat.completions.create.assert_not_called()


def test_anthropic_ping_lists_models_with_a_short_timeout():
    provider, client = _anthropic()
    provider.ping()
    client.with_options.assert_called_once_with(timeout=3.0, max_retries=0)
    client.with_options.return_value.models.list.assert_called_once_with(limit=1)
    client.messages.create.assert_not_called()


@pytest.mark.parametrize("make", [_openai, _anthropic])
def test_ping_error_does_not_echo_the_key(make):
    """OpenAI's 401 body quotes the key; the error must carry only the status."""
    provider, client = make()
    client.with_options.return_value.models.list.side_effect = _FakeAuthError(
        f"Incorrect API key provided: {KEY}"
    )

    with pytest.raises(LLMError) as exc:
        provider.ping()

    assert "_FakeAuthError (HTTP 401)" in str(exc.value)
    assert "SENTINEL" not in str(exc.value)
