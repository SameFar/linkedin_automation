"""Engine and session smoke tests against a temp SQLite database."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from linkedos.db.models import Heartbeat
from linkedos.db.session import get_engine, get_session


def test_engine_points_at_settings_db_path(temp_db: Path) -> None:
    # `db_path` is relative, so the URL is too; it resolves against the process cwd.
    assert str(get_engine().url) == "sqlite+pysqlite:///data/linkedos.db"
    assert temp_db.is_file()


def test_sqlite_pragmas_are_applied(temp_db: Path) -> None:
    with get_engine().connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_session_commits_on_success(temp_db: Path) -> None:
    with get_session() as session:
        session.add(Heartbeat(source="test"))

    with get_session() as session:
        assert session.scalar(select(func.count()).select_from(Heartbeat)) == 1


def test_session_rolls_back_on_error(temp_db: Path) -> None:
    with pytest.raises(RuntimeError, match="boom"), get_session() as session:
        session.add(Heartbeat(source="doomed"))
        session.flush()
        raise RuntimeError("boom")

    with get_session() as session:
        assert session.scalar(select(func.count()).select_from(Heartbeat)) == 0
