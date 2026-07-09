"""Health and liveness — the one service the skeleton already needs.

`ui/app.py`, `cli.py`, and `scheduler/daemon.py` all reach the database through here
and never through `db/` directly. That constraint is the whole point of this module
existing before there is any real business logic to put in it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from linkedos import __version__
from linkedos.core.config import get_settings
from linkedos.core.logging import get_logger
from linkedos.db.models import Heartbeat
from linkedos.db.session import get_session

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AppStatus:
    """A snapshot of whether the app and its daemon are alive."""

    version: str
    db_path: Path
    db_exists: bool
    heartbeat_count: int
    last_heartbeat: datetime | None


def get_app_status() -> AppStatus:
    """Report app version and database liveness without side effects.

    Deliberately does not create the database: a `status` call on a fresh checkout
    should report `db_exists=False`, not silently provision a file.
    """
    settings = get_settings()
    db_path = settings.db_path
    db_exists = db_path.is_file()

    if not db_exists:
        return AppStatus(
            version=__version__,
            db_path=db_path,
            db_exists=False,
            heartbeat_count=0,
            last_heartbeat=None,
        )

    try:
        with get_session() as session:
            count = session.scalar(select(func.count()).select_from(Heartbeat))
            latest = session.scalar(select(func.max(Heartbeat.beat_at)))
    except OperationalError:
        # Database file exists but migrations have not run yet.
        logger.warning("heartbeat table missing; run `alembic upgrade head`")
        return AppStatus(
            version=__version__,
            db_path=db_path,
            db_exists=True,
            heartbeat_count=0,
            last_heartbeat=None,
        )

    return AppStatus(
        version=__version__,
        db_path=db_path,
        db_exists=True,
        heartbeat_count=int(count or 0),
        last_heartbeat=latest,
    )


def record_heartbeat(source: str = "scheduler") -> datetime:
    """Write one heartbeat row and return the timestamp it recorded.

    Called by the scheduler's interval job. Kept in the service layer so the daemon
    never imports `db/` itself.
    """
    with get_session() as session:
        beat = Heartbeat(source=source)
        session.add(beat)
        session.flush()
        beat_at = beat.beat_at

    logger.info("heartbeat recorded source=%s at=%s", source, beat_at.isoformat())
    return beat_at
