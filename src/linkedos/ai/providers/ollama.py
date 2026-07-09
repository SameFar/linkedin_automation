"""The Ollama provider — local embeddings, and cheap local completions if you want them.

Ollama runs on the machine, so the failure modes are different from a cloud API: the
common one is "the daemon is not running", which is a `ConnectError` and is *not* worth
retrying three times. We retry read timeouts and 5xx (a model still loading into memory
returns those), and fail fast on a refused connection.

Embeddings are the reason this provider exists. `nomic-embed-text` is a 768-dimension
model that runs comfortably on a laptop and never sends the user's drafts anywhere.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from linkedos.ai.providers.base import LLMResponse, Message, Vector
from linkedos.core.config import get_settings
from linkedos.core.errors import ProviderError
from linkedos.core.logging import get_logger

logger = get_logger(__name__)

MAX_ATTEMPTS = 3
DEFAULT_EMBED_MODEL = "nomic-embed-text"


class _TransientOllamaError(Exception):
    """A failure that a second attempt might survive."""


def _log_retry(state: RetryCallState) -> None:
    logger.warning("ollama call failed, retrying attempt=%d/%d", state.attempt_number, MAX_ATTEMPTS)


class OllamaProvider:
    """Embeddings (and optional completions) against a local Ollama server."""

    name = "ollama"

    def __init__(self, base_url: str, timeout_s: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    @classmethod
    def from_settings(cls) -> OllamaProvider:
        settings = get_settings()
        return cls(base_url=settings.ollama_base_url, timeout_s=settings.ollama_timeout_s)

    def embed(self, texts: Sequence[str], *, model: str = DEFAULT_EMBED_MODEL) -> list[Vector]:
        if not texts:
            return []

        logger.info("ollama embed model=%s count=%d", model, len(texts))
        payload = self._post("/api/embed", {"model": model, "input": list(texts)})

        raw = payload.get("embeddings")
        if not isinstance(raw, list) or len(raw) != len(texts):
            raise ProviderError(
                f"ollama returned {len(raw) if isinstance(raw, list) else 'no'} "
                f"embeddings for {len(texts)} input(s)"
            )

        vectors: list[Vector] = [[float(value) for value in vector] for vector in raw]
        logger.info("ollama embed ok model=%s dim=%d", model, len(vectors[0]) if vectors else 0)
        return vectors

    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        system: str | None = None,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        """Single-shot generation. Ollama's `/api/generate` takes a flat prompt."""
        prompt = "\n\n".join(f"{message.role}: {message.content}" for message in messages)
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        if system is not None:
            body["system"] = system

        logger.info("ollama complete model=%s max_tokens=%d", model, max_tokens)
        payload = self._post("/api/generate", body)

        text = str(payload.get("response", "")).strip()
        if not text:
            raise ProviderError(f"ollama returned no text (model={model})")

        return LLMResponse(
            text=text,
            input_tokens=int(payload.get("prompt_eval_count", 0)),
            output_tokens=int(payload.get("eval_count", 0)),
            model=str(payload.get("model", model)),
        )

    @retry(
        retry=retry_if_exception_type(_TransientOllamaError),
        stop=stop_after_attempt(MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        before_sleep=_log_retry,
        reraise=True,
    )
    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """The single outbound HTTP call. Everything retryable wraps exactly this."""
        try:
            with httpx.Client(base_url=self._base_url, timeout=self._timeout_s) as client:
                response = client.post(path, json=body)
                if response.status_code >= 500:
                    raise _TransientOllamaError(f"ollama {path} returned {response.status_code}")
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
                return payload
        except httpx.ConnectError as exc:
            # The daemon is not listening. Backoff will not start it.
            raise ProviderError(f"cannot reach ollama at {self._base_url}") from exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise _TransientOllamaError(str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"ollama {path} rejected the request (status={exc.response.status_code})"
            ) from exc
        except ValueError as exc:
            raise ProviderError(f"ollama {path} returned malformed JSON") from exc
