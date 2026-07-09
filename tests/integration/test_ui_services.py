"""The service seam the Streamlit pages sit on.

Streamlit rendering is not tested here — that would test Streamlit, not linkedos. What
is tested is every service function a page calls, because a page is only allowed to
render, collect input, and call one of these. If they behave, the page behaves.

The mapping, page by page:

* `app.py`        → `status.get_app_status`, `content.get_queue`, `costs.month_to_date`,
                    `audit.list_recent`, `logs.tail`
* `1_content.py`  → `content.create_drafts`
* `2_approvals.py`→ `content.get_queue`, `workflow.approve/reject/revert_to_draft`,
                    `content.regenerate`, `audit.history_for_post`
* `9_logs.py`     → `logs.tail`, `audit.list_recent`
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from linkedos.ai.client import AIClient
from linkedos.core.errors import WorkflowError
from linkedos.db.models import Actor, AuditAction, Post, PostStatus
from linkedos.db.repo import PostRepo
from linkedos.db.session import get_session
from linkedos.services import audit, content, costs, logs, status, workflow
from linkedos.services.status import HEARTBEAT_STALE_AFTER_S

TOPIC = "why code review is a teaching tool"


def _make_post(status_: PostStatus = PostStatus.DRAFT, topic: str = TOPIC) -> int:
    with get_session() as session:
        post = PostRepo(session).add(
            Post(
                content=f"body about {topic}",
                status=status_,
                variant_group_id="g1",
                topic=topic,
                prompt_version="post_v1",
            )
        )
        return post.id


class TestQueue:
    def test_empty_database_yields_an_empty_queue(self, temp_db: Path) -> None:
        queue = content.get_queue()

        assert queue.pending == []
        assert queue.pending_count == 0
        assert queue.counts == {}

    def test_queue_separates_pending_from_approved_and_scheduled(self, temp_db: Path) -> None:
        _make_post(PostStatus.DRAFT)
        _make_post(PostStatus.DRAFT)
        _make_post(PostStatus.APPROVED)
        _make_post(PostStatus.SCHEDULED)
        _make_post(PostStatus.REJECTED)

        queue = content.get_queue()

        assert queue.pending_count == 2
        assert len(queue.pending) == 2
        assert len(queue.approved) == 1
        assert len(queue.scheduled) == 1
        assert queue.count_of(PostStatus.REJECTED) == 1
        assert queue.count_of(PostStatus.PUBLISHED) == 0

    def test_get_pending_drafts_returns_only_drafts(self, temp_db: Path) -> None:
        draft_id = _make_post(PostStatus.DRAFT)
        _make_post(PostStatus.APPROVED)

        pending = content.get_pending_drafts()

        assert [post.id for post in pending] == [draft_id]

    def test_queue_reflects_an_approval_immediately(self, temp_db: Path) -> None:
        # The pages re-read on every rerun rather than caching across one. Prove the
        # read actually changes once the write lands.
        post_id = _make_post(PostStatus.DRAFT)
        assert content.get_queue().pending_count == 1

        workflow.approve(post_id)

        queue = content.get_queue()
        assert queue.pending_count == 0
        assert [post.id for post in queue.approved] == [post_id]

    def test_views_are_detached_snapshots_not_orm_rows(self, temp_db: Path) -> None:
        # The UI must not be able to mutate a post by assigning to an attribute.
        _make_post()
        view = content.get_pending_drafts()[0]

        with pytest.raises(AttributeError):
            view.content = "mutated"  # type: ignore[misc]

    def test_get_post_raises_for_a_missing_id(self, temp_db: Path) -> None:
        with pytest.raises(WorkflowError, match="no post with id"):
            content.get_post(404)


class TestApprovalPageSeam:
    def test_approve_with_an_edit_persists_the_edit(self, temp_db: Path) -> None:
        # Exactly what the Approve button does: pass the text on screen, whatever it is.
        post_id = _make_post()

        workflow.approve(post_id, edited_content="the text the reviewer actually saw")

        assert content.get_post(post_id).content == "the text the reviewer actually saw"
        assert content.get_post(post_id).status is PostStatus.APPROVED

    def test_reject_removes_the_draft_from_the_queue(self, temp_db: Path) -> None:
        post_id = _make_post()

        workflow.reject(post_id, reason="press release energy")

        assert content.get_queue().pending_count == 0
        assert content.get_post(post_id).status is PostStatus.REJECTED

    def test_revert_puts_an_approved_post_back_in_the_queue(self, temp_db: Path) -> None:
        post_id = _make_post(PostStatus.APPROVED)

        workflow.revert_to_draft(post_id)

        assert [post.id for post in content.get_pending_drafts()] == [post_id]

    def test_approving_twice_raises_rather_than_double_approving(self, temp_db: Path) -> None:
        # Two browser tabs, same draft. The second click must lose.
        post_id = _make_post()
        workflow.approve(post_id)

        with pytest.raises(WorkflowError, match="illegal transition"):
            workflow.approve(post_id)

    def test_history_for_post_powers_the_expander(self, temp_db: Path) -> None:
        post_id = _make_post()
        workflow.approve(post_id, edited_content="edited then approved")

        history = audit.history_for_post(post_id)

        assert [entry.action for entry in history] == [AuditAction.APPROVED, AuditAction.EDITED]
        assert all(entry.entity_id == post_id for entry in history)


class TestRegenerate:
    def test_regenerate_drafts_new_variants_on_the_same_topic(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        original = content.create_drafts(TOPIC, n=1, client=ai_client).posts[0]

        batch = content.regenerate(original.id, n=2, client=ai_client)

        assert len(batch.posts) == 2
        assert all(post.topic == TOPIC for post in batch.posts)
        assert batch.variant_group_id != original.variant_group_id

    def test_regenerate_leaves_the_original_untouched(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        original = content.create_drafts(TOPIC, n=1, client=ai_client).posts[0]

        content.regenerate(original.id, n=1, client=ai_client)

        after = content.get_post(original.id)
        assert after.status is PostStatus.DRAFT
        assert after.content == original.content

    def test_regenerate_is_audited_against_the_original(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        original = content.create_drafts(TOPIC, n=1, client=ai_client).posts[0]

        batch = content.regenerate(original.id, n=1, client=ai_client)

        entry = audit.history_for_post(original.id)[0]
        assert entry.action is AuditAction.REGENERATED
        assert entry.actor is Actor.HUMAN
        assert batch.variant_group_id in entry.detail

    def test_regenerate_on_a_missing_post_raises(self, temp_db: Path, ai_client: AIClient) -> None:
        with pytest.raises(WorkflowError, match="no post with id"):
            content.regenerate(404, n=1, client=ai_client)


class TestDraftCreationIsAudited:
    def test_each_new_draft_gets_a_created_entry(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        batch = content.create_drafts(TOPIC, n=2, client=ai_client)

        for post in batch.posts:
            entry = audit.history_for_post(post.id)[0]
            assert entry.action is AuditAction.CREATED
            assert entry.actor is Actor.SYSTEM
            assert TOPIC in entry.detail


class TestHomePageSeam:
    def test_scheduler_is_dead_before_any_heartbeat(self, temp_db: Path) -> None:
        app_status = status.get_app_status()

        assert app_status.heartbeat_age_s() is None
        assert not app_status.scheduler_is_alive()

    def test_a_fresh_heartbeat_reads_as_alive(self, temp_db: Path) -> None:
        status.record_heartbeat(source="test")

        app_status = status.get_app_status()

        age = app_status.heartbeat_age_s()
        assert age is not None
        assert age < 5
        assert app_status.scheduler_is_alive()

    def test_an_old_heartbeat_reads_as_stale(self, temp_db: Path) -> None:
        status.record_heartbeat(source="test")
        app_status = status.get_app_status()

        future = datetime.now(UTC) + timedelta(seconds=HEARTBEAT_STALE_AFTER_S + 60)

        assert not app_status.scheduler_is_alive(now=future)
        age = app_status.heartbeat_age_s(now=future)
        assert age is not None
        assert age > HEARTBEAT_STALE_AFTER_S

    def test_spend_report_starts_empty_and_within_budget(self, temp_db: Path) -> None:
        report = costs.month_to_date()

        assert report.total_usd == 0.0
        assert not report.over_budget

    def test_recent_audit_is_capped_and_newest_first(self, temp_db: Path) -> None:
        first = _make_post()
        second = _make_post()
        workflow.approve(first)
        workflow.approve(second)

        entries = audit.list_recent(1)

        assert len(entries) == 1
        assert entries[0].entity_id == second

    def test_audit_entry_renders_a_one_line_summary(self, temp_db: Path) -> None:
        post_id = _make_post()
        workflow.reject(post_id, reason="too long")

        summary = audit.list_recent(1)[0].summary()

        assert "human" in summary
        assert "rejected" in summary
        assert "too long" in summary


class TestLogsPageSeam:
    def test_tail_of_a_missing_log_file_is_empty_not_an_error(self, temp_db: Path) -> None:
        assert logs.tail(10) == []

    def test_tail_parses_the_configured_log_format(self, temp_db: Path) -> None:
        path = logs.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "2026-07-10 03:14:15 INFO     linkedos.services.workflow post 3: draft -> approved\n"
            "2026-07-10 03:14:16 ERROR    linkedos.ai.client boom\n",
            encoding="utf-8",
        )

        lines = logs.tail(10)

        assert [line.level for line in lines] == ["INFO", "ERROR"]
        assert lines[0].logger == "linkedos.services.workflow"
        assert lines[0].message == "post 3: draft -> approved"

    def test_min_level_filters_before_truncating(self, temp_db: Path) -> None:
        # Asking for 1 ERROR must return the last error, not the last line.
        path = logs.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "2026-07-10 03:14:15 ERROR    linkedos.a first failure\n"
            + "".join(f"2026-07-10 03:14:2{i} INFO     linkedos.b noise {i}\n" for i in range(5)),
            encoding="utf-8",
        )

        lines = logs.tail(1, min_level="ERROR")

        assert len(lines) == 1
        assert lines[0].message == "first failure"

    def test_tail_returns_oldest_first(self, temp_db: Path) -> None:
        path = logs.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "2026-07-10 03:14:15 INFO     linkedos.a one\n"
            "2026-07-10 03:14:16 INFO     linkedos.a two\n",
            encoding="utf-8",
        )

        assert [line.message for line in logs.tail(10)] == ["one", "two"]

    def test_tail_limit_keeps_the_last_lines(self, temp_db: Path) -> None:
        path = logs.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(f"2026-07-10 03:14:1{i} INFO     linkedos.a line {i}\n" for i in range(5)),
            encoding="utf-8",
        )

        assert [line.message for line in logs.tail(2)] == ["line 3", "line 4"]

    def test_audit_browser_filters_by_action(self, temp_db: Path) -> None:
        approved = _make_post()
        rejected = _make_post()
        workflow.approve(approved)
        workflow.reject(rejected)

        entries = audit.list_recent(50, action=AuditAction.REJECTED)

        assert [entry.entity_id for entry in entries] == [rejected]

    def test_audit_browser_filters_by_post(self, temp_db: Path) -> None:
        first = _make_post()
        second = _make_post()
        workflow.approve(first)
        workflow.approve(second)

        entries = audit.list_recent(50, entity_id=second)

        assert [entry.entity_id for entry in entries] == [second]
