"""The LinkedIn publishing chokepoint.

Every post this system sends to LinkedIn goes through a `LinkedInPublisher`. The
interface is defined here so the service and scheduler layers can depend on it without
knowing whether a real HTTP call or a test double sits behind it — exactly as the AI
layer depends on `LLMProvider` rather than on `anthropic`.

Two implementations live here today:

* `FakeLinkedInPublisher` — deterministic, offline, records every call. Every test uses
  it, and it is what makes the whole publish pipeline verifiable without a network or a
  LinkedIn app.
* `LiveLinkedInPublisher` — the real one, and deliberately a stub. The OAuth flow and the
  `POST /rest/posts` call are a later milestone; until then it refuses loudly rather than
  pretending to publish. `settings.publish_enabled` defaults off so the daemon never
  reaches it by accident.

When the real transport lands it goes *inside* `LiveLinkedInPublisher.publish_post` and
nowhere else: one chokepoint per integration, wrapped with an explicit timeout, tenacity
retry on transient errors only, and structured logging that never records the token.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from linkedos.core.config import get_settings
from linkedos.core.errors import IntegrationError
from linkedos.core.logging import get_logger

logger = get_logger(__name__)


class Visibility(StrEnum):
    """Who may see a published post. Maps to LinkedIn's `MemberNetworkVisibility`."""

    PUBLIC = "PUBLIC"
    CONNECTIONS = "CONNECTIONS"


@dataclass(frozen=True, slots=True)
class PublishResult:
    """What a successful publish returns: the durable handle on the live post.

    `urn` is LinkedIn's id for the post (`urn:li:share:...`), the only lasting reference
    to something now public. It is written onto `Post.linkedin_urn` so the row and the
    live post can never drift apart.
    """

    urn: str
    at: datetime
    visibility: Visibility


class LinkedInPublisher(Protocol):
    """The one operation the publish pipeline needs: put text on LinkedIn.

    Kept to a single method on purpose. Comments and reactions are now permitted (see
    CLAUDE.md) but are their own milestone; when they arrive they extend this protocol,
    they do not sneak in through `publish_post`.
    """

    def publish_post(
        self, text: str, *, visibility: Visibility = Visibility.PUBLIC
    ) -> PublishResult:
        """Publish `text` as the connected member. Raises `IntegrationError` on failure."""
        ...


@dataclass
class FakeLinkedInPublisher:
    """A deterministic, offline `LinkedInPublisher` for tests and dry runs.

    Records every call on `.calls`, so a test can assert on the text and visibility the
    pipeline handed over, not just on the resulting post status. Set `fail_with` to make
    the next publish raise, which is how the failure path (`scheduled -> failed`) is
    exercised without a flaky network.
    """

    urn_prefix: str = "urn:li:share:fake-"
    fail_with: str | None = None
    calls: list[tuple[str, Visibility]] = field(default_factory=list)

    def publish_post(
        self, text: str, *, visibility: Visibility = Visibility.PUBLIC
    ) -> PublishResult:
        self.calls.append((text, visibility))
        if self.fail_with is not None:
            raise IntegrationError(self.fail_with)
        # Deterministic-length id, unique per call — enough to stand in for a real URN
        # and to satisfy the `linkedin_urn` uniqueness constraint across many posts.
        urn = f"{self.urn_prefix}{uuid.uuid4().hex}"
        return PublishResult(urn=urn, at=datetime.now(UTC), visibility=visibility)


class LiveLinkedInPublisher:
    """The real publisher — not yet implemented.

    The HTTP call to `POST /rest/posts`, the OAuth token, and the author URN all belong
    inside `publish_post`. Until that milestone ships this refuses, so a misconfigured
    daemon fails a post loudly instead of silently dropping it.
    """

    def publish_post(
        self,
        text: str,  # noqa: ARG002 — signature matches the protocol; nothing to send yet
        *,
        visibility: Visibility = Visibility.PUBLIC,  # noqa: ARG002 — same
    ) -> PublishResult:
        raise IntegrationError(
            "the live LinkedIn publisher is not implemented yet; the OAuth flow and the "
            "POST /rest/posts call are a later milestone. Keep PUBLISH_ENABLED off, or "
            "inject a FakeLinkedInPublisher for testing."
        )


def get_publisher() -> LinkedInPublisher:
    """The production publisher. Returns the live stub until the real transport lands.

    Services never call this directly in tests — they take a `publisher` argument and are
    handed a `FakeLinkedInPublisher`. This exists so the daemon has a default, and so the
    one place that would swap in the real implementation is obvious.
    """
    _ = get_settings()  # touched so a broken configuration fails here, not mid-publish
    return LiveLinkedInPublisher()
