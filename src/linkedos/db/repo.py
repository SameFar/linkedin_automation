"""Repositories: the only place in the codebase that writes a `select()`.

Each table gets a small class that owns every query against it and returns ORM objects
or plain dataclasses — never a `Session`, never a raw `Row`.

Why bother:

* Services stay readable — they orchestrate `PostRepo` and `VoiceRepo` instead of
  assembling statements inline.
* Query logic is tested in one place against a temp SQLite database, offline.
* Transaction boundaries stay with the caller. Repositories take a `Session` and never
  commit; only `db.session.get_session()` decides when a transaction ends.

Rules that follow from the layering in CLAUDE.md: repositories may import `db.models`
and `core`, nothing else.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from linkedos.db.models import (
    AiCall,
    AuditAction,
    AuditLog,
    Embedding,
    EmbeddingKind,
    EntityType,
    Post,
    PostStatus,
    VoiceProfile,
)


@dataclass(frozen=True, slots=True)
class SpendRow:
    """Aggregated spend for one (model, purpose) pair over some window."""

    model: str
    purpose: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class PostRepo:
    """Every query against `posts`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, post: Post) -> Post:
        self._session.add(post)
        self._session.flush()
        return post

    def add_all(self, posts: Sequence[Post]) -> Sequence[Post]:
        self._session.add_all(posts)
        self._session.flush()
        return posts

    def get(self, post_id: int) -> Post | None:
        return self._session.get(Post, post_id)

    def get_many(self, post_ids: Sequence[int]) -> Sequence[Post]:
        """Fetch posts by id. Order of the result is not the order of `post_ids`."""
        if not post_ids:
            return []
        stmt = select(Post).where(Post.id.in_(post_ids))
        return list(self._session.scalars(stmt))

    def list_by_group(self, variant_group_id: str) -> Sequence[Post]:
        stmt = select(Post).where(Post.variant_group_id == variant_group_id).order_by(Post.id)
        return list(self._session.scalars(stmt))

    def list_by_status(self, status: PostStatus, limit: int = 100) -> Sequence[Post]:
        stmt = (
            select(Post).where(Post.status == status).order_by(Post.created_at.desc()).limit(limit)
        )
        return list(self._session.scalars(stmt))

    def count(self) -> int:
        return int(self._session.scalar(select(func.count()).select_from(Post)) or 0)

    def count_by_status(self) -> dict[PostStatus, int]:
        """Row counts per status. Statuses with no rows are absent from the mapping."""
        stmt = select(Post.status, func.count()).group_by(Post.status)
        return {status: int(count) for status, count in self._session.execute(stmt)}


class AuditRepo:
    """Every query against `audit_log`.

    Deliberately offers no `update` and no `delete`. The table is append-only; that is
    the property that makes it worth having.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, entry: AuditLog) -> AuditLog:
        self._session.add(entry)
        self._session.flush()
        return entry

    def list_recent(
        self,
        limit: int = 20,
        *,
        entity_type: EntityType | None = None,
        entity_id: int | None = None,
        action: AuditAction | None = None,
    ) -> Sequence[AuditLog]:
        """Most recent entries first, optionally narrowed to one entity or action."""
        stmt = select(AuditLog).order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(limit)
        if entity_type is not None:
            stmt = stmt.where(AuditLog.entity_type == entity_type)
        if entity_id is not None:
            stmt = stmt.where(AuditLog.entity_id == entity_id)
        if action is not None:
            stmt = stmt.where(AuditLog.action == action)
        return list(self._session.scalars(stmt))

    def count(self) -> int:
        return int(self._session.scalar(select(func.count()).select_from(AuditLog)) or 0)


class AiCallRepo:
    """Every query against `ai_calls`, the cost ledger."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, call: AiCall) -> AiCall:
        self._session.add(call)
        self._session.flush()
        return call

    def get(self, call_id: int) -> AiCall | None:
        return self._session.get(AiCall, call_id)

    def spend_since(self, since: datetime) -> Sequence[SpendRow]:
        """Spend grouped by model and purpose, for calls at or after `since`."""
        stmt = (
            select(
                AiCall.model,
                AiCall.purpose,
                func.count().label("calls"),
                func.sum(AiCall.input_tokens).label("input_tokens"),
                func.sum(AiCall.output_tokens).label("output_tokens"),
                func.sum(AiCall.cost_usd).label("cost_usd"),
            )
            .where(AiCall.at >= since)
            .group_by(AiCall.model, AiCall.purpose)
            .order_by(func.sum(AiCall.cost_usd).desc())
        )
        return [
            SpendRow(
                model=row.model,
                purpose=row.purpose,
                calls=int(row.calls),
                input_tokens=int(row.input_tokens or 0),
                output_tokens=int(row.output_tokens or 0),
                cost_usd=float(row.cost_usd or 0.0),
            )
            for row in self._session.execute(stmt)
        ]

    def total_since(self, since: datetime) -> float:
        stmt = select(func.sum(AiCall.cost_usd)).where(AiCall.at >= since)
        return float(self._session.scalar(stmt) or 0.0)


class EmbeddingRepo:
    """Every query against `embeddings`.

    Reads are always scoped by `model_name`: vectors from two different embedding models
    live in the same table but occupy different spaces, and comparing across them
    produces numbers that look like similarities and mean nothing.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, embedding: Embedding) -> Embedding:
        self._session.add(embedding)
        self._session.flush()
        return embedding

    def get_for_ref(self, kind: EmbeddingKind, ref_id: int, model_name: str) -> Embedding | None:
        stmt = select(Embedding).where(
            Embedding.kind == kind,
            Embedding.ref_id == ref_id,
            Embedding.model_name == model_name,
        )
        return self._session.scalars(stmt).first()

    def list_by_kind(self, kind: EmbeddingKind, model_name: str) -> Sequence[Embedding]:
        stmt = (
            select(Embedding)
            .where(Embedding.kind == kind, Embedding.model_name == model_name)
            .order_by(Embedding.id)
        )
        return list(self._session.scalars(stmt))

    def count(self) -> int:
        return int(self._session.scalar(select(func.count()).select_from(Embedding)) or 0)


class VoiceRepo:
    """Every query against `voice_profile`."""

    DEFAULT_NAME = "default"

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_name(self, name: str) -> VoiceProfile | None:
        stmt = select(VoiceProfile).where(VoiceProfile.name == name)
        return self._session.scalars(stmt).first()

    def get_default(self) -> VoiceProfile | None:
        return self.get_by_name(self.DEFAULT_NAME)

    def upsert(self, name: str, examples: str, guidelines: str) -> VoiceProfile:
        """Create the named profile, or overwrite its examples and guidelines."""
        profile = self.get_by_name(name)
        if profile is None:
            profile = VoiceProfile(name=name, examples=examples, guidelines=guidelines)
            self._session.add(profile)
        else:
            profile.examples = examples
            profile.guidelines = guidelines
        self._session.flush()
        return profile
