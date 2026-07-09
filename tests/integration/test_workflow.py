"""The state machine, exhaustively.

The legal-transition table is small enough to test by enumeration rather than by
example, so that is what happens here: every one of the 36 `(from, to)` pairs is
exercised, and each is asserted to either succeed and audit, or raise.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from linkedos.core.errors import WorkflowError
from linkedos.db.models import Actor, AuditAction, EntityType, Post, PostStatus
from linkedos.db.repo import AuditRepo, PostRepo
from linkedos.db.session import get_session
from linkedos.services import audit, workflow

LEGAL: set[tuple[PostStatus, PostStatus]] = {
    (PostStatus.DRAFT, PostStatus.APPROVED),
    (PostStatus.DRAFT, PostStatus.REJECTED),
    (PostStatus.APPROVED, PostStatus.SCHEDULED),
    (PostStatus.APPROVED, PostStatus.DRAFT),
    (PostStatus.SCHEDULED, PostStatus.PUBLISHED),
    (PostStatus.SCHEDULED, PostStatus.FAILED),
}

ALL_PAIRS = list(itertools.product(PostStatus, PostStatus))
ILLEGAL = [pair for pair in ALL_PAIRS if pair not in LEGAL]

SOON = datetime.now(UTC) + timedelta(hours=1)


def _make_post(status: PostStatus = PostStatus.DRAFT, content: str = "original body") -> int:
    with get_session() as session:
        post = PostRepo(session).add(
            Post(
                content=content,
                status=status,
                variant_group_id="g1",
                topic="testing the state machine",
                prompt_version="post_v1",
            )
        )
        return post.id


def _status_of(post_id: int) -> PostStatus:
    with get_session() as session:
        post = PostRepo(session).get(post_id)
        assert post is not None
        return post.status


def _audit_count() -> int:
    with get_session() as session:
        return AuditRepo(session).count()


def _drive(post_id: int, to_status: PostStatus) -> workflow.PostView:
    """Invoke the public function that performs `to_status`, whatever it is called."""
    if to_status is PostStatus.APPROVED:
        return workflow.approve(post_id)
    if to_status is PostStatus.REJECTED:
        return workflow.reject(post_id, reason="not good enough")
    if to_status is PostStatus.DRAFT:
        return workflow.revert_to_draft(post_id)
    if to_status is PostStatus.SCHEDULED:
        return workflow.schedule(post_id, SOON)
    if to_status is PostStatus.PUBLISHED:
        return workflow.mark_published(post_id, linkedin_urn="urn:li:share:1")
    if to_status is PostStatus.FAILED:
        return workflow.mark_failed(post_id, error="api exploded")
    raise AssertionError(f"unhandled status {to_status}")


class TestTransitionTable:
    @pytest.mark.parametrize(("from_status", "to_status"), sorted(LEGAL))
    def test_legal_transition_succeeds(
        self, temp_db: Path, from_status: PostStatus, to_status: PostStatus
    ) -> None:
        post_id = _make_post(from_status)

        view = _drive(post_id, to_status)

        assert view.status is to_status
        assert _status_of(post_id) is to_status

    @pytest.mark.parametrize(("from_status", "to_status"), sorted(LEGAL))
    def test_legal_transition_writes_exactly_one_audit_row(
        self, temp_db: Path, from_status: PostStatus, to_status: PostStatus
    ) -> None:
        post_id = _make_post(from_status)
        before = _audit_count()

        _drive(post_id, to_status)

        assert _audit_count() == before + 1
        entry = audit.history_for_post(post_id)[0]
        assert entry.entity_type is EntityType.POST
        assert entry.entity_id == post_id

    @pytest.mark.parametrize(("from_status", "to_status"), sorted(ILLEGAL))
    def test_illegal_transition_raises(
        self, temp_db: Path, from_status: PostStatus, to_status: PostStatus
    ) -> None:
        post_id = _make_post(from_status)

        with pytest.raises(WorkflowError, match="illegal transition"):
            _drive(post_id, to_status)

    @pytest.mark.parametrize(("from_status", "to_status"), sorted(ILLEGAL))
    def test_illegal_transition_changes_nothing(
        self, temp_db: Path, from_status: PostStatus, to_status: PostStatus
    ) -> None:
        post_id = _make_post(from_status)
        before = _audit_count()

        with pytest.raises(WorkflowError):
            _drive(post_id, to_status)

        assert _status_of(post_id) is from_status
        assert _audit_count() == before

    def test_the_table_and_the_helpers_agree(self) -> None:
        # Guards against `TRANSITIONS` and this file's `LEGAL` drifting apart.
        from_table = {
            (source, target)
            for source, targets in workflow.TRANSITIONS.items()
            for target in targets
        }
        assert from_table == LEGAL

    @pytest.mark.parametrize(
        "status", [PostStatus.REJECTED, PostStatus.PUBLISHED, PostStatus.FAILED]
    )
    def test_terminal_statuses_allow_nothing(self, status: PostStatus) -> None:
        assert workflow.allowed_transitions(status) == frozenset()

    def test_can_transition_is_pure(self) -> None:
        assert workflow.can_transition(PostStatus.DRAFT, PostStatus.APPROVED)
        assert not workflow.can_transition(PostStatus.DRAFT, PostStatus.PUBLISHED)


class TestAuditContent:
    def test_approve_records_actor_and_action(self, temp_db: Path) -> None:
        post_id = _make_post()

        workflow.approve(post_id)

        entry = audit.history_for_post(post_id)[0]
        assert entry.action is AuditAction.APPROVED
        assert entry.actor is Actor.HUMAN

    def test_reject_records_the_reason(self, temp_db: Path) -> None:
        post_id = _make_post()

        workflow.reject(post_id, reason="reads like a press release")

        entry = audit.history_for_post(post_id)[0]
        assert entry.action is AuditAction.REJECTED
        assert entry.detail == "reads like a press release"

    def test_revert_is_audited_as_a_revert_not_as_a_draft(self, temp_db: Path) -> None:
        # `approved -> draft` and "a new draft" are different events; the pair
        # disambiguates them, and the audit log must not conflate the two.
        post_id = _make_post(PostStatus.APPROVED)

        workflow.revert_to_draft(post_id)

        assert audit.history_for_post(post_id)[0].action is AuditAction.REVERTED_TO_DRAFT

    def test_scheduler_actions_are_attributed_to_the_scheduler(self, temp_db: Path) -> None:
        post_id = _make_post(PostStatus.SCHEDULED)

        workflow.mark_published(post_id, linkedin_urn="urn:li:share:42")

        entry = audit.history_for_post(post_id)[0]
        assert entry.actor is Actor.SCHEDULER
        assert "urn:li:share:42" in entry.detail

    def test_history_is_newest_first(self, temp_db: Path) -> None:
        post_id = _make_post()
        workflow.approve(post_id)
        workflow.revert_to_draft(post_id)
        workflow.reject(post_id)

        actions = [entry.action for entry in audit.history_for_post(post_id)]
        assert actions == [
            AuditAction.REJECTED,
            AuditAction.REVERTED_TO_DRAFT,
            AuditAction.APPROVED,
        ]


class TestSideEffects:
    def test_schedule_sets_scheduled_at(self, temp_db: Path) -> None:
        post_id = _make_post(PostStatus.APPROVED)

        view = workflow.schedule(post_id, SOON)

        assert view.scheduled_at is not None

    def test_schedule_rejects_a_naive_datetime(self, temp_db: Path) -> None:
        post_id = _make_post(PostStatus.APPROVED)

        with pytest.raises(WorkflowError, match="timezone-aware"):
            workflow.schedule(post_id, datetime(2030, 1, 1, 9, 0))

    def test_mark_published_stores_the_urn_and_a_timestamp(self, temp_db: Path) -> None:
        post_id = _make_post(PostStatus.SCHEDULED)

        view = workflow.mark_published(post_id, linkedin_urn="urn:li:share:7")

        assert view.linkedin_urn == "urn:li:share:7"
        assert view.published_at is not None

    def test_mark_failed_records_the_error(self, temp_db: Path) -> None:
        post_id = _make_post(PostStatus.SCHEDULED)

        workflow.mark_failed(post_id, error="429 from LinkedIn")

        assert audit.history_for_post(post_id)[0].detail == "429 from LinkedIn"


class TestMissingPost:
    def test_transitioning_a_missing_post_raises(self, temp_db: Path) -> None:
        with pytest.raises(WorkflowError, match="no post with id 999"):
            workflow.approve(999)

    def test_editing_a_missing_post_raises(self, temp_db: Path) -> None:
        with pytest.raises(WorkflowError, match="no post with id 999"):
            workflow.edit_draft(999, "text")


class TestApprovingAnEditedDraft:
    def test_edit_is_persisted_then_status_flips(self, temp_db: Path) -> None:
        post_id = _make_post(content="the original text")

        view = workflow.approve(post_id, edited_content="the reviewer's better text")

        assert view.content == "the reviewer's better text"
        assert view.status is PostStatus.APPROVED

        with get_session() as session:
            post = PostRepo(session).get(post_id)
            assert post is not None
            assert post.content == "the reviewer's better text"
            assert post.status is PostStatus.APPROVED

    def test_edit_and_approval_are_audited_separately(self, temp_db: Path) -> None:
        post_id = _make_post(content="short")

        workflow.approve(post_id, edited_content="considerably longer text than before")

        actions = [entry.action for entry in audit.history_for_post(post_id)]
        assert actions == [AuditAction.APPROVED, AuditAction.EDITED]

    def test_an_unchanged_edit_is_not_audited_as_an_edit(self, temp_db: Path) -> None:
        post_id = _make_post(content="unchanged")

        workflow.approve(post_id, edited_content="unchanged")

        actions = [entry.action for entry in audit.history_for_post(post_id)]
        assert actions == [AuditAction.APPROVED]

    def test_approving_without_an_edit_keeps_the_original_text(self, temp_db: Path) -> None:
        post_id = _make_post(content="untouched body")

        view = workflow.approve(post_id)

        assert view.content == "untouched body"

    def test_a_rejected_edit_never_lands(self, temp_db: Path) -> None:
        # The transition is checked before the edit is written, so an illegal approval
        # cannot smuggle a content change into the database.
        post_id = _make_post(PostStatus.PUBLISHED, content="live text")

        with pytest.raises(WorkflowError, match="illegal transition"):
            workflow.approve(post_id, edited_content="sneaky rewrite")

        with get_session() as session:
            post = PostRepo(session).get(post_id)
            assert post is not None
            assert post.content == "live text"


class TestEditDraft:
    def test_edit_draft_saves_and_audits(self, temp_db: Path) -> None:
        post_id = _make_post(content="before")

        view = workflow.edit_draft(post_id, "after")

        assert view.content == "after"
        assert view.status is PostStatus.DRAFT
        assert audit.history_for_post(post_id)[0].action is AuditAction.EDITED

    def test_edit_draft_strips_whitespace(self, temp_db: Path) -> None:
        post_id = _make_post(content="before")

        assert workflow.edit_draft(post_id, "  after  ").content == "after"

    def test_edit_draft_rejects_empty_content(self, temp_db: Path) -> None:
        post_id = _make_post()

        with pytest.raises(WorkflowError, match="must not be empty"):
            workflow.edit_draft(post_id, "   ")

    def test_only_drafts_are_editable(self, temp_db: Path) -> None:
        # Editing an approved post would mean the approved text is not the shipped text.
        post_id = _make_post(PostStatus.APPROVED)

        with pytest.raises(WorkflowError, match="only drafts are editable"):
            workflow.edit_draft(post_id, "changed after approval")

    def test_an_identical_edit_writes_no_audit_row(self, temp_db: Path) -> None:
        post_id = _make_post(content="same")
        before = _audit_count()

        workflow.edit_draft(post_id, "same")

        assert _audit_count() == before
