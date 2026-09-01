"""scope answer-citation idempotency by the interaction correlation chain

Revision ID: f3a4b5c6d7e8
Revises: f0a1b2c3d4e5
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: str | None = "f0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_answer_citation", "answer_citations", type_="unique")
    op.create_unique_constraint(
        "uq_answer_citation",
        "answer_citations",
        ["workspace", "bundle_id", "correlation_id", "citation_ref", "citation_kind"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_answer_citation", "answer_citations", type_="unique")
    op.create_unique_constraint(
        "uq_answer_citation",
        "answer_citations",
        ["workspace", "bundle_id", "citation_ref", "citation_kind"],
    )
