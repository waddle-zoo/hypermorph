"""restored connector change

Revision ID: c5d92a71f480
Revises: b3f1c7d4e2a9
Create Date: 2026-07-27 18:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c5d92a71f480"
down_revision: str | None = "b3f1c7d4e2a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Already conventionalized (see NAMING_CONVENTION in hyperset/db/base.py and
# b3f1c7d4e2a9), so every use is wrapped in op.f() to stop Alembic prefixing
# it a second time. The wrapping happens inside upgrade()/downgrade(): at
# module level op.f() runs before Alembic establishes the Operations proxy
# and breaks `alembic revision`/`heads`/`history`.
_CONSTRAINT = "ck_connector_changes_valid_change_type"


def upgrade() -> None:
    op.drop_constraint(op.f(_CONSTRAINT), "connector_changes", type_="check")
    op.create_check_constraint(
        op.f(_CONSTRAINT),
        "connector_changes",
        "change_type IN ('created', 'updated', 'deleted', 'restored')",
    )


def downgrade() -> None:
    # 'restored' rows cannot be re-expressed as any older change type, so a
    # downgrade with reappearances already recorded fails loudly here rather
    # than silently rewriting observed history.
    op.drop_constraint(op.f(_CONSTRAINT), "connector_changes", type_="check")
    op.create_check_constraint(
        op.f(_CONSTRAINT),
        "connector_changes",
        "change_type IN ('created', 'updated', 'deleted')",
    )
