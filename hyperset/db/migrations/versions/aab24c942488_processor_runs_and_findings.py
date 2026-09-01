"""processor runs and findings

Revision ID: aab24c942488
Revises: 665edfa99782
Create Date: 2026-07-25 13:49:25.809414
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "aab24c942488"
down_revision: str | None = "665edfa99782"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NOTE: autogenerate also proposed re-creating fk_gc_current_version,
    # fk_gcv_approval_decision, and fk_oa_current_version -- the same known
    # autogenerate limitation reflecting self-referential `use_alter`
    # foreign keys against an already-migrated DB (see migration
    # 665edfa99782's note). Those three already exist; only the two new
    # tables are created here.
    op.create_table(
        "processor_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("trigger_type", sa.String(), nullable=False),
        sa.Column("trigger_ref", sa.String(), nullable=True),
        sa.Column("rule_versions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("counters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column("errors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name=op.f("ck_processor_runs_valid_status"),
        ),
        sa.CheckConstraint(
            "trigger_type IN ('sync', 'freshness', 'evaluation', 'manual')",
            name=op.f("ck_processor_runs_valid_trigger_type"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processor_runs")),
    )
    op.create_index(
        "uq_processor_runs_active_trigger",
        "processor_runs",
        ["trigger_type", sa.literal_column("COALESCE(trigger_ref, '')")],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_table(
        "findings",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("finding_type", sa.String(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("processor_run_id", sa.String(), nullable=False),
        sa.Column("affected_asset_id", sa.String(), nullable=True),
        sa.Column("affected_context_id", sa.String(), nullable=True),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("proposed_reviewer", sa.String(), nullable=True),
        sa.Column("proposed_action", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("review_task_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'error', 'critical')",
            name=op.f("ck_findings_valid_severity"),
        ),
        sa.CheckConstraint(
            "state IN ('current', 'superseded', 'resolved')", name=op.f("ck_findings_valid_state")
        ),
        sa.ForeignKeyConstraint(
            ["affected_asset_id"],
            ["observed_assets.id"],
            name=op.f("fk_findings_affected_asset_id_observed_assets"),
        ),
        sa.ForeignKeyConstraint(
            ["affected_context_id"],
            ["governed_context.id"],
            name=op.f("fk_findings_affected_context_id_governed_context"),
        ),
        sa.ForeignKeyConstraint(
            ["processor_run_id"],
            ["processor_runs.id"],
            name=op.f("fk_findings_processor_run_id_processor_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["review_task_id"],
            ["review_tasks.id"],
            name=op.f("fk_findings_review_task_id_review_tasks"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_findings")),
    )


def downgrade() -> None:
    op.drop_table("findings")
    op.drop_index(
        "uq_processor_runs_active_trigger",
        table_name="processor_runs",
        postgresql_where=sa.text("status = 'running'"),
    )
    op.drop_table("processor_runs")
