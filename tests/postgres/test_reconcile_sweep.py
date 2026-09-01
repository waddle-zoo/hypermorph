"""Bounded reconcile SWEEP over open-PR proposals (hq-3ta2, ADR 0012).

Real server, real Postgres, a real LOCAL context repository, MOCKED GitHub PR
state (no network). Proves the sweep reconciles up to `limit` open-PR tasks
oldest-first, returns a per-task summary, ISOLATES per-task failures (one raising
task never aborts the sweep or corrupts another), respects the limit, and is
idempotent (terminal tasks are not re-swept). It never merges or approves --
GitHub stays the merge authority.
"""

from __future__ import annotations

import threading

import pytest

from hyperset.flywheel import git_pr
from hyperset.repositories.postgres import (
    PostgresContextRepository,
    PostgresReviewRepository,
    PostgresWritebackConfigRepository,
)
from hyperset.transport.http import build_server
from tests.integration.test_git_context_source import CONTEXT_PATH, make_repository
from tests.postgres.test_interactive_review import _post
from tests.postgres.test_proposal_lifecycle import _pr, _routed_task

SWEEP = "/admin/api/v0/review/tasks/reconcile-sweep"


@pytest.fixture
def server_url(session_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("HYPERSET_CONTEXT_CACHE_DIR", str(tmp_path / "cache"))
    server = build_server(session_factory=session_factory, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _make_target_task(session_factory, tmp_path, *, name, key, idem):
    """A local repo + write-back target + registered source + a routed task."""
    repo = make_repository(tmp_path / name)
    target = PostgresWritebackConfigRepository(session_factory).set(
        routing_key=key, repository=str(repo), base_ref="main", manifest_path=CONTEXT_PATH
    )
    source = PostgresContextRepository(session_factory).register_source(
        repository=str(repo), ref="main", path=CONTEXT_PATH, display_name=name
    )
    task = _routed_task(
        session_factory, target_id=target.id, repository=str(repo), key=key, idem=idem
    )
    return repo, source, task


def _by_id(summary, task_id):
    return next(t for t in summary["tasks"] if t["task_id"] == task_id)


@pytest.mark.postgres
def test_sweep_reconciles_open_tasks_with_a_per_task_summary(
    server_url, session_factory, tmp_path, monkeypatch
):
    repo_a, source_a, task_a = _make_target_task(
        session_factory, tmp_path, name="a", key="revenue", idem="sw:a"
    )
    _, source_b, task_b = _make_target_task(
        session_factory, tmp_path, name="b", key="marketing", idem="sw:b"
    )

    # A's PR is merged (-> synced), B's is still open (-> recorded, not synced), so
    # the summary carries a DISTINCT per-task state for each.
    def _reader(*, repository, head_branch, token=None):
        if repository == str(repo_a):
            return {"state": "merged", "merged": True, "pr_url": "u", "pr_number": 1}
        return {"state": "open", "merged": False, "pr_url": "u", "pr_number": 2}

    monkeypatch.setattr(git_pr, "read_pr_state", _reader)

    status, payload = _post(f"{server_url}{SWEEP}", {"limit": 10})
    assert status == 200, payload
    summary = payload["sweep"]
    assert summary["count"] == 2
    assert _by_id(summary, task_a.id)["state"] == "synced"
    assert _by_id(summary, task_b.id)["state"] == "open"
    # A's source was re-synced; B's open PR was not applied.
    context = PostgresContextRepository(session_factory)
    assert context.get_source(source_a.id).last_attempt_status in ("synced", "unchanged")
    assert context.get_source(source_b.id).last_attempt_status == "never_synced"


@pytest.mark.postgres
def test_a_raising_task_does_not_abort_or_corrupt_the_sweep(
    server_url, session_factory, tmp_path, monkeypatch
):
    repo_a, source_a, task_a = _make_target_task(
        session_factory, tmp_path, name="a", key="revenue", idem="sw:raise-a"
    )
    _, source_b, task_b = _make_target_task(
        session_factory, tmp_path, name="b", key="marketing", idem="sw:ok-b"
    )

    # The PR read RAISES for A's repo, and reports a clean merge for B's.
    def _reader(*, repository, head_branch, token=None):
        if repository == str(repo_a):
            raise RuntimeError("github unreachable for A")
        return {"state": "merged", "merged": True, "pr_url": "u", "pr_number": 7}

    monkeypatch.setattr(git_pr, "read_pr_state", _reader)

    status, payload = _post(f"{server_url}{SWEEP}", {"limit": 10})
    assert status == 200, payload
    summary = payload["sweep"]
    # A failed, B succeeded -- one task's failure never aborted the sweep.
    assert _by_id(summary, task_a.id)["state"] == "error"
    assert _by_id(summary, task_b.id)["state"] == "synced"

    context = PostgresContextRepository(session_factory)
    # A's transaction rolled back cleanly: its source is untouched and no lifecycle
    # was recorded on its task (no leak into B's transaction).
    assert context.get_source(source_a.id).last_attempt_status == "never_synced"
    assert "pr_lifecycle" not in (
        PostgresReviewRepository(session_factory).get_task(task_a.id).proposal_payload
    )
    # B committed independently.
    assert context.get_source(source_b.id).last_attempt_status in ("synced", "unchanged")
    assert (
        PostgresReviewRepository(session_factory)
        .get_task(task_b.id)
        .proposal_payload["pr_lifecycle"]["state"]
        == "synced"
    )


@pytest.mark.postgres
def test_the_limit_is_respected_oldest_first(server_url, session_factory, tmp_path, monkeypatch):
    _, _, task1 = _make_target_task(session_factory, tmp_path, name="1", key="d1", idem="sw:1")
    _, _, task2 = _make_target_task(session_factory, tmp_path, name="2", key="d2", idem="sw:2")
    _, _, task3 = _make_target_task(session_factory, tmp_path, name="3", key="d3", idem="sw:3")
    monkeypatch.setattr(git_pr, "read_pr_state", _pr("merged", merged=True))

    status, payload = _post(f"{server_url}{SWEEP}", {"limit": 2})
    assert status == 200, payload
    summary = payload["sweep"]
    assert summary["count"] == 2
    swept = {t["task_id"] for t in summary["tasks"]}
    # The two OLDEST were swept; the newest was left untouched (no lifecycle).
    assert swept == {task1.id, task2.id}
    review = PostgresReviewRepository(session_factory)
    assert "pr_lifecycle" not in review.get_task(task3.id).proposal_payload


@pytest.mark.postgres
def test_the_sweep_is_idempotent_over_terminal_tasks(
    server_url, session_factory, tmp_path, monkeypatch
):
    _make_target_task(session_factory, tmp_path, name="a", key="revenue", idem="sw:idem")
    monkeypatch.setattr(git_pr, "read_pr_state", _pr("merged", merged=True))

    # First sweep synced the one open task.
    status, payload = _post(f"{server_url}{SWEEP}", {"limit": 10})
    assert status == 200 and payload["sweep"]["count"] == 1
    assert payload["sweep"]["tasks"][0]["state"] == "synced"

    # A second sweep selects it no more (terminal 'synced'), so it is a no-op.
    status, payload = _post(f"{server_url}{SWEEP}", {"limit": 10})
    assert status == 200
    assert payload["sweep"]["count"] == 0


@pytest.mark.postgres
def test_the_sweep_is_admin_only_and_validates_limit(server_url, session_factory):
    # Public prefix is refused (a deployment sync is an admin action).
    status, _ = _post(f"{server_url}/playground/api/v0/review/tasks/reconcile-sweep", {"limit": 5})
    assert status == 404
    # An out-of-range limit is a loud 400.
    status, payload = _post(f"{server_url}{SWEEP}", {"limit": 0})
    assert status == 400 and "limit" in payload["error"]["message"]
    status, payload = _post(f"{server_url}{SWEEP}", {"limit": 99999})
    assert status == 400 and "limit" in payload["error"]["message"]


@pytest.mark.postgres
def test_a_failed_summary_audit_fails_the_request_not_a_silent_200(
    server_url, session_factory, tmp_path, monkeypatch
):
    """The sweep's summary audit is MANDATORY (hq-3ta2 #437 round 2, #421 class): a
    successful admin sweep must leave a summary audit row, so a failing summary
    audit FAILS the request rather than silently returning 200. The per-task work
    (already audited per task) still commits; only the summary append fails."""
    from hyperset.repositories.postgres import PostgresAdminAuditRepository

    _, source, _task = _make_target_task(
        session_factory, tmp_path, name="a", key="revenue", idem="sw:auditfail"
    )
    monkeypatch.setattr(git_pr, "read_pr_state", _pr("merged", merged=True))

    original = PostgresAdminAuditRepository.record

    def _selective(self, **kwargs):
        # Only the SWEEP summary audit fails; the per-task reconcile audits succeed.
        if kwargs.get("action") == "review_task.reconcile_sweep":
            raise RuntimeError("audit store down")
        return original(self, **kwargs)

    monkeypatch.setattr(PostgresAdminAuditRepository, "record", _selective)

    status, _payload = _post(f"{server_url}{SWEEP}", {"limit": 10})
    assert status != 200
    assert status == 500
    # The per-task reconcile still committed and was audited per task.
    assert PostgresContextRepository(session_factory).get_source(source.id).last_attempt_status in (
        "synced",
        "unchanged",
    )
