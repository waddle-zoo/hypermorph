"""Durable, workspace-scoped admin-audit trail for the propose action (hq-hnrf area 3).

Propose is a mutating admin action that OPENS A PR against a specific write-back TARGET,
so -- like reconcile / context_source.sync / connection ops -- it must leave a durable,
tamper-evident admin-audit row naming WHO proposed WHICH task to WHICH target at WHICH
source commit. Before this it left only the task `proposal_payload` (mutable). These
tests pin the audit row's content and its per-tenant isolation (a foreign tenant cannot
read another tenant's propose actions).
"""

from __future__ import annotations

import copy
import threading

import pytest

from hyperset.repositories.postgres import (
    PostgresAdminAuditRepository,
    PostgresReviewRepository,
    PostgresWritebackConfigRepository,
)
from hyperset.repositories.scope import ALL_WORKSPACES
from hyperset.transport.http import build_server
from tests.integration.test_git_context_source import CONTEXT_PATH, make_repository
from tests.postgres.test_interactive_review import DRAFT_PAYLOAD, _post
from tests.review_api import PROPOSE_REVIEW_TO_GIT_PATH


@pytest.fixture
def server_url(session_factory, monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    server = build_server(session_factory=session_factory, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _payload_for(domain: str) -> dict:
    payload = copy.deepcopy(DRAFT_PAYLOAD)
    payload["domain"] = domain
    payload["miss"] = {"question": "q?", "domain": domain}
    return payload


def _propose(server_url, session_factory, domain, idem):
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key=idem, proposal_payload=_payload_for(domain)
    )
    status, payload = _post(
        f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": task.id}
    )
    return task.id, status, payload


def _propose_rows(session_factory, *, workspace=ALL_WORKSPACES):
    return [
        row
        for row in PostgresAdminAuditRepository(session_factory).list(workspace=workspace)
        if row.action == "review_task.propose"
    ]


@pytest.mark.postgres
def test_propose_writes_a_durable_audit_row_naming_the_routed_target_and_commit(
    server_url, session_factory, tmp_path
):
    """Two keyed targets: each propose leaves ONE durable admin-audit row whose detail
    names ITS target (id + repo) and the source commit + PR, tied to the task, never the
    sibling target."""
    repo_rev = make_repository(tmp_path / "rev")
    repo_mkt = make_repository(tmp_path / "mkt")
    targets = PostgresWritebackConfigRepository(session_factory)
    rev = targets.set(
        routing_key="revenue", repository=str(repo_rev), base_ref="main", manifest_path=CONTEXT_PATH
    )
    mkt = targets.set(
        routing_key="marketing",
        repository=str(repo_mkt),
        base_ref="main",
        manifest_path=CONTEXT_PATH,
    )

    rev_task, status, payload = _propose(server_url, session_factory, "revenue", "wb:rev")
    assert status == 200, payload
    rev_commit = payload["proposal"]["commit_sha"]
    mkt_task, status, payload = _propose(server_url, session_factory, "marketing", "wb:mkt")
    assert status == 200, payload
    mkt_commit = payload["proposal"]["commit_sha"]

    rows = {row.target: row for row in _propose_rows(session_factory)}
    assert set(rows) == {rev_task, mkt_task}, "one propose audit row per task"

    rev_row = rows[rev_task]
    assert rev_row.action == "review_task.propose" and rev_row.result == "ok"
    assert rev_row.actor  # attributed (anonymous off the gate, a subject on it)
    assert rev.id in rev_row.detail and str(repo_rev) in rev_row.detail
    assert rev_commit in rev_row.detail
    # The revenue propose audit names ONLY the revenue target -- never marketing's.
    assert mkt.id not in rev_row.detail and str(repo_mkt) not in rev_row.detail

    mkt_row = rows[mkt_task]
    assert mkt.id in mkt_row.detail and str(repo_mkt) in mkt_row.detail
    assert mkt_commit in mkt_row.detail


@pytest.mark.postgres
def test_a_propose_audit_row_is_workspace_scoped_and_not_read_by_a_foreign_tenant(
    server_url, session_factory, tmp_path
):
    """The propose audit row is stamped with the caller's workspace ('default' off the
    gate) and is invisible to a foreign tenant's read -- the audit trail never leaks one
    tenant's actions to another."""
    repo_rev = make_repository(tmp_path / "rev")
    PostgresWritebackConfigRepository(session_factory).set(
        routing_key="revenue", repository=str(repo_rev), base_ref="main", manifest_path=CONTEXT_PATH
    )
    task_id, status, _ = _propose(server_url, session_factory, "revenue", "wb:ws")
    assert status == 200

    # The default-workspace admin sees the row...
    default_rows = _propose_rows(session_factory, workspace="default")
    assert any(row.target == task_id for row in default_rows)
    assert all(row.workspace_id == "default" for row in default_rows)
    # ...a foreign tenant does NOT.
    assert _propose_rows(session_factory, workspace="beta") == []


@pytest.mark.postgres
def test_the_audit_list_is_fail_closed_and_needs_an_explicit_scope(session_factory):
    """Round-2 blocker 1: the audit list has NO silent global default. Omitting the
    workspace is a TypeError (a legacy/internal caller cannot read every tenant by
    omission); a concrete workspace scopes; only the explicit ALL_WORKSPACES sentinel
    reads across tenants."""
    audit = PostgresAdminAuditRepository(session_factory)
    with pytest.raises(TypeError):
        audit.list()
    # The explicit system opt-in is accepted (and a concrete scope is the ordinary path).
    assert audit.list(workspace=ALL_WORKSPACES) == []
    assert audit.list(workspace="default") == []


@pytest.mark.postgres
@pytest.mark.parametrize(
    "userinfo",
    [
        "git-user:s3cr3ttoken",  # ordinary credential
        "git-user:s3cr3ttoken?x=1",  # `?` INSIDE the userinfo (the #431 r8 / #443 r2 leak)
        "git-user:s3cr3ttoken#frag",  # `#` INSIDE the userinfo
    ],
)
def test_a_legacy_credential_bearing_target_never_lands_a_token_in_the_audit(
    session_factory, monkeypatch, userinfo
):
    """A legacy stored target with URL userinfo fails closed before Git or audit.

    The current target-set path rejects credential-bearing pointers, but an older row
    could still hold one. Propose must refuse that row, INCLUDING when a `?` or `#` sits
    inside the userinfo (the #431 r8 / #443 r2 redaction regression shape).
    """
    from hyperset.transport import operations

    # A stored target with URL userinfo, inserted through the repo (the legacy path that
    # bypasses the HTTP set-guard).
    repository = f"https://{userinfo}@github.example/org/repo.git"
    PostgresWritebackConfigRepository(session_factory).set(
        routing_key="revenue", repository=repository, base_ref="main", manifest_path=CONTEXT_PATH
    )
    token_calls = []
    writer_calls = []

    def resolve_token(config):
        token_calls.append(config)
        return "tok"

    def write_proposal(**kwargs):
        writer_calls.append(kwargs)
        return {"commit_sha": "must-not-exist", "pr_url": "must-not-exist"}

    monkeypatch.setattr(operations, "resolve_writeback_token", resolve_token)
    monkeypatch.setattr(operations, "_PROPOSE_WRITER", write_proposal)
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key="wb:legacy-cred", proposal_payload=_payload_for("revenue")
    )
    with pytest.raises(operations.OperationError) as raised:
        operations.run_operation(
            operations.PROPOSE_REVIEW_TO_GIT,
            {"task_id": task.id},
            session_factory=session_factory,
        )

    assert raised.value.code == operations.INVALID_REQUEST
    assert "embedded repository credentials" in raised.value.message
    assert token_calls == []
    assert writer_calls == []
    assert _propose_rows(session_factory) == []


