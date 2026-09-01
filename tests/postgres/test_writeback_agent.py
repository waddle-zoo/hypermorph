"""Proposal-only write-back agent, end to end over a real DB (hy-27nl6, epic slice 4 CAPSTONE).

Proves the FRONT HALF of the search->writeback loop and the capstone invariant: selected
search hits + a proposed change open a PROPOSAL-ONLY ReviewTask (status open) carrying the
change + the originating citations + the correct routed target (revenue->revenue, never
marketing), WITHOUT any direct authority (no governed version, no approval, no PR, no merge);
a human decision (#504) records against it; an unauthorized proposer fails closed; an
UNGOVERNED/credential-bearing domain and a PARTIAL hit are rejected before anything persists.
The behavioral matrix (validation, ACL fail-closed, idempotency) is in
tests/unit/transport/test_propose_from_search.py.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import event

from hyperset.repositories.postgres import (
    PostgresCitationDecisionRepository,
    PostgresInteractionTraceRepository,
    PostgresReviewRepository,
    PostgresWritebackConfigRepository,
)
from hyperset.security import authz
from hyperset.security.authz import Principal, Role
from hyperset.transport.operations import (
    OperationError,
    _decide_citation,
    _governed_domains,
    _propose_context_from_search,
)

# Any statement that SELECTs the snapshot CONTENT column names `context_snapshots.files`.
_FILES_SELECT = re.compile(r"\.files\b", re.IGNORECASE)

# The governed domain the checked-in revenue slice snapshots.
_DOMAIN = "revenue"
_SOURCE = "table:postgres:analytics.public.orders"
_DEFINITION = {
    "definitions": [{"term": "recognized_revenue", "statement": "net of tax"}],
    "approved_sources": [{"ref": _SOURCE, "role": "primary"}],
    "fields": [{"name": "recognized_revenue", "source_ref": _SOURCE, "expression": "SUM(net)"}],
    "filters": ["status = 'completed'"],
    "grain": "order_date",
}


@pytest.fixture(autouse=True)
def traced_search(session_factory):
    """Every direct service test starts from the same durable search trace."""
    return PostgresInteractionTraceRepository(session_factory).record(
        workspace="default",
        principal_identity="anonymous",
        session_id="session-wb",
        turn_id=None,
        tool_call_id=None,
        correlation_id="corr-wb",
        intent="proposal test",
        query="recognized revenue",
        tool_name="search_knowledge",
        search_mode="grep",
        filters={},
        hit_ids=["src-rev:revenue/context.md:12"],
        duration_ms=1,
        source_staleness={},
        miss=None,
        answer_bundle_id=None,
        status="hit",
    )


def _hit(**overrides) -> dict:
    hit = {
        "source_id": "src-rev",
        "repository": "acme/context",
        "domain": "revenue",
        "path": "revenue/context.md",
        "line": 12,
        "commit": "abc123",
        "version": "v1",
        "acl_decision": "allowed",
        "match_type": "exact",
        "snippet": "recognized revenue is net of tax and SECRET_ToKeN",
    }
    hit.update(overrides)
    return hit


@pytest.fixture
def two_targets(session_factory, tmp_path):
    """A revenue keyed target, a marketing keyed target, AND a catch-all DEFAULT target. The
    revenue proposal routes to revenue (never marketing) -- no-cross -- and the default is
    present (the realistic config the ruling names) so an UNGOVERNED domain would route + persist
    if the governed-domain gate were absent; the gate is the only thing that rejects it."""
    repo = PostgresWritebackConfigRepository(session_factory)
    rev = repo.set(
        routing_key="revenue",
        repository=str(tmp_path / "rev"),
        base_ref="main",
        manifest_path="context",
        reviewer_routing="@rev-reviewer",
    )
    mkt = repo.set(
        routing_key="marketing",
        repository=str(tmp_path / "mkt"),
        base_ref="main",
        manifest_path="context",
        reviewer_routing="@mkt-reviewer",
    )
    repo.set(
        routing_key=None,  # the default/catch-all target
        repository=str(tmp_path / "default"),
        base_ref="main",
        manifest_path="context",
        reviewer_routing="@default-reviewer",
    )
    return rev, mkt


@pytest.mark.postgres
def test_search_hits_open_a_proposal_only_task_routed_to_the_right_target(
    session_factory, revenue_slice, two_targets
):
    rev, mkt = two_targets
    result = _propose_context_from_search(
        {
            "domain": _DOMAIN,
            "definition": _DEFINITION,
            "hits": [_hit()],
            "correlation_id": "corr-wb",
            "session_id": "session-wb",
        },
        session_factory=session_factory,
        principal=None,  # authz off -> loopback dev, proposer 'anonymous'
    )
    task_id = result["task"]["id"]

    # Persisted as an OPEN task -- opening a proposal advances no status.
    stored = PostgresReviewRepository(session_factory).get_task(task_id)
    assert stored.status == "open"
    payload = stored.proposal_payload
    # It carries the proposed change + domain (the keys propose_review_to_git reads later).
    assert payload["definition"]["definitions"][0]["term"] == "recognized_revenue"
    assert payload["domain"] == _DOMAIN
    # The originating hit, as an OPAQUE citation, never a snippet.
    assert payload["citations"] == [
        {"source_id": "src-rev", "path": "revenue/context.md", "line": 12, "commit": "abc123"}
    ]
    assert "SECRET_ToKeN" not in str(payload)  # the ACL content never persists
    assert payload["correlation_id"] == "corr-wb"
    assert payload["proposer"] == "anonymous"  # server-derived
    # Routed to the REVENUE target, proposal-only -- never the marketing target.
    assert payload["review_routing"]["status"] == "proposal_only"
    assert payload["review_routing"]["target"]["id"] == rev.id
    assert payload["review_routing"]["target"]["id"] != mkt.id
    assert payload["review_routing"]["target"]["routing_key"] == "revenue"


@pytest.mark.postgres
def test_a_proposal_writes_no_governed_version_and_opens_no_pr(
    session_factory, revenue_slice, two_targets
):
    # The capstone no-direct-authority invariant, at the DB: opening a proposal creates a task
    # but writes NO governed context version -- authority stays a human Git merge (ADR 0012).
    from hyperset.db.models import GovernedContextVersion

    def _governed_count() -> int:
        with session_factory() as session:
            return session.query(GovernedContextVersion).count()

    before = _governed_count()
    _propose_context_from_search(
        {
            "domain": _DOMAIN,
            "definition": _DEFINITION,
            "hits": [_hit()],
            "correlation_id": "corr-wb",
            "session_id": "session-wb",
        },
        session_factory=session_factory,
        principal=None,
    )
    assert _governed_count() == before  # no governed version written


@pytest.mark.postgres
def test_a_human_decision_records_against_the_proposal_task(
    session_factory, revenue_slice, two_targets
):
    result = _propose_context_from_search(
        {
            "domain": _DOMAIN,
            "definition": _DEFINITION,
            "hits": [_hit()],
            "correlation_id": "corr-wb",
            "session_id": "session-wb",
        },
        session_factory=session_factory,
        principal=None,
    )
    task_id = result["task"]["id"]
    # The #504 human include/exclude/approve/reject links to THIS proposal's task.
    _decide_citation(
        {
            "decision": "approve",
            "citation_ref": "src-rev",
            "review_task_id": task_id,
            "correlation_id": "corr-wb",
        },
        session_factory=session_factory,
        principal=None,
    )
    (decision,) = PostgresCitationDecisionRepository(session_factory).for_task(
        workspace="default", review_task_id=task_id
    )
    assert decision.decision == "approve"
    assert decision.citation_ref == "src-rev"
    assert decision.review_task_id == task_id


@pytest.mark.postgres
def test_an_ungoverned_credential_domain_is_rejected_and_persists_nothing(
    session_factory, revenue_slice, two_targets
):
    # BLOCKER 1: the caller's domain lands verbatim in reason + proposal_payload with a default
    # target, so an UNGOVERNED credential-bearing domain must be VALIDATED against the
    # workspace governed set and FAIL CLOSED before anything persists. Mutation-red: drop the
    # governed-domain gate -> the credential domain routes to a target and persists.
    before = len(PostgresReviewRepository(session_factory).list_tasks())
    with pytest.raises(OperationError) as exc:
        _propose_context_from_search(
            {
                "domain": "https://u:dom_secret@host/evil",
                "definition": _DEFINITION,
                "hits": [_hit()],
                "correlation_id": "corr-wb",
                "session_id": "session-wb",
            },
            session_factory=session_factory,
            principal=None,
        )
    assert exc.value.code == "invalid_request"
    assert "dom_secret" not in str(exc.value.to_dict())  # the refusal carries no credential
    assert len(PostgresReviewRepository(session_factory).list_tasks()) == before  # nothing opened


@pytest.mark.postgres
def test_a_partial_hit_is_rejected_and_persists_nothing(
    session_factory, revenue_slice, two_targets
):
    # BLOCKER 2: an incomplete hit (here a missing commit) is rejected before create_task, the
    # same way a missing source_id is. Mutation-red: accept partial -> a citation with a null
    # field persists.
    before = len(PostgresReviewRepository(session_factory).list_tasks())
    partial = _hit()
    del partial["commit"]
    with pytest.raises(OperationError) as exc:
        _propose_context_from_search(
            {
                "domain": _DOMAIN,
                "definition": _DEFINITION,
                "hits": [partial],
                "correlation_id": "corr-wb",
                "session_id": "session-wb",
            },
            session_factory=session_factory,
            principal=None,
        )
    assert exc.value.code == "invalid_params"
    assert len(PostgresReviewRepository(session_factory).list_tasks()) == before  # nothing opened


@pytest.mark.postgres
def test_computing_the_governed_domain_set_reads_no_source_content(
    session_factory, db_engine, revenue_slice
):
    # ADVERSARY round-3: validating the domain must NOT reintroduce #500's authorize-before-
    # content leak. `_governed_domains` reads the METADATA-only `list_source_candidates` (never
    # selects `context_snapshots.files`), so computing the governed-domain set fetches no source
    # CONTENT. Mutation-red: swap it back to `list_sources` -> a files-selecting statement
    # appears -> this reds.
    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    event.listen(db_engine, "before_cursor_execute", _capture)
    try:
        domains = _governed_domains(session_factory, "default")
    finally:
        event.remove(db_engine, "before_cursor_execute", _capture)

    assert _DOMAIN in domains  # the set is really computed over the governed source
    files_selects = [s for s in captured if _FILES_SELECT.search(s)]
    assert files_selects == [], (
        "computing the governed-domain set must issue NO query selecting "
        f"context_snapshots.files (authorize-before-content): {files_selects}"
    )


@pytest.mark.postgres
def test_an_unauthorized_proposer_fails_closed_and_persists_nothing(
    session_factory, revenue_slice, two_targets
):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setitem(authz.ROLES, "reader_only", Role(name="reader_only", grants=()))
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    principal = Principal(subject="u1", issuer="https://issuer.example", roles=("reader_only",))
    before = len(PostgresReviewRepository(session_factory).list_tasks())
    try:
        with pytest.raises(OperationError) as exc:
            _propose_context_from_search(
                {"domain": _DOMAIN, "definition": _DEFINITION, "hits": [_hit()]},
                session_factory=session_factory,
                principal=principal,
            )
        assert exc.value.code == "unauthorized"
    finally:
        monkeypatch.undo()
    assert len(PostgresReviewRepository(session_factory).list_tasks()) == before  # no task opened
