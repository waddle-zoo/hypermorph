"""connector change query indexes

Revision ID: f2b6c04a91d7
Revises: d41a7c9b8e52
Create Date: 2026-07-27 20:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f2b6c04a91d7"
down_revision: str | None = "d41a7c9b8e52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `PostgresConnectorChangeRepository` reads the stream exactly two ways --
    # by run and by asset -- and orders both by `detected_at`, so these are the
    # only access paths the table has ever had (hy-y8g finding 5). The existing
    # `(connection_id, detected_at)` index serves neither, because neither
    # query filters on the connection.
    op.create_index(
        "ix_connector_changes_run_detected",
        "connector_changes",
        ["sync_run_id", "detected_at"],
    )
    op.create_index(
        "ix_connector_changes_asset_detected",
        "connector_changes",
        ["asset_id", "detected_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_connector_changes_asset_detected", table_name="connector_changes")
    op.drop_index("ix_connector_changes_run_detected", table_name="connector_changes")
