"""The durable miss-log at the serving boundary (hy-jrpm).

The write is a property of `run_operation`, not of the resolver: these assert it
fires for a miss, stays silent for a clean governed answer, never breaks the
served answer when the log write fails, and is never reached by the pure
resolver. The real persistence + migration are proven in
tests/postgres/test_resolve_miss.py; here the repository is a spy so the
behavior is tested without a database.
"""

from __future__ import annotations

import pytest

from hyperset.bundle import schema
from hyperset.db import models
from hyperset.transport import operations
from tests.unit.transport.conftest import DIRECTIVE, QUESTION, governed_bundle


class _SpyMissRepo:
    """Captures record() calls; one is constructed per _record_miss that fires."""

    made: list[_SpyMissRepo] = []

    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory
        self.calls: list[dict] = []
        _SpyMissRepo.made.append(self)

    def record(self, **kwargs):
        self.calls.append(kwargs)
        return None


@pytest.fixture
def spy_miss_repo(monkeypatch):
    _SpyMissRepo.made = []
    monkeypatch.setattr(operations, "PostgresResolveMissRepository", _SpyMissRepo)
    return _SpyMissRepo


def _stub_resolver(monkeypatch, bundle):
    monkeypatch.setattr(operations, "resolve_analytics_context", lambda **_kwargs: bundle)


def _resolve(session_factory):
    return operations.run_operation(
        "resolve_analytics_context",
        {"query": QUESTION, "directive": DIRECTIVE},
        session_factory=session_factory,
    )


def test_the_db_status_vocabulary_matches_the_served_one():
    # The miss-log's CheckConstraint mirrors the served statuses; the db layer
    # does not import the bundle layer, so the two are bound here.
    assert models.RESOLUTION_STATUSES == schema.RESOLUTION_STATUSES


def test_a_no_match_resolve_is_logged_at_the_boundary(spy_miss_repo, session_factory, monkeypatch):
    bundle = governed_bundle(
        resolution={"status": schema.NO_MATCH, "summary": "nothing governs this", "warnings": []}
    )
    _stub_resolver(monkeypatch, bundle)

    _resolve(session_factory)

    (repo,) = spy_miss_repo.made
    (call,) = repo.calls
    assert call["status"] == "no_match"
    assert call["query"] == QUESTION
    assert call["directive"] == DIRECTIVE
    assert call["bundle_id"] == bundle.bundle_id
    assert call["warning_codes"] == []


def test_an_observed_only_resolve_is_logged(spy_miss_repo, session_factory, monkeypatch):
    bundle = governed_bundle(
        resolution={"status": schema.OBSERVED_ONLY, "summary": "raw observation", "warnings": []}
    )
    _stub_resolver(monkeypatch, bundle)

    _resolve(session_factory)

    assert spy_miss_repo.made[0].calls[0]["status"] == "observed_only"


def test_a_governed_answer_with_no_warnings_logs_nothing(
    spy_miss_repo, session_factory, monkeypatch
):
    bundle = governed_bundle(
        resolution={"status": "governed", "summary": "revenue", "warnings": []}
    )
    _stub_resolver(monkeypatch, bundle)

    _resolve(session_factory)

    # A clean governed answer is not a miss: the repository is never even built.
    assert spy_miss_repo.made == []


def test_a_governed_answer_carrying_a_warning_is_logged(
    spy_miss_repo, session_factory, monkeypatch
):
    warning = schema.warning("ref_not_observed", "the ref names nothing observed")
    bundle = governed_bundle(
        resolution={"status": "governed", "summary": "revenue", "warnings": [warning]}
    )
    _stub_resolver(monkeypatch, bundle)

    _resolve(session_factory)

    call = spy_miss_repo.made[0].calls[0]
    assert call["status"] == "governed"
    assert call["warning_codes"] == ["ref_not_observed"]


def test_a_failing_miss_log_never_breaks_the_served_answer(session_factory, monkeypatch):
    class _Exploding:
        def __init__(self, _sf):
            pass

        def record(self, **_kwargs):
            raise RuntimeError("the miss-log write fell over")

    monkeypatch.setattr(operations, "PostgresResolveMissRepository", _Exploding)
    bundle = governed_bundle(
        resolution={"status": schema.NO_MATCH, "summary": "none", "warnings": []}
    )
    _stub_resolver(monkeypatch, bundle)

    # The answer is served regardless: operational logging never gates serving.
    result = _resolve(session_factory)
    assert result["resolution"]["status"] == "no_match"


def test_the_pure_resolver_writes_no_miss(spy_miss_repo, session_factory, monkeypatch):
    # Calling the resolve service directly -- the path the resolver's own tests
    # and the planner use -- never touches the miss-log. The write lives only at
    # run_operation, so the resolver stays deterministic and side-effect-free.
    bundle = governed_bundle(
        resolution={"status": schema.NO_MATCH, "summary": "none", "warnings": []}
    )
    _stub_resolver(monkeypatch, bundle)

    operations.resolve_analytics_context(
        query=QUESTION, directive=DIRECTIVE, session_factory=session_factory
    )

    assert spy_miss_repo.made == []


def test_an_engaged_pii_guard_without_presidio_persists_no_miss(
    spy_miss_repo, session_factory, monkeypatch
):
    """hy-hbtz fail-closed at the miss-log boundary: with the guard engaged and
    Presidio unhostable, the query is NOT persisted (best-effort swallows the
    guard's raise) rather than persisted unredacted -- and the served answer is
    unaffected, because the miss-log never gates a resolve."""
    from hyperset.security import pii

    monkeypatch.setattr(pii, "_engines", False)  # force the can't-host state on any seat
    monkeypatch.setenv("HYPERSET_PII_GUARD", "on")
    bundle = governed_bundle(
        resolution={"status": schema.NO_MATCH, "summary": "nothing governs this", "warnings": []}
    )
    _stub_resolver(monkeypatch, bundle)

    served = _resolve(session_factory)

    assert served["resolution"]["status"] == "no_match"  # the answer still serves
    # The guard raises before the repository is even constructed, so nothing
    # unredacted was written: no repo, no record() call.
    assert spy_miss_repo.made == []
