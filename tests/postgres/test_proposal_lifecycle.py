"""Proposal lifecycle: reconcile task -> PR -> merge -> sync (hq-ci92, ADR 0012).

Real server, real Postgres, a real LOCAL context repository, and a MOCKED GitHub
PR state (no network). Proves the bounded, explicit reconcile: given a task whose
proposal opened a PR, it reads whether a HUMAN merged it and, if so, re-syncs ONLY
the routed target's own context source. It NEVER merges, approves, or writes a
governed version -- GitHub stays the merge authority. Unmerged/unknown states are
recorded explicitly, never a silent done, and a proposal for one target never
syncs another target's source.
"""

from __future__ import annotations

import copy
import threading

import pytest

from hyperset.flywheel import git_pr
from hyperset.repositories.postgres import (
    PostgresContextRepository,
    PostgresReviewRepository,
    PostgresWritebackConfigRepository,
)
from hyperset.transport.http import build_server
from tests.integration.test_git_context_source import CONTEXT_PATH, git, make_repository
from tests.postgres.test_interactive_review import DRAFT_PAYLOAD, _governed_counts, _post
from tests.review_api import LIST_REVIEW_TASKS_PATH

RECONCILE = "/admin/api/v0/review/tasks/reconcile"


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


def _routed_task(session_factory, *, target_id, repository, key, idem):
    """A review task whose proposal already recorded a PR (review_routing with a
    backlink), as slice-8 records it -- so reconcile has a PR to check. A local
    target opens no real PR, so the backlink is set directly here."""
    payload = copy.deepcopy(DRAFT_PAYLOAD)
    payload["review_routing"] = {
        "status": "routed",
        "reviewers": ["alice"],
        "target": {
            "id": target_id,
            "routing_key": key,
            "repository": repository,
            "base_ref": "main",
        },
        "authority_commit": "deadbeefcafe",
        "backlink": "https://github.com/acme/context/pull/7",
        "head_branch": "hyperset/proposal/revenue-abc123def456",
        "path": CONTEXT_PATH,
    }
    return PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key=idem, proposal_payload=payload
    )


def _pr(state, *, merged, number=7):
    def _read(**_kwargs):
        return {
            "state": state,
            "merged": merged,
            "pr_url": "https://github.com/acme/context/pull/7",
            "pr_number": number,
        }

    return _read


def _lifecycle_from_task(session_factory, task_id):
    return (
        PostgresReviewRepository(session_factory).get_task(task_id).proposal_payload["pr_lifecycle"]
    )


@pytest.mark.postgres
def test_a_merged_pr_marks_merged_and_resyncs_the_routed_target(
    server_url, session_factory, tmp_path, monkeypatch
):
    repo = make_repository(tmp_path / "repo")
    base_before = git("rev-parse", "main", cwd=repo)
    target = PostgresWritebackConfigRepository(session_factory).set(
        routing_key="revenue", repository=str(repo), base_ref="main", manifest_path=CONTEXT_PATH
    )
    source = PostgresContextRepository(session_factory).register_source(
        repository=str(repo), ref="main", path=CONTEXT_PATH, display_name="revenue ctx"
    )
    task = _routed_task(
        session_factory, target_id=target.id, repository=str(repo), key="revenue", idem="rec:merged"
    )
    before = _governed_counts(session_factory)

    # GitHub says a HUMAN merged the PR.
    monkeypatch.setattr(git_pr, "read_pr_state", _pr("merged", merged=True))
    status, payload = _post(f"{server_url}{RECONCILE}", {"task_id": task.id})

    assert status == 200, payload
    life = payload["lifecycle"]
    assert life["state"] == "synced"
    assert life["merged"] is True
    assert life["sync"]["source_id"] == source.id
    assert life["sync"]["ok"] is True
    # The routed target's own source was re-synced...
    resynced = PostgresContextRepository(session_factory).get_source(source.id)
    assert resynced.last_attempt_status in ("synced", "unchanged")
    # ...hyperset merged NOTHING: the base ref is untouched and no governed row was
    # written (GitHub is the merge authority).
    assert git("rev-parse", "main", cwd=repo) == base_before
    assert _governed_counts(session_factory) == before
    # The lifecycle is persisted on the task and surfaces via the review read.
    assert _lifecycle_from_task(session_factory, task.id)["state"] == "synced"
    status, payload = _post(f"{server_url}/playground/api{LIST_REVIEW_TASKS_PATH}", {})
    surfaced = next(t for t in payload["tasks"] if t["id"] == task.id)
    assert surfaced["proposal_payload"]["pr_lifecycle"]["state"] == "synced"


