"""tenant/workspace isolation on config tables (hq-t6nx, ADR-0037)

Revision ID: e6f7a8b90c3d
Revises: d5e6f7a80b2c
Create Date: 2026-08-22 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6f7a8b90c3d"
down_revision: str | None = "d5e6f7a80b2c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ADDITIVE tenant/workspace isolation (hq-t6nx, ADR-0037): a `workspace_id`
    # partitions each config table by tenant. NOT NULL with a server default of
    # 'default' BACKFILLS every existing row into the single implicit 'default'
    # workspace, so a single-tenant estate is unchanged. Identity uniqueness widens
    # to include the workspace, so two tenants may hold the same source
    # repository/ref/path or the same routing key without colliding.
    for table in ("context_sources", "context_writeback_config", "connections"):
        op.add_column(
            table,
            sa.Column("workspace_id", sa.String(), nullable=False, server_default="default"),
        )
    op.drop_constraint("uq_context_source_identity", "context_sources", type_="unique")
    op.create_unique_constraint(
        "uq_context_source_identity",
        "context_sources",
        ["workspace_id", "repository", "ref", "path"],
    )
    op.drop_constraint(
        "uq_context_writeback_config_routing_key", "context_writeback_config", type_="unique"
    )
    op.create_unique_constraint(
        "uq_context_writeback_config_routing_key",
        "context_writeback_config",
        ["workspace_id", "routing_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_context_writeback_config_routing_key", "context_writeback_config", type_="unique"
    )
    op.create_unique_constraint(
        "uq_context_writeback_config_routing_key", "context_writeback_config", ["routing_key"]
    )
    op.drop_constraint("uq_context_source_identity", "context_sources", type_="unique")
    op.create_unique_constraint(
        "uq_context_source_identity", "context_sources", ["repository", "ref", "path"]
    )
    for table in ("connections", "context_writeback_config", "context_sources"):
        op.drop_column(table, "workspace_id")
