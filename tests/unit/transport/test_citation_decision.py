"""Citation decision + citation-linkage behavior at the serving boundary (hy-cpkvu).

Fast tests with spied repositories: the decision service validates + gates + redacts,
the citation recorder is degraded-explicit when its store fails, and the surface stays
off OPERATIONS/MCP (tools_hash unmoved). Real persistence + supersede are proven in
tests/postgres/test_citation_linkage.py.
"""

from __future__ import annotations

import logging

import pytest

from hyperset.observability.interaction import TraceContext, set_trace_context
from hyperset.security.authz import Principal, Role
from hyperset.transport import operations
from hyperset.transport.operations import DECIDE_CITATION, OperationError
from tests.unit.transport.conftest import DIRECTIVE, QUESTION, governed_bundle


class _SpyDecisionRepo:
    made: list[_SpyDecisionRepo] = []

    def __init__(self, session_factory) -> None:
        self.calls: list[dict] = []
        _SpyDecisionRepo.made.append(self)

    def record(self, **kwargs):
        self.calls.append(kwargs)
        from hyperset.repositories.dto import CitationDecisionRecord

        return CitationDecisionRecord(
            id="cdec-1",
            workspace=kwargs["workspace"],
            principal_identity=kwargs["principal_identity"],
            decision=kwargs["decision"],
            citation_ref=kwargs["citation_ref"],
            source_ref=kwargs["source_ref"],
            review_task_id=kwargs["review_task_id"],
            correlation_id=kwargs["correlation_id"],
            notes=kwargs["notes"],
            superseded_by=None,
            created_at=None,
        )


@pytest.fixture
def spy_decision_repo(monkeypatch):
    _SpyDecisionRepo.made = []
    monkeypatch.setattr(operations, "PostgresCitationDecisionRepository", _SpyDecisionRepo)
    return _SpyDecisionRepo


@pytest.fixture(autouse=True)
def _clear_trace_context():
    set_trace_context(None)
    yield
    set_trace_context(None)


def test_the_decision_surface_stays_off_operations_and_mcp():
    from hyperset.planner.loop import tools_hash
    from hyperset.transport.http import REVIEW_CITATION_DECIDE_PATH, ROUTES
    from hyperset.transport.operations import OPERATION_ACTIONS, OPERATIONS, REVIEW

    assert DECIDE_CITATION not in OPERATIONS  # not a served op -> not an MCP tool
    assert REVIEW_CITATION_DECIDE_PATH not in ROUTES  # not an auto-generated /v0/<op> route
    assert OPERATION_ACTIONS[DECIDE_CITATION] == REVIEW  # but still REVIEW-gated
    assert tools_hash() == "sha256:fe930a003b731211"  # the benchmark surface is unmoved


def test_a_valid_decision_is_recorded_with_server_derived_identity(
    spy_decision_repo, session_factory
):
    result = operations._decide_citation(
        {"decision": "include", "citation_ref": "cit-1"},
        session_factory=session_factory,
        principal=None,  # gate off -> loopback; identity is server-derived 'anonymous'
    )
    assert result["decision"]["decision"] == "include"
    (call,) = spy_decision_repo.made[0].calls
    assert call["principal_identity"] == "anonymous"  # never a caller field
    assert call["citation_ref"] == "cit-1"


def test_a_terminal_task_cannot_receive_a_new_citation_decision(
    spy_decision_repo, session_factory, monkeypatch
):
    from types import SimpleNamespace

    monkeypatch.setattr(
        operations,
        "_load_review_task",
        lambda _task_id, *, session_factory, workspace: SimpleNamespace(
            id="rt-1", status="resolved"
        ),
    )
    with pytest.raises(OperationError, match="read-only"):
        operations._decide_citation(
            {"decision": "include", "citation_ref": "cit-1", "review_task_id": "rt-1"},
            session_factory=session_factory,
            principal=None,
        )
    assert spy_decision_repo.made == []


