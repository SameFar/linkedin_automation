"""Batch generation and batch approval, on a fake provider and a temp database.

The unit of work here is a *set* of drafts created together — a week of content — that
share a `batch_id` and get reviewed, approved, and scheduled as one. These tests prove
the service seam the batch review screen sits on: right count, shared id, recorded cost,
and a bulk approval that flips the valid posts, audits each, and reports the rest.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from linkedos.ai.client import AIClient
from linkedos.ai.providers.fake import FAKE_EMBED_MODEL, FakeProvider
from linkedos.core.errors import WorkflowError
from linkedos.db.models import Actor, AuditAction, PostStatus
from linkedos.db.repo import AiCallRepo, PostRepo
from linkedos.db.session import get_session
from linkedos.services import audit, content, workflow

TOPICS = ["why code review teaches", "what makes a good migration", "the cost of on-call"]


def _spend_total() -> float:
    with get_session() as session:
        return AiCallRepo(session).total_since(datetime.now(UTC) - timedelta(hours=1))


class TestGenerateBatchFromTopics:
    def test_creates_one_draft_per_topic_by_default(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        result = content.generate_batch(TOPICS, client=ai_client)

        assert len(result.posts) == len(TOPICS)
        assert result.topics == TOPICS
        with get_session() as session:
            assert PostRepo(session).count() == len(TOPICS)

    def test_per_topic_multiplies_the_draft_count(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        result = content.generate_batch(TOPICS, per_topic=2, client=ai_client)

        assert len(result.posts) == len(TOPICS) * 2

    def test_all_drafts_share_one_batch_id(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        result = content.generate_batch(TOPICS, per_topic=2, client=ai_client)

        assert {post.batch_id for post in result.posts} == {result.batch_id}
        with get_session() as session:
            in_batch = PostRepo(session).list_by_batch(result.batch_id)
            assert len(in_batch) == len(TOPICS) * 2

    def test_variant_groups_still_differ_per_topic(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        # One shared batch, but each topic is its own variant group.
        result = content.generate_batch(TOPICS, per_topic=1, client=ai_client)

        assert len({post.variant_group_id for post in result.posts}) == len(TOPICS)

    def test_records_a_positive_cost_matching_the_ledger(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        result = content.generate_batch(TOPICS, client=ai_client)

        assert result.cost_usd > 0
        assert result.cost_usd == pytest.approx(_spend_total())

    def test_every_draft_is_a_draft_awaiting_review(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        result = content.generate_batch(TOPICS, client=ai_client)

        assert all(post.status is PostStatus.DRAFT for post in result.posts)

    def test_separate_batches_get_separate_ids(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        first = content.generate_batch(["one topic"], client=ai_client)
        second = content.generate_batch(["another topic"], client=ai_client)

        assert first.batch_id != second.batch_id

    def test_blank_topics_are_dropped_and_an_all_blank_list_is_rejected(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        result = content.generate_batch(["real topic", "   ", ""], client=ai_client)
        assert result.topics == ["real topic"]

        with pytest.raises(WorkflowError, match="no non-empty topics"):
            content.generate_batch(["  ", ""], client=ai_client)

    def test_a_bad_per_topic_is_rejected(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        with pytest.raises(WorkflowError, match="per_topic must be between"):
            content.generate_batch(TOPICS, per_topic=0, client=ai_client)


class TestGenerateBatchFromCount:
    def _proposing_client(self) -> AIClient:
        # The fake provider echoes this text for every call, so its lines become the
        # proposed topics and every drafted post shares that body — fine for counting.
        proposal = "\n".join(f"proposed topic {i}" for i in range(1, 6))
        return AIClient(FakeProvider(completion_text=proposal), embed_model=FAKE_EMBED_MODEL)

    def test_count_mode_proposes_topics_then_drafts_one_each(
        self, temp_db: Path, seed_voice: Path
    ) -> None:
        result = content.generate_batch(3, client=self._proposing_client())

        assert len(result.topics) == 3
        assert len(result.posts) == 3
        assert {post.batch_id for post in result.posts} == {result.batch_id}

    def test_count_mode_records_a_proposal_ledger_row(
        self, temp_db: Path, seed_voice: Path
    ) -> None:
        content.generate_batch(2, client=self._proposing_client())

        with get_session() as session:
            rows = AiCallRepo(session).spend_since(datetime.now(UTC) - timedelta(hours=1))
        purposes = {row.purpose for row in rows}
        assert content.PURPOSE_PROPOSE in purposes

    def test_count_out_of_range_is_rejected(self, temp_db: Path, seed_voice: Path) -> None:
        with pytest.raises(WorkflowError, match="count must be between"):
            content.generate_batch(0, client=self._proposing_client())


class TestGetBatchAndSummaries:
    def test_get_batch_filters_by_status(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        result = content.generate_batch(TOPICS, client=ai_client)
        workflow.approve(result.posts[0].id)

        drafts = content.get_batch(result.batch_id, PostStatus.DRAFT)
        approved = content.get_batch(result.batch_id, PostStatus.APPROVED)

        assert len(drafts) == len(TOPICS) - 1
        assert [post.id for post in approved] == [result.posts[0].id]

    def test_recent_batches_summarises_the_pending_count_and_topics(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        result = content.generate_batch(TOPICS, client=ai_client)
        workflow.approve(result.posts[0].id)

        summaries = content.recent_batches()

        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.batch_id == result.batch_id
        assert summary.total_count == len(TOPICS)
        assert summary.draft_count == len(TOPICS) - 1
        assert set(summary.topics) == set(TOPICS)


class TestBatchApprove:
    def _draft_batch(self, ai_client: AIClient) -> content.BatchResult:
        return content.generate_batch(TOPICS, client=ai_client)

    def test_flips_all_valid_posts_to_approved(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        batch = self._draft_batch(ai_client)
        ids = [post.id for post in batch.posts]

        outcome = workflow.batch_approve(ids)

        assert outcome.all_succeeded
        assert sorted(outcome.applied_ids) == sorted(ids)
        for post_id in ids:
            assert content.get_post(post_id).status is PostStatus.APPROVED

    def test_writes_one_approval_audit_row_per_post(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        batch = self._draft_batch(ai_client)
        ids = [post.id for post in batch.posts]

        workflow.batch_approve(ids)

        for post_id in ids:
            approvals = [
                entry
                for entry in audit.history_for_post(post_id)
                if entry.action is AuditAction.APPROVED
            ]
            assert len(approvals) == 1
            assert approvals[0].actor is Actor.HUMAN

    def test_reports_invalid_posts_without_touching_the_rest(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        batch = self._draft_batch(ai_client)
        valid = [post.id for post in batch.posts]

        # A published post cannot be approved; a made-up id does not exist.
        already = valid[0]
        workflow.approve(already)  # now approved -> approving again is illegal
        missing = 999_999
        ids = [*valid, missing]

        outcome = workflow.batch_approve(ids)

        applied = set(outcome.applied_ids)
        failed = {failure.post_id for failure in outcome.failed}
        assert applied == set(valid[1:])  # the still-draft posts
        assert failed == {already, missing}
        # The already-approved post kept its single approval; nothing double-approved it.
        approvals = [e for e in audit.history_for_post(already) if e.action is AuditAction.APPROVED]
        assert len(approvals) == 1

    def test_an_edit_supplied_at_approval_is_persisted(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        batch = self._draft_batch(ai_client)
        target = batch.posts[0].id

        outcome = workflow.batch_approve(
            [post.id for post in batch.posts],
            edits={target: "the reviewer's better text"},
        )

        assert outcome.all_succeeded
        approved = content.get_post(target)
        assert approved.content == "the reviewer's better text"
        assert approved.status is PostStatus.APPROVED
        actions = [entry.action for entry in audit.history_for_post(target)]
        assert AuditAction.EDITED in actions

    def test_editing_a_draft_before_batch_approval_persists_the_edit(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        # Two-step path: edit first via the state machine, then bulk-approve.
        batch = self._draft_batch(ai_client)
        target = batch.posts[0].id
        workflow.edit_draft(target, "edited before the batch approval")

        workflow.batch_approve([post.id for post in batch.posts])

        approved = content.get_post(target)
        assert approved.content == "edited before the batch approval"
        assert approved.status is PostStatus.APPROVED


class TestBatchReject:
    def test_rejects_all_valid_posts(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        batch = content.generate_batch(TOPICS, client=ai_client)
        ids = [post.id for post in batch.posts]

        outcome = workflow.batch_reject(ids, reason="off-brand")

        assert outcome.all_succeeded
        for post_id in ids:
            assert content.get_post(post_id).status is PostStatus.REJECTED
            assert audit.history_for_post(post_id)[0].action is AuditAction.REJECTED
