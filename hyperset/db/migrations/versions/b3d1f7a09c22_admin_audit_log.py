"""admin audit log (hy-gh-75)

Revision ID: b3d1f7a09c22
Revises: a1c2e3f40b5d
Create Date: 2026-08-18 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3d1f7a09c22"
down_revision: str | None = "a1c2e3f40b5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("actor_issuer", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=True),
        sa.Column("result", sa.String(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_audit_log")),
    )
    op.create_index(op.f("ix_admin_audit_log_at"), "admin_audit_log", ["at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_admin_audit_log_at"), table_name="admin_audit_log")
    op.drop_table("admin_audit_log")
