"""The admin readiness overview aggregator (hy-gh-75, first slice).

Hermetic: the DB repositories and the git-context reader are faked at the module seam,
so these exercise the STATUS MAPPING and the no-secrets/coverage guarantees without a
database or a network. The real DB reads are covered against Postgres separately.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from hyperset.ops import readiness
from hyperset.ops.readiness import (
    BLOCKED,
    COMPONENTS,
    DEGRADED,
    DISABLED,
    NOT_CONFIGURED,
    READY,
    UNKNOWN,
    admin_readiness,
)

NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)
CHECKED = datetime(2026, 8, 18, 11, 0, 0, tzinfo=UTC)
_SF = object()  # an opaque session_factory sentinel; the fakes ignore it


def _conn(connector_type, health_status, checked_at=CHECKED, *, enabled=True):
    return SimpleNamespace(
        connector_type=connector_type,
        health_status=health_status,
        health_checked_at=checked_at,
        enabled=enabled,
    )


def _git(pinned, last_attempt_status, last_attempt_at=CHECKED):
    return SimpleNamespace(
        pinned=pinned,
        last_attempt_status=last_attempt_status,
        last_attempt_at=last_attempt_at,
    )


def _wb(**over):
    """A configured write-back target, full-shaped so the multi-target readiness reader can
    read enabled/is_default/routing_key/test_result -- the live model (hy-lotg3)."""
    base = dict(
        repository="org/r",
        routing_key=None,
        is_default=True,
        enabled=True,
        test_result=None,
        token_source="local",
        token_ref=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _wire(monkeypatch, *, connections=(), git=(), writeback=None, db_raises=None):
    """Install fakes for the DB repositories + git reader at the readiness seam. `writeback`
    is the estate's TARGETS: a list, a single target (wrapped), or None (no targets)."""
    if writeback is None:
        targets = []
    elif isinstance(writeback, list):
        targets = writeback
    else:
        targets = [writeback]

    class _Conns:
        def __init__(self, _sf):
            pass

        def list(self, *, workspace=None, enabled_only=False):
            if db_raises is not None:
                raise db_raises
            return list(connections)

    class _Writeback:
        def __init__(self, _sf):
            pass

        def list(self, *, workspace=None):
            return list(targets)

    monkeypatch.setattr(readiness, "PostgresConnectionRepository", _Conns)
    monkeypatch.setattr(readiness, "PostgresWritebackConfigRepository", _Writeback)
    monkeypatch.setattr(readiness, "read_git_context", lambda _sf: list(git))


def _by_component(report):
    return {c["component"]: c for c in report["components"]}


def _healthy_env():
    return {
        readiness.MODEL_PROVIDER_ENV: "openai",
        readiness.EMBEDDING_PROVIDER_ENV: "openai",
        readiness.ANALYTICS_DB_URL_ENV: "postgresql://analytics",
        readiness.NOTIFICATIONS_WEBHOOK_ENV: "https://hooks.example/x",
    }


# --- structure & coverage ----------------------------------------------------


def test_the_report_covers_exactly_the_ten_named_components(monkeypatch):
    _wire(monkeypatch)
    report = admin_readiness(_SF, env={}, now=NOW)
    got = [c["component"] for c in report["components"]]
    assert got == list(COMPONENTS)
    assert len(COMPONENTS) == 10
    # Every line carries the operator fields the overview promises.
    for component in report["components"]:
        assert set(component) == {
            "component",
            "status",
            "checked_at",
            "owner",
            "impact",
            "recovery",
            "detail",
        }
        assert component["status"] in {
            READY,
            DISABLED,
            NOT_CONFIGURED,
            DEGRADED,
            BLOCKED,
            UNKNOWN,
        }
        assert component["owner"] and component["impact"] and component["recovery"]


def test_generated_at_and_overall_are_present(monkeypatch):
    _wire(monkeypatch)
    report = admin_readiness(_SF, env={}, now=NOW)
    assert report["generated_at"] == NOW.isoformat()
    assert report["overall"] in {READY, DEGRADED, BLOCKED, UNKNOWN}


# --- api & database ----------------------------------------------------------


def test_api_is_ready_because_it_is_answering(monkeypatch):
    _wire(monkeypatch, db_raises=RuntimeError("boom"))
    api = _by_component(admin_readiness(_SF, env={}, now=NOW))["api"]
    assert api["status"] == READY


