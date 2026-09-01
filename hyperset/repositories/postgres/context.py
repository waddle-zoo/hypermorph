"""Git-owned context persistence (hy-gh-43, ADR 0012).

`context_snapshots` is append-only: nothing here issues an UPDATE against a
snapshot's content columns. The only mutable row is `context_sources`, and
the only business-meaning field it carries is a pointer to the immutable
snapshot a Git commit produced.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased, sessionmaker

from hyperset.db.base import utcnow
from hyperset.db.models import ContextSnapshot, ContextSource
from hyperset.repositories.dto import (
    ContextSnapshotRecord,
    ContextSourceCandidate,
    ContextSourceRecord,
)
from hyperset.repositories.errors import AmbiguousIdentityError, NotFoundError


def _snapshot_record(row: ContextSnapshot | None) -> ContextSnapshotRecord | None:
    if row is None:
        return None
    return ContextSnapshotRecord(
        id=row.id,
        source_id=row.source_id,
        commit_sha=row.commit_sha,
        committed_at=row.committed_at,
        domain=row.domain,
        title=row.title,
        files=row.files,
        normalized=row.normalized,
        content_hash=row.content_hash,
        owner_refs=row.owner_refs,
        evidence_refs=row.evidence_refs,
        evidence_findings=row.evidence_findings,
        synced_at=row.synced_at,
    )


def _source_record(row: ContextSource, current: ContextSnapshot | None) -> ContextSourceRecord:
    return ContextSourceRecord(
        id=row.id,
        repository=row.repository,
        ref=row.ref,
        path=row.path,
        display_name=row.display_name,
        enabled=row.enabled,
        current_snapshot=_snapshot_record(current),
        last_attempt_status=row.last_attempt_status,
        last_attempt_at=row.last_attempt_at,
        last_attempted_commit_sha=row.last_attempted_commit_sha,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
        workspace_id=row.workspace_id,
    )


def _content_hash(files: dict) -> str:
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class PostgresContextRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def register_source(
        self,
        *,
        repository: str,
        ref: str,
        path: str,
        display_name: str | None = None,
        workspace: str = "default",
        session: Session | None = None,
    ) -> ContextSourceRecord:
        """Register (or update) a context-source pointer IN `workspace` (hq-t6nx):
        the source's identity is (workspace, repository, ref, path), so two tenants
        may each track the same repo/ref/path. When `session` is given the row is
        written in THAT transaction and not committed here, so the caller can couple
        the register to its audit append in one transaction (hy-gh-75 round 2)."""
        if session is not None:
            return self._register(session, repository, ref, path, display_name, workspace)
        with self._session_factory() as owned, owned.begin():
            return self._register(owned, repository, ref, path, display_name, workspace)

    def _register(
        self,
        session: Session,
        repository: str,
        ref: str,
        path: str,
        display_name: str | None,
        workspace: str,
    ) -> ContextSourceRecord:
        row = self._by_identity(session, repository, ref, path, workspace)
        if row is None:
            row = ContextSource(
                repository=repository,
                ref=ref,
                path=path,
                display_name=display_name or f"{repository}@{ref}:{path}",
                workspace_id=workspace,
            )
            session.add(row)
            session.flush()
        elif display_name:
            row.display_name = display_name
        return _source_record(row, self._current(session, row))

    def get_source(self, source_id: str, *, workspace: str | None = None) -> ContextSourceRecord:
        """A source by id (hq-t6nx). `workspace=None` is an internal caller (sync,
        reconcile) that does not scope; the admin surface passes the caller's
        concrete workspace, and a source in another tenant is reported as not
        found (non-disclosing)."""
        with self._session_factory() as session:
            row = session.get(ContextSource, source_id)
            if row is None or (workspace is not None and row.workspace_id != workspace):
                raise NotFoundError(f"context source {source_id!r} not found")
            return _source_record(row, self._current(session, row))

    def get_source_by_identity(
        self, *, repository: str, ref: str, path: str, workspace: str | None = None
    ) -> ContextSourceRecord:
        with self._session_factory() as session:
            row = self._by_identity(session, repository, ref, path, workspace)
            if row is None:
                raise NotFoundError(f"context source {repository}@{ref}:{path} not found")
            return _source_record(row, self._current(session, row))

    def list_sources(self, *, workspace: str | None = None) -> list[ContextSourceRecord]:
        """Every source, or every source IN `workspace` (hq-t6nx). The admin surface
        passes the caller's workspace so a tenant never sees another's sources; an
        internal caller passes None."""
        with self._session_factory() as session:
            stmt = select(ContextSource)
            if workspace is not None:
                stmt = stmt.where(ContextSource.workspace_id == workspace)
            rows = session.execute(stmt.order_by(ContextSource.created_at)).scalars().all()
            return [_source_record(row, self._current(session, row)) for row in rows]

    def list_source_candidates(
        self, *, workspace: str | None = None
    ) -> list[ContextSourceCandidate]:
        """Identity + current-snapshot version METADATA for every source (or every source in
        `workspace`), WITHOUT the snapshot's `files` content (hy-r0szz).

        The ACL-aware search uses this to authorize each source BEFORE reading its content:
        the query selects only the named metadata columns and joins the current snapshot for
        its domain/commit/version metadata, so `context_snapshots.files` is NEVER fetched
        here -- a denied source's bytes never leave the database. Content is loaded
        separately, by id, only for sources that pass authorize (`load_source_files`)."""
        with self._session_factory() as session:
            snapshot = aliased(ContextSnapshot)
            stmt = (
                select(
                    ContextSource.id,
                    ContextSource.repository,
                    ContextSource.enabled,
                    ContextSource.workspace_id,
                    ContextSource.current_snapshot_id,
                    ContextSource.last_attempt_status,
                    ContextSource.last_attempt_at,
                    snapshot.domain,
                    snapshot.commit_sha,
                    snapshot.content_hash,
                    snapshot.synced_at,
                    snapshot.committed_at,
                    snapshot.normalized["parent"].astext.label("parent"),
                )
                .select_from(ContextSource)
                .outerjoin(snapshot, ContextSource.current_snapshot_id == snapshot.id)
            )
            if workspace is not None:
                stmt = stmt.where(ContextSource.workspace_id == workspace)
            rows = session.execute(stmt.order_by(ContextSource.created_at)).all()
            return [
                ContextSourceCandidate(
                    id=row.id,
                    repository=row.repository,
                    enabled=row.enabled,
                    workspace_id=row.workspace_id,
                    current_snapshot_id=row.current_snapshot_id,
                    domain=row.domain,
                    commit_sha=row.commit_sha,
                    content_hash=row.content_hash,
                    last_attempt_status=row.last_attempt_status,
                    last_attempt_at=row.last_attempt_at,
                    synced_at=row.synced_at,
                    committed_at=row.committed_at,
                    parent=row.parent,
                )
                for row in rows
            ]

    def load_source_files(self, source_id: str, *, workspace: str | None = None) -> dict[str, str]:
        """The current snapshot's `files` for ONE source, by id (hy-r0szz).

        Called only AFTER the caller is authorized for `source_id`, so the content read is
        never on a denied source. Returns an empty mapping when the source is absent, is in
        another workspace, or has no current snapshot -- there is nothing to search."""
        with self._session_factory() as session:
            stmt = (
                select(ContextSnapshot.files)
                .select_from(ContextSource)
                .join(ContextSnapshot, ContextSource.current_snapshot_id == ContextSnapshot.id)
                .where(ContextSource.id == source_id)
            )
            if workspace is not None:
                stmt = stmt.where(ContextSource.workspace_id == workspace)
            files = session.execute(stmt).scalars().first()
            return dict(files or {})

    def source_claiming_domain(
        self, domain: str, *, exclude_source_id: str | None = None, workspace: str | None = None
    ) -> ContextSourceRecord | None:
        """An ENABLED source whose CURRENT snapshot already claims `domain`, other
        than `exclude_source_id` (hy-gh-282).

        The estate serves one source per domain, and this is the query sync and
        `enable` refuse a collision against. Enabled-only and current-snapshot-only
        on purpose: a disabled source, or one whose colliding manifest never became
        the current snapshot, is not an active claimant. `domain` lives on the
        versioned snapshot rather than the source, which is why this is a query at
        write time and not a column uniqueness constraint -- a constraint would
        forbid a source re-syncing its own domain across snapshots.
        """
        with self._session_factory() as session:
            # `.first()`, never `.scalar_one_or_none()`: an estate that ALREADY
            # holds two enabled claimants for one domain is exactly the state this
            # feature exists to reconcile, and `scalar_one_or_none` RAISES
            # `MultipleResultsFound` on it -- turning a sync or an `enable` into a
            # crash instead of the clean named-claimant refusal. One claimant is
            # all a refusal needs to name, and the `order_by` makes which one
            # deterministic (hy-gh-282, panel).
            stmt = (
                select(ContextSource)
                .join(ContextSnapshot, ContextSource.current_snapshot_id == ContextSnapshot.id)
                .where(
                    ContextSnapshot.domain == domain,
                    ContextSource.enabled.is_(True),
                    ContextSource.id != (exclude_source_id or ""),
                )
            )
            # A domain claim is per-tenant (hq-t6nx): two workspaces may each hold a
            # `revenue` source without colliding. When a workspace is given the
            # collision check is confined to it; an internal caller passes None.
            if workspace is not None:
                stmt = stmt.where(ContextSource.workspace_id == workspace)
            row = (
                session.execute(stmt.order_by(ContextSource.created_at, ContextSource.id))
                .scalars()
                .first()
            )
            return None if row is None else _source_record(row, self._current(session, row))

    def set_enabled(
        self,
        source_id: str,
        *,
        enabled: bool,
        workspace: str | None = None,
        session: Session | None = None,
    ) -> ContextSourceRecord:
        """Soft enable/disable a source (hy-gh-282), workspace-scoped (hq-t6nx).
        Disabling drops it from the catalog and resolve surfaces (both filter
        `enabled`) without deleting any governed history -- the supported, reversible
        way to reconcile a domain collision. Nothing here deletes a snapshot; ADR
        0012's no-destructive-delete posture is why removal is soft. Collision on
        re-enable is refused by the caller (`enable` in the CLI / admin handler), not
        here. A source in another tenant is reported as not found. With `session`
        given the change is made in THAT transaction and not committed here, so the
        admin handler can couple it to an audit append (hq-3fjt)."""

        def _apply(sess: Session) -> ContextSourceRecord:
            source = self._require(sess, source_id)
            if workspace is not None and source.workspace_id != workspace:
                raise NotFoundError(f"context source {source_id!r} not found")
            source.enabled = enabled
            sess.flush()
            return _source_record(source, self._current(sess, source))

        if session is not None:
            return _apply(session)
        with self._session_factory() as owned, owned.begin():
            return _apply(owned)

    def delete_source(self, source_id: str, *, session: Session | None = None) -> None:
        """Delete a context SOURCE POINTER by id (hq-3fjt). Removes the config row
        only; it is NOT a way to destroy governed history. ADR 0012 keeps
        immutable snapshots for replay/comparison, so the admin handler restricts
        this to a source that has NO snapshots (a mis-added pointer), and a source
        WITH governed history is disable-only. Raises NotFoundError if the source
        does not exist. With `session` given the delete runs in THAT transaction
        and is not committed here, so it can be coupled to an audit append."""

        def _apply(sess: Session) -> None:
            source = self._require(sess, source_id)
            sess.delete(source)
            sess.flush()

        if session is not None:
            return _apply(session)
        with self._session_factory() as owned, owned.begin():
            return _apply(owned)

    def snapshot_count(self, source_id: str) -> int:
        """How many immutable snapshots a source has recorded (hq-3fjt). The admin
        remove path uses it to keep removal non-destructive: a source with >0
        snapshots holds governed history and is disable-only, never deleted."""
        with self._session_factory() as session:
            return int(
                session.execute(
                    select(func.count())
                    .select_from(ContextSnapshot)
                    .where(ContextSnapshot.source_id == source_id)
                ).scalar_one()
            )

    def record_snapshot(
        self,
        *,
        source_id: str,
        commit_sha: str,
        committed_at: datetime | None,
        domain: str,
        title: str,
        files: dict,
        normalized: dict,
        owner_refs: list | None = None,
        evidence_refs: list | None = None,
        evidence_findings: list | None = None,
        session: Session | None = None,
    ) -> tuple[ContextSnapshotRecord, bool]:
        """With `session` given the snapshot is written in THAT transaction and not
        committed here, so a caller can couple the snapshot to a lifecycle/audit
        append in ONE transaction -- the reconcile re-sync (hq-ci92) does, so a
        failed audit rolls the snapshot back too."""

        def _apply(session: Session) -> tuple[ContextSnapshotRecord, bool]:
            source = self._require(session, source_id)
            existing = session.execute(
                select(ContextSnapshot).where(
                    ContextSnapshot.source_id == source_id,
                    ContextSnapshot.commit_sha == commit_sha,
                )
            ).scalar_one_or_none()
            now = utcnow()
            if existing is not None:
                # A commit Hyperset already snapshotted -- for example a ref
                # moved back to an earlier commit. Point at the snapshot that
                # commit produced; never rewrite it.
                source.current_snapshot_id = existing.id
                source.last_attempt_status = "unchanged"
                source.last_attempt_at = now
                source.last_attempted_commit_sha = commit_sha
                source.last_error = None
                return _snapshot_record(existing), False

            snapshot = ContextSnapshot(
                source_id=source_id,
                commit_sha=commit_sha,
                committed_at=committed_at,
                domain=domain,
                title=title,
                files=files,
                normalized=normalized,
                content_hash=_content_hash(files),
                owner_refs=owner_refs or [],
                evidence_refs=evidence_refs or [],
                evidence_findings=evidence_findings or [],
                synced_at=now,
            )
            session.add(snapshot)
            session.flush()
            source.current_snapshot_id = snapshot.id
            source.last_attempt_status = "synced"
            source.last_attempt_at = now
            source.last_attempted_commit_sha = commit_sha
            source.last_error = None
            return _snapshot_record(snapshot), True

        if session is not None:
            return _apply(session)
        with self._session_factory() as owned, owned.begin():
            return _apply(owned)

    def record_unchanged(
        self, source_id: str, *, commit_sha: str, session: Session | None = None
    ) -> ContextSourceRecord:
        def _apply(session: Session) -> ContextSourceRecord:
            source = self._require(session, source_id)
            source.last_attempt_status = "unchanged"
            source.last_attempt_at = utcnow()
            source.last_attempted_commit_sha = commit_sha
            source.last_error = None
            return _source_record(source, self._current(session, source))

        if session is not None:
            return _apply(session)
        with self._session_factory() as owned, owned.begin():
            return _apply(owned)

    def record_failure(
        self,
        source_id: str,
        *,
        error: str,
        commit_sha: str | None = None,
        session: Session | None = None,
    ) -> ContextSourceRecord:
        def _apply(session: Session) -> ContextSourceRecord:
            source = self._require(session, source_id)
            # `current_snapshot_id` is deliberately untouched: the last valid
            # context keeps serving while the customer fixes their repository.
            source.last_attempt_status = "failed"
            source.last_attempt_at = utcnow()
            source.last_attempted_commit_sha = commit_sha
            source.last_error = error
            return _source_record(source, self._current(session, source))

        if session is not None:
            return _apply(session)
        with self._session_factory() as owned, owned.begin():
            return _apply(owned)

    def repin_snapshot(
        self,
        source_id: str,
        *,
        commit_sha: str,
        workspace: str | None = None,
        session: Session | None = None,
    ) -> ContextSourceRecord:
        """Roll a source's SERVING snapshot back to a prior commit it already snapshotted
        (hy-bo5p). An INTEGRATION action: it re-points `current_snapshot_id` at an EXISTING
        snapshot in THIS source's history -- it reads no Git, writes no governed row, and
        creates no approval (ADR 0012); which commit is served is a pin, not authorship.

        FAIL CLOSED: a commit not in this source's history cannot be rolled back to (you can
        only re-pin a commit Hyperset actually served), and -- like every admin source read --
        a source in another tenant is reported as not found (workspace-scoped, hq-t6nx). With
        `session` given the re-pin is written in THAT transaction, so a caller couples it to the
        audit append and a failed audit rolls the re-pin back."""

        def _apply(session: Session) -> ContextSourceRecord:
            source = self._require(session, source_id)
            if workspace is not None and source.workspace_id != workspace:
                raise NotFoundError(f"context source {source_id!r} not found")
            snapshot = session.execute(
                select(ContextSnapshot).where(
                    ContextSnapshot.source_id == source.id,
                    ContextSnapshot.commit_sha == commit_sha,
                )
            ).scalar_one_or_none()
            if snapshot is None:
                raise NotFoundError(
                    f"context source {source_id!r} has no snapshot at commit {commit_sha!r}"
                )
            source.current_snapshot_id = snapshot.id
            source.updated_at = utcnow()
            return _source_record(source, snapshot)

        if session is not None:
            return _apply(session)
        with self._session_factory() as owned, owned.begin():
            return _apply(owned)

    def get_snapshot(self, snapshot_id: str) -> ContextSnapshotRecord:
        with self._session_factory() as session:
            row = session.get(ContextSnapshot, snapshot_id)
            if row is None:
                raise NotFoundError(f"context snapshot {snapshot_id!r} not found")
            return _snapshot_record(row)

    def history(self, source_id: str) -> list[ContextSnapshotRecord]:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(ContextSnapshot)
                    .where(ContextSnapshot.source_id == source_id)
                    .order_by(ContextSnapshot.synced_at, ContextSnapshot.id)
                )
                .scalars()
                .all()
            )
            return [_snapshot_record(row) for row in rows]

    def _by_identity(
        self, session: Session, repository: str, ref: str, path: str, workspace: str | None = None
    ) -> ContextSource | None:
        # Identity is (workspace, repository, ref, path) since hq-t6nx. With a
        # workspace given the match is confined to that tenant, and the unique
        # constraint guarantees at most one row -- an unambiguous lookup.
        stmt = select(ContextSource).where(
            ContextSource.repository == repository,
            ContextSource.ref == ref,
            ContextSource.path == path,
        )
        if workspace is not None:
            return (
                session.execute(stmt.where(ContextSource.workspace_id == workspace))
                .scalars()
                .one_or_none()
            )
        # Workspace-LESS lookup (a legacy/CLI/relation caller): the pointer alone is
        # NOT a unique key once two tenants share it. FAIL CLOSED rather than let the
        # datastore raise an unhandled `MultipleResultsFound` (a 500): an un-scoped
        # caller that cannot say which tenant it means gets an explicit
        # `AmbiguousIdentityError` (a `NotFoundError` subclass, so every existing
        # handler degrades cleanly) telling it to pass a workspace. A single matching
        # row (single-tenant, or a pointer only one tenant holds) resolves unchanged.
        rows = session.execute(stmt.order_by(ContextSource.workspace_id)).scalars().all()
        if len(rows) > 1:
            raise AmbiguousIdentityError(
                f"context source {repository}@{ref}:{path} exists in {len(rows)} "
                f"workspaces ({', '.join(row.workspace_id for row in rows)}); pass a "
                f"workspace to resolve it unambiguously"
            )
        return rows[0] if rows else None

    def _require(self, session: Session, source_id: str) -> ContextSource:
        source = session.get(ContextSource, source_id)
        if source is None:
            raise NotFoundError(f"context source {source_id!r} not found")
        return source

    def _current(self, session: Session, row: ContextSource) -> ContextSnapshot | None:
        if not row.current_snapshot_id:
            return None
        return session.get(ContextSnapshot, row.current_snapshot_id)
