"""probe_connection: honest configured/reachable/fresh rollup (hq-jedd)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from hyperset.connectors.types import ConnectionTest
from hyperset.ops.connection_probe import (
    CONNECTION_PROBE_TIMEOUT,
    freshness_from_run,
    probe_connection,
)

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _record(**over):
    base = dict(id="c1", connector_type="superset", display_name="Prod", config_ref="/bundle")
    base.update(over)
    return SimpleNamespace(**base)


def _run(status="succeeded", finished_at=None):
    """A stand-in for the latest finished SyncRunRecord: only `status`/`finished_at` matter
    to freshness."""
    return SimpleNamespace(status=status, finished_at=finished_at)


def _build_ok(_type, _source, *, timeout=None):
    return SimpleNamespace(test_connection=lambda: ConnectionTest(ok=True, detail="5 assets"))


def _build_bad(_type, _source, *, timeout=None):
    return SimpleNamespace(test_connection=lambda: ConnectionTest(ok=False, detail="401"))


def test_ready_when_configured_reachable_and_freshly_synced():
    probe = probe_connection(
        _record(),
        latest_finished_run=_run("succeeded", NOW - timedelta(hours=1)),
        now=NOW,
        build=_build_ok,
    )
    assert probe.status == "ready"
    assert (probe.configured, probe.reachable, probe.fresh) == (True, True, True)
    assert probe.health_status == "healthy"


def test_degraded_when_reachable_but_never_or_stalely_synced():
    # Reachable, but no recent sync -- green liveness is not readiness.
    fresh_probe = probe_connection(_record(), latest_finished_run=None, now=NOW, build=_build_ok)
    assert fresh_probe.status == "degraded"
    assert fresh_probe.reachable is True and fresh_probe.fresh is False
    assert fresh_probe.health_status == "healthy"  # live, but stale evidence
    stale = probe_connection(
        _record(),
        latest_finished_run=_run("succeeded", NOW - timedelta(days=3)),
        now=NOW,
        build=_build_ok,
    )
    assert stale.status == "degraded"


@pytest.mark.parametrize(
    "run,expected_fresh",
    [
        (_run("succeeded", NOW - timedelta(hours=1)), True),
        # The divergence bug (adversary round 1): a RECENT FAILED sync did not refresh the
        # evidence, so it is NOT fresh -- the old live probe counted its timestamp as fresh.
        (_run("failed", NOW - timedelta(hours=1)), False),
        (_run("succeeded", NOW - timedelta(days=3)), False),  # stale
        (None, False),  # never synced
    ],
)
def test_live_probe_freshness_matches_the_shared_rule(run, expected_fresh):
    """The live probe's freshness IS `freshness_from_run` -- the exact rule the recorded
    overview (`read_observed_source_status`) uses -- so the two can never diverge."""
    probe = probe_connection(_record(), latest_finished_run=run, now=NOW, build=_build_ok)
    assert probe.fresh is expected_fresh
    assert freshness_from_run(run, NOW) is expected_fresh


def test_probe_passes_an_explicit_short_timeout_to_the_builder():
    """The live probe runs on an INTERACTIVE admin request, so it must carry its OWN short,
    explicit deadline into the connector build -- never inherit the 30s SYNC default (a batch
    bound), which for a source that accepts then stalls could hang the request ~60s (Superset
    does login + list = two sequential calls). This is the plumbing half of the bound; the
    real-socket half (that the value actually cuts a hanging connection) is the integration
    test."""
    seen = {}

    def _capturing_build(_type, _source, *, timeout=None):
        seen["timeout"] = timeout
        return SimpleNamespace(test_connection=lambda: ConnectionTest(ok=True, detail="ok"))

    probe_connection(_record(), latest_finished_run=None, now=NOW, build=_capturing_build)
    assert seen["timeout"] == CONNECTION_PROBE_TIMEOUT


def test_the_probe_timeout_is_tighter_than_the_sync_default():
    # A bound that equalled or exceeded the 30s SYNC default would be no interactive bound at
    # all; it must be short so an unreachable/stalling source fails fast (mirrors
    # provider_probe.PROBE_TIMEOUT).
    assert 0 < CONNECTION_PROBE_TIMEOUT < 30


def test_blocked_when_not_configured():
    probe = probe_connection(_record(config_ref=None), now=NOW, build=_build_ok)
    assert probe.status == "blocked"
    assert probe.configured is False and probe.health_status == "unknown"


def test_blocked_when_build_fails_or_unreachable():
    def _build_raises(_type, _source, *, timeout=None):
        raise ValueError("live Superset sync needs HYPERSET_SUPERSET_USERNAME ...")

    missing_creds = probe_connection(_record(), now=NOW, build=_build_raises)
    assert missing_creds.status == "blocked" and missing_creds.health_status == "unhealthy"

    unreachable = probe_connection(_record(), now=NOW, build=_build_bad)
    assert unreachable.status == "blocked" and unreachable.reachable is False
    assert unreachable.health_status == "unhealthy"


def test_blocked_when_the_builder_raises_a_non_value_error():
    # hq-jedd round 2 (adversary): connector CONSTRUCTION raising a non-ValueError (a client
    # library RuntimeError) must be caught as blocked, not escape as an unhandled probe.
    def _build_boom(_type, _source, *, timeout=None):
        raise RuntimeError("client library exploded")

    probe = probe_connection(_record(), now=NOW, build=_build_boom)
    assert probe.status == "blocked"
    assert probe.reachable is False and probe.health_status == "unhealthy"
    assert probe.impact and probe.recovery


def test_never_raises_and_always_names_impact_and_recovery():
    # A non-ValueError from test_connection is caught as unreachable, not propagated.
    boom = probe_connection(
        _record(),
        now=NOW,
        build=lambda t, s, timeout=None: SimpleNamespace(
            test_connection=lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        ),
    )
    assert boom.status == "blocked" and boom.impact and boom.recovery
