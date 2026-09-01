"""observed asset version and sync run transport

Revision ID: e4c81b2f6a37
Revises: f2b6c04a91d7
Create Date: 2026-07-28 09:55:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from hyperset.db.models import TRANSPORTS

revision: str = "e4c81b2f6a37"
down_revision: str | None = "f2b6c04a91d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Which transport produced a version, so change detection can compare an
    # observation against the last one made THE SAME WAY (hy-6t4). Two
    # transports of one source carry different amounts of the server's own
    # bookkeeping, so their payloads hash differently for an asset nobody
    # edited, and an alternating schedule would append a version per sync
    # forever.
    #
    # Named `transport` and not `read_mode`: `connector_checkpoints` already
    # stores `read_mode`, holding "full_refresh" -- a different vocabulary
    # entirely -- and two columns of one name holding unrelated values is a
    # trap for the next reader. `transport` is the word the rest of the code
    # uses: `ConnectorSnapshot.transport`, `SyncResult.transport`,
    # `checkpoint["transport"]`.
    #
    # Nullable with no backfill, deliberately, and the same convention
    # `hash_basis` set: a row written before this column existed has no
    # recorded transport, and guessing one from the run's `mode` would be
    # false -- "full" covers both REST and GraphQL. NULL means "not recorded",
    # so the first sync after this lands appends exactly one version per asset,
    # which is the correct and documented consequence of a new read mode
    # appearing rather than a defect to suppress.
    op.add_column(
        "observed_asset_versions",
        sa.Column("transport", sa.String(), nullable=True),
    )
    # Constrained here rather than in a later migration, because here it is
    # free: the column does not exist yet, so there are no rows to audit. A
    # free string forks a lineage on a case difference or a trailing space --
    # "REST" and "rest " each start a second chain -- and mode-scoped
    # comparison then finds nothing to compare against, appends a version and
    # reports first-sight forever. Every symptom of that looks exactly like
    # the defect this column exists to fix.
    op.create_check_constraint(
        "valid_version_transport",
        "observed_asset_versions",
        f"transport IS NULL OR transport IN {TRANSPORTS!r}",
    )
    # `SyncRun.mode` cannot answer this: it is "full" or "fixture_import" and
    # is derived FROM the transport, so REST and GraphQL are indistinguishable
    # in it. A reader asking "what was this run compared within" needs the
    # transport itself.
    op.add_column(
        "sync_runs",
        sa.Column("transport", sa.String(), nullable=True),
    )
    op.create_check_constraint(
        "valid_transport",
        "sync_runs",
        f"transport IS NULL OR transport IN {TRANSPORTS!r}",
    )


def downgrade() -> None:
    op.drop_constraint("valid_transport", "sync_runs", type_="check")
    op.drop_column("sync_runs", "transport")
    op.drop_constraint("valid_version_transport", "observed_asset_versions", type_="check")
    op.drop_column("observed_asset_versions", "transport")
