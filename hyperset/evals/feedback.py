"""Deterministic acceptance predicates for the trace-linked feedback loop."""

from __future__ import annotations


def feedback_loop_evidence(*, feedback: list[dict], trace, forbidden: tuple[str, ...] = ()) -> dict:
    """Grade observable records, never a preferred model/tool trajectory."""
    blob = repr((feedback, trace))
    return {
        "ignore_then_accept_recorded": [item.get("outcome") for item in feedback]
        == ["ignore", "accept"],
        "feedback_linked_to_trace": [item.get("id") for item in feedback]
        == list(trace.feedback_ids),
        "trace_complete": (
            trace.duration_ms is not None
            and isinstance(trace.source_staleness, dict)
            and hasattr(trace, "miss")
        ),
        "forbidden_text_absent": not any(marker in blob for marker in forbidden),
    }
