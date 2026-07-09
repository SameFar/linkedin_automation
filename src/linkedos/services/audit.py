"""Reading the audit log.

Writes live in `services.workflow`, inside the transaction of the change being audited.
This module only reads, and returns detached dataclasses so the UI cannot hold an ORM
row past the end of its session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from linkedos.db.models import Actor, AuditAction, EntityType
from linkedos.db.repo import AuditRepo
from linkedos.db.session import get_session

# Re-exported so `ui/` can filter by action without importing `db/`.
__all__ = ["Actor", "AuditAction", "AuditEntry", "EntityType", "history_for_post", "list_recent"]


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One row of the audit log, detached from the session."""

    id: int
    at: datetime
    actor: Actor
    action: AuditAction
    entity_type: EntityType
    entity_id: int
    detail: str

    @property
    def at_utc(self) -> datetime:
        """`at`, guaranteed timezone-aware. SQLite hands back naive datetimes."""
        return self.at if self.at.tzinfo else self.at.replace(tzinfo=UTC)

    def summary(self) -> str:
        """One line, for a dashboard row."""
        base = f"{self.actor.value} {self.action.value} {self.entity_type.value} {self.entity_id}"
        return f"{base} — {self.detail}" if self.detail else base


def list_recent(
    limit: int = 20,
    *,
    entity_id: int | None = None,
    action: AuditAction | None = None,
) -> list[AuditEntry]:
    """Most recent audit entries first, optionally narrowed to one post or action."""
    with get_session() as session:
        rows = AuditRepo(session).list_recent(
            limit, entity_type=EntityType.POST, entity_id=entity_id, action=action
        )
        return [
            AuditEntry(
                id=row.id,
                at=row.at,
                actor=row.actor,
                action=row.action,
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                detail=row.detail,
            )
            for row in rows
        ]


def history_for_post(post_id: int, limit: int = 50) -> list[AuditEntry]:
    """Everything that ever happened to one post, newest first."""
    return list_recent(limit, entity_id=post_id)
