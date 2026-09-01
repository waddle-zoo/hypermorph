"""add a per-request correlation id to the admin audit log (hy-w9ntg, V1 gap Admin/8)

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-08-24 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ADDITIVE (hy-w9ntg): each admin action's audit row gains the id of the REQUEST that
    # performed it, so an operator can tie a response (returned as X-Correlation-Id) back to
    # its audit rows. NULLABLE with no server_default -- a correlation id is a per-request
    # value, so existing rows genuinely have none; NULL means "recorded before correlation
    # ids", never a fabricated or stale id.
    op.add_column("admin_audit_log", sa.Column("correlation_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("admin_audit_log", "correlation_id")
