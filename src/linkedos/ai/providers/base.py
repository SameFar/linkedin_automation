"""The provider seam: one Protocol, three implementations, no leakage.

Everything above this module — `ai.client`, `ai.memory`, every service — talks to an
`LLMProvider` and never to `anthropic` or `httpx` directly. That is what lets the whole
test suite run offline against `providers.fake.FakeProvider`.

Providers are responsible for the mechanics of one external system: timeout, retry on
transient errors, structured logging, and turning a vendor response into `LLMResponse`.
They are *not* responsible for cost accounting or model routing — those belong to
`ai.client`, the single chokepoint every caller goes through.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

#: A single embedding, as plain floats. Serialised to float32 bytes by `ai.memory`.
Vector = list[float]

Role = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class Message:
    """One conversational turn. System prompts are passed separately, not as a turn."""

    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """What a completion returned, plus what it cost in tokens.

    `model` is the model the provider *actually* used, echoed back from the response
    rather than copied from the request — the ledger should record what ran.
    """

    text: str
    input_tokens: int
    output_tokens: int
    model: str


@runtime_checkable
class LLMProvider(Protocol):
    """The contract every provider satisfies.

    A provider that cannot do one half of this (Ollama has no Claude-grade completions;
    Anthropic exposes no embedding endpoint) raises `ProviderError` from that method
    rather than pretending. `ai.client` holds a separate provider for each capability.
    """

    #: Short, stable identifier written to the `ai_calls.provider` column.
    name: str

    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        system: str | None = None,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        """Generate a completion. Raises `ProviderError` on unrecoverable failure."""
        ...

    def embed(self, texts: Sequence[str], *, model: str) -> list[Vector]:
        """Embed `texts`, returning one vector per input, in order."""
        ...
