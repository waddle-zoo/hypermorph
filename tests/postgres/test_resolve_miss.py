"""The durable miss-log, end to end over a real DB (hy-jrpm).

Proves the migration, the write at the run_operation boundary, and that the
pure resolver writes nothing. The behavioral matrix (which outcomes log, best
effort, no-write-on-clean) is in tests/unit/transport/test_miss_log.py.
"""

from __future__ import annotations

import pytest

from hyperset.bundle import resolve_analytics_context
from hyperset.bundle.directive import ContextDirective
from hyperset.repositories.postgres import PostgresResolveMissRepository
from hyperset.transport.operations import run_operation

QUESTION = "Which source and rules should an analyst use for recognized revenue by region?"
GOVERNED = {"domains": ["revenue"], "concepts": ["recognized_revenue"]}
# A well-formed superset dataset ref the estate never observed: a bad ref comes
# back inside a SERVED bundle as a warning, not a refusal, so this is a miss.
MISS = {"asset_refs": ["superset:dataset:00000000-0000-0000-0000-000000000000"]}


@pytest.mark.postgres
def test_a_clean_governed_resolve_logs_no_miss(session_factory, revenue_slice):
    result = run_operation(
        "resolve_analytics_context",
        {"query": QUESTION, "directive": GOVERNED},
        session_factory=session_factory,
    )
    assert result["resolution"]["status"] == "governed"
    assert PostgresResolveMissRepository(session_factory).recent() == []


@pytest.mark.postgres
def test_a_miss_is_persisted_at_the_boundary(session_factory, revenue_slice):
    result = run_operation(
        "resolve_analytics_context",
        {"query": QUESTION, "directive": MISS},
        session_factory=session_factory,
    )
    warnings = [entry["code"] for entry in result["resolution"]["warnings"]]
    assert warnings, "the unobserved ref should have produced a warning"

    (miss,) = PostgresResolveMissRepository(session_factory).recent()
    assert miss.query == QUESTION
    assert miss.directive == MISS
    assert miss.status == result["resolution"]["status"]
    assert miss.warning_codes == warnings
    assert miss.bundle_id == result["bundle_id"]


@pytest.mark.postgres
def test_the_pure_resolver_persists_no_miss(session_factory, revenue_slice):
    # The resolve service, called directly rather than through run_operation,
    # writes nothing: the miss-log lives only at the transport boundary.
    resolve_analytics_context(
        query=QUESTION,
        directive=ContextDirective(**MISS),
        session_factory=session_factory,
    )
    assert PostgresResolveMissRepository(session_factory).recent() == []
