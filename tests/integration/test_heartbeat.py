"""The heartbeat job writes a row, and the scheduler registers it on a 60s interval.

The job function is invoked directly. Nothing here starts a scheduler, sleeps, or waits
for a trigger to fire.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from linkedos.db.models import Heartbeat
from linkedos.db.session import get_session
from linkedos.scheduler.daemon import (
    HEARTBEAT_INTERVAL_SECONDS,
    HEARTBEAT_JOB_ID,
    PUBLISH_JOB_ID,
    build_scheduler,
)
from linkedos.scheduler.jobs import heartbeat_job
from linkedos.services.status import get_app_status


def test_heartbeat_job_writes_a_row(temp_db: Path) -> None:
    heartbeat_job()

    with get_session() as session:
        rows = list(session.scalars(select(Heartbeat)))

    assert len(rows) == 1
    assert rows[0].source == "scheduler"
    assert rows[0].beat_at is not None


def test_heartbeat_job_records_its_source(temp_db: Path) -> None:
    heartbeat_job(source="unit-test")

    with get_session() as session:
        assert session.scalar(select(Heartbeat.source)) == "unit-test"


def test_status_service_sees_the_heartbeat(temp_db: Path) -> None:
    assert get_app_status().heartbeat_count == 0

    heartbeat_job()

    status = get_app_status()
    assert status.db_exists
    assert status.heartbeat_count == 1
    assert status.last_heartbeat is not None


def test_scheduler_registers_the_heartbeat_job(temp_db: Path) -> None:
    scheduler = build_scheduler()

    job = scheduler.get_job(HEARTBEAT_JOB_ID)
    assert job is not None
    assert job.trigger.interval == timedelta(seconds=HEARTBEAT_INTERVAL_SECONDS)


def test_scheduler_registers_the_publish_job(temp_db: Path) -> None:
    scheduler = build_scheduler()

    assert {job.id for job in scheduler.get_jobs()} == {HEARTBEAT_JOB_ID, PUBLISH_JOB_ID}
    assert scheduler.get_job(PUBLISH_JOB_ID) is not None
