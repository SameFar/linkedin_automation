"""SQLAlchemy 2.0 declarative models.

`Base.metadata` is the single source of truth Alembic autogenerates against, so every
table in the app is defined in this module (or imported into it before Alembic runs).

Style: annotation-driven — `Mapped[...]` + `mapped_column(...)`, no legacy `Column`.

Timestamps are written as aware UTC datetimes. SQLite has no timezone type and hands
them back naive; treat every timestamp read from this database as UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, Float, Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for every linkedos table."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PostStatus(StrEnum):
    """Lifecycle of a post. Only `services.workflow` may move a post between these."""

    DRAFT = "draft"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    REJECTED = "rejected"
    FAILED = "failed"


class EmbeddingKind(StrEnum):
    """What an embedding vector was computed from."""

    POST = "post"
    JOB = "job"
    NOTE = "note"


class Actor(StrEnum):
    """Who caused an audited action.

    The distinction is the whole point of the audit log: a human approved this, a
    daemon published it, the system repaired it. When something appears on LinkedIn
    that nobody meant to publish, this column is the first place to look.
    """

    HUMAN = "human"
    SCHEDULER = "scheduler"
    SYSTEM = "system"


class AuditAction(StrEnum):
    """What happened. One value per state transition, plus the non-transition edits."""

    CREATED = "created"
    EDITED = "edited"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVERTED_TO_DRAFT = "reverted_to_draft"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    FAILED = "failed"
    REGENERATED = "regenerated"


class EntityType(StrEnum):
    """What kind of row an audit entry refers to."""

    POST = "post"


def _str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """Store a `StrEnum` by its *value*, as a CHECK-constrained VARCHAR.

    `native_enum=False` keeps the column human-readable under SQLite; `values_callable`
    stops SQLAlchemy from persisting the member *names* instead of the values.
    """
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda cls: [member.value for member in cls],
    )


class Heartbeat(Base):
    """One row per scheduler tick — proof the daemon is alive.

    Read by `services.status` to surface daemon liveness in the CLI and the UI.
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


class Post(Base):
    """A LinkedIn post at some point in its life, from draft to published.

    Variants generated together in one `create_drafts` run share a `variant_group_id`,
    so the approval UI can present them as alternatives and a human picks one.

    `prompt_version` records which versioned template produced the text, so a draft
    stays attributable to the prompt that wrote it after that prompt has moved on.
    """

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[PostStatus] = mapped_column(
        _str_enum(PostStatus, "post_status"),
        default=PostStatus.DRAFT,
        index=True,
        nullable=False,
    )
    variant_group_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    topic: Mapped[str] = mapped_column(String(512), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Written by the publishing integration in a later milestone. The URN LinkedIn hands
    # back is the only durable handle on a live post.
    linkedin_urn: Mapped[str | None] = mapped_column(String(128), default=None, unique=True)

    def __repr__(self) -> str:
        return f"Post(id={self.id!r}, status={self.status!r}, topic={self.topic!r})"


class AiCall(Base):
    """One row per external model call — the cost ledger.

    Written by `ai.client` after every completion and every embedding. `cost_usd` is
    computed from the token counts and the pricing table in `ai.pricing` at the moment
    of the call, so a later price change never retroactively rewrites history.
    """

    __tablename__ = "ai_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    model: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(64), default=None)

    def __repr__(self) -> str:
        return (
            f"AiCall(id={self.id!r}, model={self.model!r}, "
            f"purpose={self.purpose!r}, cost_usd={self.cost_usd!r})"
        )


class Embedding(Base):
    """A vector for one piece of text, stored as raw little-endian float32 bytes.

    Deliberately not a vector database. The corpus is one person's writing — hundreds of
    rows, not millions — so a NumPy dot product over every row beats any index, and
    there is one less service to run.

    `model_name` is part of a vector's identity: vectors from different embedding models
    are not comparable and must never be mixed in a similarity search.
    """

    __tablename__ = "embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[EmbeddingKind] = mapped_column(
        _str_enum(EmbeddingKind, "embedding_kind"), index=True, nullable=False
    )
    ref_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    model_name: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"Embedding(id={self.id!r}, kind={self.kind!r}, ref_id={self.ref_id!r})"


class AuditLog(Base):
    """Append-only record of every state change in the system.

    Written inside the same transaction as the change it describes, so a post cannot
    reach `approved` without an audit row saying who approved it. Nothing updates or
    deletes rows here; a mistake is corrected by a later row, not by a rewrite.

    `detail` is free text for whatever the action needs — a rejection reason, the
    number of characters an edit changed, the error that failed a publish.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True, nullable=False
    )
    actor: Mapped[Actor] = mapped_column(_str_enum(Actor, "actor"), index=True, nullable=False)
    action: Mapped[AuditAction] = mapped_column(
        _str_enum(AuditAction, "audit_action"), index=True, nullable=False
    )
    entity_type: Mapped[EntityType] = mapped_column(
        _str_enum(EntityType, "entity_type"), index=True, nullable=False
    )
    entity_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="", nullable=False)

    def __repr__(self) -> str:
        return (
            f"AuditLog(id={self.id!r}, actor={self.actor!r}, action={self.action!r}, "
            f"entity_id={self.entity_id!r})"
        )


class VoiceProfile(Base):
    """How the user writes: real past posts as few-shot examples, plus explicit rules.

    Seeded once from `data/seed_voice.md`. A single active row is the normal case; the
    `name` column exists so alternate voices (a second brand, a conference bio) can be
    added later without a schema change.
    """

    __tablename__ = "voice_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    examples: Mapped[str] = mapped_column(Text, default="", nullable=False)
    guidelines: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"VoiceProfile(id={self.id!r}, name={self.name!r})"
