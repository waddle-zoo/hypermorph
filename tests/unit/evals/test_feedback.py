from types import SimpleNamespace

from hyperset.evals.feedback import feedback_loop_evidence


def test_feedback_evidence_grades_observable_links_and_redaction():
    feedback = [{"id": "afb-1", "outcome": "ignore"}, {"id": "afb-2", "outcome": "accept"}]
    trace = SimpleNamespace(
        feedback_ids=["afb-1", "afb-2"],
        duration_ms=3,
        source_staleness={"src-1": {"stale": False}},
        miss=None,
    )

    assert feedback_loop_evidence(feedback=feedback, trace=trace, forbidden=("secret",)) == {
        "ignore_then_accept_recorded": True,
        "feedback_linked_to_trace": True,
        "trace_complete": True,
        "forbidden_text_absent": True,
    }
