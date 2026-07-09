"""SQLAlchemy 2.0 declarative models.

`Base.metadata` is the single source of truth Alembic autogenerates against, so every
table in the app is defined in this module (or imported into it before Alembic runs).

Style: annotation-driven — `Mapped[...]` + `mapped_column(...)`, no legacy `Column`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for every linkedos table."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Heartbeat(Base):
    """One row per scheduler tick — proof the daemon is alive.

    Read by `services.status` to surface daemon liveness in the CLI and the UI.

    `beat_at` is written as an aware UTC datetime, but SQLite has no timezone type and
    hands it back naive. Treat every timestamp read from this database as UTC.
    """

    __tablename__ = "heartbeat"

    id: Mapped[int] = mapped_column(primary_key=True)
    beat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        index=True,
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(64), default="scheduler", nullable=False)

    def __repr__(self) -> str:
        return f"Heartbeat(id={self.id!r}, beat_at={self.beat_at!r}, source={self.source!r})"
