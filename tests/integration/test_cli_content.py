"""`linkedos draft` and `linkedos costs`, driven through `main()` with a fake provider.

`create_drafts` is reached through the real CLI path; only `get_client` is swapped, which
is the single seam between the service layer and the network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from linkedos.ai.client import AIClient
from linkedos.ai.providers.fake import FAKE_EMBED_MODEL, FakeProvider
from linkedos.cli import main


@pytest.fixture
def offline_client(monkeypatch: pytest.MonkeyPatch) -> AIClient:
    """Point `services.content.get_client` at the fake provider."""
    client = AIClient(FakeProvider(), embed_model=FAKE_EMBED_MODEL)
    monkeypatch.setattr("linkedos.services.content.get_client", lambda: client)
    return client


class TestDraftCommand:
    def test_prints_each_variant_and_the_run_cost(
        self,
        temp_db: Path,
        seed_voice: Path,
        offline_client: AIClient,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert main(["draft", "why code review is a teaching tool", "-n", "2"]) == 0

        out = capsys.readouterr().out
        assert "variant 1/2" in out
        assert "variant 2/2" in out
        assert "FAKE DRAFT" in out
        assert "2 draft(s)" in out
        assert "this run cost $" in out
        assert "prompt post_v1" in out

    def test_defaults_to_three_variants(
        self,
        temp_db: Path,
        seed_voice: Path,
        offline_client: AIClient,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert main(["draft", "shipping small pull requests"]) == 0

        assert "3 draft(s)" in capsys.readouterr().out

    def test_warns_when_the_topic_was_already_written_about(
        self,
        temp_db: Path,
        seed_voice: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        topic = "why code review is a teaching tool"
        client = AIClient(FakeProvider(completion_text=topic), embed_model=FAKE_EMBED_MODEL)
        monkeypatch.setattr("linkedos.services.content.get_client", lambda: client)

        main(["draft", topic, "-n", "1"])
        capsys.readouterr()
        main(["draft", topic, "-n", "1"])

        assert "you have written about this before" in capsys.readouterr().out

    def test_an_empty_topic_exits_nonzero_with_a_message(
        self, temp_db: Path, seed_voice: Path, offline_client: AIClient
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(["draft", "   "])

        assert excinfo.value.code == 1

    def test_a_missing_seed_file_exits_nonzero(
        self, temp_db: Path, offline_client: AIClient, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(["draft", "anything"])

        assert excinfo.value.code == 1
        assert "seed file" in capsys.readouterr().err


class TestCostsCommand:
    def test_reports_nothing_on_a_fresh_ledger(
        self, temp_db: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["costs"]) == 0
        assert "no model calls recorded yet." in capsys.readouterr().out

    def test_breaks_spend_down_by_model_and_purpose(
        self,
        temp_db: Path,
        seed_voice: Path,
        offline_client: AIClient,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        main(["draft", "why code review is a teaching tool", "-n", "2"])
        capsys.readouterr()

        assert main(["costs"]) == 0
        out = capsys.readouterr().out

        assert "claude-haiku-4-5" in out
        assert "draft_post" in out
        assert FAKE_EMBED_MODEL in out
        assert "embed_post" in out
        assert "total" in out
        assert "budget $30.00" in out

    def test_flags_going_over_budget(
        self, temp_db: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from datetime import UTC, datetime

        from linkedos.db.models import AiCall
        from linkedos.db.repo import AiCallRepo
        from linkedos.db.session import get_session

        with get_session() as session:
            AiCallRepo(session).add(
                AiCall(
                    at=datetime.now(UTC),
                    provider="anthropic",
                    model="claude-sonnet-5",
                    purpose="draft_post",
                    input_tokens=1,
                    output_tokens=1,
                    cost_usd=100.0,
                )
            )

        assert main(["costs"]) == 0
        assert "over the configured budget" in capsys.readouterr().out
