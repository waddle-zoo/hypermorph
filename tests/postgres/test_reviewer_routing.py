"""Reviewer routing for a proposal-only write-back (hq-1rq7).

Real server, real Postgres, a real local target repository. When a review task is
PROPOSED, the reviewers come from the SAME write-back target the proposal routed
to (existing domain routing, hq-1h1z), are RECORDED on the task, and surface in
the read-only Review task detail. FAIL CLOSED: a target with no reviewer routing
yields an explicit needs-routing state -- the proposal-only PR still opens (that
is not approval, ADR 0012), but no reviewer is invented and nothing is
auto-approved. A proposal for one domain can never carry another target's
reviewers. Recorded in `proposal_payload`, which the Review surface returns
verbatim, so this moves no served response SHAPE (SCHEMA_VERSION/tools_hash
unchanged, ADR 0018).
"""

from __future__ import annotations

import copy
import json
import threading

import pytest

from hyperset.repositories.postgres import (
    PostgresReviewRepository,
    PostgresWritebackConfigRepository,
)
from hyperset.transport.http import build_server
from tests.integration.test_git_context_source import CONTEXT_PATH, git, make_repository
from tests.postgres.test_interactive_review import (
    DRAFT_PAYLOAD,
    _get,
    _governed_counts,
    _post,
)
from tests.review_api import LIST_REVIEW_TASKS_PATH, PROPOSE_REVIEW_TO_GIT_PATH


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


def _draft_for(domain: str) -> dict:
    """A draft payload keyed to `domain`, so routing picks that domain's target."""
    payload = copy.deepcopy(DRAFT_PAYLOAD)
    payload["domain"] = domain
    payload["miss"]["domain"] = domain
    return payload


def _routing_for(server_url, session_factory, task_id: str) -> dict:
    """The `review_routing` the read-only Review task detail surfaces for a task."""
    status, payload = _post(f"{server_url}/playground/api{LIST_REVIEW_TASKS_PATH}", {})
    assert status == 200, payload
    task = next(t for t in payload["tasks"] if t["id"] == task_id)
    return task["proposal_payload"]["review_routing"]


@pytest.mark.postgres
def test_a_routed_target_records_its_reviewers_and_surfaces_them(
    server_url, session_factory, tmp_path
):
    repo = make_repository(tmp_path)
    base_before = git("rev-parse", "main", cwd=repo)
    PostgresWritebackConfigRepository(session_factory).set(
        routing_key="revenue",
        reviewer_routing="alice, bob",
        repository=str(repo),
        base_ref="main",
        manifest_path=CONTEXT_PATH,
    )
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft",
        idempotency_key="authoring:revenue:routed",
        proposal_payload=_draft_for("revenue"),
    )
    before = _governed_counts(session_factory)

    status, payload = _post(
        f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": task.id}
    )
    assert status == 200, payload
    proposal = payload["proposal"]

    # The routing is RECORDED on the task, from the target the proposal routed to.
    routing = _routing_for(server_url, session_factory, task.id)
    assert routing["status"] == "routed"
    assert routing["reviewers"] == ["alice", "bob"]
    assert routing["target"]["routing_key"] == "revenue"
    # The handoff a reviewer needs: the authority commit and the PR backlink point
    # at the exact proposal the reviewers must judge.
    assert routing["authority_commit"] == proposal["commit_sha"]
    assert routing["backlink"] == proposal["pr_url"]

    # NO auto-approve: the base ref is untouched, the task stays open/unapproved,
    # and nothing governed was written (ADR 0012).
    assert git("rev-parse", "main", cwd=repo) == base_before
    reloaded = PostgresReviewRepository(session_factory).get_task(task.id)
    assert reloaded.status == "open"
    assert reloaded.proposal_payload["governance"] == "unapproved"
    assert _governed_counts(session_factory) == before


@pytest.mark.postgres
def test_an_unrouted_target_fails_closed_to_needs_routing(server_url, session_factory, tmp_path):
    """A routed target with NO reviewer routing does not drop the proposal or
    invent a reviewer: it records an explicit needs-routing state. The
    proposal-only PR still opens -- that is not approval."""
    repo = make_repository(tmp_path)
    PostgresWritebackConfigRepository(session_factory).set(
        routing_key="revenue",
        repository=str(repo),
        base_ref="main",
        manifest_path=CONTEXT_PATH,
    )
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft",
        idempotency_key="authoring:revenue:unrouted",
        proposal_payload=_draft_for("revenue"),
    )
    before = _governed_counts(session_factory)

    status, payload = _post(
        f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": task.id}
    )
    assert status == 200, payload
    # The proposal opened (proposal-only), and the routing is honest, not green.
    assert payload["proposal"]["head_branch"].startswith("hyperset/proposal/")
    routing = _routing_for(server_url, session_factory, task.id)
    assert routing["status"] == "needs_routing"
    assert routing["reviewers"] == []

    # Still no auto-approve.
    reloaded = PostgresReviewRepository(session_factory).get_task(task.id)
    assert reloaded.status == "open"
    assert _governed_counts(session_factory) == before


