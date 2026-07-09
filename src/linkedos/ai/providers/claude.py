"""The Claude provider — the one place `anthropic` is imported.

Every Claude call in this project goes through `ClaudeProvider.complete`, wrapped with
an explicit timeout, tenacity retry on transient errors only, and structured logging.

Retry policy: the Anthropic SDK retries by default. We turn that off (`max_retries=0`)
so exactly one layer owns backoff and the log line count matches the request count.
Transient means overloaded, rate-limited, timed out, or a connection failure. A 400 or a
401 is a bug or a bad key; retrying it just burns time.
"""

from __future__ import annotations

from collections.abc import Sequence

import anthropic
from anthropic.types import MessageParam
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from linkedos.ai.providers.base import LLMResponse, Message, Vector
from linkedos.core.config import get_settings
from linkedos.core.errors import ConfigError, ProviderError
from linkedos.core.logging import get_logger

logger = get_logger(__name__)

MAX_ATTEMPTS = 3

#: Failures worth trying again. Everything else propagates on the first attempt.
TRANSIENT_ERRORS = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,
    anthropic.RateLimitError,
)


def _log_retry(state: RetryCallState) -> None:
    exc = state.outcome.exception() if state.outcome else None
    logger.warning(
        "claude call failed, retrying attempt=%d/%d error=%s",
        state.attempt_number,
        MAX_ATTEMPTS,
        type(exc).__name__ if exc else "unknown",
    )


class ClaudeProvider:
    """Completions via the Anthropic Messages API.

    Does not embed: Anthropic ships no embedding endpoint, so `embed` raises rather
    than silently falling back to something the caller did not ask for.
    """

    name = "anthropic"

    def __init__(self, api_key: str, timeout_s: float) -> None:
        if not api_key:
            raise ConfigError("ANTHROPIC_API_KEY is not set")
        self._timeout_s = timeout_s
        # `max_retries=0`: tenacity owns retry, not the SDK. Two backoff layers would
        # multiply into 9 attempts for one logical call.
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=0)

    @classmethod
    def from_settings(cls) -> ClaudeProvider:
        settings = get_settings()
        return cls(
            api_key=settings.anthropic_api_key.get_secret_value(),
            timeout_s=settings.llm_timeout_s,
        )

    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        system: str | None = None,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        payload: list[MessageParam] = [
            {"role": message.role, "content": message.content} for message in messages
        ]
        logger.info(
            "claude complete model=%s messages=%d max_tokens=%d",
            model,
            len(payload),
            max_tokens,
        )
        response = self._create(payload, model=model, system=system, max_tokens=max_tokens)

        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise ProviderError(f"claude returned no text content (model={model})")

        logger.info(
            "claude complete ok model=%s in_tokens=%d out_tokens=%d stop=%s",
            response.model,
            response.usage.input_tokens,
            response.usage.output_tokens,
            response.stop_reason,
        )
        return LLMResponse(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
        )

    @retry(
        retry=retry_if_exception_type(TRANSIENT_ERRORS),
        stop=stop_after_attempt(MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        before_sleep=_log_retry,
        reraise=True,
    )
    def _create(
        self,
        payload: list[MessageParam],
        *,
        model: str,
        system: str | None,
        max_tokens: int,
    ) -> anthropic.types.Message:
        """The single outbound HTTP call. Everything retryable wraps exactly this."""
        try:
            if system is None:
                return self._client.messages.create(
                    model=model, max_tokens=max_tokens, messages=payload
                )
            return self._client.messages.create(
                model=model, max_tokens=max_tokens, system=system, messages=payload
            )
        except TRANSIENT_ERRORS:
            raise
        except anthropic.APIStatusError as exc:
            # Never log `exc` wholesale: the SDK renders request bodies, and the auth
            # header is not the only place a secret can hide.
            raise ProviderError(f"claude rejected the request (status={exc.status_code})") from exc
        except anthropic.AnthropicError as exc:
            raise ProviderError(f"claude call failed: {type(exc).__name__}") from exc

    def embed(self, texts: Sequence[str], *, model: str) -> list[Vector]:  # noqa: ARG002
        # Arguments unused but required: the signature satisfies `LLMProvider`.
        raise ProviderError("anthropic exposes no embedding endpoint; use the ollama provider")
