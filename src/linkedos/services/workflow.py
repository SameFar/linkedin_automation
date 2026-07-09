"""The post state machine. The only module in the system that changes `posts.status`.

That is not a style preference. A post reaching `published` is the one irreversible act
this software performs, and every path to it runs through `_transition` below, which
refuses illegal moves and writes an audit row in the same transaction as the change.
If status changes were scattered across services and UI callbacks, "how did this get
published?" would have no answer. Here it always has one.

The legal graph:

    draft ──approve──▶ approved ──schedule──▶ scheduled ──▶ published
      │                    │                                 └──▶ failed
      │                    └──revert──▶ draft
      └──reject──▶ rejected

`rejected`, `published`, and `failed` are terminal. Rejection is cheap to redo — write
another draft — so it does not need an undo, and an undo would blur the audit trail.

Milestone 2 exercises approve, reject, and revert. `schedule`, `mark_published`, and
`mark_failed` exist because the state machine is the wrong place to leave a gap, and
because the M3 scheduler must move posts through the same gate the UI does. Neither
this module nor anything it calls talks to LinkedIn.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from linkedos.core.errors import WorkflowError
from linkedos.core.logging import get_logger
from linkedos.db.models import Actor, AuditAction, AuditLog, EntityType, Post, PostStatus
from linkedos.db.repo import AuditRepo, PostRepo
from linkedos.db.session import get_session

logger = get_logger(__name__)

#: Which statuses a post may move to from each status. Absent target ⇒ illegal move.
TRANSITIONS: dict[PostStatus, frozenset[PostStatus]] = {
    PostStatus.DRAFT: frozenset({PostStatus.APPROVED, PostStatus.REJECTED}),
    PostStatus.APPROVED: frozenset({PostStatus.SCHEDULED, PostStatus.DRAFT}),
    PostStatus.SCHEDULED: frozenset({PostStatus.PUBLISHED, PostStatus.FAILED}),
    PostStatus.REJECTED: frozenset(),
    PostStatus.PUBLISHED: frozenset(),
    PostStatus.FAILED: frozenset(),
}

#: The audit action each transition records, keyed by `(from_status, to_status)`.
#: `approved → draft` is a revert, not a "draft"; the pair is what disambiguates it.
_ACTIONS: dict[tuple[PostStatus, PostStatus], AuditAction] = {
    (PostStatus.DRAFT, PostStatus.APPROVED): AuditAction.APPROVED,
    (PostStatus.DRAFT, PostStatus.REJECTED): AuditAction.REJECTED,
    (PostStatus.APPROVED, PostStatus.DRAFT): AuditAction.REVERTED_TO_DRAFT,
    (PostStatus.APPROVED, PostStatus.SCHEDULED): AuditAction.SCHEDULED,
    (PostStatus.SCHEDULED, PostStatus.PUBLISHED): AuditAction.PUBLISHED,
    (PostStatus.SCHEDULED, PostStatus.FAILED): AuditAction.FAILED,
}


@dataclass(frozen=True, slots=True)
class PostView:
    """A post as the layers above the database see it.

    A plain snapshot, not an ORM row: the UI and the CLI must not be able to mutate a
    post by assigning to an attribute, and must not depend on a live `Session`.
    """

    id: int
    content: str
    status: PostStatus
    topic: str
    variant_group_id: str
    prompt_version: str
    created_at: datetime
    updated_at: datetime
    scheduled_at: datetime | None
    published_at: datetime | None
    linkedin_urn: str | None


def to_view(post: Post) -> PostView:
    """Detach an ORM row into a `PostView`. Call before the session closes."""
    return PostView(
        id=post.id,
        content=post.content,
        status=post.status,
        topic=post.topic,
        variant_group_id=post.variant_group_id,
        prompt_version=post.prompt_version,
        created_at=post.created_at,
        updated_at=post.updated_at,
        scheduled_at=post.scheduled_at,
        published_at=post.published_at,
        linkedin_urn=post.linkedin_urn,
    )


def can_transition(from_status: PostStatus, to_status: PostStatus) -> bool:
    """Whether the state machine permits this move. Pure; no database access."""
    return to_status in TRANSITIONS[from_status]


def allowed_transitions(from_status: PostStatus) -> frozenset[PostStatus]:
    """Every status reachable in one step. Empty for terminal statuses."""
    return TRANSITIONS[from_status]


def record_audit(
    session: Session,
    *,
    actor: Actor,
    action: AuditAction,
    entity_id: int,
    detail: str = "",
    entity_type: EntityType = EntityType.POST,
) -> AuditLog:
    """Append one audit row. Takes the caller's session so it commits atomically."""
    return AuditRepo(session).add(
        AuditLog(
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
        )
    )