@pytest.mark.postgres
def test_a_proposal_never_carries_another_targets_reviewers(server_url, session_factory, tmp_path):
    """Two keyed targets, two reviewer sets. A proposal for `revenue` routes to
    the revenue target and carries ONLY its reviewer -- the marketing target's
    reviewer appears nowhere in the recorded routing."""
    revenue_repo = make_repository(tmp_path / "revenue")
    marketing_repo = make_repository(tmp_path / "marketing")
    targets = PostgresWritebackConfigRepository(session_factory)
    targets.set(
        routing_key="revenue",
        reviewer_routing="alice",
        repository=str(revenue_repo),
        base_ref="main",
        manifest_path=CONTEXT_PATH,
    )
    targets.set(
        routing_key="marketing",
        reviewer_routing="bob",
        repository=str(marketing_repo),
        base_ref="main",
        manifest_path=CONTEXT_PATH,
    )
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft",
        idempotency_key="authoring:revenue:isolation",
        proposal_payload=_draft_for("revenue"),
    )

    status, payload = _post(
        f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": task.id}
    )
    assert status == 200, payload

    routing = _routing_for(server_url, session_factory, task.id)
    assert routing["reviewers"] == ["alice"]
    assert routing["target"]["routing_key"] == "revenue"
    # The other target's reviewer is nowhere in what was recorded for this task.
    assert "bob" not in json.dumps(routing)


@pytest.mark.postgres
def test_the_admin_target_surface_round_trips_reviewer_routing(
    server_url, session_factory, tmp_path
):
    """The admin write of a keyed target carries reviewer_routing, the target view
    surfaces it, and a proposal then routed to it resolves those reviewers
    (de-duplicated, order preserved)."""
    repo = make_repository(tmp_path)
    status, payload = _post(
        f"{server_url}/admin/api/v0/review/writeback-targets",
        {
            "routing_key": "revenue",
            "repository": str(repo),
            "base_ref": "main",
            "manifest_path": CONTEXT_PATH,
            "reviewer_routing": "carol, dave, carol",
        },
    )
    assert status == 200, payload
    assert payload["target"]["reviewer_routing"] == "carol, dave, carol"

    # The list read surfaces it too.
    status, payload = _get(f"{server_url}/admin/api/v0/review/writeback-targets")
    assert status == 200
    target = next(t for t in payload["targets"] if t["routing_key"] == "revenue")
    assert target["reviewer_routing"] == "carol, dave, carol"

    # A proposal routed here resolves the reviewers, de-duplicated in order.
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft",
        idempotency_key="authoring:revenue:admin",
        proposal_payload=_draft_for("revenue"),
    )
    status, payload = _post(
        f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": task.id}
    )
    assert status == 200, payload
    routing = _routing_for(server_url, session_factory, task.id)
    assert routing["reviewers"] == ["carol", "dave"]
    assert routing["status"] == "routed"


@pytest.mark.postgres
def test_the_default_target_reviewer_routing_routes_a_fallback_proposal(
    server_url, session_factory, tmp_path
):
    """The DEFAULT/catch-all target's reviewer routing is editable via the config
    endpoint (hq-hig7, #435 round 3), and a proposal with NO keyed target falls
    back to the default and resolves ITS reviewers -- the common case a keyed
    target does not cover."""
    repo = make_repository(tmp_path)
    status, payload = _post(
        f"{server_url}/admin/api/v0/review/writeback-config",
        {
            "repository": str(repo),
            "base_ref": "main",
            "manifest_path": CONTEXT_PATH,
            "reviewer_routing": "alice, bob",
        },
    )
    assert status == 200, payload
    # The config view surfaces the default's reviewer routing (so the form prefills).
    assert payload["config"]["reviewer_routing"] == "alice, bob"
    status, payload = _get(f"{server_url}/playground/api/v0/review/writeback-config")
    assert payload["config"]["reviewer_routing"] == "alice, bob"

    # A proposal for a domain with no keyed target falls back to the default and
    # carries the default's reviewers.
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft",
        idempotency_key="authoring:revenue:default",
        proposal_payload=_draft_for("revenue"),
    )
    status, payload = _post(
        f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": task.id}
    )
    assert status == 200, payload
    routing = _routing_for(server_url, session_factory, task.id)
    assert routing["status"] == "routed"
    assert routing["reviewers"] == ["alice", "bob"]
    # It routed to the DEFAULT target (null routing key), not a keyed one.
    assert routing["target"]["routing_key"] is None


@pytest.mark.postgres
def test_a_default_target_without_reviewers_fails_closed_to_needs_routing(
    server_url, session_factory, tmp_path
):
    """A default-routed proposal whose default target has no reviewer routing is
    needs-routing, not auto-approved -- the same fail-closed rule as a keyed
    target, on the fallback path."""
    repo = make_repository(tmp_path)
    _post(
        f"{server_url}/admin/api/v0/review/writeback-config",
        {"repository": str(repo), "base_ref": "main", "manifest_path": CONTEXT_PATH},
    )
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft",
        idempotency_key="authoring:revenue:default-unrouted",
        proposal_payload=_draft_for("revenue"),
    )
    status, payload = _post(
        f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": task.id}
    )
    assert status == 200, payload
    routing = _routing_for(server_url, session_factory, task.id)
    assert routing["status"] == "needs_routing"
    assert routing["reviewers"] == []
