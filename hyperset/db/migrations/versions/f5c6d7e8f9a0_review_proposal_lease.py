"""make proposal reservations reclaimable after a writer crash

Revision ID: f5c6d7e8f9a0
Revises: f4b5c6d7e8f9
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f5c6d7e8f9a0"
down_revision: str | None = "f4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("review_tasks", sa.Column("proposal_lease_id", sa.String(), nullable=True))
    op.add_column(
        "review_tasks",
        sa.Column("proposal_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("review_tasks", "proposal_lease_expires_at")
    op.drop_column("review_tasks", "proposal_lease_id")
