"""connector changes

Revision ID: b3f1c7d4e2a9
Revises: aab24c942488
Create Date: 2026-07-26 22:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3f1c7d4e2a9"
down_revision: str | None = "aab24c942488"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connector_changes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("connection_id", sa.String(), nullable=False),
        sa.Column("sync_run_id", sa.String(), nullable=False),
        sa.Column("asset_id", sa.String(), nullable=False),
        sa.Column("change_type", sa.String(), nullable=False),
        sa.Column("from_version_id", sa.String(), nullable=True),
        sa.Column("to_version_id", sa.String(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "change_type IN ('created', 'updated', 'deleted')",
            name=op.f("ck_connector_changes_valid_change_type"),
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["observed_assets.id"],
            name=op.f("fk_connector_changes_asset_id_observed_assets"),
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            name=op.f("fk_connector_changes_connection_id_connections"),
        ),
        sa.ForeignKeyConstraint(
            ["from_version_id"], ["observed_asset_versions.id"], name="fk_cc_from_version"
        ),
        sa.ForeignKeyConstraint(
            ["sync_run_id"],
            ["sync_runs.id"],
            name=op.f("fk_connector_changes_sync_run_id_sync_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["to_version_id"], ["observed_asset_versions.id"], name="fk_cc_to_version"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connector_changes")),
        sa.UniqueConstraint("to_version_id", name="uq_connector_change_version"),
    )
    op.create_index(
        "ix_connector_changes_connection_detected",
        "connector_changes",
        ["connection_id", "detected_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_connector_changes_connection_detected", table_name="connector_changes")
    op.drop_table("connector_changes")