def _transition(
    post_id: int,
    to_status: PostStatus,
    *,
    actor: Actor,
    detail: str = "",
    edited_content: str | None = None,
    scheduled_at: datetime | None = None,
    published_at: datetime | None = None,
    linkedin_urn: str | None = None,
) -> PostView:
    """Move one post, audit it, and commit both together.

    Raises:
        WorkflowError: if the post does not exist, or the move is not in `TRANSITIONS`.
    """
    with get_session() as session:
        repo = PostRepo(session)
        post = repo.get(post_id)
        if post is None:
            raise WorkflowError(f"no post with id {post_id}")

        from_status = post.status
        if not can_transition(from_status, to_status):
            targets = sorted(status.value for status in TRANSITIONS[from_status])
            allowed = ", ".join(targets) if targets else "nothing — it is terminal"
            raise WorkflowError(
                f"illegal transition {from_status.value} -> {to_status.value} "
                f"for post {post_id}; allowed: {allowed}"
            )

        # An edit rides along inside the same transaction as the approval, so a post can
        # never be approved with text the reviewer did not see.
        if edited_content is not None and edited_content != post.content:
            record_audit(
                session,
                actor=actor,
                action=AuditAction.EDITED,
                entity_id=post_id,
                detail=f"content changed: {len(post.content)} -> {len(edited_content)} chars",
            )
            post.content = edited_content

        post.status = to_status
        if scheduled_at is not None:
            post.scheduled_at = scheduled_at
        if published_at is not None:
            post.published_at = published_at
        if linkedin_urn is not None:
            post.linkedin_urn = linkedin_urn

        record_audit(
            session,
            actor=actor,
            action=_ACTIONS[(from_status, to_status)],
            entity_id=post_id,
            detail=detail,
        )
        session.flush()
        view = to_view(post)

    logger.info("post %d: %s -> %s by %s", post_id, from_status.value, to_status.value, actor.value)
    return view


def approve(
    post_id: int, *, edited_content: str | None = None, actor: Actor = Actor.HUMAN
) -> PostView:
    """Approve a draft, first saving `edited_content` if the reviewer changed the text."""
    return _transition(post_id, PostStatus.APPROVED, actor=actor, edited_content=edited_content)


def reject(post_id: int, *, reason: str = "", actor: Actor = Actor.HUMAN) -> PostView:
    """Reject a draft. Terminal — write a new draft rather than un-rejecting this one."""
    return _transition(post_id, PostStatus.REJECTED, actor=actor, detail=reason)


def revert_to_draft(post_id: int, *, actor: Actor = Actor.HUMAN) -> PostView:
    """Pull an approved post back to draft so it can be edited again."""
    return _transition(post_id, PostStatus.DRAFT, actor=actor)


def edit_draft(post_id: int, content: str, *, actor: Actor = Actor.HUMAN) -> PostView:
    """Save new text on a draft without changing its status.

    Only drafts are editable. Editing an approved post would mean the text that was
    approved is not the text that ships; revert it to draft first.
    """
    content = content.strip()
    if not content:
        raise WorkflowError("post content must not be empty")

    with get_session() as session:
        post = PostRepo(session).get(post_id)
        if post is None:
            raise WorkflowError(f"no post with id {post_id}")
        if post.status is not PostStatus.DRAFT:
            raise WorkflowError(f"only drafts are editable; post {post_id} is {post.status.value}")

        if content != post.content:
            record_audit(
                session,
                actor=actor,
                action=AuditAction.EDITED,
                entity_id=post_id,
                detail=f"content changed: {len(post.content)} -> {len(content)} chars",
            )
            post.content = content
            session.flush()
        return to_view(post)


def schedule(post_id: int, at: datetime, *, actor: Actor = Actor.HUMAN) -> PostView:
    """Queue an approved post for publication. M3 wires the daemon to act on this."""
    if at.tzinfo is None:
        raise WorkflowError("scheduled time must be timezone-aware")
    return _transition(
        post_id,
        PostStatus.SCHEDULED,
        actor=actor,
        scheduled_at=at,
        detail=f"scheduled for {at.isoformat()}",
    )


def mark_published(post_id: int, *, linkedin_urn: str, actor: Actor = Actor.SCHEDULER) -> PostView:
    """Record that a scheduled post went live. Called by M3 *after* the API succeeds."""
    return _transition(
        post_id,
        PostStatus.PUBLISHED,
        actor=actor,
        published_at=datetime.now(UTC),
        linkedin_urn=linkedin_urn,
        detail=f"urn={linkedin_urn}",
    )


def mark_failed(post_id: int, *, error: str, actor: Actor = Actor.SCHEDULER) -> PostView:
    """Record that publishing a scheduled post failed."""
    return _transition(post_id, PostStatus.FAILED, actor=actor, detail=error)
