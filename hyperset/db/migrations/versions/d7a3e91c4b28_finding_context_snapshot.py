"""finding pins the git context snapshot and dedupes current findings

Revision ID: d7a3e91c4b28
Revises: c5d2a91f7b60
Create Date: 2026-07-27 20:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7a3e91c4b28"
down_revision: str | None = "c5d2a91f7b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "uq_findings_current_subject"


def upgrade() -> None:
    op.add_column("findings", sa.Column("affected_context_snapshot_id", sa.String(), nullable=True))
    op.create_foreign_key(
        op.f("fk_findings_affected_context_snapshot_id_context_snapshots"),
        "findings",
        "context_snapshots",
        ["affected_context_snapshot_id"],
        ["id"],
    )
    # Partial, so superseded and resolved history accumulates freely while a
    # rule can hold only one open question per asset per commit.
    op.create_index(
        _INDEX,
        "findings",
        ["finding_type", "affected_asset_id", "affected_context_snapshot_id"],
        unique=True,
        postgresql_where=sa.text("state = 'current'"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="findings")
    op.drop_constraint(
        op.f("fk_findings_affected_context_snapshot_id_context_snapshots"),
        "findings",
        type_="foreignkey",
    )
    op.drop_column("findings", "affected_context_snapshot_id")
