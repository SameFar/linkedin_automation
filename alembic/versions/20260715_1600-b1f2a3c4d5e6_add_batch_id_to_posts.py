"""add batch_id to posts

Revision ID: b1f2a3c4d5e6
Revises: c9439aab940d
Create Date: 2026-07-15 16:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1f2a3c4d5e6"
down_revision: str | Sequence[str] | None = "c9439aab940d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("posts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("batch_id", sa.String(length=32), nullable=True))
        batch_op.create_index(batch_op.f("ix_posts_batch_id"), ["batch_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("posts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_posts_batch_id"))
        batch_op.drop_column("batch_id")
