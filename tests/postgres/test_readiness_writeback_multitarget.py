"""Admin Readiness reflects the LIVE multi-target write-back model (hy-lotg3, QA f06ddc9).

Against a real postgres repository: the readiness writeback line must read the whole estate's
configured targets (the same model the router `get_by_routing` serves), not the legacy
default-only `.get()`. The QA repro is an estate whose only target is routing-keyed (no
default): it used to read as "no write-back target / unknown"; it must now show the target.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hyperset.ops.readiness import DISABLED, NOT_CONFIGURED, READY, admin_readiness
from hyperset.repositories.postgres import PostgresWritebackConfigRepository

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def _writeback(report):
    return next(c for c in report["components"] if c["component"] == "writeback")


def test_zero_targets_reads_as_not_configured_accurately(session_factory):
    wb = _writeback(admin_readiness(session_factory, env={}, now=NOW))
    assert wb["status"] == NOT_CONFIGURED
    assert "no write-back target configured" in wb["detail"]


def test_a_routing_keyed_only_estate_is_shown_not_read_as_no_target(session_factory):
    # THE repro: only a keyed target, no default. The legacy .get() returned None here ->
    # "no target/unknown". The live list() serves it, so readiness must show it READY.
    PostgresWritebackConfigRepository(session_factory).set(
        routing_key="revenue",
        repository="/srv/revenue-ctx",
        base_ref="main",
        manifest_path="domains/revenue",
        token_source="local",
    )
    wb = _writeback(admin_readiness(session_factory, env={}, now=NOW))
    assert wb["status"] == READY, wb
    assert "no write-back target configured" not in wb["detail"]
    assert "revenue" in wb["detail"]
    assert "1 write-back target(s) configured (1 enabled)" in wb["detail"]


def test_multiple_targets_are_each_reflected_with_their_state(session_factory):
    repo = PostgresWritebackConfigRepository(session_factory)
    repo.set(
        repository="/srv/default-ctx",
        base_ref="main",
        manifest_path="domains/default",
        token_source="local",
    )  # the default target
    keyed = repo.set(
        routing_key="revenue",
        repository="/srv/revenue-ctx",
        base_ref="main",
        manifest_path="domains/revenue",
        token_source="local",
    )
    repo.set_enabled(keyed.id, enabled=False, workspace="default")  # disable the keyed one

    wb = _writeback(admin_readiness(session_factory, env={}, now=NOW))
    # One enabled (default) + one disabled (revenue) -> still READY (the default serves), and
    # both are enumerated with their state.
    assert wb["status"] == READY, wb
    assert "2 write-back target(s) configured (1 enabled)" in wb["detail"]
    assert "default" in wb["detail"] and "revenue" in wb["detail"]
    assert "disabled" in wb["detail"]


def test_all_targets_disabled_is_informational(session_factory):
    repo = PostgresWritebackConfigRepository(session_factory)
    target = repo.set(
        repository="/srv/ctx",
        base_ref="main",
        manifest_path="domains/x",
        token_source="local",
    )
    repo.set_enabled(target.id, enabled=False, workspace="default")
    wb = _writeback(admin_readiness(session_factory, env={}, now=NOW))
    assert wb["status"] == DISABLED, wb
    assert "NONE enabled" in wb["detail"]
