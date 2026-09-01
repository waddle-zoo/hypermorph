"""The read-only operator view's surfaces (hy-gh-72).

S1 (hy-9vji): sync health -- per connection, whether the last sync SUCCEEDED and
when, read through the same repositories the served playground-status path uses
(`PostgresConnectionRepository.list`, `PostgresSyncRepository`).

S2 (hy-3yri): git context -- per source, what commit the governed context is
pinned to and whether the last sync attempt succeeded, read through the same
reader `cmd_context_status`/`cmd_context_show` print
(`PostgresContextRepository.list_sources`).

READ-ONLY: no writer is constructed or called anywhere in `hyperset/ops`,
enforced structurally by tests/unit/ops/test_read_only_guard.py -- which
discovers every repository writer and asserts this package calls none, so the
git-context path is covered the moment it lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hyperset.repositories.dto import SyncRunRecord
from hyperset.repositories.postgres import (
    PostgresConnectionRepository,
    PostgresContextRepository,
    PostgresSyncRepository,
)
from hyperset.repositories.scope import ALL_WORKSPACES


@dataclass(frozen=True)
class ConnectionSyncHealth:
    """One connection's sync health: what it is, and its last FINISHED run.

    `last_sync` is the most recent run that reached a terminal state (succeeded
    or failed), or `None` when the connection has never finished one -- a run
    still `running` is not health, it is a read in progress. The outcome, time
    and counters ride on the `SyncRunRecord` so a reader sees exactly what the
    last sync did.
    """

    connection_id: str
    connector_type: str
    display_name: str
    enabled: bool
    last_sync: SyncRunRecord | None

    @property
    def outcome(self) -> str:
        """`succeeded`, `failed`, or `never` -- the one-word health verdict."""
        return self.last_sync.status if self.last_sync is not None else "never"


def read_sync_health(session_factory) -> list[ConnectionSyncHealth]:
    """Every connection's sync health, read-only.

    One row per registered connection (enabled or not: a disabled connection
    whose last sync failed is exactly what an operator needs to see), each with
    its last finished run. No mutation, no live probe of the source -- this
    reports what the store already recorded.
    """
    # An operator sync-health overview spans every tenant on PURPOSE (hq-t6nx): an
    # EXPLICIT system opt-in, not a silent global default.
    connections = PostgresConnectionRepository(session_factory).list(workspace=ALL_WORKSPACES)
    syncs = PostgresSyncRepository(session_factory)
    return [
        ConnectionSyncHealth(
            connection_id=connection.id,
            connector_type=connection.connector_type,
            display_name=connection.display_name,
            enabled=connection.enabled,
            last_sync=syncs.latest_finished_run(connection.id),
        )
        for connection in connections
    ]


@dataclass(frozen=True)
class GitContextHealth:
    """One Git context source: what it is pinned to, and its last sync attempt.

    THE PIN is the current snapshot -- the commit the governed context is served
    from right now. `pinned` is False when the source has never synced
    successfully (`current_snapshot` is None); the snapshot fields are then None
    and only the attempt half is populated, which is exactly the state an operator
    needs to see (a source that was added but never pinned).

    THE ATTEMPT is the last sync's health, whether or not it changed the pin: a
    failed attempt leaves the previous snapshot serving (`last_attempt_status`
    `failed`, `last_error` set, `last_attempted_commit_sha` naming the commit it
    tried), so the pin can be current while the last attempt failed -- the two are
    reported separately for that reason.
    """

    source_id: str
    repository: str
    ref: str
    path: str
    display_name: str
    enabled: bool
    snapshot_id: str | None
    commit_sha: str | None
    committed_at: datetime | None
    content_hash: str | None
    domain: str | None
    title: str | None
    last_attempt_status: str
    last_attempt_at: datetime | None
    last_attempted_commit_sha: str | None
    last_error: str | None

    @property
    def pinned(self) -> bool:
        """Whether a snapshot is currently pinned (a successful sync happened)."""
        return self.snapshot_id is not None


def read_git_context(session_factory, *, workspace: str | None = None) -> list[GitContextHealth]:
    """Every Git context source's pin and last-attempt health, read-only.

    One row per registered source, via the same `list_sources` reader the CLI's
    `context status`/`context show` already print. No mutation, no network probe
    of the remote -- it reports the pin the store already resolved and the last
    attempt it already recorded. Scoped to `workspace` when given (hq-t6nx): the
    admin sources list passes the caller's workspace so a tenant sees only its own.
    """
    sources = PostgresContextRepository(session_factory).list_sources(workspace=workspace)
    rows: list[GitContextHealth] = []
    for source in sources:
        snapshot = source.current_snapshot
        rows.append(
            GitContextHealth(
                source_id=source.id,
                repository=source.repository,
                ref=source.ref,
                path=source.path,
                display_name=source.display_name,
                enabled=source.enabled,
                snapshot_id=snapshot.id if snapshot else None,
                commit_sha=snapshot.commit_sha if snapshot else None,
                committed_at=snapshot.committed_at if snapshot else None,
                content_hash=snapshot.content_hash if snapshot else None,
                domain=snapshot.domain if snapshot else None,
                title=snapshot.title if snapshot else None,
                last_attempt_status=source.last_attempt_status,
                last_attempt_at=source.last_attempt_at,
                last_attempted_commit_sha=source.last_attempted_commit_sha,
                last_error=source.last_error,
            )
        )
    return rows


# The observed-asset fields the operator view surfaces, out of the richer entry
# `_linked_evidence` emits. Named so the CLI and the tests read the same list.
#
# `linked_version_id` and `observed_version_id` are BOTH carried on purpose (hy-hske
# round 3): `observed_version` / `content_sha256` describe the asset's CURRENT version and
# DRIFT when the source moves after the commit was pinned, so on their own the operator
# cannot tell which exact observed version Git actually linked against. `linked_version_id`
# is that pinned identity (null for an observed-only ref no commit linked), and
# `observed_version_id` is the current version's id; surfacing both makes drift legible --
# they are equal when the source has not moved, and differ once it has.
_OBSERVED_ASSET_KEYS = (
    "ref",
    "connector",
    "observed_version",
    "observed_version_id",
    "content_sha256",
    "linked_version_id",
    "governance",
)


@dataclass(frozen=True)
class DomainEvidence:
    """The linked evidence a source's governed context serves, per domain.

    OBSERVED-ASSET FRESHNESS, not a governed policy: `freshness` here is the
    observed version's own timeline (`last_observed_at`, `observed_version_at`,
    `source_modified_at`, `deleted_at`), the state a sync recorded -- NOT the
    per-source `freshness` facet or the `freshness_stale` reconciliation kind,
    which are governed declarations. `deprecations` are the two the resolver
    emits: `source_deleted` (the observation is gone) and `prohibited_by_context`
    (Git forbids the ref). Every field is exactly what the served bundle carries,
    because it is produced by the same `_linked_evidence`.
    """

    source_id: str
    domain: str
    observed_assets: tuple[dict, ...]
    freshness: tuple[dict, ...]
    deprecations: tuple[dict, ...]


def read_linked_evidence(session_factory) -> list[DomainEvidence]:
    """The linked evidence per resolved domain, read-only.

    REUSES `bundle/resolver.py::_linked_evidence` -- the exact per-ref evidence
    the served bundle carries -- rather than re-deriving it, so the operator view
    and the bundle cannot drift. One `DomainEvidence` per source that has a pinned
    snapshot (a source with no snapshot governs nothing, so it has no linked
    evidence). An empty `ContextDirective` names no extra refs, so the evidence is
    exactly the commit's own declared refs.

    `_linked_evidence` reads only (`PostgresObservedAssetRepository.get`,
    `PostgresProcessorRepository.list_findings`); it constructs and calls no
    writer, so this stays within the read-only boundary the ops guard enforces.
    """
    from hyperset.bundle.directive import ContextDirective
    from hyperset.bundle.resolver import _linked_evidence

    sources = PostgresContextRepository(session_factory).list_sources()
    rows: list[DomainEvidence] = []
    for source in sources:
        snapshot = source.current_snapshot
        if snapshot is None:
            continue
        evidence, _relationships, _warnings = _linked_evidence(
            session_factory, snapshot=snapshot, directive=ContextDirective()
        )
        rows.append(
            DomainEvidence(
                source_id=source.id,
                domain=snapshot.domain,
                observed_assets=tuple(
                    {key: asset.get(key) for key in _OBSERVED_ASSET_KEYS}
                    for asset in evidence.get("observed_assets") or []
                ),
                freshness=tuple(evidence.get("freshness") or []),
                deprecations=tuple(evidence.get("deprecations") or []),
            )
        )
    return rows
