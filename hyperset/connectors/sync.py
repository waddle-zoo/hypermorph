"""Persisted sync orchestration (hy-gh-27, extended by hy-gh-17): wires
any `Connector`'s normalize() output through the hy-gh-26 repositories
(`PostgresSyncRepository`, `PostgresObservedAssetRepository`).

Source- and transport-neutral: the same orchestration runs a Superset
export bundle, a Superset REST read, and a DataHub GraphQL read. The
connector decides what it read and discloses it on the snapshot; this
module only persists it. Nothing here knows a source product name.

Callers own connection lifecycle (`PostgresConnectionRepository`
.create_or_update` + `connector.test_connection()`) before calling this --
sync orchestration only needs an already-valid `connection_id`, keeping
"can I reach the source" and "persist what I read" as separate concerns.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hyperset.connectors.types import Connector
from hyperset.repositories.errors import NotFoundError
from hyperset.repositories.postgres.observed_assets import PostgresObservedAssetRepository
from hyperset.repositories.postgres.sync import PostgresSyncRepository

# `SyncRun.mode` values (hyperset.db.models.SYNC_MODES) per transport: a
# live read is a full refresh of the source; a bundle read is an import of
# a static export. An unknown transport falls back to "full" rather than
# claiming a mode the source never proved.
_MODE_BY_TRANSPORT = {"rest": "full", "graphql": "full", "export_bundle": "fixture_import"}


def _deletion_declined(connector: Connector, asset_type: str) -> str:
    """The refusal, said out loud, once per asset type per run (hy-6nit).

    Silent non-deletion is its own hazard, and it is the one that hides for
    months: adds and updates keep flowing, so a run that deleted nothing looks
    like a source that lost nothing. So the decline is a warning on the run,
    persisted with it, rather than a comment in this file.

    Names which connector declined -- its own class and transport, read off
    the object, so this module still knows no source product name -- which
    entity type, the missing token by the name it has in the code, and the
    cost, which a reader should meet stated rather than infer.
    """
    return (
        f"deletion declined: {type(connector).__name__} over transport "
        f"'{connector.transport}' set no ConnectorSnapshot.established_denominators "
        f"entry for asset type '{asset_type}', so this run soft-deleted none of that "
        "type. This is a refusal, not an empty result. Cost: an asset genuinely "
        "removed at source stays live, and governed context keeps citing it. Accepted "
        "against the alternative -- one incomplete read soft-deleting every asset it "
        "never served (hy-6nit)."
    )


@dataclass
class SyncResult:
    """One run's in-memory summary: per-asset created/updated/restored/
    unchanged/deleted external ids, plus every warning either the connector
    snapshot or this sync raised.

    The buckets mirror the `ConnectorChange.change_type` values the
    repository wrote, `restored` included (hy-y8g.1) -- an operator reading
    these counters must never be told "unchanged" about an asset the change
    stream announced as back from the dead (hy-y8g.6).

    The durable record hy-gh-38's processor consumes is `connector_changes`
    (`PostgresConnectorChangeRepository.list_for_run(sync_run_id)`), written
    transactionally with each new observed version -- this object is a
    convenience for the caller that ran the sync, not the source of truth."""

    sync_run_id: str
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    transport: str = ""
    source_version: str | None = None
    checkpoint: dict | None = None
    relationships: int = 0
    """How many declared references the run's assets hold after it (hy-d7xh).

    Reported next to the unresolved-link warnings so "the source declares
    references" and "we could not resolve one" are both visible from one run,
    instead of only the failures being. Kept out of `counters`, which mirrors
    `ConnectorChange.change_type` values and stays a per-asset outcome count.

    Scoped to THIS run's assets, not to the table: an asset the source stopped
    serving is absent from the declaration, so `replace_relationships` leaves
    its outgoing rows standing (a partial read must not retract what it did not
    re-read) and they drop out of this number. So a fall from 3 to 2 means "one
    fewer asset was read", NOT "a reference was retracted" -- the retracted
    case is an asset that WAS read and declared less than before, and this
    number alone cannot tell the two apart (hy-ponl)."""

    @property
    def _buckets(self) -> dict[str, list[str]]:
        return {
            "created": self.created,
            "updated": self.updated,
            "restored": self.restored,
            "unchanged": self.unchanged,
            "deleted": self.deleted,
        }

    def record_outcome(self, outcome: str, external_id: str) -> None:
        """Bucket one asset by the outcome the repository reported, instead
        of re-deriving a change type the repository already decided."""
        self._buckets[outcome].append(external_id)

    @property
    def counters(self) -> dict:
        return {name: len(ids) for name, ids in self._buckets.items()}


def run_sync(
    *,
    connector: Connector,
    connection_id: str,
    session_factory,
    mode: str | None = None,
    partial: bool = False,
) -> SyncResult:
    """Run one sync: snapshot -> normalize -> persist.

    `partial=True` skips `mark_missing_deleted` entirely -- "partial syncs
    never imply deletion" (Required decisions). A snapshot also narrows
    deletion checks to the asset types it actually covered, so a REST
    snapshot (databases and datasets) never soft-deletes charts or
    dashboards observed earlier through an export bundle.

    Covering a type is necessary and not sufficient: a full sync soft-deletes
    only the types whose snapshot carries an `EstablishedDenominator`, and
    declines the rest out loud (hy-6nit). Default-deny, because a read that
    was short and a source that lost assets are the same input here, and only
    one of them may be acted on. No connector produces a denominator yet, so
    today this refuses every deletion pass on both connectors; adds, updates,
    restores and relationships are untouched.

    Never creates or touches `GovernedContext` -- observation only.
    """
    syncs = PostgresSyncRepository(session_factory)
    assets = PostgresObservedAssetRepository(session_factory)

    previous_checkpoint = syncs.get_checkpoint(connection_id)
    run = syncs.begin_run(
        connection_id,
        mode=mode or _MODE_BY_TRANSPORT.get(connector.transport, "full"),
        transport=connector.transport,
    )
    result = SyncResult(sync_run_id=run.id, transport=connector.transport)

    # Every step after begin_run is inside the guard, not just the read: a
    # raise anywhere in normalize/upsert/deletion would otherwise leave the
    # run in status "running" forever, and hy-gh-49's job execution builds
    # retry semantics on run status.
    try:
        return _run(
            connector=connector,
            connection_id=connection_id,
            syncs=syncs,
            assets=assets,
            run_id=run.id,
            previous_checkpoint=previous_checkpoint,
            partial=partial,
            result=result,
        )
    except Exception as exc:
        syncs.fail_run(run.id, errors=[str(exc)])
        raise


def _run(
    *,
    connector: Connector,
    connection_id: str,
    syncs: PostgresSyncRepository,
    assets: PostgresObservedAssetRepository,
    run_id: str,
    previous_checkpoint: dict | None,
    partial: bool,
    result: SyncResult,
) -> SyncResult:
    """The body of one run, with no failure handling of its own -- `run_sync`
    owns marking the `SyncRun` failed for anything raised in here."""
    snapshot = connector.snapshot(previous_checkpoint)

    result.warnings.extend(snapshot.warnings)
    result.source_version = snapshot.source_version
    # Pre-seed every asset type this snapshot covered, not just the ones it
    # happened to contain -- otherwise a covered type with zero assets (e.g.
    # every dashboard deleted upstream) never gets checked for deletion at
    # all, since it would never appear as a key from the normalize() loop.
    seen_by_type: dict[str, set[str]] = {
        asset_type: set() for asset_type in snapshot.covered_asset_types
    }

    # from_asset_id -> [(from_asset_type, from_external_id, UnresolvedLink)],
    # keyed rather than a flat list so that a source serving one asset twice in
    # one snapshot resolves the LAST yield's links, not the union of both: the
    # version chain already gives that asset's payload last-one-wins, and a
    # projection holding the union would claim a reference the newest payload
    # does not make (hy-3h7f).
    pending_links: dict[str, list[tuple[str, str, object]]] = {}
    # Every asset this snapshot covered gets an entry, including the ones that
    # declared no link at all: "this asset now references nothing" is an
    # observation the projection has to record, or a reference the source
    # dropped would stand forever.
    declared: dict[str, list[tuple[str, str]]] = {}
    for item in connector.normalize(snapshot):
        seen_by_type.setdefault(item.asset_type, set()).add(item.external_id)
        record, outcome = assets.upsert(
            connection_id=connection_id,
            external_id=item.external_id,
            asset_type=item.asset_type,
            sync_run_id=run_id,
            raw_payload=item.raw_payload,
            normalized=item.normalized,
            source_modified_at=item.source_modified_at,
            hash_basis=item.hash_basis,
            # Change detection compares within a transport (hy-6t4): two
            # transports of one source carry different amounts of the server's
            # own bookkeeping, so an asset nobody edited hashes differently
            # across them.
            transport=connector.transport,
        )
        result.record_outcome(outcome, item.external_id)
        declared[record.id] = []
        pending_links[record.id] = [
            (item.asset_type, item.external_id, link) for link in item.links
        ]

    # Resolve links against everything ever observed on this connection,
    # not just this snapshot -- a link's target may have been observed on
    # an earlier sync. A resolved link becomes an `asset_relationships` row
    # (hy-d7xh): the connector still only observes references, and this
    # orchestration is what turns an external id into the internal one the
    # projection can point at, exactly as `UnresolvedLink` says. An
    # unresolvable link is still only a warning (hy-gh-38 rule 7, "broken
    # asset relationship") -- a reference whose target was never observed
    # has no second endpoint to persist.
    for from_asset_id, links in pending_links.items():
        for from_type, from_external_id, link in links:
            try:
                target = assets.get_by_external_id(
                    connection_id=connection_id,
                    external_id=link.target_external_id,
                    asset_type=link.kind,
                )
            except NotFoundError:
                result.warnings.append(
                    f"unresolved link: {from_type}/{from_external_id} -> "
                    f"{link.kind}/{link.target_external_id} (target not observed)"
                )
                continue
            declared[from_asset_id].append((link.relation, target.id))

    result.relationships = len(assets.replace_relationships(declared=declared))

    if not partial:
        for asset_type, seen_ids in seen_by_type.items():
            producer = snapshot.established_denominators.get(asset_type)
            if not producer:
                result.warnings.append(_deletion_declined(connector, asset_type))
                continue
            deleted_ids = assets.mark_missing_deleted(
                connection_id=connection_id,
                asset_type=asset_type,
                seen_external_ids=seen_ids,
                sync_run_id=run_id,
            )
            result.deleted.extend(deleted_ids)

    if snapshot.checkpoint is not None:
        syncs.set_checkpoint(connection_id, checkpoint=snapshot.checkpoint, sync_run_id=run_id)
        result.checkpoint = snapshot.checkpoint

    syncs.finish_run(run_id, counters=result.counters, warnings=result.warnings)
    return result
