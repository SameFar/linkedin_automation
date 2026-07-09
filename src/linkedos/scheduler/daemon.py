"""The linkedos scheduler daemon.

Runs a `BackgroundScheduler` whose jobstore is the same SQLite file the UI reads, so
jobs survive a reboot and the dashboard can see what the daemon has been doing. One
job today: a 60-second heartbeat.

Run it with `make run-scheduler` (`python -m linkedos.scheduler`), or under launchd — see
`deploy/` and the README.

The jobstore is built from `settings.db_path` rather than from `db.session.get_engine()`
on purpose: the scheduler layer imports `core/` and `services/` only, never `db/`.
"""

from __future__ import annotations

import signal
import threading
from types import FrameType

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from linkedos import __version__
from linkedos.core.config import get_settings
from linkedos.core.logging import configure_logging, get_logger
from linkedos.scheduler.jobs import heartbeat_job
from linkedos.services.status import get_app_status

logger = get_logger(__name__)

HEARTBEAT_JOB_ID = "heartbeat"
HEARTBEAT_INTERVAL_SECONDS = 60
JOBSTORE_TABLE = "apscheduler_jobs"


def build_scheduler() -> BackgroundScheduler:
    """Construct the scheduler and register every job. Does not start it.

    Split out from `main()` so tests can inspect the registered jobs without running
    them, and without waiting 60 seconds for anything.
    """
    settings = get_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)

    jobstore = SQLAlchemyJobStore(
        url=f"sqlite:///{settings.db_path}",
        tablename=JOBSTORE_TABLE,
    )
    scheduler = BackgroundScheduler(
        jobstores={"default": jobstore},
        job_defaults={
            # A missed heartbeat is not worth replaying; skip straight to the next.
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 30,
        },
    )
    scheduler.add_job(
        heartbeat_job,
        trigger="interval",
        seconds=HEARTBEAT_INTERVAL_SECONDS,
        id=HEARTBEAT_JOB_ID,
        name="Write a heartbeat row",
        replace_existing=True,
    )
    return scheduler


def main() -> int:
    """Run the scheduler in the foreground until SIGINT or SIGTERM. Returns exit code."""
    configure_logging()
    settings = get_settings()

    status = get_app_status()
    if not status.db_exists:
        logger.warning("database %s does not exist; run `alembic upgrade head`", settings.db_path)

    scheduler = build_scheduler()
    stop = threading.Event()

    def _request_stop(signum: int, _frame: FrameType | None) -> None:
        # A supervisor may deliver the signal to the process group as well as the
        # process; only the first one is worth logging or acting on.
        if stop.is_set():
            return
        logger.info("received signal %s; shutting down", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    scheduler.start()
    logger.info(
        "scheduler started version=%s db=%s heartbeat=%ss",
        __version__,
        settings.db_path,
        HEARTBEAT_INTERVAL_SECONDS,
    )

    stop.wait()

    scheduler.shutdown(wait=True)
    logger.info("scheduler stopped")
    return 0
