"""The stale-governed-context eval family, by REAL execution (hy-2m0r, step 6).

No live model (ADR 0013): the finding is the processor's real
`approved_expression_drift`, the bundle is the real resolver's output over the
drifted revenue estate, and the scorer is the deterministic detector. The
positive fixture (a drifted sync) flags stale; the negative (a fresh baseline)
does not. Reuses the pinned Superset 6.1.0 drift captures.
"""

from __future__ import annotations

import pytest

from hyperset.bundle import ContextDirective, resolve_analytics_context
from hyperset.evals.cases import STALE_GOVERNED_CONTEXT, Case
from hyperset.evals.pins import RunPins, repository_pins
from hyperset.evals.recording import GOVERNED_ARM, RECORDING_SCHEMA_VERSION, Recording
from hyperset.evals.scorers import score
from hyperset.planner.trace import TOOL_RESULT
from hyperset.processor import run_sync_processing
from tests.postgres.conftest import sync_superset
from tests.unit.evals.test_pins import HOST

STALE_CASE = Case(
    id="revenue_definition_drifted",
    family=STALE_GOVERNED_CONTEXT,
    question="Which source and rules should an analyst use for recognized revenue by region?",
    expected_domain="revenue",
    must_cite=(),
    must_not_cite=(),
    must_state=(),
    requires_plan_validation=False,
    reason="",
)


def _recording_of(bundle_dict: dict) -> Recording:
    """A recording carrying one real resolve result. `score()` reads only the
    trace and the arm; it does not verify pins, so the real served bundle is
    scored exactly as a recorded run's resolve step would be."""
    return Recording(
        run_id="d" * 32,
        schema_version=RECORDING_SCHEMA_VERSION,
        arm=GOVERNED_ARM,
        case_id=STALE_CASE.id,
        task_version="revenue@real",
        git_commit="0" * 40,
        recorded_at="2026-08-08T00:00:00+00:00",
        pins=RunPins(**{**repository_pins(GOVERNED_ARM), **HOST}),
        trace={
            "provenance": {"runtime": "openai_agents_sdk"},
            "steps": [
                {
                    "kind": TOOL_RESULT,
                    "at": "2026-08-08T00:00:00+00:00",
                    "summary": "",
                    "detail": {
                        "operation": "resolve_analytics_context",
                        "result": bundle_dict,
                        "retryable_warnings": [],
                    },
                }
            ],
        },
        source_refs=[],
    )


def _resolve(session_factory) -> dict:
    bundle = resolve_analytics_context(
        query=STALE_CASE.question,
        directive=ContextDirective(domains=["revenue"], concepts=["recognized_revenue"]),
        session_factory=session_factory,
    )
    return bundle.to_dict()


@pytest.mark.postgres
def test_a_drifted_governed_context_is_flagged_stale(session_factory, revenue_slice):
    drift = sync_superset(revenue_slice["connection_id"], session_factory, "drift")
    run_sync_processing(sync_run_id=drift.sync_run_id, session_factory=session_factory)

    served = _resolve(session_factory)
    # The real bundle carries the real drift finding.
    finding_types = {f["finding_type"] for f in served["linked_evidence"]["findings"]}
    assert "approved_expression_drift" in finding_types

    scores = score(_recording_of(served), STALE_CASE)
    surfaced = next(s for s in scores if s.predicate == "stale_governed_context_surfaced")
    assert surfaced.passed is True
    assert surfaced.code.value == "surfaced_the_stale_governed_context"


@pytest.mark.postgres
def test_a_fresh_governed_context_is_not_flagged_stale(session_factory, revenue_slice):
    # No drift processed: the served bundle carries no drift finding, so the
    # detector does not flag staleness -- the negative arm proving it can be
    # silent and is not a dead gate.
    served = _resolve(session_factory)
    assert served["linked_evidence"]["findings"] == []

    scores = score(_recording_of(served), STALE_CASE)
    surfaced = next(s for s in scores if s.predicate == "stale_governed_context_surfaced")
    assert surfaced.passed is False