def test_an_unknown_decision_is_rejected(spy_decision_repo, session_factory):
    with pytest.raises(OperationError) as exc:
        operations._decide_citation(
            {"decision": "maybe", "citation_ref": "cit-1"},
            session_factory=session_factory,
            principal=None,
        )
    assert exc.value.code == "invalid_params"
    assert spy_decision_repo.made == []  # never reached the store


def test_an_unauthorized_principal_is_rejected_before_the_store(
    spy_decision_repo, session_factory, monkeypatch
):
    from hyperset.security import authz

    monkeypatch.setitem(authz.ROLES, "reader_only", Role(name="reader_only", grants=()))
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    principal = Principal(subject="u1", issuer="https://issuer.example", roles=("reader_only",))

    with pytest.raises(OperationError) as exc:
        operations._decide_citation(
            {"decision": "approve", "citation_ref": "cit-1"},
            session_factory=session_factory,
            principal=principal,
        )
    assert exc.value.code == "unauthorized"
    # Fail-closed at the SERVICE: the store is never even constructed.
    assert _SpyDecisionRepo.made == []


def test_caller_text_is_redacted_and_linkage_validated_unconditionally(
    spy_decision_repo, session_factory, monkeypatch
):
    # No HYPERSET_PII_GUARD: the durable decision row must STILL strip caller credentials
    # from notes AND the refs, and drop a malformed correlation link (dual-block discipline
    # applied to slice 3). Mutation: drop any redaction/validation -> a secret reappears.
    monkeypatch.delenv("HYPERSET_PII_GUARD", raising=False)
    result = operations._decide_citation(
        {
            "decision": "exclude",
            "citation_ref": "https://u:c_secret@host/ref",
            "source_ref": "https://u:s_secret@host/ds",
            "correlation_id": "https://u:corr_secret@h",
            "notes": "see https://u:n_secret@h",
        },
        session_factory=session_factory,
        principal=None,
    )
    (call,) = spy_decision_repo.made[0].calls
    blob = repr(call) + repr(result)
    for secret in ("c_secret", "s_secret", "corr_secret", "n_secret"):
        assert secret not in blob, f"the decision row leaked {secret}"
    # A malformed correlation link is dropped, not persisted raw.
    assert call["correlation_id"] is None


def test_a_malformed_review_task_id_is_rejected(spy_decision_repo, session_factory):
    with pytest.raises(OperationError) as exc:
        operations._decide_citation(
            {
                "decision": "include",
                "citation_ref": "cit-1",
                "review_task_id": "https://u:secret@host",
            },
            session_factory=session_factory,
            principal=None,
        )
    assert exc.value.code == "invalid_params"
    assert spy_decision_repo.made == []  # never reached the store


def test_a_broken_citation_store_reports_degraded_not_silent(session_factory, monkeypatch, caplog):
    class _Exploding:
        def __init__(self, _sf):
            pass

        def record(self, **_kwargs):
            raise RuntimeError("the citation store fell over")

    monkeypatch.setattr(operations, "PostgresAnswerCitationRepository", _Exploding)
    monkeypatch.setattr(operations, "resolve_analytics_context", lambda **_k: governed_bundle())
    set_trace_context(TraceContext(correlation_id="c-cite-degraded"))

    with caplog.at_level(logging.WARNING, logger="hyperset.transport.operations"):
        result = operations.run_operation(
            "resolve_analytics_context",
            {"query": QUESTION, "directive": DIRECTIVE},
            session_factory=session_factory,
        )

    assert result["resolution"]["status"] == "governed"  # the answer still serves
    degraded = [r for r in caplog.records if "answer citation linkage degraded" in r.getMessage()]
    assert degraded, "a failed citation write must be reported as degraded"


def test_the_db_decision_vocabulary_matches_the_transport_one():
    # The service validates against the db model's CHECK vocabulary; bind them so a new
    # decision word cannot be accepted by one and rejected by the other.
    from hyperset.db import models
    from hyperset.transport.operations import CITATION_DECISIONS

    assert CITATION_DECISIONS == models.CITATION_DECISIONS
