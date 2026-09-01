"""context write-back reviewer routing (hq-1rq7)

Revision ID: d5e6f7a80b2c
Revises: c4d5e6f70a1b
Create Date: 2026-08-21 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a80b2c"
down_revision: str | None = "c4d5e6f70a1b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Reviewer routing (hq-1rq7): a write-back TARGET may name the reviewer
    # handle(s) a proposal routed to it should go to (comma-separated). Null is
    # the honest FAIL-CLOSED default -- a proposal to a target with no reviewer
    # routing is recorded as an explicit needs-routing state, never silently
    # dropped or auto-approved. It confers no authority: authority stays a human
    # Git merge (ADR 0012). Nullable with no server default, so existing targets
    # read as unrouted until an operator sets reviewers.
    op.add_column(
        "context_writeback_config",
        sa.Column("reviewer_routing", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("context_writeback_config", "reviewer_routing")
