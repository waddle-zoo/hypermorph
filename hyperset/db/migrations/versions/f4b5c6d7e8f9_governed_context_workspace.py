"""scope governed context identities by workspace

Revision ID: f4b5c6d7e8f9
Revises: f3a4b5c6d7e8
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4b5c6d7e8f9"
down_revision: str | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "governed_context",
        sa.Column("workspace", sa.String(), nullable=False, server_default="default"),
    )
    op.drop_constraint("uq_governed_context_identity", "governed_context", type_="unique")
    op.create_unique_constraint(
        "uq_governed_context_identity",
        "governed_context",
        ["workspace", "context_type", "domain", "name"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_governed_context_identity", "governed_context", type_="unique")
    op.create_unique_constraint(
        "uq_governed_context_identity", "governed_context", ["context_type", "domain", "name"]
    )
    op.drop_column("governed_context", "workspace")
