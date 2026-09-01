"""scope review tasks and reserve remote proposals

Revision ID: f0a1b2c3d4e5
Revises: e6e671acd85d
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f0a1b2c3d4e5"
down_revision: str | None = "e6e671acd85d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "review_tasks",
        sa.Column("workspace", sa.String(), nullable=False, server_default="default"),
    )
    op.add_column(
        "review_tasks",
        sa.Column("proposal_in_flight", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.drop_constraint("uq_review_task_idempotency_key", "review_tasks", type_="unique")
    op.create_unique_constraint(
        "uq_review_task_idempotency_key",
        "review_tasks",
        ["workspace", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_review_task_idempotency_key", "review_tasks", type_="unique")
    op.create_unique_constraint(
        "uq_review_task_idempotency_key", "review_tasks", ["idempotency_key"]
    )
    op.drop_column("review_tasks", "proposal_in_flight")
    op.drop_column("review_tasks", "workspace")
