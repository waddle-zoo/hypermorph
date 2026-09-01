"""context write-back encrypted token

Revision ID: f1a2b3c4d5e6
Revises: e7b3f9a2c815
Create Date: 2026-08-09 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e7b3f9a2c815"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # An encrypted-at-rest write-back token, as an ALTERNATIVE to the env_ref
    # NAME reference (hy-up4k, ADR 0026). `token_source` selects the mode
    # ('env_ref' | 'encrypted'); the ciphertext and nonce hold an AES-256-GCM
    # encryption of the token, decryptable only with the KEK from the
    # environment -- never stored here. All nullable: existing rows are env_ref,
    # and a local-path target uses no token at all.
    op.add_column(
        "context_writeback_config",
        sa.Column("token_source", sa.String(), nullable=True),
    )
    op.add_column(
        "context_writeback_config",
        sa.Column("token_ciphertext", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "context_writeback_config",
        sa.Column("token_nonce", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("context_writeback_config", "token_nonce")
    op.drop_column("context_writeback_config", "token_ciphertext")
    op.drop_column("context_writeback_config", "token_source")
