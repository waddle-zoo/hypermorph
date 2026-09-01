"""Proposal-only Git-PR write-back TARGETS, as mutable config rows (hy-8o8m, hq-1h1z).

A set of target rows: `get` reads the DEFAULT target (backward compat), `set`
upserts a target, `get_by_routing` picks the target a proposal's domain routes
to, `list`/`get_by_id` read them, and `set_enabled`/`record_test_result`/`delete`
are the admin manage surface (hq-095h). It stores targets and nothing more --
there is no method here that approves, merges, or advances governed context.
Authority stays a human Git merge (ADR 0012).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from hyperset.db.models import WRITEBACK_SINGLETON_ID, WritebackConfig
from hyperset.repositories.dto import WritebackConfigRecord


def _record(row: WritebackConfig) -> WritebackConfigRecord:
    return WritebackConfigRecord(
        repository=row.repository,
        base_ref=row.base_ref,
        manifest_path=row.manifest_path,
        updated_at=row.updated_at,
        id=row.id,
        routing_key=row.routing_key,
        is_default=row.is_default,
        enabled=row.enabled,
        test_result=row.test_result,
        reviewer_routing=row.reviewer_routing,
        workspace_id=row.workspace_id,
        token_ref=row.token_ref,
        token_source=row.token_source,
        token_ciphertext=row.token_ciphertext,
        token_nonce=row.token_nonce,
        app_id=row.app_id,
        app_key_ciphertext=row.app_key_ciphertext,
        app_key_nonce=row.app_key_nonce,
    )


class PostgresWritebackConfigRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get(self, *, workspace: str = "default") -> WritebackConfigRecord | None:
        """The DEFAULT write-back target for `workspace`, or None when none is set.

        Backward-compatible reader for the surfaces that operate on the one default
        target (the admin config view/write and readiness): it returns the
        `is_default` row of the workspace -- which the migration set to the legacy
        singleton for the 'default' workspace -- so a single-tenant estate behaves
        exactly as before hq-t6nx.
        """
        with self._session_factory() as session:
            return self._default(session, workspace)

    def get_by_routing(
        self, domain: str | None, *, workspace: str = "default"
    ) -> WritebackConfigRecord | None:
        """The target a proposal for `domain` routes to WITHIN `workspace`, or None
        (FAIL CLOSED).

        Routing is exact then default, never fan-out (hq-1h1z) and never across
        tenants (hq-t6nx): an ENABLED target of THIS WORKSPACE whose `routing_key`
        equals `domain` wins; otherwise the workspace's single ENABLED default
        target; otherwise None so the caller refuses rather than writing to an
        arbitrary target. A disabled target is skipped, a keyed match is never
        combined with the default, and a target in ANOTHER workspace is never
        eligible -- so a proposal touches one repository and never crosses tenants.
        """
        with self._session_factory() as session:
            if domain is not None:
                keyed = session.scalars(
                    select(WritebackConfig)
                    .where(WritebackConfig.workspace_id == workspace)
                    .where(WritebackConfig.routing_key == domain)
                    .where(WritebackConfig.enabled.is_(True))
                ).one_or_none()
                if keyed is not None:
                    return _record(keyed)
            return self._default(session, workspace)

    def list(self, *, workspace: str | None = None) -> list[WritebackConfigRecord]:
        """Every configured target IN `workspace`, default first then by routing key
        (hq-t6nx): a tenant's admin never sees another tenant's targets. `None` is an
        internal caller that does not scope; the admin handler passes a concrete one."""
        with self._session_factory() as session:
            stmt = select(WritebackConfig)
            if workspace is not None:
                stmt = stmt.where(WritebackConfig.workspace_id == workspace)
            rows = session.scalars(
                stmt.order_by(WritebackConfig.is_default.desc(), WritebackConfig.routing_key)
            ).all()
            return [_record(row) for row in rows]

    def get_by_id(
        self, target_id: str, *, workspace: str | None = None
    ) -> WritebackConfigRecord | None:
        """One target by its stable id IFF it is in `workspace`, else None (hq-095h,
        hq-t6nx). A target in another tenant is reported as absent -- non-disclosing,
        so existence never leaks across workspaces."""
        with self._session_factory() as session:
            row = session.get(WritebackConfig, target_id)
            if row is None or (workspace is not None and row.workspace_id != workspace):
                return None
            return _record(row)

    @staticmethod
    def _in_workspace(
        sess: Session, target_id: str, workspace: str | None
    ) -> WritebackConfig | None:
        """The target by id IFF it is in `workspace`, else None (hq-t6nx). A target
        in another tenant is treated as absent, so a manage action never reaches
        across workspaces and never leaks existence. `workspace=None` is an internal
        caller that does not scope; the admin handlers always pass a concrete one."""
        row = sess.get(WritebackConfig, target_id)
        if row is None or (workspace is not None and row.workspace_id != workspace):
            return None
        return row

    def set_enabled(
        self, target_id: str, enabled: bool, *, workspace: str | None = None, session=None
    ) -> WritebackConfigRecord | None:
        """Enable or disable a target by id in `workspace` (hq-095h, hq-t6nx).
        Soft-disable: a disabled target keeps its config/secret refs but is excluded
        from routing (`get_by_routing` filters on `enabled`), so an operator can
        pause a target a probe found unreachable without losing its settings. Returns
        None when no such target exists IN THE WORKSPACE. With `session` given the
        change is made in THAT transaction and not committed here, so the caller can
        couple it to an audit append (hq-095h)."""

        def _apply(sess: Session) -> WritebackConfigRecord | None:
            row = self._in_workspace(sess, target_id, workspace)
            if row is None:
                return None
            row.enabled = enabled
            sess.flush()
            return _record(row)

        if session is not None:
            return _apply(session)
        with self._session_factory() as owned, owned.begin():
            return _apply(owned)

    def record_test_result(
        self, target_id: str, result: str, *, workspace: str | None = None, session=None
    ) -> WritebackConfigRecord | None:
        """Persist the last non-secret probe result on a target in `workspace`
        (hq-095h, hq-t6nx). The value is a short status string (never a secret).
        Returns None when no such target exists in the workspace."""

        def _apply(sess: Session) -> WritebackConfigRecord | None:
            row = self._in_workspace(sess, target_id, workspace)
            if row is None:
                return None
            row.test_result = result
            sess.flush()
            return _record(row)

        if session is not None:
            return _apply(session)
        with self._session_factory() as owned, owned.begin():
            return _apply(owned)

    def delete(self, target_id: str, *, workspace: str | None = None, session=None) -> bool:
        """Delete a target by id in `workspace` (hq-095h, hq-t6nx). Returns True if a
        row was removed; False if absent OR in another workspace.

        Mechanical only -- the caller enforces policy (the default/catch-all
        target is not deletable through the manage surface; disable it instead),
        so this never silently strips routing's fallback without that check. With
        `session` given the delete is made in THAT transaction and not committed
        here, so the caller can couple it to an audit append.
        """

        def _apply(sess: Session) -> bool:
            row = self._in_workspace(sess, target_id, workspace)
            if row is None:
                return False
            sess.delete(row)
            return True

        if session is not None:
            return _apply(session)
        with self._session_factory() as owned, owned.begin():
            return _apply(owned)

    @staticmethod
    def _default(session: Session, workspace: str) -> WritebackConfigRecord | None:
        row = session.scalars(
            select(WritebackConfig)
            .where(WritebackConfig.workspace_id == workspace)
            .where(WritebackConfig.is_default.is_(True))
            .where(WritebackConfig.enabled.is_(True))
        ).one_or_none()
        return _record(row) if row is not None else None

    def set(
        self,
        *,
        repository: str,
        base_ref: str,
        manifest_path: str,
        routing_key: str | None = None,
        enabled: bool | None = None,
        test_result: str | None = None,
        reviewer_routing: str | None = None,
        workspace: str = "default",
        token_source: str = "env_ref",
        token_ref: str | None = None,
        token_ciphertext: bytes | None = None,
        token_nonce: bytes | None = None,
        app_id: int | None = None,
        app_key_ciphertext: bytes | None = None,
        app_key_nonce: bytes | None = None,
        session: Session | None = None,
    ) -> WritebackConfigRecord:
        """Upsert a config target. Configures the target only.

        With `routing_key=None` this upserts the DEFAULT target (the legacy
        singleton row, `is_default=True`) so the existing admin config path stays
        byte-for-byte the same. With a `routing_key` it upserts the keyed target
        for that domain (creating a fresh, non-default row on first write), so a
        proposal for that domain routes to it (hq-1h1z).

        In `env_ref` mode `token_ref` is the NAME of a server-side secret; in
        `encrypted` mode `token_ciphertext`/`token_nonce` hold the AES-256-GCM
        ciphertext of the token (hy-up4k); in `github_app` mode `app_id` and the
        encrypted App private key `app_key_ciphertext`/`app_key_nonce` are stored
        (hy-bdhg). This method stores bytes and a mode -- it never encrypts,
        decrypts, holds a plaintext secret, or mints a token. A local-path target
        leaves all of them null.

        `reviewer_routing` is a config field the admin form owns like repository
        and base_ref (hq-1rq7): it is written from the form value, so clearing it
        clears the target's reviewers (the honest FAIL-CLOSED needs-routing state),
        unlike `enabled`/`test_result` which have their own endpoints and are
        preserved on update.

        When `session` is given the row is written in THAT transaction and not committed
        here, so the caller can couple the config write to its audit append in one
        transaction (hy-gh-75 round 2).
        """
        fields = dict(
            repository=repository,
            base_ref=base_ref,
            manifest_path=manifest_path,
            reviewer_routing=reviewer_routing,
            token_source=token_source,
            token_ref=token_ref,
            token_ciphertext=token_ciphertext,
            token_nonce=token_nonce,
            app_id=app_id,
            app_key_ciphertext=app_key_ciphertext,
            app_key_nonce=app_key_nonce,
        )
        if session is not None:
            return self._set(session, routing_key, enabled, test_result, workspace, fields)
        with self._session_factory() as owned, owned.begin():
            return self._set(owned, routing_key, enabled, test_result, workspace, fields)

    @staticmethod
    def _set(
        session: Session,
        routing_key: str | None,
        enabled: bool | None,
        test_result: str | None,
        workspace: str,
        fields: dict,
    ) -> WritebackConfigRecord:
        created = False
        if routing_key is None:
            # The default/catch-all target of THIS WORKSPACE (hq-t6nx): each tenant
            # has its own default, found-or-created here so exactly one exists. The
            # 'default' workspace's default is the migrated legacy singleton
            # (is_default, workspace_id='default'), so a single-tenant estate is
            # unchanged.
            row = session.scalars(
                select(WritebackConfig)
                .where(WritebackConfig.workspace_id == workspace)
                .where(WritebackConfig.is_default.is_(True))
            ).one_or_none()
            if row is None:
                # The 'default' workspace keeps the fixed legacy singleton id, so a
                # single-tenant estate is byte-identical; other tenants get a fresh
                # auto-generated id (never passed explicitly, so the model default
                # fires).
                new_default = dict(is_default=True, routing_key=None, workspace_id=workspace)
                if workspace == "default":
                    new_default["id"] = WRITEBACK_SINGLETON_ID
                row = WritebackConfig(**new_default)
                session.add(row)
                created = True
        else:
            row = session.scalars(
                select(WritebackConfig)
                .where(WritebackConfig.workspace_id == workspace)
                .where(WritebackConfig.routing_key == routing_key)
            ).one_or_none()
            if row is None:
                row = WritebackConfig(
                    routing_key=routing_key, is_default=False, workspace_id=workspace
                )
                session.add(row)
                created = True
        for key, value in fields.items():
            setattr(row, key, value)
        # `enabled` and `test_result` are preserved on update unless explicitly
        # given: editing a target's config never silently re-enables a paused
        # target nor wipes its last probe result. A fresh target defaults to
        # enabled with no probe result.
        if enabled is not None:
            row.enabled = enabled
        elif created:
            row.enabled = True
        if test_result is not None:
            row.test_result = test_result
        session.flush()
        return _record(row)