def test_database_unreachable_blocks_db_and_makes_db_derived_unknown(monkeypatch):
    _wire(monkeypatch, db_raises=RuntimeError("connection refused"))
    by = _by_component(admin_readiness(_SF, env=_healthy_env(), now=NOW))
    assert by["database"]["status"] == BLOCKED
    assert "connection refused" not in by["database"]["detail"]  # exc TYPE, not the message
    assert by["database"]["detail"] and "RuntimeError" in by["database"]["detail"]
    for component in ("superset", "datahub", "git_context"):
        assert by[component]["status"] == UNKNOWN
        assert by[component]["checked_at"] is None
    # A DB-down deployment is BLOCKED overall regardless of config-derived readies.
    assert admin_readiness(_SF, env=_healthy_env(), now=NOW)["overall"] == BLOCKED


def test_database_ready_when_the_read_succeeds(monkeypatch):
    _wire(monkeypatch)
    assert _by_component(admin_readiness(_SF, env={}, now=NOW))["database"]["status"] == READY


# --- connections -------------------------------------------------------------


def test_a_healthy_connection_is_ready_with_its_checked_at(monkeypatch):
    _wire(monkeypatch, connections=[_conn("superset", "healthy")])
    superset = _by_component(admin_readiness(_SF, env={}, now=NOW))["superset"]
    assert superset["status"] == READY
    assert superset["checked_at"] == CHECKED.isoformat()
    assert "1/1" in superset["detail"]


def test_an_unhealthy_connection_blocks_that_connector(monkeypatch):
    _wire(monkeypatch, connections=[_conn("datahub", "unhealthy")])
    by = _by_component(admin_readiness(_SF, env={}, now=NOW))
    assert by["datahub"]["status"] == BLOCKED
    assert by["superset"]["status"] == NOT_CONFIGURED  # none registered
    assert by["superset"]["checked_at"] is None


def test_a_connector_aggregates_to_its_worst_connection(monkeypatch):
    _wire(
        monkeypatch,
        connections=[_conn("superset", "healthy"), _conn("superset", "unhealthy")],
    )
    superset = _by_component(admin_readiness(_SF, env={}, now=NOW))["superset"]
    assert superset["status"] == BLOCKED
    assert "1/2" in superset["detail"]


def test_disabled_connections_are_informational_and_do_not_mask_enabled_health(monkeypatch):
    _wire(
        monkeypatch,
        connections=[
            _conn("superset", "healthy"),
            _conn("superset", "unhealthy", enabled=False),
            _conn("datahub", "unhealthy", enabled=False),
        ],
    )
    by = _by_component(admin_readiness(_SF, env={}, now=NOW))
    assert by["superset"]["status"] == READY
    assert by["superset"]["detail"] == "1/1 enabled connection(s) healthy; 1 disabled"
    assert by["datahub"]["status"] == DISABLED


# --- git context -------------------------------------------------------------


def test_git_pinned_and_synced_is_ready(monkeypatch):
    _wire(monkeypatch, git=[_git(True, "synced")])
    assert _by_component(admin_readiness(_SF, env={}, now=NOW))["git_context"]["status"] == READY


def test_git_pinned_but_last_attempt_failed_is_degraded(monkeypatch):
    _wire(monkeypatch, git=[_git(True, "failed")])
    git = _by_component(admin_readiness(_SF, env={}, now=NOW))["git_context"]
    assert git["status"] == DEGRADED


def test_git_never_pinned_and_failing_is_blocked(monkeypatch):
    _wire(monkeypatch, git=[_git(False, "failed")])
    assert _by_component(admin_readiness(_SF, env={}, now=NOW))["git_context"]["status"] == BLOCKED


def test_git_added_but_never_synced_is_unknown(monkeypatch):
    _wire(monkeypatch, git=[_git(False, "never_synced")])
    assert _by_component(admin_readiness(_SF, env={}, now=NOW))["git_context"]["status"] == UNKNOWN


def test_no_git_source_is_unknown(monkeypatch):
    _wire(monkeypatch, git=[])
    git = _by_component(admin_readiness(_SF, env={}, now=NOW))["git_context"]
    assert git["status"] == UNKNOWN
    assert "no Git context source" in git["detail"]


# --- config-derived components ----------------------------------------------


@pytest.mark.parametrize("component", ["model", "embeddings"])
def test_required_config_component_is_unknown_when_unset_and_ready_when_set(monkeypatch, component):
    _wire(monkeypatch)
    off = _by_component(admin_readiness(_SF, env={}, now=NOW))[component]
    assert off["status"] == UNKNOWN
    on = _by_component(admin_readiness(_SF, env=_healthy_env(), now=NOW))[component]
    assert on["status"] == READY


@pytest.mark.parametrize("component", ["analytics_db", "notifications"])
def test_optional_config_component_is_not_configured_when_unset_and_ready_when_set(
    monkeypatch, component
):
    _wire(monkeypatch)
    off = _by_component(admin_readiness(_SF, env={}, now=NOW))[component]
    assert off["status"] == NOT_CONFIGURED
    on = _by_component(admin_readiness(_SF, env=_healthy_env(), now=NOW))[component]
    assert on["status"] == READY


