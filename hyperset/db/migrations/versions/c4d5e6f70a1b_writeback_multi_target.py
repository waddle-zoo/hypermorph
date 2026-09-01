"""context write-back multi-target routing (hq-1h1z)

Revision ID: c4d5e6f70a1b
Revises: b3d1f7a09c22
Create Date: 2026-08-20 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d5e6f70a1b"
down_revision: str | None = "b3d1f7a09c22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Phase-2 multi-target write-back (hq-1h1z): the write-back config becomes a
    # SET of targets rather than one singleton row. `routing_key` selects which
    # target a proposal's domain routes to (null on the default/catch-all target,
    # unique among keyed targets so a domain never fans out); `is_default` marks
    # the single catch-all a null/unmatched lookup falls back to; `enabled`
    # soft-disables a target without deleting it; `test_result` records the last
    # non-secret connectivity result.
    #
    # BACKWARD COMPAT: the existing singleton row (id='writeback') is promoted to
    # THE default target (`is_default=true`, null routing key), so a single-target
    # estate routes exactly as before -- no re-config, no lost secret refs.
    op.add_column(
        "context_writeback_config",
        sa.Column("routing_key", sa.String(), nullable=True),
    )
    op.add_column(
        "context_writeback_config",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "context_writeback_config",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "context_writeback_config",
        sa.Column("test_result", sa.String(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_context_writeback_config_routing_key",
        "context_writeback_config",
        ["routing_key"],
    )
    # Promote the pre-existing singleton to the default target. Scoped to the
    # known legacy id so it cannot accidentally flag a keyed target added later.
    op.execute("UPDATE context_writeback_config SET is_default = true WHERE id = 'writeback'")


def downgrade() -> None:
    op.drop_constraint(
        "uq_context_writeback_config_routing_key",
        "context_writeback_config",
        type_="unique",
    )
    op.drop_column("context_writeback_config", "test_result")
    op.drop_column("context_writeback_config", "enabled")
    op.drop_column("context_writeback_config", "is_default")
    op.drop_column("context_writeback_config", "routing_key")
