"""The served answer-feedback operation boundary (hy-8f2r4)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from hyperset.observability.interaction import TraceContext, set_trace_context
from hyperset.transport import operations
from hyperset.transport.operations import OperationError


class _SpyFeedbackRepository:
    made: list[_SpyFeedbackRepository] = []

    def __init__(self, _session_factory) -> None:
        self.record_calls: list[dict] = []
        self.lookup_calls: list[dict] = []
        self.__class__.made.append(self)

    def record(self, **kwargs):
        self.record_calls.append(kwargs)
        return SimpleNamespace(
            id="afb-1",
            outcome=kwargs["outcome"],
            session_id=kwargs["session_id"],
            correlation_id=kwargs["correlation_id"],
            bundle_id=kwargs["bundle_id"],
            source_ref=kwargs["source_ref"],
            review_task_id=kwargs["review_task_id"],
            principal_identity=kwargs["principal_identity"],
            notes=kwargs["notes"],
            created_at=datetime(2026, 8, 29, tzinfo=UTC),
        )

    def lookup(self, **kwargs):
        self.lookup_calls.append(kwargs)
        return []


@pytest.fixture(autouse=True)
def _feedback_spy(monkeypatch):
    _SpyFeedbackRepository.made = []
    monkeypatch.setattr(operations, "PostgresAnswerFeedbackRepository", _SpyFeedbackRepository)
    set_trace_context(None)
    yield
    set_trace_context(None)


@pytest.mark.parametrize(
    "outcome", ["accept", "reject", "include", "ignore", "correct", "needs_review"]
)
def test_every_published_outcome_is_appendable(outcome, session_factory):
    set_trace_context(TraceContext(session_id="sess-1", correlation_id="corr-1"))

    result = operations.run_operation(
        "record_answer_feedback",
        {"outcome": outcome, "source_ref": "src-1:docs/a.md"},
        session_factory=session_factory,
    )

    assert result["feedback"]["outcome"] == outcome
    (call,) = _SpyFeedbackRepository.made[0].record_calls
    assert call["session_id"] == "sess-1"
    assert call["correlation_id"] == "corr-1"


def test_record_derives_linkage_and_redacts_all_free_text(session_factory):
    set_trace_context(TraceContext(session_id="sess-1", correlation_id="corr-1"))

    operations.run_operation(
        "record_answer_feedback",
        {
            "outcome": "ignore",
            "source_ref": "https://user:source_secret@host/doc",
            "notes": "see https://user:note_secret@host/path",
        },
        session_factory=session_factory,
    )

    (call,) = _SpyFeedbackRepository.made[0].record_calls
    assert call["principal_identity"] == "anonymous"
    assert "source_secret" not in call["source_ref"]
    assert "note_secret" not in call["notes"]


def test_record_requires_a_real_trace_context_and_target(session_factory):
    with pytest.raises(OperationError) as missing_context:
        operations.run_operation(
            "record_answer_feedback",
            {"outcome": "accept", "bundle_id": "cb-0123456789abcdef"},
            session_factory=session_factory,
        )
    assert missing_context.value.code == "invalid_params"

    set_trace_context(TraceContext(session_id="sess-1", correlation_id="corr-1"))
    with pytest.raises(OperationError) as missing_target:
        operations.run_operation(
            "record_answer_feedback", {"outcome": "accept"}, session_factory=session_factory
        )
    assert missing_target.value.code == "invalid_params"


def test_lookup_is_bounded_workspace_scoped_and_requires_a_filter(session_factory):
    with pytest.raises(OperationError) as unbounded:
        operations.run_operation("lookup_answer_feedback", {}, session_factory=session_factory)
    assert unbounded.value.code == "invalid_params"

    result = operations.run_operation(
        "lookup_answer_feedback",
        {"correlation_id": "corr-1", "limit": 7},
        session_factory=session_factory,
    )
    assert result["feedback"] == [] and result["count"] == 0
    (call,) = _SpyFeedbackRepository.made[-1].lookup_calls
    assert call["workspace"] == "default"
    assert call["correlation_id"] == "corr-1"
    assert call["limit"] == 7
