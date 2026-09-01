"""context write-back config

Revision ID: d4e8b1c60a29
Revises: c1f7a2e93b64
Create Date: 2026-08-08 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e8b1c60a29"
down_revision: str | None = "c1f7a2e93b64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "context_writeback_config",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("repository", sa.String(), nullable=False),
        sa.Column("base_ref", sa.String(), nullable=False),
        sa.Column("manifest_path", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_context_writeback_config")),
    )


def downgrade() -> None:
    op.drop_table("context_writeback_config")
