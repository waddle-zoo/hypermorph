"""Durable feedback records linked to the MCP interaction trace (hy-8f2r4)."""

from __future__ import annotations

import json

import pytest

from hyperset.evals.feedback import feedback_loop_evidence
from hyperset.observability.interaction import TraceContext, set_trace_context
from hyperset.repositories.postgres import (
    PostgresAnswerFeedbackRepository,
    PostgresContextRepository,
    PostgresInteractionTraceRepository,
)
from hyperset.security.authz import Principal
from hyperset.transport.operations import OperationError, run_operation


@pytest.fixture(autouse=True)
def _clear_trace_context():
    set_trace_context(None)
    yield
    set_trace_context(None)


def _search_source(session_factory, *, workspace="default"):
    repository = PostgresContextRepository(session_factory)
    source = repository.register_source(
        repository="git@example/feedback", ref="main", path="context", workspace=workspace
    )
    repository.record_snapshot(
        source_id=source.id,
        commit_sha="feedback-commit",
        committed_at=None,
        domain="feedback",
        title="Feedback",
        files={"docs/revenue.md": "recognized revenue"},
        normalized={},
    )
    return source.id


@pytest.mark.postgres
def test_two_feedback_outcomes_append_and_lookup_with_atomic_trace_links(session_factory):
    source_id = _search_source(session_factory)
    context = TraceContext(session_id="sess-feedback", correlation_id="corr-feedback")
    set_trace_context(context)
    result = run_operation(
        "search_knowledge", {"query": "recognized"}, session_factory=session_factory
    )
    source_ref = f"{source_id}:docs/revenue.md"
    assert result["hits"][0]["source_id"] == source_id

    ignored = run_operation(
        "record_answer_feedback",
        {"outcome": "ignore", "source_ref": source_ref},
        session_factory=session_factory,
    )
    accepted = run_operation(
        "record_answer_feedback",
        {"outcome": "accept", "source_ref": source_ref},
        session_factory=session_factory,
    )
    looked_up = run_operation(
        "lookup_answer_feedback",
        {"correlation_id": "corr-feedback"},
        session_factory=session_factory,
    )

    assert [item["outcome"] for item in looked_up["feedback"]] == ["ignore", "accept"]
    assert looked_up["count"] == 2
    assert {item["session_id"] for item in looked_up["feedback"]} == {"sess-feedback"}
    assert {item["source_ref"] for item in looked_up["feedback"]} == {source_ref}
    (trace,) = PostgresInteractionTraceRepository(session_factory).session_chain("sess-feedback")
    assert trace.feedback_ids == [ignored["feedback"]["id"], accepted["feedback"]["id"]]
    assert trace.duration_ms is not None and trace.duration_ms >= 0
    assert trace.source_staleness[source_id]["stale"] is False
    assert trace.miss is None
    evidence = feedback_loop_evidence(feedback=looked_up["feedback"], trace=trace)
    assert evidence and all(evidence.values())


@pytest.mark.postgres
def test_miss_trace_explicitly_records_what_was_searched(session_factory):
    source_id = _search_source(session_factory)
    set_trace_context(TraceContext(session_id="sess-miss-new", correlation_id="corr-miss-new"))

    run_operation("search_knowledge", {"query": "no-such-token"}, session_factory=session_factory)

    (trace,) = PostgresInteractionTraceRepository(session_factory).session_chain("sess-miss-new")
    assert trace.status == "miss"
    assert trace.hit_ids == []
    assert trace.duration_ms is not None and trace.duration_ms >= 0
    assert trace.source_staleness[source_id]["stale"] is False
    assert trace.miss == {
        "operation": "search_knowledge",
        "searched_sources": [source_id],
    }


@pytest.mark.postgres
def test_feedback_rejects_a_fabricated_or_cross_workspace_target(session_factory):
    source_id = _search_source(session_factory, workspace="tenant-a")
    principal_a = Principal(
        subject="agent-a", issuer="issuer", roles=("service",), workspace="tenant-a"
    )
    principal_b = Principal(
        subject="agent-b", issuer="issuer", roles=("service",), workspace="tenant-b"
    )
    set_trace_context(TraceContext(session_id="sess-isolated", correlation_id="corr-isolated"))
    run_operation(
        "search_knowledge",
        {"query": "recognized"},
        session_factory=session_factory,
        principal=principal_a,
    )

    for principal, source_ref in (
        (principal_a, "made-up:docs/nope.md"),
        (principal_b, f"{source_id}:docs/revenue.md"),
    ):
        with pytest.raises(OperationError) as excinfo:
            run_operation(
                "record_answer_feedback",
                {"outcome": "ignore", "source_ref": source_ref},
                session_factory=session_factory,
                principal=principal,
            )
        assert excinfo.value.code == "invalid_params"

    assert (
        PostgresAnswerFeedbackRepository(session_factory).lookup(
            workspace="tenant-a", correlation_id="corr-isolated"
        )
        == []
    )


@pytest.mark.postgres
def test_each_supplied_target_must_match_and_a_resolved_bundle_is_linkable(
    session_factory, revenue_slice
):
    source_id = _search_source(session_factory)
    set_trace_context(TraceContext(session_id="sess-bundle", correlation_id="corr-bundle"))
    run_operation("search_knowledge", {"query": "recognized"}, session_factory=session_factory)
    resolved = run_operation(
        "resolve_analytics_context",
        {
            "query": "recognized revenue",
            "directive": {"domains": ["revenue"], "concepts": ["recognized_revenue"]},
        },
        session_factory=session_factory,
    )

    recorded = run_operation(
        "record_answer_feedback",
        {"outcome": "accept", "bundle_id": resolved["bundle_id"]},
        session_factory=session_factory,
    )
    assert recorded["feedback"]["bundle_id"] == resolved["bundle_id"]

    # A valid source must not smuggle an invented answer id through an OR match.
    with pytest.raises(OperationError):
        run_operation(
            "record_answer_feedback",
            {
                "outcome": "ignore",
                "source_ref": f"{source_id}:docs/revenue.md",
                "bundle_id": "cb-fabricated",
            },
            session_factory=session_factory,
        )


@pytest.mark.postgres
def test_feedback_free_text_is_redacted_in_the_database(session_factory):
    source_id = _search_source(session_factory)
    set_trace_context(TraceContext(session_id="sess-redact", correlation_id="corr-redact"))
    run_operation("search_knowledge", {"query": "recognized"}, session_factory=session_factory)

    run_operation(
        "record_answer_feedback",
        {
            "outcome": "needs_review",
            "source_ref": f"{source_id}:docs/revenue.md",
            "notes": "check https://user:feedback_secret@host/path",
        },
        session_factory=session_factory,
    )

    (record,) = PostgresAnswerFeedbackRepository(session_factory).lookup(
        workspace="default", correlation_id="corr-redact"
    )
    assert "feedback_secret" not in json.dumps(record.__dict__, default=str)
