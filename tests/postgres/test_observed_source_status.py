"""Per-source observed-status overview (hq-hnrf area 2).

Every configured observed-evidence source is a DISTINCT record (a connection), and an
operator sees, per source, whether it is CONFIGURED, REACHABLE, and FRESH -- with the
impact and recovery -- from RECORDED state (no live probe). Source identity is retained;
this reports integration health, never governed Git authority.
"""

from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from hyperset.ops.observed_status import read_observed_source_status
from hyperset.repositories.postgres import (
    PostgresConnectionRepository,
    PostgresSyncRepository,
)
from hyperset.repositories.scope import ALL_WORKSPACES
from hyperset.transport.http import build_server


@pytest.fixture
def server_url(session_factory, monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    server = build_server(session_factory=session_factory, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _connection(session_factory, *, display_name, health=None, sync=None, workspace="default"):
    """A connection, optionally with a recorded health and a last finished sync.
    `sync` is None (never synced), "succeeded", or "failed"."""
    conns = PostgresConnectionRepository(session_factory)
    record = conns.create_or_update(
        connector_type="superset",
        display_name=display_name,
        config_ref="https://superset.example/",
        workspace=workspace,
    )
    if health is not None:
        conns.record_health(record.id, status=health, workspace=workspace)
    if sync is not None:
        syncs = PostgresSyncRepository(session_factory)
        run = syncs.begin_run(record.id, mode="full")
        if sync == "succeeded":
            syncs.finish_run(run.id, counters={"created": 1})
        else:
            syncs.fail_run(run.id, errors=["the source rejected the read"])
    return record


@pytest.mark.postgres
def test_each_source_reports_configured_reachable_fresh_with_recovery(server_url, session_factory):
    ready = _connection(session_factory, display_name="prod", health="healthy", sync="succeeded")
    degraded = _connection(session_factory, display_name="staging", health="healthy", sync=None)
    # A recent FAILED sync on a healthy/reachable connector is STALE, not fresh (the exact
    # recorded-vs-live divergence adversary round 1 named): it must read degraded, not ready.
    failed = _connection(session_factory, display_name="failing", health="healthy", sync="failed")
    blocked = _connection(session_factory, display_name="broken", health="unhealthy")
    unknown = _connection(session_factory, display_name="new", health=None)

    with urllib.request.urlopen(f"{server_url}/admin/api/v0/observed-sources/status") as response:
        body = json.loads(response.read())
    by_id = {s["connection_id"]: s for s in body["sources"]}
    assert set(by_id) == {ready.id, degraded.id, failed.id, blocked.id, unknown.id}

    # A healthy connector whose last sync FAILED is reachable but not fresh -> degraded.
    f = by_id[failed.id]
    assert (f["status"], f["reachable"], f["fresh"]) == ("degraded", True, False)
    assert f["last_sync_status"] == "failed" and "run a sync" in f["recovery"]

    # READY: configured + reachable + fresh, no recovery needed.
    r = by_id[ready.id]
    assert (r["status"], r["configured"], r["reachable"], r["fresh"]) == ("ready", True, True, True)
    assert r["display_name"] == "prod" and r["connector_type"] == "superset"  # identity retained
    assert r["recovery"] == ""

    # DEGRADED: reachable but never freshly synced -> run a sync.
    d = by_id[degraded.id]
    assert (d["status"], d["reachable"], d["fresh"]) == ("degraded", True, False)
    assert "run a sync" in d["recovery"]

    # BLOCKED: the last probe found it unreachable -> check URL/credential.
    b = by_id[blocked.id]
    assert (b["status"], b["reachable"], b["fresh"]) == ("blocked", False, False)
    assert "reachable" in b["recovery"]

    # UNKNOWN: never probed -> reachability not yet known, probe it.
    u = by_id[unknown.id]
    assert (u["status"], u["reachable"]) == ("unknown", None)
    assert "probe" in u["recovery"]


@pytest.mark.postgres
def test_observed_status_is_workspace_scoped_and_fail_closed(session_factory):
    _connection(session_factory, display_name="alpha-src", health="healthy", workspace="alpha")
    _connection(session_factory, display_name="beta-src", health="healthy", workspace="beta")

    alpha = read_observed_source_status(session_factory, workspace="alpha")
    assert [s.display_name for s in alpha] == ["alpha-src"]  # only its own source
    beta = read_observed_source_status(session_factory, workspace="beta")
    assert [s.display_name for s in beta] == ["beta-src"]
    # The system opt-in sees both; a concrete tenant never sees a sibling.
    everyone = read_observed_source_status(session_factory, workspace=ALL_WORKSPACES)
    assert {"alpha-src", "beta-src"} <= {s.display_name for s in everyone}

    # Fail-closed: the scope is required, never a silent global read by omission.
    with pytest.raises(TypeError):
        read_observed_source_status(session_factory)
