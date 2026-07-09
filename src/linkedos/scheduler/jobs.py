"""Job functions the scheduler registers. Stub — the heartbeat is the only one so far.

Each job is a module-level function taking JSON-serialisable arguments, because the
SQLAlchemy jobstore persists jobs by import path (`linkedos.scheduler.jobs:name`) and
must be able to resolve them again after a reboot. A job body does nothing but call a
service function and let exceptions escape to APScheduler's error logging.

Planned: draft generation, engagement metric refresh, weekly digest, database backup.
"""

from __future__ import annotations

from linkedos.core.logging import get_logger
from linkedos.services.status import record_heartbeat

logger = get_logger(__name__)


def heartbeat_job(source: str = "scheduler") -> None:
    """Record one heartbeat. Registered on a 60-second interval by `daemon`."""
    beat_at = record_heartbeat(source=source)
    logger.debug("heartbeat job finished at=%s", beat_at.isoformat())
