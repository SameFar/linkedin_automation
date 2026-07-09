"""Engine and session management for the single local SQLite database.

The engine is built once per process from `settings.db_path`. Both the Streamlit app
and the scheduler daemon open the same file, so the connection is tuned for concurrent
readers with one writer: WAL journaling plus a busy timeout instead of instant `database
is locked` failures.

Tests reset state with `get_engine.cache_clear()` after pointing `DB_PATH` at a tmp dir.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from linkedos.core.config import get_settings

BUSY_TIMEOUT_MS = 5_000


def _configure_sqlite(dbapi_connection: Any, _connection_record: Any) -> None:  # noqa: ANN401
    """Apply per-connection PRAGMAs. SQLite forgets these between connections."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    finally:
        cursor.close()


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the process-wide engine, creating the data directory if needed."""
    settings = get_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite+pysqlite:///{settings.db_path}",
        # The scheduler hands connections between worker threads.
        connect_args={"check_same_thread": False},
        future=True,
    )
    event.listen(engine, "connect", _configure_sqlite)
    return engine


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    """Return the process-wide session factory."""
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a session inside a transaction, committing on success.

    Any exception rolls the transaction back and propagates; the session is closed
    either way.

        with get_session() as session:
            session.add(Heartbeat())
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
