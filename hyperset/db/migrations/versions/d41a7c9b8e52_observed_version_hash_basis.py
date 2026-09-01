"""observed asset version hash basis

Revision ID: d41a7c9b8e52
Revises: d7a3e91c4b28
Create Date: 2026-07-27 20:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d41a7c9b8e52"
down_revision: str | None = "d7a3e91c4b28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable with no server default, deliberately: a row written before this
    # column existed had its hash basis applied in connector memory and never
    # recorded, so backfilling `{}` ("the hash covers the whole payload") would
    # assert something false about REST and GraphQL rows. NULL means "basis not
    # recorded"; every row written from here on carries the rule it was hashed
    # under (hy-y8g finding 2).
    op.add_column(
        "observed_asset_versions",
        sa.Column("hash_basis", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("observed_asset_versions", "hash_basis")
