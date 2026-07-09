"""The chokepoint. Every model call in the app goes through here, and every one is billed.

`AIClient` does four things and delegates everything else:

1. **Routes** a `Tier` to a concrete model id, so no caller hardcodes `claude-haiku-4-5`.
2. **Times** the call.
3. **Writes an `ai_calls` ledger row** — always, including for zero-cost local embeddings.
4. Returns the response *together with* what it cost, so a service can report the price
   of a run without going back to the database to guess which rows were its own.

Retry and timeout live one layer down, in the providers: CLAUDE.md puts them at the
chokepoint per *integration*, and stacking a second tenacity loop here would multiply
attempts. What lives here is the accounting no provider should know about.

The ledger write opens its own short transaction and commits immediately. Metering must
not depend on the caller remembering to commit, and a service must never hold a write
transaction open across a multi-second network call.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from linkedos.ai.pricing import Tier, cost_usd, model_for_tier
from linkedos.ai.providers.base import LLMProvider, LLMResponse, Message, Vector
from linkedos.core.config import get_settings
from linkedos.core.logging import get_logger
from linkedos.db.models import AiCall
from linkedos.db.repo import AiCallRepo
from linkedos.db.session import get_session

logger = get_logger(__name__)

DEFAULT_EMBED_MODEL = "nomic-embed-text"


@dataclass(frozen=True, slots=True)
class MeteredResponse:
    """An `LLMResponse` and the ledger row it produced."""

    response: LLMResponse
    call_id: int
    cost_usd: float
    latency_ms: int

    @property
    def text(self) -> str:
        return self.response.text


@dataclass(frozen=True, slots=True)
class MeteredVectors:
    """Embeddings and the ledger row they produced."""

    vectors: list[Vector]
    model: str
    call_id: int
    cost_usd: float
    latency_ms: int


class AIClient:
    """The only object services use to reach a model.

    Args:
        completion_provider: Handles `complete`. Claude in production.
        embedding_provider: Handles `embed`. Ollama in production. Defaults to
            `completion_provider`, which is what the fake provider wants — it does both.
    """

    def __init__(
        self,
        completion_provider: LLMProvider,
        embedding_provider: LLMProvider | None = None,
        *,
        embed_model: str = DEFAULT_EMBED_MODEL,
    ) -> None:
        self._completion = completion_provider
        self._embedding = embedding_provider or completion_provider
        self._embed_model = embed_model

    @property
    def embed_model(self) -> str:
        """The embedding model name. Callers persist this alongside every vector."""
        return self._embed_model

    def complete(
        self,
        messages: Sequence[Message],
        *,
        tier: Tier,
        purpose: str,
        system: str | None = None,
        max_tokens: int = 2048,
        prompt_version: str | None = None,
    ) -> MeteredResponse:
        """Generate a completion and bill it."""
        model = model_for_tier(tier)

        started = time.perf_counter()
        response = self._completion.complete(
            messages, model=model, system=system, max_tokens=max_tokens
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        call_id, cost = self._record(
            provider=self._completion.name,
            model=response.model,
            purpose=purpose,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_ms=latency_ms,
            prompt_version=prompt_version,
        )
        return MeteredResponse(
            response=response, call_id=call_id, cost_usd=cost, latency_ms=latency_ms
        )

    def embed(self, texts: Sequence[str], *, purpose: str) -> MeteredVectors:
        """Embed `texts` and bill it. Local embeddings cost $0 but still get a row."""
        if not texts:
            raise ValueError("embed() requires at least one text")

        started = time.perf_counter()
        vectors = self._embedding.embed(texts, model=self._embed_model)
        latency_ms = int((time.perf_counter() - started) * 1000)

        # Ollama's embed endpoint reports no token usage; charge the ledger a
        # character-derived estimate so the row is honest about volume, not just count.
        input_tokens = sum(max(1, len(text) // 4) for text in texts)

        call_id, cost = self._record(
            provider=self._embedding.name,
            model=self._embed_model,
            purpose=purpose,
            input_tokens=input_tokens,
            output_tokens=0,
            latency_ms=latency_ms,
            prompt_version=None,
        )
        return MeteredVectors(
            vectors=vectors,
            model=self._embed_model,
            call_id=call_id,
            cost_usd=cost,
            latency_ms=latency_ms,
        )

    def _record(
        self,
        *,
        provider: str,
        model: str,
        purpose: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        prompt_version: str | None,
    ) -> tuple[int, float]:
        cost = cost_usd(model, input_tokens, output_tokens)

        with get_session() as session:
            call = AiCallRepo(session).add(
                AiCall(
                    provider=provider,
                    model=model,
                    purpose=purpose,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost,
                    latency_ms=latency_ms,
                    prompt_version=prompt_version,
                )
            )
            call_id = call.id

        logger.info(
            "ai call recorded id=%d provider=%s model=%s purpose=%s "
            "in=%d out=%d cost=$%.6f latency=%dms",
            call_id,
            provider,
            model,
            purpose,
            input_tokens,
            output_tokens,
            cost,
            latency_ms,
        )
        return call_id, cost


def get_client() -> AIClient:
    """Build the production client: Claude for completions, Ollama for embeddings.

    The providers are imported lazily so that `linkedos status` and the offline test
    suite never construct an Anthropic client, and never need an API key to exist.
    """
    from linkedos.ai.providers.claude import ClaudeProvider
    from linkedos.ai.providers.ollama import OllamaProvider

    settings = get_settings()
    return AIClient(
        completion_provider=ClaudeProvider.from_settings(),
        embedding_provider=OllamaProvider.from_settings(),
        embed_model=settings.embed_model,
    )
