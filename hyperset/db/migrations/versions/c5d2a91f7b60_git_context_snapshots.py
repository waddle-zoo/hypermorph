"""git context sources and snapshots

Revision ID: c5d2a91f7b60
Revises: c5d92a71f480
Create Date: 2026-07-27 19:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c5d2a91f7b60"
down_revision: str | None = "c5d92a71f480"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "context_sources",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("repository", sa.String(), nullable=False),
        sa.Column("ref", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("current_snapshot_id", sa.String(), nullable=True),
        sa.Column("last_attempt_status", sa.String(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempted_commit_sha", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "last_attempt_status IN ('never_synced', 'synced', 'unchanged', 'failed')",
            name=op.f("ck_context_sources_valid_last_attempt_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_context_sources")),
        sa.UniqueConstraint("repository", "ref", "path", name="uq_context_source_identity"),
    )
    op.create_table(
        "context_snapshots",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("commit_sha", sa.String(), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("files", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("normalized", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("owner_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["context_sources.id"],
            name=op.f("fk_context_snapshots_source_id_context_sources"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_context_snapshots")),
        sa.UniqueConstraint("source_id", "commit_sha", name="uq_context_snapshot_commit"),
    )
    op.create_index(
        "ix_context_snapshots_source_synced", "context_snapshots", ["source_id", "synced_at"]
    )
    # Added after both tables exist: the pointer is circular (a source names
    # its current snapshot, a snapshot names its source), same `use_alter`
    # shape the observed-asset/governed-context current-version pointers use.
    op.create_foreign_key(
        "fk_ctxsrc_current_snapshot",
        "context_sources",
        "context_snapshots",
        ["current_snapshot_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_ctxsrc_current_snapshot", "context_sources", type_="foreignkey")
    op.drop_index("ix_context_snapshots_source_synced", table_name="context_snapshots")
    op.drop_table("context_snapshots")
    op.drop_table("context_sources")
