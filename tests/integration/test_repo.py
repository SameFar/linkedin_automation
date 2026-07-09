"""Repository CRUD against a temp SQLite database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from linkedos.db.models import AiCall, Embedding, EmbeddingKind, Post, PostStatus
from linkedos.db.repo import AiCallRepo, EmbeddingRepo, PostRepo, VoiceRepo
from linkedos.db.session import get_session


def _post(topic: str = "testing", group: str = "g1", content: str = "body") -> Post:
    return Post(
        content=content,
        status=PostStatus.DRAFT,
        variant_group_id=group,
        topic=topic,
        prompt_version="post_v1",
    )


class TestPostRepo:
    def test_add_assigns_an_id_and_defaults_to_draft(self, temp_db: Path) -> None:
        with get_session() as session:
            post = PostRepo(session).add(_post())

            assert post.id is not None
            assert post.status is PostStatus.DRAFT
            assert post.published_at is None
            assert post.linkedin_urn is None

    def test_get_round_trips_the_content(self, temp_db: Path) -> None:
        with get_session() as session:
            post_id = PostRepo(session).add(_post(content="hello world")).id

        with get_session() as session:
            fetched = PostRepo(session).get(post_id)

            assert fetched is not None
            assert fetched.content == "hello world"

    def test_get_returns_none_for_a_missing_id(self, temp_db: Path) -> None:
        with get_session() as session:
            assert PostRepo(session).get(9999) is None

    def test_status_persists_as_its_string_value(self, temp_db: Path) -> None:
        # Guards against SQLAlchemy storing the member *name* ("DRAFT") instead.
        with get_session() as session:
            PostRepo(session).add(_post())

        with get_session() as session:
            raw = session.connection().exec_driver_sql("SELECT status FROM posts").scalar()
            assert raw == "draft"

    def test_list_by_group_returns_only_that_group_in_id_order(self, temp_db: Path) -> None:
        with get_session() as session:
            repo = PostRepo(session)
            repo.add_all([_post(group="a"), _post(group="a"), _post(group="b")])

        with get_session() as session:
            group_a = PostRepo(session).list_by_group("a")

            assert len(group_a) == 2
            assert [p.id for p in group_a] == sorted(p.id for p in group_a)

    def test_list_by_status_excludes_other_statuses(self, temp_db: Path) -> None:
        with get_session() as session:
            repo = PostRepo(session)
            repo.add(_post())
            approved = repo.add(_post())
            approved.status = PostStatus.APPROVED

        with get_session() as session:
            drafts = PostRepo(session).list_by_status(PostStatus.DRAFT)
            assert len(drafts) == 1

    def test_get_many_fetches_a_set_of_ids(self, temp_db: Path) -> None:
        with get_session() as session:
            posts = PostRepo(session).add_all([_post(), _post(), _post()])
            ids = [p.id for p in posts]

        with get_session() as session:
            assert {p.id for p in PostRepo(session).get_many(ids[:2])} == set(ids[:2])

    def test_get_many_with_no_ids_hits_no_database(self, temp_db: Path) -> None:
        with get_session() as session:
            assert PostRepo(session).get_many([]) == []

    def test_count(self, temp_db: Path) -> None:
        with get_session() as session:
            assert PostRepo(session).count() == 0
            PostRepo(session).add_all([_post(), _post()])
            assert PostRepo(session).count() == 2


class TestAiCallRepo:
    def test_add_and_get(self, temp_db: Path) -> None:
        with get_session() as session:
            call = AiCallRepo(session).add(
                AiCall(
                    provider="fake",
                    model="claude-haiku-4-5",
                    purpose="draft_post",
                    input_tokens=100,
                    output_tokens=50,
                    cost_usd=0.00035,
                    latency_ms=12,
                    prompt_version="post_v1",
                )
            )
            call_id = call.id

        with get_session() as session:
            fetched = AiCallRepo(session).get(call_id)
            assert fetched is not None
            assert fetched.cost_usd == 0.00035
            assert fetched.prompt_version == "post_v1"

    def test_spend_since_groups_by_model_and_purpose(self, temp_db: Path) -> None:
        with get_session() as session:
            repo = AiCallRepo(session)
            for _ in range(2):
                repo.add(
                    AiCall(
                        provider="anthropic",
                        model="claude-haiku-4-5",
                        purpose="draft_post",
                        input_tokens=100,
                        output_tokens=10,
                        cost_usd=0.001,
                    )
                )
            repo.add(
                AiCall(
                    provider="ollama",
                    model="nomic-embed-text",
                    purpose="embed_post",
                    input_tokens=20,
                    output_tokens=0,
                    cost_usd=0.0,
                )
            )

        with get_session() as session:
            rows = AiCallRepo(session).spend_since(datetime.now(UTC) - timedelta(hours=1))

        by_key = {(row.model, row.purpose): row for row in rows}
        assert len(by_key) == 2

        drafts = by_key[("claude-haiku-4-5", "draft_post")]
        assert drafts.calls == 2
        assert drafts.input_tokens == 200
        assert drafts.output_tokens == 20
        assert drafts.cost_usd == 0.002

    def test_spend_since_excludes_calls_before_the_window(self, temp_db: Path) -> None:
        old = datetime.now(UTC) - timedelta(days=40)
        with get_session() as session:
            AiCallRepo(session).add(
                AiCall(
                    at=old,
                    provider="anthropic",
                    model="claude-haiku-4-5",
                    purpose="draft_post",
                    cost_usd=99.0,
                )
            )

        with get_session() as session:
            repo = AiCallRepo(session)
            since = datetime.now(UTC) - timedelta(days=1)

            assert repo.spend_since(since) == []
            assert repo.total_since(since) == 0.0

    def test_total_since_on_an_empty_ledger_is_zero_not_none(self, temp_db: Path) -> None:
        with get_session() as session:
            assert AiCallRepo(session).total_since(datetime.now(UTC)) == 0.0


class TestEmbeddingRepo:
    def test_add_and_get_for_ref(self, temp_db: Path) -> None:
        with get_session() as session:
            EmbeddingRepo(session).add(
                Embedding(
                    kind=EmbeddingKind.POST,
                    ref_id=7,
                    model_name="fake-embed",
                    vector=b"\x00\x01",
                )
            )

        with get_session() as session:
            found = EmbeddingRepo(session).get_for_ref(EmbeddingKind.POST, 7, "fake-embed")
            assert found is not None
            assert found.vector == b"\x00\x01"

    def test_get_for_ref_is_scoped_by_model_name(self, temp_db: Path) -> None:
        # Two models' vectors live in one table and must never be confused.
        with get_session() as session:
            EmbeddingRepo(session).add(
                Embedding(kind=EmbeddingKind.POST, ref_id=7, model_name="model-a", vector=b"\x00")
            )

        with get_session() as session:
            repo = EmbeddingRepo(session)
            assert repo.get_for_ref(EmbeddingKind.POST, 7, "model-a") is not None
            assert repo.get_for_ref(EmbeddingKind.POST, 7, "model-b") is None

    def test_list_by_kind_is_scoped_by_kind_and_model(self, temp_db: Path) -> None:
        with get_session() as session:
            repo = EmbeddingRepo(session)
            repo.add(Embedding(kind=EmbeddingKind.POST, ref_id=1, model_name="m", vector=b"\x00"))
            repo.add(Embedding(kind=EmbeddingKind.NOTE, ref_id=2, model_name="m", vector=b"\x00"))
            repo.add(
                Embedding(kind=EmbeddingKind.POST, ref_id=3, model_name="other", vector=b"\x00")
            )

        with get_session() as session:
            rows = EmbeddingRepo(session).list_by_kind(EmbeddingKind.POST, "m")
            assert [row.ref_id for row in rows] == [1]

    def test_list_by_kind_on_an_empty_store_returns_empty(self, temp_db: Path) -> None:
        with get_session() as session:
            assert EmbeddingRepo(session).list_by_kind(EmbeddingKind.POST, "m") == []


class TestVoiceRepo:
    def test_get_default_is_none_before_seeding(self, temp_db: Path) -> None:
        with get_session() as session:
            assert VoiceRepo(session).get_default() is None

    def test_upsert_creates_then_overwrites_in_place(self, temp_db: Path) -> None:
        with get_session() as session:
            created = VoiceRepo(session).upsert("default", examples="a", guidelines="g")
            created_id = created.id

        with get_session() as session:
            updated = VoiceRepo(session).upsert("default", examples="b", guidelines="g2")

            assert updated.id == created_id  # updated, not duplicated
            assert updated.examples == "b"
            assert updated.guidelines == "g2"

    def test_upsert_keeps_distinct_names_separate(self, temp_db: Path) -> None:
        with get_session() as session:
            repo = VoiceRepo(session)
            repo.upsert("default", examples="a", guidelines="g")
            repo.upsert("conference", examples="b", guidelines="g")

        with get_session() as session:
            repo = VoiceRepo(session)
            default = repo.get_default()
            other = repo.get_by_name("conference")

            assert default is not None and default.examples == "a"
            assert other is not None and other.examples == "b"
