"""The scheduler's publish job respects the `PUBLISH_ENABLED` gate.

Off (the default), a due post is left untouched — the daemon must not fail-loop every
scheduled post before the real LinkedIn transport exists. On, the job drives the same
`publish_due` sweep the service tests cover, here with a fake publisher swapped in for
`get_publisher`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from linkedos.core.config import get_settings
from linkedos.db.models import Post, PostStatus
from linkedos.db.repo import PostRepo
from linkedos.db.session import get_session
from linkedos.integrations.linkedin import FakeLinkedInPublisher
from linkedos.scheduler.jobs import publish_due_posts_job
from linkedos.services import publishing


def _make_due_post() -> int:
    with get_session() as session:
        post = PostRepo(session).add(
            Post(
                content="ready to go",
                status=PostStatus.SCHEDULED,
                variant_group_id="g1",
                topic="publishing",
                prompt_version="post_v1",
                scheduled_at=datetime(2000, 1, 1, tzinfo=UTC),  # long past: always due
            )
        )
        return post.id


def _status_of(post_id: int) -> PostStatus:
    with get_session() as session:
        post = PostRepo(session).get(post_id)
        assert post is not None
        return post.status


def test_publish_job_is_a_noop_when_disabled(temp_db: Path) -> None:
    post_id = _make_due_post()
    assert get_settings().publish_enabled is False  # the default

    publish_due_posts_job()

    assert _status_of(post_id) is PostStatus.SCHEDULED


def test_publish_job_publishes_when_enabled(temp_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLISH_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(publishing, "get_publisher", lambda: FakeLinkedInPublisher())

    post_id = _make_due_post()
    publish_due_posts_job()

    assert _status_of(post_id) is PostStatus.PUBLISHED
