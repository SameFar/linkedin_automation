"""The publish pipeline, end to end, offline.

A `FakeLinkedInPublisher` stands in for LinkedIn, so every path — a post going live, a
post failing, the daemon draining a due queue — is exercised without a socket. The one
irreversible act in the system is tested here more than anywhere else.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from linkedos.core.errors import IntegrationError, WorkflowError
from linkedos.db.models import Actor, AuditAction, EntityType, Post, PostStatus
from linkedos.db.repo import AuditRepo, PostRepo
from linkedos.db.session import get_session
from linkedos.integrations.linkedin import (
    FakeLinkedInPublisher,
    LiveLinkedInPublisher,
    Visibility,
    get_publisher,
)
from linkedos.services import publishing

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def _make_scheduled(
    scheduled_at: datetime | None = None,
    *,
    status: PostStatus = PostStatus.SCHEDULED,
    content: str = "a scheduled post",
) -> int:
    with get_session() as session:
        post = PostRepo(session).add(
            Post(
                content=content,
                status=status,
                variant_group_id="g1",
                topic="publishing",
                prompt_version="post_v1",
                scheduled_at=scheduled_at,
            )
        )
        return post.id


def _status_of(post_id: int) -> PostStatus:
    with get_session() as session:
        post = PostRepo(session).get(post_id)
        assert post is not None
        return post.status


def _post(post_id: int) -> Post:
    with get_session() as session:
        post = PostRepo(session).get(post_id)
        assert post is not None
        session.expunge(post)
        return post


# --- publish_post ------------------------------------------------------------


def test_publish_post_marks_published_with_urn(temp_db: Path) -> None:
    post_id = _make_scheduled(NOW)
    publisher = FakeLinkedInPublisher()

    outcome = publishing.publish_post(post_id, publisher=publisher)

    assert outcome.ok
    assert outcome.urn is not None
    assert outcome.urn.startswith("urn:li:share:fake-")
    assert _status_of(post_id) is PostStatus.PUBLISHED

    stored = _post(post_id)
    assert stored.linkedin_urn == outcome.urn
    assert stored.published_at is not None
    # The publisher actually received the post's text.
    assert publisher.calls == [("a scheduled post", Visibility.PUBLIC)]


def test_publish_post_passes_visibility_through(temp_db: Path) -> None:
    post_id = _make_scheduled(NOW)
    publisher = FakeLinkedInPublisher()

    publishing.publish_post(post_id, publisher=publisher, visibility=Visibility.CONNECTIONS)

    assert publisher.calls == [("a scheduled post", Visibility.CONNECTIONS)]


def test_publish_post_records_a_published_audit_row(temp_db: Path) -> None:
    post_id = _make_scheduled(NOW)

    publishing.publish_post(post_id, publisher=FakeLinkedInPublisher())

    with get_session() as session:
        rows = AuditRepo(session).list_recent(
            entity_type=EntityType.POST, entity_id=post_id, action=AuditAction.PUBLISHED
        )
    assert len(rows) == 1
    assert rows[0].actor is Actor.SCHEDULER


def test_publish_post_failure_marks_failed_and_keeps_no_urn(temp_db: Path) -> None:
    post_id = _make_scheduled(NOW)
    publisher = FakeLinkedInPublisher(fail_with="LinkedIn said no")

    outcome = publishing.publish_post(post_id, publisher=publisher)

    assert not outcome.ok
    assert outcome.error == "LinkedIn said no"
    assert outcome.urn is None
    assert _status_of(post_id) is PostStatus.FAILED
    assert _post(post_id).linkedin_urn is None


def test_publish_post_rejects_a_post_that_is_not_scheduled(temp_db: Path) -> None:
    post_id = _make_scheduled(status=PostStatus.DRAFT)

    with pytest.raises(WorkflowError, match="scheduled"):
        publishing.publish_post(post_id, publisher=FakeLinkedInPublisher())

    assert _status_of(post_id) is PostStatus.DRAFT


def test_publish_post_rejects_a_missing_post(temp_db: Path) -> None:
    with pytest.raises(WorkflowError, match="no post"):
        publishing.publish_post(999, publisher=FakeLinkedInPublisher())


# --- publish_due -------------------------------------------------------------


def test_publish_due_publishes_only_posts_whose_time_has_come(temp_db: Path) -> None:
    past = _make_scheduled(NOW - timedelta(minutes=1))
    exactly_now = _make_scheduled(NOW)
    future = _make_scheduled(NOW + timedelta(hours=1))

    run = publishing.publish_due(now=NOW, publisher=FakeLinkedInPublisher())

    assert run.attempted == 2
    assert {o.post_id for o in run.published} == {past, exactly_now}
    assert _status_of(future) is PostStatus.SCHEDULED


def test_publish_due_drains_oldest_slot_first(temp_db: Path) -> None:
    later = _make_scheduled(NOW - timedelta(minutes=1))
    earlier = _make_scheduled(NOW - timedelta(minutes=5))

    run = publishing.publish_due(now=NOW, publisher=FakeLinkedInPublisher())

    assert [o.post_id for o in run.published] == [earlier, later]


def test_publish_due_continues_past_a_failure(temp_db: Path) -> None:
    _make_scheduled(NOW - timedelta(minutes=1))
    _make_scheduled(NOW - timedelta(minutes=1))

    # A publisher that fails every call: the sweep must attempt both, not stop at the first.
    run = publishing.publish_due(now=NOW, publisher=FakeLinkedInPublisher(fail_with="down"))

    assert run.attempted == 2
    assert len(run.failed) == 2
    assert not run.all_succeeded


def test_publish_due_on_empty_queue_is_a_noop(temp_db: Path) -> None:
    run = publishing.publish_due(now=NOW, publisher=FakeLinkedInPublisher())
    assert run.attempted == 0
    assert run.all_succeeded


# --- the integration doubles themselves --------------------------------------


def test_fake_publisher_returns_unique_urns(temp_db: Path) -> None:
    publisher = FakeLinkedInPublisher()
    first = publisher.publish_post("a")
    second = publisher.publish_post("b")
    assert first.urn != second.urn


def test_live_publisher_refuses_until_implemented(temp_db: Path) -> None:
    with pytest.raises(IntegrationError, match="not implemented"):
        LiveLinkedInPublisher().publish_post("hello")


def test_get_publisher_returns_the_live_stub(temp_db: Path) -> None:
    assert isinstance(get_publisher(), LiveLinkedInPublisher)
