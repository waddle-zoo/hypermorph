"""The append-only admin audit repository (hy-gh-75).

APPEND + LIST only. It deliberately exposes no update or delete method, so the trail
cannot be rewritten through the application -- an admin action is recorded once and read
back read-only. It never stores a secret value; callers pass a short, non-secret `detail`.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from hyperset.db.models import AdminAuditLog
from hyperset.observability.correlation import current_correlation_id
from hyperset.repositories.dto import AdminAuditRecord
from hyperset.repositories.scope import _AllWorkspaces


def _record(row: AdminAuditLog) -> AdminAuditRecord:
    return AdminAuditRecord(
        id=row.id,
        at=row.at,
        actor=row.actor,
        actor_issuer=row.actor_issuer,
        action=row.action,
        target=row.target,
        result=row.result,
        detail=row.detail,
        workspace_id=row.workspace_id,
        correlation_id=row.correlation_id,
    )


class PostgresAdminAuditRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def record(
        self,
        *,
        actor: str,
        action: str,
        result: str,
        actor_issuer: str | None = None,
        target: str | None = None,
        detail: str | None = None,
        workspace: str = "default",
        session: Session | None = None,
    ) -> AdminAuditRecord:
        """Record one admin action. Append-only: it inserts a new row and mutates none.

        `workspace` stamps the tenant this action happened in (hq-hnrf): the trail is
        workspace-scoped, so `list` confines a tenant's admin to its OWN actions. A
        caller passes the verified principal's workspace; an internal/single-tenant
        caller leaves the 'default'.

        When `session` is given, the row is added to THAT transaction and neither begun
        nor committed here -- so a caller can couple the audit append to the mutation it
        describes in one transaction, and a failed append rolls the mutation back (the
        audit trail is not omittable at its own failure mode, hy-gh-75 round 2). With no
        session it opens and commits its own transaction, as before.
        """
        if session is not None:
            return self._add(
                session, actor, actor_issuer, action, target, result, detail, workspace
            )
        with self._session_factory() as owned, owned.begin():
            return self._add(owned, actor, actor_issuer, action, target, result, detail, workspace)

    @staticmethod
    def _add(
        session: Session,
        actor: str,
        actor_issuer: str | None,
        action: str,
        target: str | None,
        result: str,
        detail: str | None,
        workspace: str = "default",
    ) -> AdminAuditRecord:
        row = AdminAuditLog(
            actor=actor,
            actor_issuer=actor_issuer,
            action=action,
            target=target,
            result=result,
            detail=detail,
            workspace_id=workspace or "default",
            # The id of the request that performed this action (hy-w9ntg), read from the
            # per-request contextvar the transport sets. None off-request (a test or internal
            # write records no id rather than a stale one). Not a caller argument, so no
            # record() call site can forge or omit it.
            correlation_id=current_correlation_id(),
        )
        session.add(row)
        session.flush()
        return _record(row)

    def list(self, *, workspace: str | _AllWorkspaces, limit: int = 200) -> list[AdminAuditRecord]:
        """The most recent entries, newest first (bounded). `workspace` is REQUIRED and
        FAIL-CLOSED (hq-hnrf, adversary round 2): a concrete tenant reads only its OWN
        actions, and only the explicit `ALL_WORKSPACES` sentinel reads across every
        tenant (a SYSTEM opt-in). There is no silent global default -- omitting the
        argument is a TypeError, never a cross-tenant audit read by omission. The served
        admin route passes the caller's concrete workspace."""
        with self._session_factory() as session:
            stmt = select(AdminAuditLog)
            if not isinstance(workspace, _AllWorkspaces):
                stmt = stmt.where(AdminAuditLog.workspace_id == workspace)
            rows = (
                session.execute(stmt.order_by(AdminAuditLog.at.desc()).limit(limit)).scalars().all()
            )
            return [_record(row) for row in rows]
