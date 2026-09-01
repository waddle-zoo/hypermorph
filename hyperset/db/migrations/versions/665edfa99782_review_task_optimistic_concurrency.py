"""review task optimistic concurrency

Revision ID: 665edfa99782
Revises: 7474282d0d4f
Create Date: 2026-07-25 11:21:50.546227
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "665edfa99782"
down_revision: str | None = "7474282d0d4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NOTE: autogenerate also proposed re-creating fk_gc_current_version,
    # fk_gcv_approval_decision, and fk_oa_current_version -- a known
    # autogenerate limitation reflecting self-referential `use_alter`
    # foreign keys against an already-migrated DB. Those three constraints
    # already exist from the initial schema migration; re-adding them here
    # would fail with "constraint already exists". Only the real change
    # (the new column) is kept.
    op.add_column(
        "review_tasks", sa.Column("row_version", sa.Integer(), nullable=False, server_default="1")
    )
    op.alter_column("review_tasks", "row_version", server_default=None)


def downgrade() -> None:
    op.drop_column("review_tasks", "row_version")
