"""The deterministic in-memory provider every offline test uses. Never touches a socket.

Two properties matter, and both are load-bearing for the test suite:

* **Deterministic.** The same input produces byte-identical output on every machine and
  every run. `hash()` is salted per-process in Python, so tokens are hashed with
  `blake2b` instead.
* **Semantically ordered embeddings.** A real embedding model puts "python asyncio" near
  "asyncio tips" and far from "sourdough". A random vector would satisfy the type
  signature while making `memory.most_similar` untestable. So `embed` does feature
  hashing over word tokens: shared words mean shared dimensions mean high cosine
  similarity. It is a bag of words, not an understanding of language, and that is
  exactly enough to prove the retrieval plumbing works.

Every call is recorded on `.calls` so tests can assert on what the code under test asked
for, not just what it did with the answer.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from hashlib import blake2b

from linkedos.ai.providers.base import LLMResponse, Message, Vector

#: Small enough to eyeball in a failing assertion, large enough to avoid collisions.
FAKE_EMBED_DIM = 64
FAKE_EMBED_MODEL = "fake-embed"

_WORD = re.compile(r"[a-z0-9']+")


@dataclass(frozen=True, slots=True)
class RecordedCall:
    """One provider invocation, captured for assertions."""

    kind: str  # "complete" | "embed"
    model: str
    system: str | None = None
    messages: tuple[Message, ...] = ()
    texts: tuple[str, ...] = ()


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _bucket(token: str) -> int:
    """Stable across processes and machines. `hash()` is not — PYTHONHASHSEED salts it."""
    digest = blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % FAKE_EMBED_DIM


def _estimate_tokens(text: str) -> int:
    """Roughly four characters per token — close enough for a cost-math fixture."""
    return max(1, len(text) // 4)


@dataclass
class FakeProvider:
    """A canned, deterministic `LLMProvider`.

    Args:
        completion_text: Overrides the generated draft entirely. When `None`, `complete`
            returns a stable template that echoes the system prompt length and the last
            user turn, so tests can assert the prompt actually reached the provider.
    """

    name: str = "fake"
    completion_text: str | None = None
    calls: list[RecordedCall] = field(default_factory=list)

    @property
    def complete_calls(self) -> list[RecordedCall]:
        return [call for call in self.calls if call.kind == "complete"]

    @property
    def embed_calls(self) -> list[RecordedCall]:
        return [call for call in self.calls if call.kind == "embed"]

    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        system: str | None = None,
        max_tokens: int = 2048,  # noqa: ARG002 — required by LLMProvider; nothing to truncate
    ) -> LLMResponse:
        self.calls.append(
            RecordedCall(kind="complete", model=model, system=system, messages=tuple(messages))
        )

        last_user = next(
            (message.content for message in reversed(messages) if message.role == "user"),
            "",
        )
        text = self.completion_text or (
            f"FAKE DRAFT [{model}]\n\n"
            f"Responding to: {last_user.strip()[:200]}\n\n"
            f"(system prompt was {len(system or '')} chars)"
        )

        prompt_chars = len(system or "") + sum(len(message.content) for message in messages)
        return LLMResponse(
            text=text,
            input_tokens=_estimate_tokens("x" * prompt_chars),
            output_tokens=_estimate_tokens(text),
            model=model,
        )

    def embed(self, texts: Sequence[str], *, model: str = FAKE_EMBED_MODEL) -> list[Vector]:
        self.calls.append(RecordedCall(kind="embed", model=model, texts=tuple(texts)))
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> Vector:
        """Hash each word into a bucket, count, then L2-normalise."""
        vector = [0.0] * FAKE_EMBED_DIM
        for token in _tokens(text):
            vector[_bucket(token)] += 1.0

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            # An empty or punctuation-only string. Return a fixed unit vector rather
            # than a zero vector, so cosine similarity stays defined downstream.
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]
