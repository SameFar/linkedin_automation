"""Job functions the scheduler registers.

Each job is a module-level function taking JSON-serialisable arguments, because the
SQLAlchemy jobstore persists jobs by import path (`linkedos.scheduler.jobs:name`) and
must be able to resolve them again after a reboot. A job body does nothing but call a
service function and let exceptions escape to APScheduler's error logging.

Planned: draft generation, engagement metric refresh, weekly digest, database backup.
"""

from __future__ import annotations

from linkedos.core.config import get_settings
from linkedos.core.logging import get_logger
from linkedos.services.publishing import publish_due
from linkedos.services.status import record_heartbeat

logger = get_logger(__name__)


def heartbeat_job(source: str = "scheduler") -> None:
    """Record one heartbeat. Registered on a 60-second interval by `daemon`."""
    beat_at = record_heartbeat(source=source)
    logger.debug("heartbeat job finished at=%s", beat_at.isoformat())


def publish_due_posts_job() -> None:
    """Publish every scheduled post whose time has come.

    Gated on `settings.publish_enabled`, which is off by default: until the real LinkedIn
    transport ships, publishing would only fail every due post, so the job stays inert and
    a scheduled post simply waits. When the flag is on, this hands the sweep to
    `services.publishing`, which uses the production publisher.
    """
    if not get_settings().publish_enabled:
        logger.debug("publish job skipped: PUBLISH_ENABLED is off")
        return
    run = publish_due()
    logger.info(
        "publish job finished: %d published, %d failed", len(run.published), len(run.failed)
    )
