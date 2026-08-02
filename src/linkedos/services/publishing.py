"""Publishing: the bridge between a scheduled post and LinkedIn.

This is the service the M3 scheduler calls. It does the smallest possible thing on top of
two pieces that already exist — the `workflow` state machine and a `LinkedInPublisher` —
and owns none of their logic:

1. take a post the workflow has already moved to `scheduled`,
2. hand its text to the publisher,
3. and record the outcome back through the workflow: `mark_published` with the returned
   URN, or `mark_failed` with the error.

A publish is the one irreversible act in the whole system, so the ordering is strict:
LinkedIn accepts the post *first*, and only then does the row become `published`. If the
call fails the post goes to `failed`, never silently back to a state that would publish it
twice. Every transition is audited by the workflow layer, so "who published this, and
when" always has an answer.

Nothing here holds a database transaction open across the network call. `publish_due`
reads the due posts in one short transaction, closes it, and then publishes them one at a
time — each `mark_published` / `mark_failed` opens its own transaction. A slow LinkedIn
call can never block the daemon's other writers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from linkedos.core.errors import IntegrationError, WorkflowError
from linkedos.core.logging import get_logger
from linkedos.db.models import Actor, PostStatus
from linkedos.db.repo import PostRepo
from linkedos.db.session import get_session
from linkedos.integrations.linkedin import (
    LinkedInPublisher,
    Visibility,
    get_publisher,
)
from linkedos.services import workflow
from linkedos.services.workflow import PostView

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PublishOutcome:
    """The result of trying to publish one post.

    Exactly one of `view` (on success) or `error` (on failure) is set. `ok` is the thing
    callers branch on; the other field carries the detail for the log or the UI.
    """

    post_id: int
    ok: bool
    view: PostView | None = None
    error: str | None = None

    @property
    def urn(self) -> str | None:
        return self.view.linkedin_urn if self.view is not None else None


@dataclass(frozen=True, slots=True)
class PublishRun:
    """The result of one `publish_due` sweep: what went live, and what failed."""

    published: list[PublishOutcome] = field(default_factory=list)
    failed: list[PublishOutcome] = field(default_factory=list)

    @property
    def attempted(self) -> int:
        return len(self.published) + len(self.failed)

    @property
    def all_succeeded(self) -> bool:
        return not self.failed


def publish_post(
    post_id: int,
    *,
    publisher: LinkedInPublisher | None = None,
    visibility: Visibility = Visibility.PUBLIC,
    actor: Actor = Actor.SCHEDULER,
) -> PublishOutcome:
    """Publish one scheduled post, then record the outcome through the workflow.

    The post must be in `scheduled`; publishing anything else is a bug in the caller, not
    a runtime condition, so it raises rather than returning a failed outcome.

    A publisher failure is *not* raised — it is caught, the post is moved to `failed`, and
    a failed `PublishOutcome` is returned. That lets `publish_due` keep draining the queue
    when one post cannot go out, instead of the whole sweep dying on the first bad post.

    Raises:
        WorkflowError: if the post does not exist or is not `scheduled`.
    """
    resolved = publisher or get_publisher()

    with get_session() as session:
        post = PostRepo(session).get(post_id)
        if post is None:
            raise WorkflowError(f"no post with id {post_id}")
        if post.status is not PostStatus.SCHEDULED:
            raise WorkflowError(
                f"only scheduled posts can be published; post {post_id} is {post.status.value}"
            )
        text = post.content

    try:
        result = resolved.publish_post(text, visibility=visibility)
    except IntegrationError as exc:
        error = str(exc)
        logger.warning("publish failed post=%d: %s", post_id, error)
        view = workflow.mark_failed(post_id, error=error, actor=actor)
        return PublishOutcome(post_id=post_id, ok=False, view=view, error=error)

    view = workflow.mark_published(post_id, linkedin_urn=result.urn, actor=actor)
    logger.info("published post=%d urn=%s", post_id, result.urn)
    return PublishOutcome(post_id=post_id, ok=True, view=view)


def publish_due(
    *,
    now: datetime | None = None,
    publisher: LinkedInPublisher | None = None,
    visibility: Visibility = Visibility.PUBLIC,
    actor: Actor = Actor.SCHEDULER,
    limit: int = 50,
) -> PublishRun:
    """Publish every scheduled post whose time has come, oldest slot first.

    The daemon's job body. Reads the due posts in one short transaction, then publishes
    them one at a time so a slow call never holds a write lock. One post failing does not
    stop the rest — its failure lands in `PublishRun.failed` and the sweep continues.
    """
    moment = now if now is not None else datetime.now(UTC)
    resolved = publisher or get_publisher()

    with get_session() as session:
        due_ids = [post.id for post in PostRepo(session).list_due(moment, limit)]

    run = PublishRun()
    for post_id in due_ids:
        outcome = publish_post(post_id, publisher=resolved, visibility=visibility, actor=actor)
        (run.published if outcome.ok else run.failed).append(outcome)

    if run.attempted:
        logger.info("publish sweep: %d published, %d failed", len(run.published), len(run.failed))
    return run
