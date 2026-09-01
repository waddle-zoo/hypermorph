"""answer feedback and complete interaction trace

Revision ID: e6e671acd85d
Revises: d9e2f3a4b5c6
Create Date: 2026-08-29 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e6e671acd85d"
down_revision: str | None = "d9e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("mcp_interaction_trace", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_mcp_interaction_trace_nonnegative_interaction_duration",
        "mcp_interaction_trace",
        "duration_ms IS NULL OR duration_ms >= 0",
    )
    op.add_column(
        "mcp_interaction_trace",
        sa.Column(
            "source_staleness",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "mcp_interaction_trace",
        sa.Column("miss", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "mcp_interaction_trace", sa.Column("answer_bundle_id", sa.String(), nullable=True)
    )
    for name in ("decision_ids", "feedback_ids"):
        op.add_column(
            "mcp_interaction_trace",
            sa.Column(
                name,
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )

    op.create_table(
        "answer_feedback",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace", sa.String(), nullable=False, server_default="default"),
        sa.Column("principal_identity", sa.String(), nullable=False),
        sa.Column("trace_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("correlation_id", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("bundle_id", sa.String(), nullable=True),
        sa.Column("source_ref", sa.String(), nullable=True),
        sa.Column("review_task_id", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('accept', 'reject', 'include', 'ignore', 'correct', 'needs_review')",
            name=op.f("ck_answer_feedback_valid_answer_feedback_outcome"),
        ),
        sa.ForeignKeyConstraint(
            ["review_task_id"],
            ["review_tasks.id"],
            name=op.f("fk_answer_feedback_review_task_id_review_tasks"),
        ),
        sa.ForeignKeyConstraint(
            ["trace_id"],
            ["mcp_interaction_trace.id"],
            name=op.f("fk_answer_feedback_trace_id_mcp_interaction_trace"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_answer_feedback")),
    )
    op.create_index(
        "ix_answer_feedback_session", "answer_feedback", ["workspace", "session_id", "created_at"]
    )
    op.create_index(
        "ix_answer_feedback_correlation", "answer_feedback", ["workspace", "correlation_id"]
    )
    op.create_index("ix_answer_feedback_source", "answer_feedback", ["workspace", "source_ref"])
    op.create_index(
        "ix_answer_feedback_review_task", "answer_feedback", ["workspace", "review_task_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_answer_feedback_review_task", table_name="answer_feedback")
    op.drop_index("ix_answer_feedback_source", table_name="answer_feedback")
    op.drop_index("ix_answer_feedback_correlation", table_name="answer_feedback")
    op.drop_index("ix_answer_feedback_session", table_name="answer_feedback")
    op.drop_table("answer_feedback")
    op.drop_constraint(
        "ck_mcp_interaction_trace_nonnegative_interaction_duration",
        "mcp_interaction_trace",
        type_="check",
    )
    for name in (
        "feedback_ids",
        "decision_ids",
        "answer_bundle_id",
        "miss",
        "source_staleness",
        "duration_ms",
    ):
        op.drop_column("mcp_interaction_trace", name)
