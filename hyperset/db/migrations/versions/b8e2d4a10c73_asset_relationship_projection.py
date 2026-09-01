"""asset relationship projection constraints

Revision ID: b8e2d4a10c73
Revises: a91f3c7d5e04
Create Date: 2026-07-30 01:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b8e2d4a10c73"
down_revision: str | None = "a91f3c7d5e04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `asset_relationships` shipped in the initial schema and nothing ever
    # wrote a row (hy-d7xh). Now that sync persists the references connectors
    # already observe, the table needs the two things a projection of declared
    # references needs and never had: one row per (from, to, relation), so a
    # resync cannot duplicate what the source declared once, and an index for
    # the only read a reference count performs.
    #
    # Both are additive on a table that is empty in every environment, so
    # there is no duplicate to collapse first.
    op.create_unique_constraint(
        "uq_asset_relationship",
        "asset_relationships",
        ["from_asset_id", "to_asset_id", "relation"],
    )
    op.create_index(
        "ix_asset_relationships_to_relation",
        "asset_relationships",
        ["to_asset_id", "relation"],
    )


def downgrade() -> None:
    op.drop_index("ix_asset_relationships_to_relation", table_name="asset_relationships")
    op.drop_constraint("uq_asset_relationship", "asset_relationships", type_="unique")