@pytest.mark.postgres
def test_a_fail_closed_propose_writes_no_audit_row(server_url, session_factory, tmp_path):
    """The mirror of the success rows: an audit entry is evidence of a PR that opened,
    so a propose that FAILS CLOSED (no routable target) must leave NONE. The row is
    coupled to the routing write inside the propose, AFTER the target resolves and the
    PR opens, so a domain with no target -- and a domain whose only target is DISABLED
    (routing fails closed the same way) -- refuses before anything is written. A row on
    either path would assert a proposal that never happened.
    """
    # Absent: no target configured for this domain at all.
    _, status, payload = _propose(server_url, session_factory, "revenue", "wb:absent")
    assert status != 200, payload
    assert "no write-back target" in str(payload)
    assert _propose_rows(session_factory) == []

    # Disabled: a target keyed to the domain exists but is turned off, so
    # `get_by_routing` skips it and routing still fails closed.
    targets = PostgresWritebackConfigRepository(session_factory)
    target = targets.set(
        routing_key="revenue",
        repository=str(make_repository(tmp_path / "rev")),
        base_ref="main",
        manifest_path=CONTEXT_PATH,
    )
    targets.set_enabled(target.id, False)

    _, status, payload = _propose(server_url, session_factory, "revenue", "wb:disabled")
    assert status != 200, payload
    assert "no write-back target" in str(payload)
    assert _propose_rows(session_factory) == []
