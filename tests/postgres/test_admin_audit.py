"""The append-only admin audit repository over a real store (hy-gh-75).

Behavioral against Postgres because it is a repository reader/writer: it appends an
entry and lists them newest-first, and it exposes NO update or delete method, so the
trail cannot be rewritten through the application.
"""

from __future__ import annotations

import pytest

from hyperset.repositories.errors import NotFoundError
from hyperset.repositories.postgres import (
    PostgresAdminAuditRepository,
    PostgresContextRepository,
)
from hyperset.repositories.scope import ALL_WORKSPACES


def test_append_then_list_returns_newest_first(session_factory):
    repo = PostgresAdminAuditRepository(session_factory)
    first = repo.record(actor="alice", action="context_source.add", result="ok", target="src-1")
    second = repo.record(
        actor="bob",
        action="writeback_config.set",
        result="ok",
        target="org/repo",
        actor_issuer="https://idp/",
        detail="token_source=env_ref",
    )
    entries = repo.list(workspace=ALL_WORKSPACES)
    assert [e.id for e in entries[:2]] == [second.id, first.id]  # newest first
    top = entries[0]
    assert top.actor == "bob" and top.action == "writeback_config.set"
    assert top.actor_issuer == "https://idp/" and top.detail == "token_source=env_ref"
    assert top.at is not None


def test_the_repository_is_append_only():
    # The append-only contract is structural: no update/delete/set method exists to rewrite
    # a recorded entry from the application.
    for forbidden in ("update", "delete", "set", "remove", "edit"):
        assert not hasattr(PostgresAdminAuditRepository, forbidden), forbidden


def test_list_is_bounded_by_limit(session_factory):
    repo = PostgresAdminAuditRepository(session_factory)
    for i in range(5):
        repo.record(actor="a", action="context_source.sync", result="ok", target=f"s{i}")
    assert len(repo.list(workspace=ALL_WORKSPACES, limit=3)) == 3


def test_a_register_and_its_audit_commit_in_one_transaction(session_factory):
    # The add path couples the register to its audit append in ONE transaction (hy-gh-75
    # round 2). When it commits, BOTH the source and its audit row are durable.
    context = PostgresContextRepository(session_factory)
    audit = PostgresAdminAuditRepository(session_factory)
    with session_factory() as session, session.begin():
        source = context.register_source(
            repository="org/ctx", ref="main", path="domains", session=session
        )
        audit.record(
            actor="alice",
            action="context_source.add",
            result="ok",
            target=source.id,
            detail="org/ctx@main:domains",
            session=session,
        )
    persisted = context.get_source_by_identity(repository="org/ctx", ref="main", path="domains")
    assert persisted.id == source.id
    assert any(entry.target == source.id for entry in audit.list(workspace=ALL_WORKSPACES))


def test_a_failed_audit_append_rolls_back_the_register(session_factory):
    # The adversary's invariant, at the datastore: if the audit append fails inside the
    # coupled transaction, the register is rolled back too -- so NO context source is ever
    # persisted without an audit row. Here the append raises inside the transaction; the
    # source must not survive it.
    context = PostgresContextRepository(session_factory)
    audit = PostgresAdminAuditRepository(session_factory)
    with pytest.raises(RuntimeError, match="audit down"):
        with session_factory() as session, session.begin():
            context.register_source(
                repository="org/rollback", ref="main", path="domains", session=session
            )
            raise RuntimeError("audit down")  # stands in for a failed audit append
    with pytest.raises(NotFoundError):
        context.get_source_by_identity(repository="org/rollback", ref="main", path="domains")
    assert all(entry.target != "org/rollback" for entry in audit.list(workspace=ALL_WORKSPACES))


def test_record_stamps_the_current_correlation_id_and_defaults_to_none(session_factory):
    """hy-w9ntg: record() stamps the id of the request that performed the action, read from the
    per-request contextvar -- not a caller argument, so no call site can forge or omit it. Off a
    request (no id set) it records None, never a stale id."""
    from hyperset.observability.correlation import set_correlation_id
    from hyperset.repositories.scope import ALL_WORKSPACES

    repo = PostgresAdminAuditRepository(session_factory)
    set_correlation_id("req-abc123")
    on_request = repo.record(actor="a", action="connection.set", result="ok", target="c1")
    set_correlation_id(None)
    off_request = repo.record(actor="a", action="connection.set", result="ok", target="c2")

    assert on_request.correlation_id == "req-abc123"
    assert off_request.correlation_id is None
    # And it round-trips through list().
    by_id = {e.id: e for e in repo.list(workspace=ALL_WORKSPACES)}
    assert by_id[on_request.id].correlation_id == "req-abc123"
    assert by_id[off_request.id].correlation_id is None
