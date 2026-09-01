"""workspace-scope the admin audit log (hq-hnrf, ADR-0037)

Revision ID: a1b2c3d4e5f6
Revises: e6f7a8b90c3d
Create Date: 2026-08-22 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "e6f7a8b90c3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ADDITIVE (hq-hnrf, ADR-0037): the admin audit trail gains a `workspace_id` so it
    # is tenant-scoped like every other config table (#438 scoped sources/targets/
    # connections but left the audit log global -- a cross-tenant READ of admin actions).
    # NOT NULL server_default 'default' BACKFILLS every existing row into the single
    # implicit 'default' workspace, so a single-tenant estate is unchanged.
    op.add_column(
        "admin_audit_log",
        sa.Column("workspace_id", sa.String(), nullable=False, server_default="default"),
    )


def downgrade() -> None:
    op.drop_column("admin_audit_log", "workspace_id")
