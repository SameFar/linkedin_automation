"""The content engine end to end, on a fake provider and a temp database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from linkedos.ai.client import AIClient
from linkedos.ai.providers.fake import FAKE_EMBED_MODEL, FakeProvider
from linkedos.core.errors import WorkflowError
from linkedos.db.models import EmbeddingKind, PostStatus
from linkedos.db.repo import AiCallRepo, EmbeddingRepo, PostRepo
from linkedos.db.session import get_session
from linkedos.services import content
from linkedos.services.content import create_drafts

TOPIC = "why code review is a teaching tool"


def _spend_by_purpose() -> dict[str, int]:
    with get_session() as session:
        rows = AiCallRepo(session).spend_since(datetime.now(UTC) - timedelta(hours=1))
    return {row.purpose: row.calls for row in rows}


class TestCreateDrafts:
    def test_saves_n_drafts(self, temp_db: Path, seed_voice: Path, ai_client: AIClient) -> None:
        batch = create_drafts(TOPIC, n=3, client=ai_client)

        assert len(batch.posts) == 3
        with get_session() as session:
            assert PostRepo(session).count() == 3

    def test_drafts_are_persisted_as_status_draft(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        create_drafts(TOPIC, n=2, client=ai_client)

        with get_session() as session:
            posts = PostRepo(session).list_by_status(PostStatus.DRAFT)
            assert len(posts) == 2
            assert all(post.published_at is None for post in posts)
            assert all(post.scheduled_at is None for post in posts)
            assert all(post.linkedin_urn is None for post in posts)

    def test_variants_share_one_group_id(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        batch = create_drafts(TOPIC, n=3, client=ai_client)

        assert {post.variant_group_id for post in batch.posts} == {batch.variant_group_id}
        with get_session() as session:
            assert len(PostRepo(session).list_by_group(batch.variant_group_id)) == 3

    def test_separate_runs_get_separate_group_ids(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        first = create_drafts(TOPIC, n=1, client=ai_client)
        second = create_drafts(TOPIC, n=1, client=ai_client)

        assert first.variant_group_id != second.variant_group_id

    def test_prompt_version_is_recorded_on_every_draft(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        batch = create_drafts(TOPIC, n=2, client=ai_client)

        assert batch.prompt_version == "post_v1"
        assert all(post.prompt_version == "post_v1" for post in batch.posts)

    def test_topic_is_recorded_on_every_draft(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        batch = create_drafts(f"  {TOPIC}  ", n=1, client=ai_client)

        assert batch.posts[0].topic == TOPIC  # whitespace stripped

    def test_one_model_call_per_variant(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient, fake_provider: FakeProvider
    ) -> None:
        create_drafts(TOPIC, n=3, client=ai_client)

        assert len(fake_provider.complete_calls) == 3

    def test_records_one_draft_ledger_row_per_variant(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        create_drafts(TOPIC, n=3, client=ai_client)

        assert _spend_by_purpose()[content.PURPOSE_DRAFT] == 3

    def test_reported_cost_equals_the_sum_of_the_draft_ledger_rows(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        batch = create_drafts(TOPIC, n=3, client=ai_client)

        with get_session() as session:
            rows = AiCallRepo(session).spend_since(datetime.now(UTC) - timedelta(hours=1))
        drafted = sum(row.cost_usd for row in rows if row.purpose == content.PURPOSE_DRAFT)

        assert batch.cost_usd == pytest.approx(drafted)
        assert batch.cost_usd > 0

    def test_each_draft_is_embedded_into_memory(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        batch = create_drafts(TOPIC, n=3, client=ai_client)

        with get_session() as session:
            repo = EmbeddingRepo(session)
            assert repo.count() == 3
            for post in batch.posts:
                assert (
                    repo.get_for_ref(EmbeddingKind.POST, post.id, ai_client.embed_model) is not None
                )

    def test_prompt_carries_the_voice_profile_and_the_topic(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient, fake_provider: FakeProvider
    ) -> None:
        create_drafts(TOPIC, n=1, client=ai_client)
        call = fake_provider.complete_calls[0]

        assert call.system is not None
        assert "Write plainly." in call.system  # guidelines from the seed
        assert "Shipped the migration today." in call.system  # examples from the seed
        assert TOPIC in call.messages[0].content

    def test_each_variant_is_told_which_variant_it_is(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient, fake_provider: FakeProvider
    ) -> None:
        create_drafts(TOPIC, n=3, client=ai_client)

        prompts = [call.messages[0].content for call in fake_provider.complete_calls]
        assert "variant 1 of 3" in prompts[0]
        assert "variant 2 of 3" in prompts[1]
        assert "variant 3 of 3" in prompts[2]

    def test_first_run_has_no_prior_art_and_skips_the_dedup_embed(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        batch = create_drafts(TOPIC, n=1, client=ai_client)

        assert batch.similar == []
        assert batch.duplicate_warning is None
        assert content.PURPOSE_QUERY not in _spend_by_purpose()

    def test_second_run_retrieves_the_first_run_as_prior_art(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient, fake_provider: FakeProvider
    ) -> None:
        create_drafts(TOPIC, n=1, client=ai_client)
        batch = create_drafts(TOPIC, n=1, client=ai_client)

        assert batch.similar
        assert _spend_by_purpose()[content.PURPOSE_QUERY] == 1

        # The retrieved post is fed to the prompt so the model avoids repeating it.
        last_prompt = fake_provider.complete_calls[-1].messages[0].content
        assert "<previously_written>" in last_prompt
        assert batch.similar[0].post.content in last_prompt

    def test_repeating_a_topic_raises_the_duplicate_warning(
        self, temp_db: Path, seed_voice: Path
    ) -> None:
        # Pin the draft body to the topic itself. The dedup check compares the new
        # topic against past post *bodies*, so this is the case where a stored post is
        # genuinely about the same thing and the similarity should saturate.
        client = AIClient(FakeProvider(completion_text=TOPIC), embed_model=FAKE_EMBED_MODEL)

        create_drafts(TOPIC, n=1, client=client)
        batch = create_drafts(TOPIC, n=1, client=client)

        warning = batch.duplicate_warning
        assert warning is not None
        assert warning.score >= content.DEDUP_THRESHOLD

    def test_an_unrelated_topic_raises_no_duplicate_warning(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        create_drafts("sourdough starter hydration ratios", n=1, client=ai_client)
        batch = create_drafts("kubernetes ingress controller tuning", n=1, client=ai_client)

        assert batch.duplicate_warning is None


class TestValidation:
    def test_empty_topic_is_rejected(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        with pytest.raises(WorkflowError, match="topic must not be empty"):
            create_drafts("   ", n=1, client=ai_client)

    @pytest.mark.parametrize("n", [0, -1, content.MAX_VARIANTS + 1])
    def test_variant_count_out_of_range_is_rejected(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient, n: int
    ) -> None:
        with pytest.raises(WorkflowError, match="n must be between"):
            create_drafts(TOPIC, n=n, client=ai_client)

    def test_nothing_is_written_when_validation_fails(
        self, temp_db: Path, seed_voice: Path, ai_client: AIClient
    ) -> None:
        with pytest.raises(WorkflowError):
            create_drafts("", n=1, client=ai_client)

        with get_session() as session:
            assert PostRepo(session).count() == 0
            assert AiCallRepo(session).total_since(datetime.now(UTC) - timedelta(hours=1)) == 0.0

    def test_a_missing_voice_profile_and_seed_file_is_a_clear_error(
        self, temp_db: Path, ai_client: AIClient
    ) -> None:
        # No `seed_voice` fixture: nothing to seed from.
        with pytest.raises(WorkflowError, match="no seed file"):
            create_drafts(TOPIC, n=1, client=ai_client)
