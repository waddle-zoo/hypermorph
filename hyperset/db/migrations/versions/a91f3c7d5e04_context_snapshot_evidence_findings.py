"""context snapshot carries the refs that resolved to nothing

Revision ID: a91f3c7d5e04
Revises: e4c81b2f6a37
Create Date: 2026-07-29 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a91f3c7d5e04"
down_revision: str | None = "e4c81b2f6a37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing snapshots are backfilled with an empty list rather than NULL:
    # before this revision a snapshot could only exist if every declared ref
    # resolved, so "no unresolved refs" is what those rows actually mean.
    op.add_column(
        "context_snapshots",
        sa.Column(
            "evidence_findings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("context_snapshots", "evidence_findings", server_default=None)


def downgrade() -> None:
    op.drop_column("context_snapshots", "evidence_findings")
