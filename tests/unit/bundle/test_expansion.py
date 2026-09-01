"""The expansion result's own shape and vocabulary (#230 slice 4, hy-fgga). The
estate-walking behaviour is proved end-to-end in tests/postgres/test_context_sync.py;
here the guardrail-1 labelling and the disclosure vocabulary are pinned DB-free."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hyperset.bundle.expansion import (
    EXPANSION_WARNING_CODES,
    ExpansionResult,
    _warn,
)
from hyperset.bundle.schema import SCHEMA_VERSION


def _result(**overrides) -> ExpansionResult:
    payload = {
        "request": {"query": "q", "start": "revenue"},
        "start": "revenue",
        "domains": [{"domain": "revenue", "available": True}],
        "edges": [],
        "warnings": [],
        "resolved_at": datetime(2026, 8, 16, tzinfo=UTC),
    }
    payload.update(overrides)
    return ExpansionResult(**payload)


def test_the_result_is_labelled_navigation_and_carries_no_governed_sections():
    # Guardrail 1 (Mayor): expand output can never read as a governed answer. The label is
    # pinned to the LITERAL "navigation", not the constant it is built from -- a constant
    # comparison would pass at any value.
    served = _result().to_dict()
    assert served["result_kind"] == "navigation"
    assert served["schema_version"] == SCHEMA_VERSION
    for governed_key in ("context_authority", "instructions", "linked_evidence", "bundle_id"):
        assert governed_key not in served


def test_the_disclosure_vocabulary_is_closed():
    # A navigation disclosure uses expand's OWN codes, not the bundle's WARNING_CODES.
    assert _warn("expansion_bounded", "m") == {"code": "expansion_bounded", "message": "m"}
    with pytest.raises(ValueError):
        _warn("over_context_budget", "m")  # a bundle code is not an expansion code
    assert "expansion_over_context_budget" in EXPANSION_WARNING_CODES
    assert "expansion_domain_unavailable" in EXPANSION_WARNING_CODES
