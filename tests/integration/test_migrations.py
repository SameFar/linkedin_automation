"""Schema-freshness detection.

The bug these cover: a `linkedos.db` created before a migration was written stays on
disk, `db_exists` is `True`, and the first query against a table added by that migration
dies with `sqlite3.OperationalError: no such table: posts` — several layers below anyone
who could explain it. `AppStatus.needs_migration` is the check that turns that crash
into a sentence.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from linkedos.db.migrations import current_revision, head_revision, schema_is_current
from linkedos.db.session import get_engine
from linkedos.services.status import get_app_status

STALE = "stale00000ab"


def _set_revision(revision: str) -> None:
    with get_engine().begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = :rev"), {"rev": revision})


def test_head_revision_is_a_single_resolvable_revision() -> None:
    """Two heads means someone branched the migration chain and never merged it."""
    assert head_revision() is not None


def test_temp_db_is_stamped_at_head(temp_db: Path) -> None:
    assert current_revision() == head_revision()
    assert schema_is_current()


def test_status_reports_a_current_schema(temp_db: Path) -> None:
    app_status = get_app_status()

    assert app_status.db_exists
    assert app_status.schema_is_current
    assert not app_status.needs_migration
    assert app_status.db_revision == app_status.head_revision


def test_status_flags_a_database_behind_the_code(temp_db: Path) -> None:
    _set_revision(STALE)

    app_status = get_app_status()

    assert app_status.db_exists
    assert app_status.needs_migration
    assert not app_status.schema_is_current
    assert app_status.db_revision == STALE
    assert app_status.head_revision != STALE


def test_status_flags_a_database_that_was_never_migrated(temp_db: Path) -> None:
    with get_engine().begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))

    app_status = get_app_status()

    assert app_status.needs_migration
    assert app_status.db_revision is None


def test_status_on_a_stale_schema_does_not_query_the_tables(temp_db: Path) -> None:
    """The point of the guard: report, do not touch tables that may not exist."""
    _set_revision(STALE)
    with get_engine().begin() as connection:
        connection.execute(text("DROP TABLE heartbeat"))

    app_status = get_app_status()

    assert app_status.needs_migration
    assert app_status.heartbeat_count == 0
    assert app_status.last_heartbeat is None


def test_a_missing_database_needs_no_migration(isolated_env: Path) -> None:
    """Nothing to migrate and nothing to warn about: `alembic upgrade head` creates it."""
    app_status = get_app_status()

    assert not app_status.db_exists
    assert not app_status.needs_migration
    assert not (isolated_env / "data" / "linkedos.db").exists()