@pytest.mark.postgres
def test_a_merged_pr_never_syncs_another_targets_source(
    server_url, session_factory, tmp_path, monkeypatch
):
    repo_a = make_repository(tmp_path / "a")
    repo_b = make_repository(tmp_path / "b")
    target_a = PostgresWritebackConfigRepository(session_factory).set(
        routing_key="revenue", repository=str(repo_a), base_ref="main", manifest_path=CONTEXT_PATH
    )
    context = PostgresContextRepository(session_factory)
    source_a = context.register_source(
        repository=str(repo_a), ref="main", path=CONTEXT_PATH, display_name="a"
    )
    source_b = context.register_source(
        repository=str(repo_b), ref="main", path=CONTEXT_PATH, display_name="b"
    )
    task = _routed_task(
        session_factory,
        target_id=target_a.id,
        repository=str(repo_a),
        key="revenue",
        idem="rec:iso",
    )

    monkeypatch.setattr(git_pr, "read_pr_state", _pr("merged", merged=True))
    status, payload = _post(f"{server_url}{RECONCILE}", {"task_id": task.id})
    assert status == 200, payload

    # Only target A's source was synced; B was never touched (no cross-target sync).
    assert payload["lifecycle"]["sync"]["source_id"] == source_a.id
    assert context.get_source(source_a.id).last_attempt_status in ("synced", "unchanged")
    assert context.get_source(source_b.id).last_attempt_status == "never_synced"


@pytest.mark.postgres
def test_a_closed_unmerged_pr_is_recorded_and_nothing_is_synced(
    server_url, session_factory, tmp_path, monkeypatch
):
    repo = make_repository(tmp_path / "repo")
    target = PostgresWritebackConfigRepository(session_factory).set(
        routing_key="revenue", repository=str(repo), base_ref="main", manifest_path=CONTEXT_PATH
    )
    source = PostgresContextRepository(session_factory).register_source(
        repository=str(repo), ref="main", path=CONTEXT_PATH, display_name="ctx"
    )
    task = _routed_task(
        session_factory, target_id=target.id, repository=str(repo), key="revenue", idem="rec:closed"
    )

    monkeypatch.setattr(git_pr, "read_pr_state", _pr("closed_unmerged", merged=False))
    status, payload = _post(f"{server_url}{RECONCILE}", {"task_id": task.id})

    assert status == 200, payload
    assert payload["lifecycle"]["state"] == "closed_unmerged"
    assert payload["lifecycle"]["sync"] is None
    # An unmerged PR is not applied: the source is not synced.
    assert (
        PostgresContextRepository(session_factory).get_source(source.id).last_attempt_status
        == "never_synced"
    )


@pytest.mark.postgres
def test_an_unknown_pr_state_is_explicit_never_a_silent_done(
    server_url, session_factory, tmp_path, monkeypatch
):
    repo = make_repository(tmp_path / "repo")
    target = PostgresWritebackConfigRepository(session_factory).set(
        routing_key="revenue", repository=str(repo), base_ref="main", manifest_path=CONTEXT_PATH
    )
    source = PostgresContextRepository(session_factory).register_source(
        repository=str(repo), ref="main", path=CONTEXT_PATH, display_name="ctx"
    )
    task = _routed_task(
        session_factory, target_id=target.id, repository=str(repo), key="revenue", idem="rec:unk"
    )

    monkeypatch.setattr(git_pr, "read_pr_state", _pr("unknown", merged=False))
    status, payload = _post(f"{server_url}{RECONCILE}", {"task_id": task.id})

    assert status == 200, payload
    assert payload["lifecycle"]["state"] == "unknown"
    assert payload["lifecycle"]["sync"] is None
    assert (
        PostgresContextRepository(session_factory).get_source(source.id).last_attempt_status
        == "never_synced"
    )


