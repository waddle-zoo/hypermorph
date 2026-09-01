"""context write-back token reference

Revision ID: e7b3f9a2c815
Revises: d4e8b1c60a29
Create Date: 2026-08-09 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7b3f9a2c815"
down_revision: str | None = "d4e8b1c60a29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The NAME of a server-side secret (an env var / secret-store key), never the
    # token value (hy-eji4). A URL write-back target reads its GitHub token from
    # the environment under this name at propose time; the value is never stored.
    op.add_column(
        "context_writeback_config",
        sa.Column("token_ref", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("context_writeback_config", "token_ref")
