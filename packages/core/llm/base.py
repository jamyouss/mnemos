from __future__ import annotations

from typing import Literal, Protocol, TypedDict, runtime_checkable


Role = Literal["system", "user", "assistant"]


class Message(TypedDict):
    role: Role
    content: str


class LLMError(RuntimeError):
    """Raised when an LLM provider fails (network, parse, auth)."""


@runtime_checkable
class LLMProvider(Protocol):
    """Generic chat-completion interface used by Mnemos LLM consumers.

    Implementations must support:
    - basic chat completion with system + user messages
    - optional JSON-mode (provider does its best to return strict JSON)
    - graceful failure (raise LLMError, never return None)
    """

    name: str
    model: str

    def complete(
        self,
        messages: list[Message],
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float = 0.4,
        timeout: float = 60.0,
    ) -> str:
        """Run a chat completion; return the assistant's text response.

        Raises LLMError on any failure.
        """
        ...

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
        """Convenience wrapper around complete() for a single user prompt."""
        ...
