from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from hyperset.db.base import utcnow
from hyperset.db.models import Connection, ObservedAsset
from hyperset.repositories.dto import ConnectionRecord
from hyperset.repositories.errors import NotFoundError
from hyperset.repositories.scope import _AllWorkspaces


def _to_record(row: Connection) -> ConnectionRecord:
    return ConnectionRecord(
        id=row.id,
        connector_type=row.connector_type,
        display_name=row.display_name,
        enabled=row.enabled,
        health_status=row.health_status,
        health_checked_at=row.health_checked_at,
        health_detail=row.health_detail,
        config_ref=row.config_ref,
        created_at=row.created_at,
        updated_at=row.updated_at,
        workspace_id=row.workspace_id,
    )


class PostgresConnectionRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _in_workspace(sess: Session, connection_id: str, workspace: str | None) -> Connection:
        """The connection by id IFF it is in `workspace`, else NotFound (hq-t6nx).

        A by-id load from the admin surface is workspace-scoped: a caller in one
        tenant must never read or mutate another tenant's connection, and the denial
        is NON-DISCLOSING -- a connection in a different workspace is reported
        exactly as one that does not exist, so existence never leaks across tenants.
        `workspace=None` means an internal caller that does not scope (backward-compat);
        the admin handlers always pass the verified principal's concrete workspace."""
        row = sess.get(Connection, connection_id)
        if row is None or (workspace is not None and row.workspace_id != workspace):
            raise NotFoundError(f"connection {connection_id!r} not found")
        return row

    def create_or_update(
        self,
        *,
        connection_id: str | None = None,
        connector_type: str,
        display_name: str,
        config_encrypted: bytes | None = None,
        config_ref: str | None = None,
        enabled: bool = True,
        workspace: str | None = None,
        session: Session | None = None,
    ) -> ConnectionRecord:
        """Create or update a connection (hq-jedd). A create stamps `workspace_id`
        with the caller's `workspace`; an update is workspace-scoped, so a caller
        cannot edit another tenant's connection (hq-t6nx). With `session` given the
        write runs in THAT transaction and is not committed here, so the admin
        handler can couple it to an audit append."""

        def _apply(sess: Session) -> ConnectionRecord:
            if connection_id is not None:
                row = self._in_workspace(sess, connection_id, workspace)
                row.connector_type = connector_type
                row.display_name = display_name
                if config_encrypted is not None:
                    row.config_encrypted = config_encrypted
                if config_ref is not None:
                    row.config_ref = config_ref
                row.enabled = enabled
            else:
                row = Connection(
                    connector_type=connector_type,
                    display_name=display_name,
                    config_encrypted=config_encrypted,
                    config_ref=config_ref,
                    enabled=enabled,
                    workspace_id=workspace or "default",
                )
                sess.add(row)
            sess.flush()
            return _to_record(row)

        if session is not None:
            return _apply(session)
        with self._session_factory() as owned, owned.begin():
            return _apply(owned)

    def set_enabled(
        self,
        connection_id: str,
        *,
        enabled: bool,
        workspace: str | None = None,
        session: Session | None = None,
    ) -> ConnectionRecord:
        """Enable or disable a connection (hq-jedd), workspace-scoped (hq-t6nx). Soft
        and reversible: a disabled connection keeps its config and health history but
        is excluded from serving (catalog/resolve/gather filter `enabled`).
        Session-aware for audit coupling."""

        def _apply(sess: Session) -> ConnectionRecord:
            row = self._in_workspace(sess, connection_id, workspace)
            row.enabled = enabled
            sess.flush()
            return _to_record(row)

        if session is not None:
            return _apply(session)
        with self._session_factory() as owned, owned.begin():
            return _apply(owned)

    def delete(
        self, connection_id: str, *, workspace: str | None = None, session: Session | None = None
    ) -> None:
        """Remove a connection by id (hq-jedd), workspace-scoped (hq-t6nx). Raises
        NotFoundError if absent OR in another workspace. The observed-asset rows keyed
        on this connection are governed evidence; the admin handler restricts removal
        to a connection with NO observed assets and disables one that has history, so
        this never orphans evidence. Session-aware for audit coupling."""

        def _apply(sess: Session) -> None:
            row = self._in_workspace(sess, connection_id, workspace)
            sess.delete(row)
            sess.flush()

        if session is not None:
            return _apply(session)
        with self._session_factory() as owned, owned.begin():
            return _apply(owned)

    def get(self, connection_id: str, *, workspace: str | None = None) -> ConnectionRecord:
        with self._session_factory() as session:
            return _to_record(self._in_workspace(session, connection_id, workspace))

    def list(
        self,
        *,
        workspace: str | _AllWorkspaces,
        enabled_only: bool = False,
    ) -> list[ConnectionRecord]:
        """Connections in `workspace` (hq-t6nx). `workspace` is REQUIRED and
        FAIL-CLOSED: a concrete tenant scopes the list to that tenant, and only the
        explicit `ALL_WORKSPACES` sentinel reads across every tenant (a SYSTEM opt-in).
        There is no silent global default, so an enumeration of connections -- and the
        observed assets keyed on them -- can never span tenants by omission. A plain
        `None`/unknown value filters to nothing rather than everything (still fail
        closed)."""
        with self._session_factory() as session:
            stmt = select(Connection)
            if not isinstance(workspace, _AllWorkspaces):
                stmt = stmt.where(Connection.workspace_id == workspace)
            if enabled_only:
                stmt = stmt.where(Connection.enabled.is_(True))
            rows = session.execute(stmt.order_by(Connection.created_at)).scalars().all()
            return [_to_record(r) for r in rows]

    def observed_asset_count(self, connection_id: str, *, workspace: str | None = None) -> int:
        """How many observed assets this connection has recorded (hq-jedd). The admin
        remove path uses it to keep removal non-destructive of governed evidence: a
        connection with >0 observed assets is disable-only, never deleted. Scoped to
        the caller's workspace (hq-t6nx): a connection in another tenant is reported
        as not found, so its asset count never leaks."""
        with self._session_factory() as session:
            self._in_workspace(session, connection_id, workspace)
            return int(
                session.execute(
                    select(func.count())
                    .select_from(ObservedAsset)
                    .where(ObservedAsset.connection_id == connection_id)
                ).scalar_one()
            )

    def record_health(
        self,
        connection_id: str,
        *,
        status: str,
        detail: str | None = None,
        workspace: str | None = None,
        session: Session | None = None,
    ) -> ConnectionRecord:
        def _apply(sess: Session) -> ConnectionRecord:
            row = self._in_workspace(sess, connection_id, workspace)
            row.health_status = status
            row.health_detail = detail
            row.health_checked_at = utcnow()
            sess.flush()
            return _to_record(row)

        if session is not None:
            return _apply(session)
        with self._session_factory() as owned, owned.begin():
            return _apply(owned)
