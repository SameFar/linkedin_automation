"""The chokepoint bills every call. If a code path can reach a model unmetered, it is a bug."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from linkedos.ai.client import AIClient
from linkedos.ai.pricing import Tier, cost_usd
from linkedos.ai.providers.base import Message
from linkedos.ai.providers.fake import FAKE_EMBED_MODEL, FakeProvider
from linkedos.core.errors import ProviderError
from linkedos.db.repo import AiCallRepo
from linkedos.db.session import get_session

HELLO = [Message(role="user", content="write something about testing")]


class TestComplete:
    def test_routes_the_draft_tier_to_a_haiku_class_model(
        self, temp_db: Path, ai_client: AIClient, fake_provider: FakeProvider
    ) -> None:
        ai_client.complete(HELLO, tier=Tier.DRAFT, purpose="draft_post")

        assert fake_provider.complete_calls[0].model == "claude-haiku-4-5"

    def test_writes_exactly_one_ledger_row_per_call(
        self, temp_db: Path, ai_client: AIClient
    ) -> None:
        ai_client.complete(HELLO, tier=Tier.DRAFT, purpose="draft_post")
        ai_client.complete(HELLO, tier=Tier.DRAFT, purpose="draft_post")

        with get_session() as session:
            (row,) = AiCallRepo(session).spend_since(datetime.now(UTC) - timedelta(hours=1))
            assert row.calls == 2

    def test_ledger_cost_matches_the_pricing_table(
        self, temp_db: Path, ai_client: AIClient
    ) -> None:
        result = ai_client.complete(HELLO, tier=Tier.DRAFT, purpose="draft_post")

        expected = cost_usd(
            "claude-haiku-4-5",
            result.response.input_tokens,
            result.response.output_tokens,
        )
        assert result.cost_usd == expected

        with get_session() as session:
            row = AiCallRepo(session).get(result.call_id)
            assert row is not None
            assert row.cost_usd == expected
            assert row.input_tokens == result.response.input_tokens
            assert row.output_tokens == result.response.output_tokens

    def test_ledger_records_provider_purpose_and_prompt_version(
        self, temp_db: Path, ai_client: AIClient
    ) -> None:
        result = ai_client.complete(
            HELLO, tier=Tier.DRAFT, purpose="draft_post", prompt_version="post_v1"
        )

        with get_session() as session:
            row = AiCallRepo(session).get(result.call_id)
            assert row is not None
            assert row.provider == "fake"
            assert row.purpose == "draft_post"
            assert row.prompt_version == "post_v1"
            assert row.model == "claude-haiku-4-5"

    def test_ledger_records_the_model_the_provider_actually_ran(self, temp_db: Path) -> None:
        # Not the model we asked for — the one echoed back. They can differ.
        client = AIClient(FakeProvider(), embed_model=FAKE_EMBED_MODEL)
        result = client.complete(HELLO, tier=Tier.HIGH_STAKES, purpose="rewrite")

        assert result.response.model == "claude-sonnet-5"
        with get_session() as session:
            row = AiCallRepo(session).get(result.call_id)
            assert row is not None
            assert row.model == "claude-sonnet-5"

    def test_system_prompt_reaches_the_provider(
        self, temp_db: Path, ai_client: AIClient, fake_provider: FakeProvider
    ) -> None:
        ai_client.complete(HELLO, tier=Tier.DRAFT, purpose="draft_post", system="be terse")

        assert fake_provider.complete_calls[0].system == "be terse"

    def test_latency_is_recorded(self, temp_db: Path, ai_client: AIClient) -> None:
        result = ai_client.complete(HELLO, tier=Tier.DRAFT, purpose="draft_post")

        assert result.latency_ms >= 0
        with get_session() as session:
            row = AiCallRepo(session).get(result.call_id)
            assert row is not None
            assert row.latency_ms == result.latency_ms


class TestEmbed:
    def test_returns_one_vector_per_input(self, temp_db: Path, ai_client: AIClient) -> None:
        result = ai_client.embed(["one", "two", "three"], purpose="embed_post")

        assert len(result.vectors) == 3

    def test_local_embeddings_are_free_but_still_metered(
        self, temp_db: Path, ai_client: AIClient
    ) -> None:
        result = ai_client.embed(["some text to embed"], purpose="embed_post")

        assert result.cost_usd == 0.0
        with get_session() as session:
            row = AiCallRepo(session).get(result.call_id)
            assert row is not None
            assert row.cost_usd == 0.0
            assert row.model == FAKE_EMBED_MODEL
            assert row.input_tokens > 0  # volume is recorded even when the price is zero
            assert row.output_tokens == 0

    def test_embedding_nothing_is_a_programming_error(
        self, temp_db: Path, ai_client: AIClient
    ) -> None:
        with pytest.raises(ValueError, match="at least one text"):
            ai_client.embed([], purpose="embed_post")


class TestProviderSplit:
    def test_completions_and_embeddings_can_use_different_providers(self, temp_db: Path) -> None:
        completions = FakeProvider(name="anthropic")
        embeddings = FakeProvider(name="ollama")
        client = AIClient(completions, embeddings, embed_model=FAKE_EMBED_MODEL)

        client.complete(HELLO, tier=Tier.DRAFT, purpose="draft_post")
        client.embed(["text"], purpose="embed_post")

        assert len(completions.complete_calls) == 1
        assert completions.embed_calls == []
        assert len(embeddings.embed_calls) == 1

        with get_session() as session:
            providers = {
                row.model
                for row in AiCallRepo(session).spend_since(datetime.now(UTC) - timedelta(hours=1))
            }
            assert providers == {"claude-haiku-4-5", FAKE_EMBED_MODEL}

    def test_a_provider_that_cannot_embed_says_so(self, temp_db: Path) -> None:
        from linkedos.ai.providers.claude import ClaudeProvider

        provider = ClaudeProvider(api_key="sk-test-not-used", timeout_s=1.0)
        with pytest.raises(ProviderError, match="no embedding endpoint"):
            provider.embed(["text"], model="whatever")
