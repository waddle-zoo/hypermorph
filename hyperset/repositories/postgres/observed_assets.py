from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, aliased, sessionmaker

from hyperset.db.base import utcnow
from hyperset.db.models import (
    AssetRelationship,
    Connection,
    ConnectorChange,
    ObservedAsset,
    ObservedAssetVersion,
)
from hyperset.repositories.dto import (
    AssetRelationshipRecord,
    ConnectorChangeRecord,
    IncomingReferenceRecord,
    ObservedAssetRecord,
    ObservedAssetVersionRecord,
)
from hyperset.repositories.errors import NotFoundError
from hyperset.repositories.hash_basis import EMPTY, apply_hash_basis
from hyperset.repositories.scope import _AllWorkspaces


def _version_record(row: ObservedAssetVersion | None) -> ObservedAssetVersionRecord | None:
    if row is None:
        return None
    return ObservedAssetVersionRecord(
        id=row.id,
        asset_id=row.asset_id,
        sync_run_id=row.sync_run_id,
        version=row.version,
        raw_payload=row.raw_payload,
        normalized=row.normalized,
        content_hash=row.content_hash,
        hash_basis=row.hash_basis,
        transport=row.transport,
        created_at=row.created_at,
    )


def _asset_record(row: ObservedAsset, current: ObservedAssetVersion | None) -> ObservedAssetRecord:
    return ObservedAssetRecord(
        id=row.id,
        connection_id=row.connection_id,
        external_id=row.external_id,
        asset_type=row.asset_type,
        current_version=_version_record(current),
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        source_modified_at=row.source_modified_at,
        deleted_at=row.deleted_at,
    )


def _relationship_record(row: AssetRelationship) -> AssetRelationshipRecord:
    return AssetRelationshipRecord(
        id=row.id,
        from_asset_id=row.from_asset_id,
        to_asset_id=row.to_asset_id,
        relation=row.relation,
        detail=row.detail,
    )


def _change_record(row: ConnectorChange) -> ConnectorChangeRecord:
    return ConnectorChangeRecord(
        id=row.id,
        connection_id=row.connection_id,
        sync_run_id=row.sync_run_id,
        asset_id=row.asset_id,
        change_type=row.change_type,
        from_version_id=row.from_version_id,
        to_version_id=row.to_version_id,
        detail=row.detail,
        detected_at=row.detected_at,
    )


def _search_text(normalized: dict) -> str:
    return " ".join(str(v) for v in normalized.values() if isinstance(v, str)) or " "


