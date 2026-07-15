"""Schema-revision introspection: is this database file current with the code?

A database that exists is not a database that is usable. A checkout that grows a new
migration leaves every already-created `linkedos.db` one revision behind, and the first
query against the new table dies with `no such table`. `schema_is_current()` turns that
crash into a question the callers can ask before they query.

Nothing here runs a migration. `alembic upgrade head` is a human's decision.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from linkedos.core.logging import get_logger
from linkedos.db.session import get_engine

logger = get_logger(__name__)


def _script_location() -> Path | None:
    """Find the `alembic/` directory by walking up from this file.

    Returns `None` when the migration scripts are not on disk, which is the normal case
    for an installed wheel: only a source checkout ships them.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "alembic"
        if (candidate / "env.py").is_file():
            return candidate
    return None


@lru_cache(maxsize=1)
def head_revision() -> str | None:
    """The newest revision on disk, or `None` if it cannot be determined.

    Reads `alembic/versions/` directly; `env.py` is never imported, so this opens no
    connection and re-reads no settings.
    """
    location = _script_location()
    if location is None:
        return None

    config = Config()
    config.set_main_option("script_location", str(location))
    heads = ScriptDirectory.from_config(config).get_heads()

    if len(heads) != 1:
        # Zero heads means no migrations; several means a branch nobody merged.
        logger.warning("expected exactly one migration head, found %d", len(heads))
        return None
    return heads[0]


def current_revision() -> str | None:
    """The revision the database is stamped at, or `None` if it was never migrated.

    Connecting creates the file if it is absent, so callers must check that the database
    exists first when the answer matters.
    """
    with get_engine().connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def schema_is_current() -> bool:
    """Whether the database is stamped at the newest revision on disk.

    An unknowable head counts as current: we cannot prove the schema is stale, and
    refusing to run on that basis would break every install that ships without the
    migration scripts.
    """
    head = head_revision()
    if head is None:
        return True
    return current_revision() == head
