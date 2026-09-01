"""mcp interaction trace

Revision ID: c8f1a2b3d4e5
Revises: b7c8d9e0f1a2
Create Date: 2026-08-26 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c8f1a2b3d4e5"
down_revision: str | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_interaction_trace",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace", sa.String(), nullable=False, server_default="default"),
        sa.Column("principal_identity", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("turn_id", sa.String(), nullable=True),
        sa.Column("tool_call_id", sa.String(), nullable=True),
        sa.Column("correlation_id", sa.String(), nullable=False),
        sa.Column("intent", sa.Text(), nullable=True),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("search_mode", sa.String(), nullable=True),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("hit_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('hit', 'miss', 'denied')",
            name=op.f("ck_mcp_interaction_trace_valid_interaction_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_interaction_trace")),
    )
    op.create_index(
        "ix_mcp_trace_session_created",
        "mcp_interaction_trace",
        ["session_id", "created_at"],
    )
    op.create_index("ix_mcp_trace_correlation", "mcp_interaction_trace", ["correlation_id"])
    op.create_index("ix_mcp_trace_created", "mcp_interaction_trace", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_mcp_trace_created", table_name="mcp_interaction_trace")
    op.drop_index("ix_mcp_trace_correlation", table_name="mcp_interaction_trace")
    op.drop_index("ix_mcp_trace_session_created", table_name="mcp_interaction_trace")
    op.drop_table("mcp_interaction_trace")