@pytest.mark.postgres
def test_a_target_edited_after_propose_reconciles_to_target_changed_and_syncs_nothing(
    server_url, session_factory, tmp_path, monkeypatch
):
    """TOCTOU (hq-ci92 #436 round 2): the target row is mutable. If target A is
    edited to point at B AFTER the proposal recorded A, reconcile must NOT read B's
    PR or sync B -- it reconciles against the RECORDED snapshot, sees the live row
    no longer matches, returns 'target_changed', and syncs NOTHING."""
    repo_a = make_repository(tmp_path / "a")
    repo_b = make_repository(tmp_path / "b")
    targets = PostgresWritebackConfigRepository(session_factory)
    target = targets.set(
        routing_key="revenue", repository=str(repo_a), base_ref="main", manifest_path=CONTEXT_PATH
    )
    context = PostgresContextRepository(session_factory)
    source_a = context.register_source(
        repository=str(repo_a), ref="main", path=CONTEXT_PATH, display_name="a"
    )
    source_b = context.register_source(
        repository=str(repo_b), ref="main", path=CONTEXT_PATH, display_name="b"
    )
    # The proposal recorded target A (repo_a).
    task = _routed_task(
        session_factory,
        target_id=target.id,
        repository=str(repo_a),
        key="revenue",
        idem="rec:toctou",
    )

    # The SAME target row is now edited to point at repo_b (same routing key -> same id).
    targets.set(
        routing_key="revenue", repository=str(repo_b), base_ref="main", manifest_path=CONTEXT_PATH
    )

    # The PR reader must NOT be consulted once the target no longer matches the record.
    def _must_not_read(**_kwargs):
        raise AssertionError("reconcile read a PR after the target identity changed")

    monkeypatch.setattr(git_pr, "read_pr_state", _must_not_read)

    status, payload = _post(f"{server_url}{RECONCILE}", {"task_id": task.id})
    assert status == 200, payload
    assert payload["lifecycle"]["state"] == "target_changed"
    assert payload["lifecycle"]["sync"] is None
    # NEITHER repo's source was synced.
    assert context.get_source(source_a.id).last_attempt_status == "never_synced"
    assert context.get_source(source_b.id).last_attempt_status == "never_synced"


@pytest.mark.postgres
def test_a_failed_audit_rolls_back_the_sync_snapshot_too(
    server_url, session_factory, tmp_path, monkeypatch
):
    """Atomicity (hq-ci92 round 2, #421/#428 discipline): the merge re-sync's
    snapshot, the lifecycle write, and the audit rows share ONE transaction, so if
    the audit append fails EVERYTHING rolls back -- the source snapshot included --
    and the op reports 'nothing recorded' rather than leaving a synced source with
    no lifecycle/audit."""
    from hyperset.repositories.postgres import PostgresAdminAuditRepository

    repo = make_repository(tmp_path / "repo")
    target = PostgresWritebackConfigRepository(session_factory).set(
        routing_key="revenue", repository=str(repo), base_ref="main", manifest_path=CONTEXT_PATH
    )
    source = PostgresContextRepository(session_factory).register_source(
        repository=str(repo), ref="main", path=CONTEXT_PATH, display_name="ctx"
    )
    task = _routed_task(
        session_factory, target_id=target.id, repository=str(repo), key="revenue", idem="rec:roll"
    )
    monkeypatch.setattr(git_pr, "read_pr_state", _pr("merged", merged=True))

    # The audit append fails inside the reconcile transaction.
    def _boom(self, **_kwargs):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(PostgresAdminAuditRepository, "record", _boom)

    status, payload = _post(f"{server_url}{RECONCILE}", {"task_id": task.id})
    assert status == 500
    assert "nothing" in payload["error"]["message"].lower()

    # The source snapshot rolled back WITH the lifecycle and audit: the source was
    # NOT left synced, and no lifecycle was recorded on the task.
    assert (
        PostgresContextRepository(session_factory).get_source(source.id).last_attempt_status
        == "never_synced"
    )
    assert "pr_lifecycle" not in (
        PostgresReviewRepository(session_factory).get_task(task.id).proposal_payload
    )


@pytest.mark.postgres
def test_reconcile_is_admin_only_and_needs_a_task_id(server_url, session_factory):
    # Public prefix is refused (a deployment sync is an admin action).
    status, _ = _post(f"{server_url}/playground/api/v0/review/tasks/reconcile", {"task_id": "x"})
    assert status == 404
    # Admin prefix with no task_id is a loud 400.
    status, payload = _post(f"{server_url}{RECONCILE}", {})
    assert status == 400 and "task_id" in payload["error"]["message"]
    # A nonexistent task is diagnosed as the task, not the deployment.
    status, payload = _post(f"{server_url}{RECONCILE}", {"task_id": "rt_nope"})
    assert status == 400 and "task" in payload["error"]["message"].lower()
