"""Proposal-only write-back agent at the serving boundary (hy-27nl6, epic slice 4 CAPSTONE).

Fast tests with spied repositories: the propose service validates + gates + routes + redacts,
creates a ReviewTask (status open) and NEVER exercises direct authority (no approve, no
governed write, no PR, no merge), and the surface stays off OPERATIONS/MCP (tools_hash
unmoved). Real persistence + cross-target routing are proven in
tests/postgres/test_writeback_agent.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from hyperset.repositories.dto import ReviewTaskRecord, WritebackConfigRecord
from hyperset.security.authz import Principal, Role
from hyperset.transport import operations
from hyperset.transport.operations import PROPOSE_CONTEXT_FROM_SEARCH, OperationError

_SOURCE = "table:postgres:analytics.public.orders"
_DEFINITION = {
    "definitions": [{"term": "recognized_revenue", "statement": "net of tax"}],
    "approved_sources": [{"ref": _SOURCE, "role": "primary"}],
    "fields": [{"name": "recognized_revenue", "source_ref": _SOURCE, "expression": "SUM(net)"}],
    "filters": ["status = 'completed'"],
    "grain": "order_date",
}


def _hit(**overrides) -> dict:
    hit = {
        "source_id": "src-rev",
        "repository": "git@example.com:acme/context.git",
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


def _fake_task(**kwargs) -> ReviewTaskRecord:
    now = datetime.now(UTC)
    return ReviewTaskRecord(
        id="rt-new",
        reason=kwargs["reason"],
        priority=kwargs.get("priority", 2),
        affected_asset_ids=[],
        affected_context_id=None,
        proposal_payload=kwargs["proposal_payload"],
        processor_evidence={},
        evaluation_impact=None,
        assignee=None,
        status="open",
        idempotency_key=kwargs["idempotency_key"],
        row_version=1,
        created_at=now,
        updated_at=now,
    )


class _SpyReviewRepo:
    made: list[_SpyReviewRepo] = []

    def __init__(self, session_factory) -> None:
        self.calls: list[dict] = []
        _SpyReviewRepo.made.append(self)

    def create_task(self, **kwargs) -> ReviewTaskRecord:
        self.calls.append(kwargs)
        return _fake_task(**kwargs)


class _RoutingRepo:
    """Routes revenue -> the revenue keyed target, ANY other domain -> the DEFAULT target.

    A default target is deliberately present (the realistic config the ruling names), so
    routing NEVER fails closed on the domain -- the governed-domain gate is the ONLY thing that
    can reject a bad domain. That isolates the gate: without it a credential-bearing domain
    would route to the default target and persist."""

    def __init__(self, session_factory) -> None:
        pass

    def get_by_routing(self, domain: str, *, workspace: str) -> WritebackConfigRecord | None:
        if domain == "revenue":
            return WritebackConfigRecord(
                id="wb-revenue",
                repository="https://u:tok3n@github.com/acme/revenue-context.git",
                base_ref="main",
                manifest_path="context",
                updated_at=datetime.now(UTC),
                routing_key="revenue",
                is_default=False,
                enabled=True,
                reviewer_routing="@rev-reviewer",
                workspace_id=workspace,
            )
        return WritebackConfigRecord(
            id="wb-default",
            repository="https://u:dtok@github.com/acme/default-context.git",
            base_ref="main",
            manifest_path="context",
            updated_at=datetime.now(UTC),
            routing_key=None,
            is_default=True,
            enabled=True,
            reviewer_routing="@default-reviewer",
            workspace_id=workspace,
        )


class _ContextRepo:
    """Governs exactly the `revenue` domain in this workspace -- the catalog vocabulary a
    proposal's domain is validated against (an enabled source with a current snapshot). A
    default write-back target exists (see `_RoutingRepo`), so an UNGOVERNED domain is stopped
    only by the governed-domain gate, never by routing."""

    def __init__(self, session_factory) -> None:
        pass

    def list_source_candidates(self, *, workspace: str):
        return [SimpleNamespace(enabled=True, domain="revenue")]


class _TraceRepo:
    def __init__(self, session_factory) -> None:
        pass

    def for_correlation(self, *, workspace, correlation_id, limit=1000):
        return [
            SimpleNamespace(
                session_id="session-1",
                tool_name="search_knowledge",
                status="hit",
                hit_ids=[
                    "src-rev:revenue/context.md:12",
                    "src-other:revenue/context.md:12",
                ],
            )
        ]


@pytest.fixture
def spies(monkeypatch):
    _SpyReviewRepo.made = []
    monkeypatch.setattr(operations, "PostgresReviewRepository", _SpyReviewRepo)
    monkeypatch.setattr(operations, "PostgresWritebackConfigRepository", _RoutingRepo)
    monkeypatch.setattr(operations, "PostgresContextRepository", _ContextRepo)
    monkeypatch.setattr(operations, "PostgresInteractionTraceRepository", _TraceRepo)
    return _SpyReviewRepo


def _propose(session_factory, **overrides):
    principal = overrides.pop("principal", None)
    params = {
        "domain": "revenue",
        "definition": _DEFINITION,
        "hits": [_hit()],
        "session_id": "session-1",
        "correlation_id": "corr-1",
    }
    params.update(overrides)
    return operations._propose_context_from_search(
        params, session_factory=session_factory, principal=principal
    )


def test_the_propose_surface_stays_off_operations_and_mcp():
    from hyperset.planner.loop import tools_hash
    from hyperset.transport.http import REVIEW_PROPOSE_FROM_SEARCH_PATH, ROUTES
    from hyperset.transport.operations import OPERATION_ACTIONS, OPERATIONS, REVIEW

    assert PROPOSE_CONTEXT_FROM_SEARCH not in OPERATIONS  # not a served op -> not an MCP tool
    assert REVIEW_PROPOSE_FROM_SEARCH_PATH not in ROUTES  # not an auto-generated /v0/<op> route
    assert OPERATION_ACTIONS[PROPOSE_CONTEXT_FROM_SEARCH] == REVIEW  # but still REVIEW-gated
    assert tools_hash() == "sha256:fe930a003b731211"  # the benchmark surface is unmoved


def test_a_proposal_creates_an_open_task_with_the_change_citations_and_route(
    spies, session_factory
):
    result = _propose(session_factory)
    task = result["task"]
    assert task["status"] == "open"
    payload = task["proposal_payload"]
    # The proposed change + the domain (the keys propose_review_to_git later reads).
    assert payload["definition"]["definitions"][0]["term"] == "recognized_revenue"
    assert payload["domain"] == "revenue"
    # The originating hit, as an OPAQUE citation -- source id/path/line/commit, never a snippet.
    (citation,) = payload["citations"]
    assert citation == {
        "source_id": "src-rev",
        "path": "revenue/context.md",
        "line": 12,
        "commit": "abc123",
    }
    # Routed to the revenue target, recorded PROPOSAL-ONLY (never routed/approved/merged).
    assert payload["review_routing"]["status"] == "proposal_only"
    assert payload["review_routing"]["target"]["id"] == "wb-revenue"
    assert payload["source"] == "search"


def test_no_snippet_or_credential_lands_in_the_durable_task(monkeypatch):
    # UNCONDITIONAL redaction (no HYPERSET_PII_GUARD): the durable proposal_payload carries NO
    # hit snippet (ACL content) and NO credential from the routed repo or the caller's notes.
    captured: dict = {}

    class _Capture:
        def __init__(self, session_factory) -> None:
            pass

        def create_task(self, **kwargs):
            captured.update(kwargs["proposal_payload"])
            return _fake_task(**kwargs)

    monkeypatch.delenv("HYPERSET_PII_GUARD", raising=False)
    monkeypatch.setattr(operations, "PostgresReviewRepository", _Capture)
    monkeypatch.setattr(operations, "PostgresWritebackConfigRepository", _RoutingRepo)
    monkeypatch.setattr(operations, "PostgresContextRepository", _ContextRepo)
    monkeypatch.setattr(operations, "PostgresInteractionTraceRepository", _TraceRepo)
    operations._propose_context_from_search(
        {
            "domain": "revenue",
            "definition": _DEFINITION,
            "hits": [_hit()],
            "notes": "see https://u:notes_secret@h/ref",
            "session_id": "session-1",
            "correlation_id": "corr-1",
        },
        session_factory=None,
        principal=None,
    )
    blob = json.dumps(captured)
    assert "SECRET_ToKeN" not in blob  # the hit snippet never persists
    assert "tok3n" not in blob  # the routed repo credential is redacted
    assert "notes_secret" not in blob  # caller notes are redacted unconditionally


def test_a_proposal_never_exercises_direct_authority(spies, session_factory, monkeypatch):
    # The capstone invariant: opening a proposal writes NO governed version, opens NO PR, and
    # approves/merges NOTHING (ADR 0012). The only review write is create_task (status open);
    # the Git proposal writer is never called. Mutation-red: wire an auto-PR/approve -> the
    # writer spy fires or a second review write appears.
    proposal_writer_called: list[bool] = []
    monkeypatch.setattr(
        operations, "_propose_writer", lambda **_k: proposal_writer_called.append(True) or {}
    )
    result = _propose(session_factory)
    (repo,) = _SpyReviewRepo.made
    (call,) = repo.calls  # exactly ONE review write...
    assert set(call) <= {
        "reason",
        "idempotency_key",
        "workspace",
        "proposal_payload",
    }  # ...create_task only
    assert result["task"]["status"] == "open"  # never approved/resolved
    assert proposal_writer_called == []  # no PR opened


def test_a_hit_that_is_not_admitted_is_rejected(spies, session_factory):
    # FAIL-CLOSED on ACL: a hit not marked admitted (a hand-crafted denied one) cannot be
    # cited, so a proposal never references an ACL-denied item. Mutation-red: drop the check
    # -> the denied hit is accepted and a task is created.
    with pytest.raises(OperationError) as exc:
        _propose(session_factory, hits=[_hit(acl_decision="denied")])
    assert exc.value.code == "invalid_request"
    assert _SpyReviewRepo.made == []  # never reached the store


def test_an_invalid_definition_is_rejected_before_the_store(spies, session_factory):
    with pytest.raises(OperationError) as exc:
        _propose(session_factory, definition={"unsupported_field": 1})
    assert exc.value.code == "invalid_request"
    assert _SpyReviewRepo.made == []


def test_an_ungoverned_domain_is_rejected_and_nothing_persists(spies, session_factory):
    # BLOCKER 1: the caller's domain lands verbatim in reason + proposal_payload, so an
    # UNGOVERNED (e.g. credential-bearing) domain must be VALIDATED against the workspace's
    # governed set and FAIL CLOSED before anything is routed or persisted -- not merely
    # redacted. Mutation-red: drop the governed-domain gate -> the domain routes to a target
    # and persists.
    with pytest.raises(OperationError) as exc:
        _propose(session_factory, domain="https://u:dom_secret@host/evil")
    assert exc.value.code == "invalid_request"
    assert "dom_secret" not in str(exc.value.to_dict())  # the refusal itself carries no cred
    assert _SpyReviewRepo.made == []  # nothing persisted


@pytest.mark.parametrize(
    "bad",
    [
        {"path": None},
        {"path": 123},
        {"commit": None},
        {"commit": 5},
        {"line": "12"},
        {"line": None},
    ],
)
def test_a_partial_hit_is_rejected_before_the_store(spies, session_factory, bad):
    # BLOCKER 2: the promised provenance is the full source_id/path/line/commit tuple, so an
    # incomplete hit (missing/non-string path or commit, non-int line) is rejected the SAME way
    # a missing source_id is -- before create_task. Mutation-red: accept partial -> a citation
    # with a null field persists.
    with pytest.raises(OperationError) as exc:
        _propose(session_factory, hits=[_hit(**bad)])
    assert exc.value.code == "invalid_params"
    assert _SpyReviewRepo.made == []  # never reached the store


def test_an_unauthorized_proposer_is_rejected_before_the_store(spies, session_factory, monkeypatch):
    from hyperset.security import authz

    monkeypatch.setitem(authz.ROLES, "reader_only", Role(name="reader_only", grants=()))
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    principal = Principal(subject="u1", issuer="https://issuer.example", roles=("reader_only",))
    with pytest.raises(OperationError) as exc:
        _propose(session_factory, principal=principal)
    assert exc.value.code == "unauthorized"
    assert _SpyReviewRepo.made == []  # fail-closed at the service, before the store


def test_the_idempotency_key_is_deterministic_and_server_derived(spies, session_factory):
    # Re-submitting the SAME proposal computes the SAME key, so create_task upserts one task.
    _propose(session_factory)
    _propose(session_factory)
    keys = [c["idempotency_key"] for repo in _SpyReviewRepo.made for c in repo.calls]
    assert len(keys) == 2
    assert keys[0] == keys[1]  # deterministic
    assert keys[0].startswith("propose-search:")  # server-derived digest, not caller text
    # A different proposed change yields a different key.
    _propose(session_factory, hits=[_hit(source_id="src-other", commit="def456")])
    other = _SpyReviewRepo.made[-1].calls[0]["idempotency_key"]
    assert other != keys[0]
