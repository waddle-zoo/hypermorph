"""Phase-2 multi-target write-back routing (hq-1h1z).

Real server, real Postgres, real LOCAL target repositories. Proves the write
gap phase 2 closes: the proposal-only writer routes a review task to the
write-back TARGET its domain belongs to, and only that target. A keyed target
wins for its domain; the default target is the catch-all; a domain with no
route FAILS CLOSED; and no proposal ever crosses into another target's repo.
The hard boundary (ADR 0012) is unchanged -- every propose OPENS A PR PROPOSAL
only and never advances a base ref.
"""

from __future__ import annotations

import copy
import threading

import pytest

from hyperset.repositories.postgres import (
    PostgresReviewRepository,
    PostgresWritebackConfigRepository,
)
from hyperset.transport.http import build_server
from tests.integration.test_git_context_source import CONTEXT_PATH, git, make_repository
from tests.postgres.test_interactive_review import DRAFT_PAYLOAD, _governed_counts, _post
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
    """A draft payload identical to the revenue fixture but for `domain`, so the
    proposal branch and routing key both key off it."""
    payload = copy.deepcopy(DRAFT_PAYLOAD)
    payload["domain"] = domain
    payload["miss"] = {"question": "q?", "domain": domain}
    return payload


def _proposal_branches(repo) -> str:
    return git("branch", "--list", "hyperset/proposal/*", cwd=repo).strip()


def _propose(server_url, session_factory, domain, idem):
    task = PostgresReviewRepository(session_factory).create_task(
        reason="draft", idempotency_key=idem, proposal_payload=_payload_for(domain)
    )
    return _post(f"{server_url}/playground/api{PROPOSE_REVIEW_TO_GIT_PATH}", {"task_id": task.id})


@pytest.mark.postgres
def test_two_domains_route_to_their_own_targets_and_never_cross(
    server_url,
    session_factory,
    tmp_path,
):
    """Two keyed targets, two domains: each proposal lands in ITS target's repo
    only. Proving the no-cross-target invariant directly -- after a revenue
    propose the marketing repo has no branch, and vice versa."""
    repo_rev = make_repository(tmp_path / "rev")
    repo_mkt = make_repository(tmp_path / "mkt")
    rev_base = git("rev-parse", "main", cwd=repo_rev)
    mkt_base = git("rev-parse", "main", cwd=repo_mkt)
    repo = PostgresWritebackConfigRepository(session_factory)
    repo.set(
        routing_key="revenue", repository=str(repo_rev), base_ref="main", manifest_path=CONTEXT_PATH
    )
    repo.set(
        routing_key="marketing",
        repository=str(repo_mkt),
        base_ref="main",
        manifest_path=CONTEXT_PATH,
    )
    before = _governed_counts(session_factory)

    status, payload = _propose(server_url, session_factory, "revenue", "wb:rev")
    assert status == 200, payload
    assert payload["proposal"]["head_branch"].startswith("hyperset/proposal/")
    # Landed in the revenue target...
    assert _proposal_branches(repo_rev) != ""
    # ...and NOT in the marketing target -- no cross-target write.
    assert _proposal_branches(repo_mkt) == ""
    assert git("rev-parse", "main", cwd=repo_mkt) == mkt_base

    status, payload = _propose(server_url, session_factory, "marketing", "wb:mkt")
    assert status == 200, payload
    assert payload["proposal"]["head_branch"].startswith("hyperset/proposal/")
    assert _proposal_branches(repo_mkt) != ""
    # The revenue repo still holds ONLY its own revenue proposal -- no marketing branch.
    assert "marketing" not in _proposal_branches(repo_rev)

    # Both base refs untouched, nothing governed written (ADR 0012).
    assert git("rev-parse", "main", cwd=repo_rev) == rev_base
    assert git("rev-parse", "main", cwd=repo_mkt) == mkt_base
    assert _governed_counts(session_factory) == before


@pytest.mark.postgres
def test_an_unmatched_domain_falls_back_to_the_default_and_not_a_keyed_target(
    server_url,
    session_factory,
    tmp_path,
):
    """A keyed target plus a default: a domain with no keyed target routes to the
    DEFAULT, never to some other domain's keyed target."""
    repo_rev = make_repository(tmp_path / "rev")
    repo_def = make_repository(tmp_path / "def")
    repo = PostgresWritebackConfigRepository(session_factory)
    repo.set(
        routing_key="revenue", repository=str(repo_rev), base_ref="main", manifest_path=CONTEXT_PATH
    )
    # The default target (null routing key) -- the catch-all.
    repo.set(repository=str(repo_def), base_ref="main", manifest_path=CONTEXT_PATH)

    status, payload = _propose(server_url, session_factory, "marketing", "wb:fallback")
    assert status == 200, payload
    # Landed in the DEFAULT, not the revenue keyed target.
    assert _proposal_branches(repo_def) != ""
    assert _proposal_branches(repo_rev) == ""


@pytest.mark.postgres
def test_a_domain_with_no_route_fails_closed_and_writes_nothing(
    server_url,
    session_factory,
    tmp_path,
):
    """No keyed target for the domain and NO default: propose FAILS CLOSED with a
    domain-named message and touches no repository -- it does not fall through to
    an unrelated target."""
    repo_rev = make_repository(tmp_path / "rev")
    PostgresWritebackConfigRepository(session_factory).set(
        routing_key="revenue", repository=str(repo_rev), base_ref="main", manifest_path=CONTEXT_PATH
    )
    before = _governed_counts(session_factory)

    status, payload = _propose(server_url, session_factory, "marketing", "wb:noroute")
    assert status == 400
    assert (
        "no write-back target is configured for domain 'marketing'" in payload["error"]["message"]
    )
    # No cross-target write: the revenue target was never touched.
    assert _proposal_branches(repo_rev) == ""
    assert _governed_counts(session_factory) == before


@pytest.mark.postgres
def test_a_single_default_target_still_routes_exactly_as_before(
    server_url,
    session_factory,
    tmp_path,
):
    """Backward compat: one default target (a null routing key, exactly the
    pre-hq-1h1z shape) still serves every domain's proposal."""
    repo_def = make_repository(tmp_path / "def")
    base_before = git("rev-parse", "main", cwd=repo_def)
    PostgresWritebackConfigRepository(session_factory).set(
        repository=str(repo_def), base_ref="main", manifest_path=CONTEXT_PATH
    )

    status, payload = _propose(server_url, session_factory, "revenue", "wb:default")
    assert status == 200, payload
    assert payload["proposal"]["head_branch"].startswith("hyperset/proposal/")
    assert _proposal_branches(repo_def) != ""
    # Proposal-only: the base ref never moved.
    assert git("rev-parse", "main", cwd=repo_def) == base_before