def _content_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class PostgresObservedAssetRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _normalised(transport: str | None) -> str | None:
        """Trimmed and lowercased before it decides a lineage.

        The CHECK constraint would reject `"REST"` and `"rest "` outright, but
        rejecting a sync for a stray space is a worse outcome than accepting
        the value it obviously meant. Normalising here makes the constraint a
        backstop rather than the only defence (hy-6t4).
        """
        return transport.strip().lower() if transport is not None else None

    @staticmethod
    def _latest_in_transport(session, asset, transport):
        """The most recent version this read mode produced, or `None` when it
        has produced none -- which is why the first read by a new mode appends
        exactly one version (hy-6t4). That is correct rather than a defect:
        suppressing it would be indistinguishable from failing to notice a
        genuine change."""
        return session.execute(
            select(ObservedAssetVersion)
            .where(
                ObservedAssetVersion.asset_id == asset.id,
                ObservedAssetVersion.transport.is_(None)
                if transport is None
                else ObservedAssetVersion.transport == transport,
            )
            .order_by(ObservedAssetVersion.version.desc())
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def _next_version(session, asset) -> int:
        """One chain per asset, numbered by observation and not by mode."""
        highest = session.execute(
            select(func.max(ObservedAssetVersion.version)).where(
                ObservedAssetVersion.asset_id == asset.id
            )
        ).scalar_one_or_none()
        return (highest or 0) + 1

    def upsert(
        self,
        *,
        connection_id: str,
        external_id: str,
        asset_type: str,
        sync_run_id: str,
        raw_payload: dict,
        normalized: dict | None = None,
        source_modified_at: datetime | None = None,
        hash_basis: dict | None = None,
        transport: str | None = None,
    ) -> tuple[ObservedAssetRecord, str]:
        """`raw_payload` is always stored whole. `hash_basis`, when given, is
        the connector's declared rule for narrowing what the content hash
        covers -- excluding values the source re-renders per request (see
        `ObservedAssetInput.hash_basis`). Without it, an unchanged asset whose
        transport returns a relative timestamp would create a new immutable
        version on every sync.

        The projection is computed here and the rule is stored on the version
        row, so a stored hash stays recomputable from stored state alone
        rather than depending on connector code that ran once (hy-y8g).

        `source_modified_at=None` means "this read did not disclose one", not
        "the source has none", and leaves any timestamp an earlier read
        established standing (hy-y8g.3). Superset's export YAML carries no
        `changed_on` while its REST detail body does, so once one connection
        carries both read modes (hy-gh-48) an unconditional write would let
        the read that knows less erase what the read that knew more proved.

        Returns the asset and the outcome this call recorded: the
        `ConnectorChange.change_type` it wrote ("created", "updated" or
        "restored"), or "unchanged" when the asset was already alive and
        identical and no change row was written. Callers report that outcome
        instead of re-deriving one from the version number, so an
        operator-facing tally cannot contradict the persisted change stream
        (hy-y8g.6)."""
        normalized = normalized or {}
        transport = self._normalised(transport)
        basis = hash_basis if hash_basis is not None else EMPTY
        content_hash = _content_hash(apply_hash_basis(raw_payload, basis))
        with self._session_factory() as session, session.begin():
            asset = session.execute(
                select(ObservedAsset).where(
                    ObservedAsset.connection_id == connection_id,
                    ObservedAsset.external_id == external_id,
                    ObservedAsset.asset_type == asset_type,
                )
            ).scalar_one_or_none()
            now = utcnow()
            current: ObservedAssetVersion | None = None
            deleted_at = asset.deleted_at if asset is not None else None

            first_ever = True
            if asset is None:
                asset = ObservedAsset(
                    connection_id=connection_id,
                    external_id=external_id,
                    asset_type=asset_type,
                    first_seen_at=now,
                    last_seen_at=now,
                    source_modified_at=source_modified_at,
                )
                session.add(asset)
                session.flush()
                next_version = 1
                changed = True
            else:
                asset.last_seen_at = now
                if source_modified_at is not None:
                    asset.source_modified_at = source_modified_at
                asset.deleted_at = None  # reappearance clears any prior soft-delete
                # Compared against the last observation BY THIS READ MODE, not
                # against the newest version of any mode (hy-6t4). Two
                # transports of one source carry different amounts of the
                # server's own bookkeeping, so their payloads hash differently
                # for an asset nobody edited, and an alternating schedule would
                # append an immutable version per sync forever.
                #
                # WHAT THIS GIVES UP, recorded here because this is where
                # someone would try to take it back: cross-mode comparison is
                # deliberately not attempted. The two reads agree COMPLETELY
                # about the source and disagree only about bookkeeping -- for a
                # Superset dataset, 12 shared column keys with identical
                # values, export adding `datetime_format`, REST adding
                # `changed_on`, `created_on`, `id`, `type_generic`, `uuid`.
                # They cannot be reconciled by projection without discarding
                # `database_uuid`, which only the export carries and which the
                # parent-database link resolves from -- and a basis without it
                # reads a dataset repointed at a different database as
                # UNCHANGED. So the comparison given up is one of bookkeeping,
                # and buying it back costs a silent wrong answer about the
                # source of a governed number.
                current = self._latest_in_transport(session, asset, transport)
                changed = current is None or current.content_hash != content_hash
                # Whether the ASSET is new is a different question from whether
                # THIS MODE has seen it: a bundle read of an asset REST already
                # observed appends that mode's first version, and calling that
                # "created" would tell the change stream an asset appeared that
                # has been there all along (hy-6t4).
                first_ever = asset.current_version_id is None
                # Numbered by observation rather than by mode: one asset, one
                # chain, and `current_version_id` stays "the most recently
                # observed version, whichever mode observed it".
                next_version = self._next_version(session, asset)

            previous = current
            version_row: ObservedAssetVersion | None = None
            if changed:
                version_row = ObservedAssetVersion(
                    asset_id=asset.id,
                    sync_run_id=sync_run_id,
                    version=next_version,
                    raw_payload=raw_payload,
                    normalized=normalized,
                    content_hash=content_hash,
                    hash_basis=basis,
                    transport=transport,
                )
                session.add(version_row)
                session.flush()
                session.execute(
                    update(ObservedAssetVersion)
                    .where(ObservedAssetVersion.id == version_row.id)
                    .values(search_vector=func.to_tsvector("english", _search_text(normalized)))
                )
                asset.current_version_id = version_row.id
                current = version_row

            # An unchanged reappearance writes no version but is still a
            # change to report: without it the stream alone still says the
            # asset is deleted (hy-y8g.1). Same transaction as the version
            # insert, so one real source change stays one immutable version
            # and one ConnectorChange.
            if deleted_at is not None:
                change_type = "restored"
            elif not changed:
                change_type = "unchanged"
            elif first_ever:
                change_type = "created"
            else:
                change_type = "updated"

            if change_type != "unchanged":
                detail = {
                    "external_id": external_id,
                    "asset_type": asset_type,
                    "from_content_hash": previous.content_hash if previous else None,
                    "to_content_hash": content_hash,
                    # Which lineage this claim was made within (hy-6t4).
                    # Structured rather than left to be inferred, because
                    # "created" and "updated" now mean created or updated
                    # RELATIVE TO THIS READ MODE, and a reader holding the
                    # older meaning would draw a wrong conclusion. It travels
                    # as far as a finding's detail, which is where change types
                    # already reach, and no further: the served contract --
                    # bundle, catalog, plan validation -- carries no change
                    # type at all, so this is not a disclosure-vocabulary code.
                    "transport": transport,
                }
                if change_type == "restored":
                    detail["deleted_at"] = deleted_at.isoformat()
                    detail["content_changed"] = changed
                session.add(
                    ConnectorChange(
                        connection_id=connection_id,
                        sync_run_id=sync_run_id,
                        asset_id=asset.id,
                        change_type=change_type,
                        from_version_id=previous.id if previous else None,
                        to_version_id=version_row.id if version_row else None,
                        detail=detail,
                    )
                )
                session.flush()

            return _asset_record(asset, current), change_type

    def mark_missing_deleted(
        self, *, connection_id: str, asset_type: str, seen_external_ids: set[str], sync_run_id: str
    ) -> list[str]:
        with self._session_factory() as session, session.begin():
            rows = (
                session.execute(
                    select(ObservedAsset).where(
                        ObservedAsset.connection_id == connection_id,
                        ObservedAsset.asset_type == asset_type,
                        ObservedAsset.deleted_at.is_(None),
                    )
                )
                .scalars()
                .all()
            )
            now = utcnow()
            deleted_ids = []
            for row in rows:
                if row.external_id not in seen_external_ids:
                    row.deleted_at = now
                    deleted_ids.append(row.id)
                    # Disappearance observes no new content, so there is no
                    # `to_version_id` -- only the last version that existed.
                    session.add(
                        ConnectorChange(
                            connection_id=connection_id,
                            sync_run_id=sync_run_id,
                            asset_id=row.id,
                            change_type="deleted",
                            from_version_id=row.current_version_id,
                            to_version_id=None,
                            detail={
                                "external_id": row.external_id,
                                "asset_type": asset_type,
                            },
                        )
                    )
            session.flush()
            return deleted_ids

    def get(self, asset_id: str) -> ObservedAssetRecord:
        with self._session_factory() as session:
            asset = session.get(ObservedAsset, asset_id)
            if asset is None:
                raise NotFoundError(f"observed asset {asset_id!r} not found")
            current = (
                session.get(ObservedAssetVersion, asset.current_version_id)
                if asset.current_version_id
                else None
            )
            return _asset_record(asset, current)

    def get_by_external_id(
        self, *, connection_id: str, external_id: str, asset_type: str
    ) -> ObservedAssetRecord:
        with self._session_factory() as session:
            asset = session.execute(
                select(ObservedAsset).where(
                    ObservedAsset.connection_id == connection_id,
                    ObservedAsset.external_id == external_id,
                    ObservedAsset.asset_type == asset_type,
                )
            ).scalar_one_or_none()
            if asset is None:
                raise NotFoundError(f"observed asset {external_id!r} not found")
            current = (
                session.get(ObservedAssetVersion, asset.current_version_id)
                if asset.current_version_id
                else None
            )
            return _asset_record(asset, current)

    def history(self, asset_id: str) -> list[ObservedAssetVersionRecord]:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(ObservedAssetVersion)
                    .where(ObservedAssetVersion.asset_id == asset_id)
                    .order_by(ObservedAssetVersion.version)
                )
                .scalars()
                .all()
            )
            return [_version_record(r) for r in rows]

    def list_all(
        self,
        *,
        workspace: str | _AllWorkspaces,
        connection_id: str | None = None,
        asset_type: str | None = None,
        include_deleted: bool = True,
    ) -> list[ObservedAssetRecord]:
        """Every asset at HEAD -- for policy scans, not search relevance.
        `include_deleted=True` by default since hy-gh-38's rules need to
        see soft-deleted assets to flag them, not have them filtered out.

        `workspace` is REQUIRED and FAIL-CLOSED (hq-t6nx): an asset is keyed on a
        connection, and this enumeration joins `Connection` and returns only assets
        whose connection is in `workspace`; only the explicit `ALL_WORKSPACES` sentinel
        reads across every tenant. There is no silent global default, so a new
        estate-wide scan cannot cross tenants by omission."""
        with self._session_factory() as session:
            stmt = (
                select(ObservedAsset, ObservedAssetVersion)
                .outerjoin(
                    ObservedAssetVersion,
                    ObservedAsset.current_version_id == ObservedAssetVersion.id,
                )
                .join(Connection, Connection.id == ObservedAsset.connection_id)
            )
            if not isinstance(workspace, _AllWorkspaces):
                stmt = stmt.where(Connection.workspace_id == workspace)
            if connection_id is not None:
                stmt = stmt.where(ObservedAsset.connection_id == connection_id)
            if asset_type is not None:
                stmt = stmt.where(ObservedAsset.asset_type == asset_type)
            if not include_deleted:
                stmt = stmt.where(ObservedAsset.deleted_at.is_(None))
            rows = session.execute(stmt).all()
            return [_asset_record(asset, version) for asset, version in rows]

    def search(
        self, query: str, *, asset_type: str | None = None, limit: int = 20
    ) -> list[ObservedAssetRecord]:
        with self._session_factory() as session:
            stmt = (
                select(ObservedAsset, ObservedAssetVersion)
                .join(
                    ObservedAssetVersion,
                    ObservedAsset.current_version_id == ObservedAssetVersion.id,
                )
                .where(
                    ObservedAssetVersion.search_vector.op("@@")(
                        func.plainto_tsquery("english", query)
                    )
                )
            )
            if asset_type is not None:
                stmt = stmt.where(ObservedAsset.asset_type == asset_type)
            rows = session.execute(stmt.limit(limit)).all()
            return [_asset_record(asset, version) for asset, version in rows]

    def count_by_type(self, *, workspace: str | _AllWorkspaces) -> list[tuple[str, str, int]]:
        """How many live assets of each kind each connection reports.

        Counted in the database rather than by loading `list_all`: the
        discovery catalog says how much exists, so it must not read the
        corpus it exists to avoid sending.

        `workspace` is REQUIRED and FAIL-CLOSED (hq-t6nx): the count joins
        `Connection` and covers only connections in `workspace`, so one tenant's
        catalog can never carry another tenant's connection id or asset count; only
        the explicit `ALL_WORKSPACES` sentinel counts across every tenant."""
        with self._session_factory() as session:
            stmt = (
                select(
                    ObservedAsset.connection_id,
                    ObservedAsset.asset_type,
                    func.count(ObservedAsset.id),
                )
                .join(Connection, Connection.id == ObservedAsset.connection_id)
                .where(ObservedAsset.deleted_at.is_(None))
                .group_by(ObservedAsset.connection_id, ObservedAsset.asset_type)
                .order_by(ObservedAsset.connection_id, ObservedAsset.asset_type)
            )
            if not isinstance(workspace, _AllWorkspaces):
                stmt = stmt.where(Connection.workspace_id == workspace)
            return [(row[0], row[1], row[2]) for row in session.execute(stmt).all()]

    def replace_relationships(
        self, *, declared: dict[str, list[tuple[str, str]]]
    ) -> list[AssetRelationshipRecord]:
        """Set each named asset's outgoing references to exactly what its
        newest observation declared (hy-d7xh).

        Reconciled row by row rather than deleted and re-inserted, so a
        resync that declares the same references keeps the same row ids: the
        projection must not churn just because the source was read again.
        Removing what is no longer declared is the other half of that -- a
        row left behind would claim a reference the source has stopped
        making, and nothing else in the schema would ever contradict it.

        One transaction for every asset in `declared`, because a sync's whole
        set of references is one observation of the source's shape; a partial
        write would leave the projection describing no single read.

        Every existing row is read in ONE select over `from_asset_id.in_(...)`
        and reconciled in Python, so the round trips this makes are O(1) in
        asset count rather than one per asset (hy-iv4y: 200 link-free assets
        cost 201 selects and 0.3793s per-asset against 0.0054s batched, and a
        sync's transaction stayed open for all of it). Reconciliation itself is
        still row by row -- that is what preserves row ids, and it never needed
        a query per asset. The `in_` clause spends one bind parameter per
        asset, so a sync above Postgres' 65535-parameter limit fails; hy-yuea
        carries that number.
        """
        if not declared:
            return []
        with self._session_factory() as session, session.begin():
            existing_by_asset: dict[str, list[AssetRelationship]] = {}
            for row in (
                session.execute(
                    select(AssetRelationship).where(AssetRelationship.from_asset_id.in_(declared))
                )
                .scalars()
                .all()
            ):
                existing_by_asset.setdefault(row.from_asset_id, []).append(row)
            for from_asset_id, pairs in declared.items():
                wanted = set(pairs)
                for row in existing_by_asset.get(from_asset_id, ()):
                    key = (row.relation, row.to_asset_id)
                    if key in wanted:
                        wanted.discard(key)
                    else:
                        session.delete(row)
                for relation, to_asset_id in sorted(wanted):
                    session.add(
                        AssetRelationship(
                            from_asset_id=from_asset_id,
                            to_asset_id=to_asset_id,
                            relation=relation,
                        )
                    )
            session.flush()
            rows = (
                session.execute(
                    select(AssetRelationship)
                    .where(AssetRelationship.from_asset_id.in_(declared))
                    .order_by(
                        AssetRelationship.from_asset_id,
                        AssetRelationship.relation,
                        AssetRelationship.to_asset_id,
                    )
                )
                .scalars()
                .all()
            )
            return [_relationship_record(row) for row in rows]

    def list_relationships(
        self,
        *,
        from_asset_id: str | None = None,
        to_asset_id: str | None = None,
        include_deleted: bool = True,
    ) -> list[AssetRelationshipRecord]:
        """Declared references out of and/or into one asset.

        `include_deleted=True` by default and named the same as `list_all`'s,
        because the default is the same choice: the reference really was
        observed while both assets existed, and `mark_missing_deleted` keeps
        history rather than erasing it. `include_deleted=False` returns only
        rows whose BOTH endpoints are still live, which is the read hy-gh-124
        performs -- it is done here as a join because
        `AssetRelationshipRecord` carries no `deleted_at`, so a caller
        filtering for itself pays one `get()` per row: an N+1 answering the
        very read `ix_asset_relationships_to_relation` exists for (hy-z21y).

        At least one endpoint is required. Every other read on this repository
        is scoped to a connection or an id; a no-argument call here returned
        the whole table across every connection, which no call site wanted and
        the signature only permitted by accident.
        """
        if from_asset_id is None and to_asset_id is None:
            raise ValueError(
                "list_relationships needs from_asset_id or to_asset_id: "
                "the projection is read by endpoint, never whole"
            )
        with self._session_factory() as session:
            stmt = select(AssetRelationship)
            if from_asset_id is not None:
                stmt = stmt.where(AssetRelationship.from_asset_id == from_asset_id)
            if to_asset_id is not None:
                stmt = stmt.where(AssetRelationship.to_asset_id == to_asset_id)
            if not include_deleted:
                source = aliased(ObservedAsset)
                target = aliased(ObservedAsset)
                stmt = (
                    stmt.join(source, AssetRelationship.from_asset_id == source.id)
                    .join(target, AssetRelationship.to_asset_id == target.id)
                    .where(source.deleted_at.is_(None), target.deleted_at.is_(None))
                )
            rows = (
                session.execute(
                    stmt.order_by(
                        AssetRelationship.from_asset_id,
                        AssetRelationship.relation,
                        AssetRelationship.to_asset_id,
                    )
                )
                .scalars()
                .all()
            )
            return [_relationship_record(row) for row in rows]

    def list_live_references(self, *, to_asset_ids: list[str]) -> list[IncomingReferenceRecord]:
        """Live references INTO each of `to_asset_ids`, in one statement.

        Live means both endpoints are undeleted, the same join
        `list_relationships(include_deleted=False)` performs and for the same
        reason: the projection keeps rows whose endpoints were soft-deleted, so
        counting them would rank a deleted chart's dataset above a live one.

        Set-at-a-time rather than one call per asset. The per-endpoint read
        already exists and answers this question, but a caller ranking every
        dataset in the estate would issue one statement per candidate -- the
        N+1 that `ix_asset_relationships_to_relation` exists to avoid. Each row
        carries the REFERRING asset's own identity, because a rank that says
        "two things reference this" without naming them states a signal the
        reader cannot check (hy-g1y8).

        An empty `to_asset_ids` returns no rows without querying: `IN ()` is a
        statement whose answer is already known.
        """
        if not to_asset_ids:
            return []
        with self._session_factory() as session:
            source = aliased(ObservedAsset)
            target = aliased(ObservedAsset)
            rows = session.execute(
                select(
                    AssetRelationship.to_asset_id,
                    AssetRelationship.from_asset_id,
                    AssetRelationship.relation,
                    source.asset_type,
                    source.external_id,
                    source.connection_id,
                )
                .join(source, AssetRelationship.from_asset_id == source.id)
                .join(target, AssetRelationship.to_asset_id == target.id)
                .where(
                    AssetRelationship.to_asset_id.in_(to_asset_ids),
                    source.deleted_at.is_(None),
                    target.deleted_at.is_(None),
                )
                .order_by(
                    AssetRelationship.to_asset_id,
                    AssetRelationship.relation,
                    source.external_id,
                )
            ).all()
            return [
                IncomingReferenceRecord(
                    to_asset_id=row.to_asset_id,
                    from_asset_id=row.from_asset_id,
                    from_asset_type=row.asset_type,
                    from_external_id=row.external_id,
                    from_connection_id=row.connection_id,
                    relation=row.relation,
                )
                for row in rows
            ]


class PostgresConnectorChangeRepository:
    """Read side of `connector_changes`. Writes live in
    `PostgresObservedAssetRepository`, in the same transaction as the
    version they describe, so this repository is query-only."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_for_run(self, sync_run_id: str) -> list[ConnectorChangeRecord]:
        return self._list(ConnectorChange.sync_run_id == sync_run_id)

    def list_for_asset(self, asset_id: str) -> list[ConnectorChangeRecord]:
        return self._list(ConnectorChange.asset_id == asset_id)

    def _list(self, condition) -> list[ConnectorChangeRecord]:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(ConnectorChange)
                    .where(condition)
                    .order_by(ConnectorChange.detected_at, ConnectorChange.id)
                )
                .scalars()
                .all()
            )
            return [_change_record(r) for r in rows]