# --- write-back --------------------------------------------------------------


def test_writeback_unconfigured_is_not_configured(monkeypatch):
    _wire(monkeypatch, writeback=None)
    wb = _by_component(admin_readiness(_SF, env={}, now=NOW))["writeback"]
    assert wb["status"] == NOT_CONFIGURED
    assert "Propose to Git is disabled" in wb["detail"]


def test_writeback_env_ref_missing_credential_is_degraded(monkeypatch):
    config = _wb(
        token_source="env_ref",
        token_ref="HYPERSET_WRITEBACK_TOKEN",
        repository="https://github.com/org/repo",
    )
    _wire(monkeypatch, writeback=config)
    wb = _by_component(admin_readiness(_SF, env={}, now=NOW))["writeback"]
    assert wb["status"] == DEGRADED


def test_writeback_env_ref_present_credential_is_ready(monkeypatch):
    config = _wb(
        token_source="env_ref",
        token_ref="HYPERSET_WRITEBACK_TOKEN",
        repository="https://github.com/org/repo",
    )
    _wire(monkeypatch, writeback=config)
    env = {"HYPERSET_WRITEBACK_TOKEN": "ghp_supersecretvalue"}
    wb = _by_component(admin_readiness(_SF, env=env, now=NOW))["writeback"]
    assert wb["status"] == READY


# --- multi-target (hy-lotg3) --------------------------------------------------


def test_a_routing_keyed_only_estate_is_no_longer_read_as_no_target(monkeypatch):
    # THE QA f06ddc9 repro: an estate whose only target is routing-keyed (no default) used to
    # read as "no write-back target configured / unknown" because readiness read the legacy
    # DEFAULT-only .get(). The live router (get_by_routing) serves this target, so readiness
    # must show it. MUTATION-RED against the .get()-based reader (which returned None here).
    keyed = _wb(
        is_default=False, routing_key="revenue", repository="git@host/revenue", enabled=True
    )
    _wire(monkeypatch, writeback=[keyed])
    wb = _by_component(admin_readiness(_SF, env={}, now=NOW))["writeback"]
    assert wb["status"] == READY
    assert "no write-back target configured" not in (wb["detail"] or "")
    assert (
        "revenue" in wb["detail"]
        and "1 write-back target(s) configured (1 enabled)" in wb["detail"]
    )


def test_multiple_targets_are_each_shown_with_their_enabled_and_test_state(monkeypatch):
    targets = [
        _wb(is_default=True, repository="git@host/default", enabled=True, test_result="ok"),
        _wb(is_default=False, routing_key="revenue", repository="git@host/rev", enabled=False),
    ]
    _wire(monkeypatch, writeback=targets)
    wb = _by_component(admin_readiness(_SF, env={}, now=NOW))["writeback"]
    detail = wb["detail"]
    assert "2 write-back target(s) configured (1 enabled)" in detail
    assert "default" in detail and "enabled" in detail and "test ok" in detail
    assert "revenue" in detail and "disabled" in detail


def test_all_targets_disabled_is_informational(monkeypatch):
    _wire(
        monkeypatch,
        writeback=[_wb(enabled=False), _wb(routing_key="revenue", is_default=False, enabled=False)],
    )
    wb = _by_component(admin_readiness(_SF, env={}, now=NOW))["writeback"]
    assert wb["status"] == DISABLED
    assert "NONE enabled" in wb["detail"]


def test_a_credential_bearing_target_repository_is_redacted_in_the_detail(monkeypatch):
    import json

    _wire(monkeypatch, writeback=[_wb(repository="https://alice:ghp_SECRET@github.com/acme/ctx")])
    report = admin_readiness(_SF, env={}, now=NOW)
    wb = _by_component(report)["writeback"]
    assert "https://github.com/acme/ctx" in wb["detail"]
    assert "ghp_SECRET" not in json.dumps(report)
    assert "alice:" not in json.dumps(report)


# --- no secrets --------------------------------------------------------------


def test_the_report_never_carries_a_secret_value(monkeypatch):
    import json

    config = _wb(
        token_source="env_ref",
        token_ref="HYPERSET_WRITEBACK_TOKEN",
        repository="https://github.com/org/repo",
    )
    _wire(monkeypatch, connections=[_conn("superset", "healthy")], writeback=config)
    secret = "ghp_THIS_IS_THE_SECRET_VALUE_0xdeadbeef"
    env = {**_healthy_env(), "HYPERSET_WRITEBACK_TOKEN": secret}
    report = admin_readiness(_SF, env=env, now=NOW)
    assert secret not in json.dumps(report), "a secret value leaked into the readiness report"


