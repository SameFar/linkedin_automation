"""Shared fixtures.

Every test runs in its own temp directory with a clean environment, so a stray `.env`
or an exported `ANTHROPIC_API_KEY` on the developer's machine can never leak into a
test run. Settings and the engine are cached per process, so both caches are cleared
around each test.

Nothing here reaches the network. The `ai_client` fixture wires `AIClient` to
`FakeProvider`, which is the only provider any test outside `tests/evals` ever sees.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from linkedos.ai.client import AIClient
from linkedos.ai.providers.fake import FAKE_EMBED_MODEL, FakeProvider
from linkedos.core.config import Settings, get_settings
from linkedos.db.models import Base
from linkedos.db.session import get_engine, get_sessionmaker

SETTINGS_ENV_VARS = tuple(name.upper() for name in Settings.model_fields)

SEED_VOICE = """\
# Voice profile

## Guidelines

Write plainly. No hooks, no emoji, no hashtags.

## Examples

Shipped the migration today. It took four hours and broke nothing, which is the
only review a migration ever gets.
"""


def _clear_caches() -> None:
    get_settings.cache_clear()
    get_sessionmaker.cache_clear()
    get_engine.cache_clear()


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Run each test in a scratch cwd with no linkedos environment variables set."""
    for var in SETTINGS_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)

    _clear_caches()
    yield tmp_path

    if get_engine.cache_info().currsize:
        get_engine().dispose()
    _clear_caches()


@pytest.fixture
def temp_db(isolated_env: Path) -> Path:
    """Create an empty schema in a temp SQLite file and return its path.

    Uses `Base.metadata.create_all` rather than Alembic: the tests assert on the models,
    and a migration run per test would be slow. `make check` keeps the two honest by
    running against the same metadata that Alembic autogenerates from.
    """
    Base.metadata.create_all(get_engine())
    return isolated_env / "data" / "linkedos.db"


@pytest.fixture
def fake_provider() -> FakeProvider:
    """A deterministic provider that records every call and never opens a socket."""
    return FakeProvider()


@pytest.fixture
def ai_client(fake_provider: FakeProvider) -> AIClient:
    """An `AIClient` wired to the fake provider for both completions and embeddings."""
    return AIClient(fake_provider, embed_model=FAKE_EMBED_MODEL)


@pytest.fixture
def seed_voice(isolated_env: Path) -> Path:
    """Write a filled-in `data/seed_voice.md` into the temp cwd."""
    path = isolated_env / "data" / "seed_voice.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SEED_VOICE, encoding="utf-8")
    return path
