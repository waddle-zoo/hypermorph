"""context write-back github app auth

Revision ID: a1c2e3f40b5d
Revises: f1a2b3c4d5e6
Create Date: 2026-08-09 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c2e3f40b5d"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # GitHub App write-back auth (hy-bdhg, ADR 0027): the enterprise default, a
    # third `token_source` ('github_app'). `app_id` is the App id; the App
    # PRIVATE KEY is stored encrypted at rest (AES-256-GCM, KEK from the
    # environment) in `app_key_ciphertext`/`app_key_nonce`. All nullable:
    # existing rows use env_ref or the encrypted PAT, and a local-path target
    # uses no auth at all. The minted installation token is never stored.
    op.add_column(
        "context_writeback_config",
        sa.Column("app_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "context_writeback_config",
        sa.Column("app_key_ciphertext", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "context_writeback_config",
        sa.Column("app_key_nonce", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("context_writeback_config", "app_key_nonce")
    op.drop_column("context_writeback_config", "app_key_ciphertext")
    op.drop_column("context_writeback_config", "app_id")