def test_a_missing_credential_names_the_reference_not_the_value(monkeypatch):
    import json

    # The DEGRADED path names the credential REFERENCE (the env var NAME) so an admin
    # knows what to set -- the NAME is not the value, and the value is never read here.
    config = _wb(
        token_source="env_ref",
        token_ref="HYPERSET_WRITEBACK_TOKEN",
        repository="https://github.com/org/repo",
    )
    _wire(monkeypatch, writeback=config)
    report = admin_readiness(_SF, env={}, now=NOW)  # credential absent -> degraded
    blob = json.dumps(report)
    assert "HYPERSET_WRITEBACK_TOKEN" in blob  # the NAME is surfaced
    assert _by_component(report)["writeback"]["status"] == DEGRADED


def test_a_local_writeback_target_does_not_require_an_env_secret(monkeypatch):
    config = _wb(
        token_source="env_ref",
        token_ref="HYPERSET_TEST_TOKEN",
        repository="/repo/.runtime/playground-contexts",
    )
    _wire(monkeypatch, writeback=config)
    wb = _by_component(admin_readiness(_SF, env={}, now=NOW))["writeback"]
    assert wb["status"] == READY
    assert "credential reference missing" not in wb["detail"]


# --- overall = worst ---------------------------------------------------------


def test_overall_is_the_worst_component(monkeypatch):
    # All healthy -> ready (BOTH connectors present + healthy, git pinned, everything set).
    _wire(
        monkeypatch,
        connections=[_conn("superset", "healthy"), _conn("datahub", "healthy")],
        git=[_git(True, "synced")],
        writeback=_wb(token_source="local", token_ref=None, repository="org/r"),
    )
    assert admin_readiness(_SF, env=_healthy_env(), now=NOW)["overall"] == READY
    # One unhealthy connection -> blocked overall, even with everything else healthy.
    _wire(
        monkeypatch,
        connections=[_conn("superset", "healthy"), _conn("datahub", "unhealthy")],
        git=[_git(True, "synced")],
        writeback=_wb(token_source="local", token_ref=None, repository="org/r"),
    )
    assert admin_readiness(_SF, env=_healthy_env(), now=NOW)["overall"] == BLOCKED


def test_optional_absence_and_disabled_targets_do_not_degrade_a_ready_core(monkeypatch):
    _wire(
        monkeypatch,
        connections=[_conn("superset", "healthy"), _conn("datahub", "healthy")],
        git=[_git(True, "synced")],
        writeback=_wb(enabled=False),
    )
    env = {
        readiness.MODEL_PROVIDER_ENV: "openai",
        readiness.EMBEDDING_PROVIDER_ENV: "openai",
    }
    report = admin_readiness(_SF, env=env, now=NOW)
    by = _by_component(report)
    assert report["overall"] == READY
    assert "ollama" not in by
    assert by["analytics_db"]["status"] == NOT_CONFIGURED
    assert by["writeback"]["status"] == DISABLED
    assert by["notifications"]["status"] == NOT_CONFIGURED


@pytest.mark.parametrize("connector_type", ["superset", "datahub"])
def test_required_connector_with_zero_healthy_connections_is_not_ready(monkeypatch, connector_type):
    other_type = "datahub" if connector_type == "superset" else "superset"
    _wire(
        monkeypatch,
        connections=[
            *[_conn(connector_type, "unknown", checked_at=None) for _ in range(4)],
            _conn(other_type, "healthy"),
        ],
        git=[_git(True, "synced")],
    )
    env = {
        readiness.MODEL_PROVIDER_ENV: "openai",
        readiness.EMBEDDING_PROVIDER_ENV: "openai",
    }
    report = admin_readiness(_SF, env=env, now=NOW)
    component = _by_component(report)[connector_type]
    assert component["status"] == UNKNOWN
    assert component["detail"] == "0/4 enabled connection(s) healthy"
    assert report["overall"] == UNKNOWN


def test_unconfigured_required_connectors_are_not_ready(monkeypatch):
    _wire(monkeypatch, git=[_git(True, "synced")])
    report = admin_readiness(_SF, env=_healthy_env(), now=NOW)
    by = _by_component(report)
    assert by["superset"]["status"] == NOT_CONFIGURED
    assert by["datahub"]["status"] == NOT_CONFIGURED
    assert report["overall"] == UNKNOWN


def test_an_unrecognised_future_optional_status_defaults_overall_to_unknown(monkeypatch):
    _wire(monkeypatch)
    components = [
        readiness.ComponentReadiness(
            component="notifications",
            status="future_status",
            checked_at=None,
            owner="owner",
            impact="impact",
            recovery="recovery",
        )
    ]
    assert readiness._overall(components, {}) == UNKNOWN
