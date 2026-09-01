"""The flywheel Review surface's read route, end to end (hy-167c).

A real server on a loopback port, a real review task carrying a step-4
UNAPPROVED draft, and the read-only GET the playground Review tab issues. The
route presents the miss's evidence and the draft; it approves nothing.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from hyperset.repositories.postgres import PostgresReviewRepository
from hyperset.transport.http import build_server
from tests.postgres.test_interactive_review import _post
from tests.review_api import LIST_REVIEW_TASKS_PATH

DRAFT_PAYLOAD = {
    "governance": "unapproved",
    "domain": "revenue",
    "undeclared_concepts": ["churn"],
    "miss": {
        "question": "How much did customer churn cost us last quarter?",
        "domain": "revenue",
        "undeclared_concepts": ["churn"],
        "resolve_miss_id": "rm-123",
    },
    "gathered_sources": [
        {
            "rank": 1,
            "ref": "superset:dataset:abc",
            "asset_type": "dataset",
            "governance": "observed",
            "signals": ["git_engagement"],
        }
    ],
    "definition": {"definitions": [{"term": "churn", "statement": "customers lost in a period"}]},
    "produced_by": {"producer": "authoring/1", "model": None},
}


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


def _get(url):
    try:
        with urllib.request.urlopen(url) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.mark.postgres
def test_the_review_route_presents_the_unapproved_draft(server_url, session_factory):
    task = PostgresReviewRepository(session_factory).create_task(
        reason="agent-drafted candidate definition for 'revenue' (unapproved)",
        idempotency_key="authoring:revenue:test",
        proposal_payload=DRAFT_PAYLOAD,
    )

    # Reached the same way the playground tab reaches it, through the api proxy.
    status, payload = _post(f"{server_url}/playground/api{LIST_REVIEW_TASKS_PATH}", {})

    assert status == 200
    tasks = payload["tasks"]
    assert len(tasks) == 1
    served = tasks[0]
    assert served["id"] == task.id
    assert served["status"] == "open"
    # The step-4 draft is presented whole, and it is labelled UNAPPROVED.
    assert served["proposal_payload"] == DRAFT_PAYLOAD
    assert served["proposal_payload"]["governance"] == "unapproved"
    # The Review surface can show the miss and the gathered sources, not only the
    # draft: both reach the client through the route (hy-1q9w).
    assert served["proposal_payload"]["miss"]["question"]
    assert served["proposal_payload"]["gathered_sources"][0]["ref"] == "superset:dataset:abc"
    # Nothing here is an applied approval: no approved version, no decision.
    assert "approved_version_id" not in served
    assert "decision" not in served


@pytest.mark.postgres
def test_the_review_route_filters_by_status(server_url, session_factory):
    PostgresReviewRepository(session_factory).create_task(
        reason="open draft",
        idempotency_key="authoring:revenue:open",
        proposal_payload=DRAFT_PAYLOAD,
    )

    status, payload = _post(
        f"{server_url}/playground/api{LIST_REVIEW_TASKS_PATH}", {"status": "resolved"}
    )

    assert status == 200
    assert payload["tasks"] == []
