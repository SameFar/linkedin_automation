"""Semantic memory: store vectors, retrieve the nearest, warn about repeats.

The fake provider's embeddings are bag-of-words feature hashes, so "shared words" stands
in for "shared meaning". That is enough to prove the retrieval plumbing — packing,
model-scoping, cosine ranking, orphan handling — without a network call.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from linkedos.ai import memory
from linkedos.ai.client import AIClient
from linkedos.ai.providers.fake import FAKE_EMBED_DIM, FakeProvider
from linkedos.core.errors import WorkflowError
from linkedos.db.models import EmbeddingKind, Post, PostStatus
from linkedos.db.repo import EmbeddingRepo, PostRepo
from linkedos.db.session import get_session

ASYNCIO = "python asyncio event loop concurrency"
ASYNCIO_NEAR = "concurrency in python with asyncio tasks"
SOURDOUGH = "sourdough bread starter hydration baking"


def _add_post(session: Session, content: str) -> Post:
    return PostRepo(session).add(
        Post(
            content=content,
            status=PostStatus.DRAFT,
            variant_group_id="g",
            topic=content[:40],
            prompt_version="post_v1",
        )
    )


def _seed_posts(client: AIClient, *contents: str) -> list[int]:
    """Insert posts, embed them, then save the vectors — the same three phases as the
    content service, and for the same single-writer reason."""
    with get_session() as session:
        ids = [_add_post(session, content).id for content in contents]

    vectors = [memory.embed_text(content, client=client) for content in contents]

    with get_session() as session:
        for post_id, vector in zip(ids, vectors, strict=True):
            memory.save_vector(
                session,
                kind=EmbeddingKind.POST,
                ref_id=post_id,
                vector=vector,
                model_name=client.embed_model,
            )
    return ids


class TestPacking:
    def test_pack_unpack_round_trips(self) -> None:
        vector = [0.5, -0.25, 0.125]
        assert memory.unpack(memory.pack(vector)).tolist() == vector

    def test_pack_produces_four_bytes_per_dimension(self) -> None:
        assert len(memory.pack([1.0] * FAKE_EMBED_DIM)) == 4 * FAKE_EMBED_DIM


class TestSaveVector:
    def test_save_vector_persists_a_vector_tagged_with_the_model(
        self, temp_db: Path, ai_client: AIClient
    ) -> None:
        (post_id,) = _seed_posts(ai_client, ASYNCIO)

        with get_session() as session:
            stored = EmbeddingRepo(session).get_for_ref(
                EmbeddingKind.POST, post_id, ai_client.embed_model
            )

        assert stored is not None
        assert stored.model_name == ai_client.embed_model
        assert len(memory.unpack(stored.vector)) == FAKE_EMBED_DIM

    def test_save_vector_is_idempotent(self, temp_db: Path, ai_client: AIClient) -> None:
        (post_id,) = _seed_posts(ai_client, ASYNCIO)
        vector = memory.embed_text(ASYNCIO, client=ai_client)

        with get_session() as session:
            first = memory.save_vector(
                session,
                kind=EmbeddingKind.POST,
                ref_id=post_id,
                vector=vector,
                model_name=ai_client.embed_model,
            )
            second = memory.save_vector(
                session,
                kind=EmbeddingKind.POST,
                ref_id=post_id,
                vector=vector,
                model_name=ai_client.embed_model,
            )

            assert second.id == first.id

        with get_session() as session:
            assert EmbeddingRepo(session).count() == 1

    def test_has_vector_reports_presence_per_model(
        self, temp_db: Path, ai_client: AIClient
    ) -> None:
        (post_id,) = _seed_posts(ai_client, ASYNCIO)

        with get_session() as session:
            assert memory.has_vector(
                session, kind=EmbeddingKind.POST, ref_id=post_id, model_name=ai_client.embed_model
            )
            assert not memory.has_vector(
                session, kind=EmbeddingKind.POST, ref_id=post_id, model_name="some-other-model"
            )

    def test_embedding_billed_once_per_text(
        self, temp_db: Path, ai_client: AIClient, fake_provider: FakeProvider
    ) -> None:
        _seed_posts(ai_client, ASYNCIO, SOURDOUGH)

        assert len(fake_provider.embed_calls) == 2

    def test_embedding_empty_text_is_an_error(self, temp_db: Path, ai_client: AIClient) -> None:
        with pytest.raises(WorkflowError, match="empty text"):
            memory.embed_text("   ", client=ai_client)


class TestMostSimilar:
    def test_empty_store_returns_nothing_without_embedding_the_query(
        self, temp_db: Path, ai_client: AIClient, fake_provider: FakeProvider
    ) -> None:
        with get_session() as session:
            assert memory.most_similar(session, ASYNCIO, k=3, client=ai_client) == []

        assert fake_provider.embed_calls == []

    def test_returns_the_nearest_item_first(self, temp_db: Path, ai_client: AIClient) -> None:
        asyncio_id, sourdough_id = _seed_posts(ai_client, ASYNCIO, SOURDOUGH)

        with get_session() as session:
            matches = memory.most_similar(
                session, "python asyncio concurrency", k=2, client=ai_client
            )

        assert [match.ref_id for match in matches] == [asyncio_id, sourdough_id]
        assert matches[0].score > matches[1].score

    def test_an_unrelated_document_scores_near_zero(
        self, temp_db: Path, ai_client: AIClient
    ) -> None:
        _seed_posts(ai_client, SOURDOUGH)

        with get_session() as session:
            (match,) = memory.most_similar(session, ASYNCIO, k=1, client=ai_client)

        assert match.score == pytest.approx(0.0, abs=0.01)

    def test_an_identical_document_scores_one(self, temp_db: Path, ai_client: AIClient) -> None:
        _seed_posts(ai_client, ASYNCIO)

        with get_session() as session:
            (match,) = memory.most_similar(session, ASYNCIO, k=1, client=ai_client)

        assert match.score == pytest.approx(1.0, abs=1e-6)

    def test_ranks_a_paraphrase_above_an_unrelated_post(
        self, temp_db: Path, ai_client: AIClient
    ) -> None:
        near_id, far_id = _seed_posts(ai_client, ASYNCIO_NEAR, SOURDOUGH)

        with get_session() as session:
            matches = memory.most_similar(session, ASYNCIO, k=2, client=ai_client)

        scores = {match.ref_id: match.score for match in matches}
        assert scores[near_id] > scores[far_id]

    def test_k_caps_the_result_size(self, temp_db: Path, ai_client: AIClient) -> None:
        _seed_posts(ai_client, ASYNCIO, ASYNCIO_NEAR, SOURDOUGH)

        with get_session() as session:
            assert len(memory.most_similar(session, ASYNCIO, k=2, client=ai_client)) == 2

    def test_k_of_zero_returns_nothing(self, temp_db: Path, ai_client: AIClient) -> None:
        _seed_posts(ai_client, ASYNCIO)

        with get_session() as session:
            assert memory.most_similar(session, ASYNCIO, k=0, client=ai_client) == []

    def test_is_deterministic_across_calls(self, temp_db: Path, ai_client: AIClient) -> None:
        _seed_posts(ai_client, ASYNCIO, SOURDOUGH)

        with get_session() as session:
            first = memory.most_similar(session, ASYNCIO, k=2, client=ai_client)
            second = memory.most_similar(session, ASYNCIO, k=2, client=ai_client)

        assert first == second

    def test_ignores_vectors_from_a_different_embedding_model(
        self, temp_db: Path, ai_client: AIClient
    ) -> None:
        _seed_posts(ai_client, ASYNCIO)
        other_model = AIClient(FakeProvider(), embed_model="nomic-embed-text")

        with get_session() as session:
            assert memory.most_similar(session, ASYNCIO, k=3, client=other_model) == []

    def test_dimension_change_under_a_reused_model_name_raises(
        self, temp_db: Path, ai_client: AIClient
    ) -> None:
        # A truncated vector stored under the same model name would otherwise produce a
        # shape error deep inside NumPy. Fail with an instruction instead.
        (post_id,) = _seed_posts(ai_client, ASYNCIO)

        with get_session() as session:
            stored = EmbeddingRepo(session).get_for_ref(
                EmbeddingKind.POST, post_id, ai_client.embed_model
            )
            assert stored is not None
            stored.vector = memory.pack([1.0, 2.0])

        with get_session() as session, pytest.raises(WorkflowError, match="dimension changed"):
            memory.most_similar(session, ASYNCIO, k=1, client=ai_client)


class TestSimilarPosts:
    def test_returns_posts_not_just_ids(self, temp_db: Path, ai_client: AIClient) -> None:
        _seed_posts(ai_client, ASYNCIO)

        with get_session() as session:
            (hit,) = memory.similar_posts(session, ASYNCIO, k=1, client=ai_client)

        assert hit.post.content == ASYNCIO
        assert hit.score == pytest.approx(1.0, abs=1e-6)

    def test_min_score_filters_weak_matches(self, temp_db: Path, ai_client: AIClient) -> None:
        _seed_posts(ai_client, SOURDOUGH)

        with get_session() as session:
            hits = memory.similar_posts(session, ASYNCIO, k=3, client=ai_client, min_score=0.5)

        assert hits == []

    def test_skips_an_embedding_whose_post_was_deleted(
        self, temp_db: Path, ai_client: AIClient
    ) -> None:
        (post_id,) = _seed_posts(ai_client, ASYNCIO)

        with get_session() as session:
            post = PostRepo(session).get(post_id)
            assert post is not None
            session.delete(post)

        with get_session() as session:
            # The orphaned vector still matches, but there is no post to return.
            assert memory.similar_posts(session, ASYNCIO, k=3, client=ai_client) == []
            assert len(memory.most_similar(session, ASYNCIO, k=3, client=ai_client)) == 1
