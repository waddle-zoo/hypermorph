"""citation<->answer + human decision linkage

Revision ID: d9e2f3a4b5c6
Revises: c8f1a2b3d4e5
Create Date: 2026-08-26 01:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9e2f3a4b5c6"
down_revision: str | None = "c8f1a2b3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answer_citations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace", sa.String(), nullable=False, server_default="default"),
        sa.Column("correlation_id", sa.String(), nullable=False),
        sa.Column("bundle_id", sa.String(), nullable=False),
        sa.Column("citation_ref", sa.String(), nullable=False),
        sa.Column("citation_kind", sa.String(), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "citation_kind IN ('provenance', 'approved_source')",
            name=op.f("ck_answer_citations_valid_citation_kind"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_answer_citations")),
        sa.UniqueConstraint(
            "workspace", "bundle_id", "citation_ref", "citation_kind", name="uq_answer_citation"
        ),
    )
    op.create_index("ix_answer_citation_bundle", "answer_citations", ["bundle_id"])
    op.create_index("ix_answer_citation_correlation", "answer_citations", ["correlation_id"])
    op.create_index("ix_answer_citation_ref", "answer_citations", ["citation_ref"])
    op.create_index("ix_answer_citation_source", "answer_citations", ["source_ref"])

    op.create_table(
        "citation_decisions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace", sa.String(), nullable=False, server_default="default"),
        sa.Column("principal_identity", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("citation_ref", sa.String(), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=True),
        sa.Column("review_task_id", sa.String(), nullable=True),
        sa.Column("correlation_id", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("superseded_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('include', 'exclude', 'approve', 'reject')",
            name=op.f("ck_citation_decisions_valid_citation_decision"),
        ),
        sa.ForeignKeyConstraint(
            ["review_task_id"],
            ["review_tasks.id"],
            name=op.f("fk_citation_decisions_review_task_id_review_tasks"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_citation_decisions")),
    )
    # Exactly one LIVE decision per (workspace, review_task, citation, principal): a
    # PARTIAL unique index over the not-yet-superseded rows, so a re-submit that first
    # supersedes the prior live row can insert without colliding. review_task_id is
    # NULLABLE and Postgres treats NULLs as DISTINCT, so a plain column index would let
    # bare-citation decisions (no task) have MANY live rows; COALESCE-normalize the
    # nullable key to a fixed empty sentinel so two live bare decisions collide.
    op.create_index(
        "uq_citation_decision_live",
        "citation_decisions",
        [
            "workspace",
            sa.text("coalesce(review_task_id, '')"),
            "citation_ref",
            "principal_identity",
        ],
        unique=True,
        postgresql_where=sa.text("superseded_by IS NULL"),
    )
    op.create_index("ix_citation_decision_task", "citation_decisions", ["review_task_id"])
    op.create_index("ix_citation_decision_ref", "citation_decisions", ["citation_ref"])
    op.create_index("ix_citation_decision_correlation", "citation_decisions", ["correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_citation_decision_correlation", table_name="citation_decisions")
    op.drop_index("ix_citation_decision_ref", table_name="citation_decisions")
    op.drop_index("ix_citation_decision_task", table_name="citation_decisions")
    op.drop_index("uq_citation_decision_live", table_name="citation_decisions")
    op.drop_table("citation_decisions")
    op.drop_index("ix_answer_citation_source", table_name="answer_citations")
    op.drop_index("ix_answer_citation_ref", table_name="answer_citations")
    op.drop_index("ix_answer_citation_correlation", table_name="answer_citations")
    op.drop_index("ix_answer_citation_bundle", table_name="answer_citations")
    op.drop_table("answer_citations")
