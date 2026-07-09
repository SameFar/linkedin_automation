"""Shared fixtures.

Every test runs in its own temp directory with a clean environment, so a stray `.env`
or an exported `ANTHROPIC_API_KEY` on the developer's machine can never leak into a
test run. Settings and the engine are cached per process, so both caches are cleared
around each test.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from linkedos.core.config import Settings, get_settings
from linkedos.db.models import Base
from linkedos.db.session import get_engine, get_sessionmaker

SETTINGS_ENV_VARS = tuple(name.upper() for name in Settings.model_fields)


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
