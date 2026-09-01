"""resolve miss log

Revision ID: c1f7a2e93b64
Revises: b8e2d4a10c73
Create Date: 2026-08-08 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c1f7a2e93b64"
down_revision: str | None = "b8e2d4a10c73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resolve_miss",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("directive", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("warning_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("bundle_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('governed', 'mixed', 'observed_only', 'no_match')",
            name=op.f("ck_resolve_miss_valid_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resolve_miss")),
    )
    op.create_index("ix_resolve_miss_status_created", "resolve_miss", ["status", "created_at"])
    op.create_index("ix_resolve_miss_created", "resolve_miss", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_resolve_miss_created", table_name="resolve_miss")
    op.drop_index("ix_resolve_miss_status_created", table_name="resolve_miss")
    op.drop_table("resolve_miss")
