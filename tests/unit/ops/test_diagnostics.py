"""Maintainer failure-diagnostics classifier (hy-bue7r, V1 gap Integrator/3).

Every one of the five named classes is reachable from a real signal, an unconfigured/
informational signal is NOT a failure, and the mapping is deterministic.
"""

from __future__ import annotations

from hyperset.ops.diagnostics import (
    CONNECTOR_OUTAGE,
    DIAGNOSTIC_CLASSES,
    INVALID_INPUT,
    MISSING_MODEL,
    REGRESSION,
    STALE_CONTEXT,
    diagnose,
)


def _classes(rows):
    return {row.diagnostic_class for row in rows}


def test_each_of_the_five_classes_is_reachable_from_a_real_signal():
    rows = diagnose(
        readiness_components=[
            {
                "component": "ollama",
                "status": "blocked",
                "detail": "unreachable",
                "recovery": "start it",
            },
            {
                "component": "git_context",
                "status": "degraded",
                "detail": "stale",
                "recovery": "sync",
            },
        ],
        observed_sources=[
            {
                "display_name": "Prod",
                "status": "blocked",
                "reachable": False,
                "fresh": False,
                "reason": "refused",
                "recovery": "check url",
            },
        ],
        provider_probes=[
            {
                "component": "openai",
                "status": "blocked",
                "configured": True,
                "reason": "401",
                "recovery": "set key",
            }
        ],
        warnings=[
            {"code": "ref_not_observed", "message": "gone"},
            {"code": "ref_malformed", "message": "bad"},
        ],
        conflicts=[{"kind": "source_deleted_while_governed", "severity": "error"}],
        error_code="invalid_params",
    )
    assert _classes(rows) == set(DIAGNOSTIC_CLASSES)
    by_subject = {row.subject: row.diagnostic_class for row in rows}
    assert by_subject["ollama"] == MISSING_MODEL
    assert by_subject["openai"] == MISSING_MODEL
    assert by_subject["git_context"] == STALE_CONTEXT
    assert by_subject["Prod"] == CONNECTOR_OUTAGE
    assert by_subject["ref_not_observed"] == REGRESSION
    assert by_subject["source_deleted_while_governed"] == REGRESSION
    assert by_subject["ref_malformed"] == INVALID_INPUT
    assert by_subject["request"] == INVALID_INPUT


def test_a_reachable_but_stale_connection_is_stale_context_not_an_outage():
    (row,) = diagnose(
        observed_sources=[
            {
                "display_name": "Cat",
                "status": "degraded",
                "reachable": True,
                "fresh": False,
                "reason": "stale",
                "recovery": "sync",
            },
        ]
    )
    assert row.diagnostic_class == STALE_CONTEXT


def test_an_unknown_disabled_or_unconfigured_component_is_not_a_failure():
    rows = diagnose(
        readiness_components=[
            {"component": "model", "status": "unknown"},
            {"component": "superset", "status": "disabled"},
            {"component": "notifications", "status": "not_configured"},
        ],
        provider_probes=[
            {"component": "ollama", "status": "unknown", "configured": False},
            {
                "component": "openai",
                "status": "blocked",
                "configured": False,
            },  # blocked but unconfigured
        ],
    )
    assert rows == []


def test_an_informational_warning_and_a_warning_severity_conflict_are_not_failures():
    rows = diagnose(
        warnings=[
            {"code": "projection_bounded", "message": "info"},
            {"code": "over_context_budget"},
        ],
        conflicts=[{"kind": "x", "severity": "warning"}],
    )
    assert rows == []


def test_the_analytics_db_provider_is_a_connector_outage_not_a_missing_model():
    (row,) = diagnose(
        provider_probes=[
            {
                "component": "analytics_db",
                "status": "blocked",
                "configured": True,
                "reason": "refused",
                "recovery": "check",
            }
        ]
    )
    assert row.diagnostic_class == CONNECTOR_OUTAGE


def test_rows_are_ordered_by_class_severity_then_subject():
    rows = diagnose(
        warnings=[
            {"code": "ref_malformed"},
            {"code": "ref_not_observed"},
            {"code": "ref_awaiting_sync"},
        ],
    )
    # regression (worst) before stale_context before invalid_input.
    assert [r.diagnostic_class for r in rows] == [REGRESSION, STALE_CONTEXT, INVALID_INPUT]
    # And it is deterministic.
    assert (
        diagnose(
            warnings=[
                {"code": "ref_malformed"},
                {"code": "ref_not_observed"},
                {"code": "ref_awaiting_sync"},
            ]
        )
        == rows
    )
