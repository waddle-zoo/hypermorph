"""Admin management of observed-evidence CONNECTIONS (hq-jedd, slice 6).

Real server, real Postgres, real Superset export bundles as the connection source
(no network). Proves the admin manage surface over the EXISTING Connection model --
list / add / update / enable-disable / remove -- and a bounded LIVE PROBE that reports
configured/reachable/fresh honestly (green liveness is not readiness). Secrets never
touch a connection row (credentials live in the server environment), so no response
carries a secret. Observed evidence keeps its source identity: a connection with
observed assets is disable-only, never deleted.
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from hyperset.connectors.types import ConnectionTest
from hyperset.db.models import Connection, ObservedAsset
from hyperset.transport.http import build_server
from tests.postgres.test_cli import _write_bundle_zip
from tests.postgres.test_interactive_review import _get, _post

CONN = "/admin/api/v0/connections"


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


@pytest.mark.postgres
def test_connection_crud_and_no_plaintext_secret(server_url, session_factory, tmp_path):
    bundle = _write_bundle_zip(tmp_path)

    # ADD.
    status, payload = _post(
        f"{server_url}{CONN}",
        {"connector_type": "superset", "display_name": "Prod Superset", "config_ref": bundle},
    )
    assert status == 200, payload
    conn = payload["connection"]
    conn_id = conn["id"]
    assert conn["connector_type"] == "superset" and conn["enabled"] is True
    # No secret field is ever present (credentials live in the server environment).
    assert not any(k in json.dumps(payload) for k in ("config_encrypted", "password", "token"))

    # A credential-bearing config_ref is refused.
    status, payload = _post(
        f"{server_url}{CONN}",
        {"connector_type": "superset", "display_name": "x", "config_ref": "https://u:tok@h/api"},
    )
    assert status == 400 and "must not embed credentials" in payload["error"]["message"]

    # LIST.
    status, payload = _get(f"{server_url}{CONN}")
    assert status == 200
    assert conn_id in {c["id"] for c in payload["connections"]}
    assert "config_encrypted" not in json.dumps(payload)

    # UPDATE (same id) renames without duplicating.
    status, payload = _post(
        f"{server_url}{CONN}",
        {
            "id": conn_id,
            "connector_type": "superset",
            "display_name": "Renamed",
            "config_ref": bundle,
        },
    )
    assert status == 200 and payload["connection"]["display_name"] == "Renamed"
    status, payload = _get(f"{server_url}{CONN}")
    assert len([c for c in payload["connections"] if c["id"] == conn_id]) == 1

    # DISABLE then ENABLE.
    status, payload = _post(f"{server_url}{CONN}/enable", {"id": conn_id, "enabled": False})
    assert status == 200 and payload["connection"]["enabled"] is False
    status, payload = _post(f"{server_url}{CONN}/enable", {"id": conn_id, "enabled": True})
    assert status == 200 and payload["connection"]["enabled"] is True

    # REMOVE a connection with no observed assets -> ok.
    status, payload = _post(f"{server_url}{CONN}/remove", {"id": conn_id})
    assert status == 200 and payload["removed"] == conn_id
    status, payload = _get(f"{server_url}{CONN}")
    assert conn_id not in {c["id"] for c in payload["connections"]}


@pytest.mark.postgres
def test_remove_refuses_a_connection_with_observed_assets(server_url, session_factory, tmp_path):
    bundle = _write_bundle_zip(tmp_path)
    status, payload = _post(
        f"{server_url}{CONN}",
        {"connector_type": "superset", "display_name": "Has evidence", "config_ref": bundle},
    )
    conn_id = payload["connection"]["id"]
    # Seed one observed asset (governed evidence keyed on the connection).
    with session_factory() as session, session.begin():
        session.add(ObservedAsset(connection_id=conn_id, external_id="ds-1", asset_type="dataset"))

    status, payload = _post(f"{server_url}{CONN}/remove", {"id": conn_id})
    assert status == 400 and "cannot be removed" in payload["error"]["message"]
    # It is still there (disable-only), and the list flags it so the UI pre-disables Remove.
    status, payload = _get(f"{server_url}{CONN}")
    row = next(c for c in payload["connections"] if c["id"] == conn_id)
    assert row["has_observed_assets"] is True


@pytest.mark.postgres
def test_two_connections_probe_independently_and_surface_degraded(
    server_url, session_factory, tmp_path
):
    """A reachable-but-never-synced source is DEGRADED (green liveness != readiness); an
    unreachable one is BLOCKED. Two connections report independently."""
    good_bundle = _write_bundle_zip(tmp_path)
    status, payload = _post(
        f"{server_url}{CONN}",
        {"connector_type": "superset", "display_name": "reachable", "config_ref": good_bundle},
    )
    reachable_id = payload["connection"]["id"]
    status, payload = _post(
        f"{server_url}{CONN}",
        {
            "connector_type": "superset",
            "display_name": "missing",
            "config_ref": str(tmp_path / "does-not-exist.zip"),
        },
    )
    blocked_id = payload["connection"]["id"]

    # Reachable bundle, no sync yet -> DEGRADED (honest, not green).
    status, payload = _post(f"{server_url}{CONN}/probe", {"id": reachable_id})
    assert status == 200, payload
    assert payload["probe"]["status"] == "degraded"
    assert payload["probe"]["reachable"] is True and payload["probe"]["fresh"] is False
    assert payload["probe"]["impact"] and payload["probe"]["recovery"]

    # Missing bundle -> BLOCKED, independently.
    status, payload = _post(f"{server_url}{CONN}/probe", {"id": blocked_id})
    assert payload["probe"]["status"] == "blocked"
    assert payload["probe"]["reachable"] is False

    # The probe recorded each connection's health independently.
    status, payload = _get(f"{server_url}{CONN}")
    by_id = {c["id"]: c for c in payload["connections"]}
    assert by_id[reachable_id]["health_status"] == "healthy"
    assert by_id[blocked_id]["health_status"] == "unhealthy"


@pytest.mark.postgres
def test_probe_and_list_redact_a_credential_in_connector_output(
    server_url, session_factory, monkeypatch
):
    """hq-jedd round 2 (critic): a credential-bearing URL in the connector's output must be
    redacted at BOTH layers -- the probe response, AND the persisted health_detail served by
    the list -- with the canonical detector, mirroring #431's last_error test."""
    from hyperset.ops import connection_probe

    status, payload = _post(
        f"{server_url}{CONN}",
        {"connector_type": "superset", "display_name": "leaky", "config_ref": "https://s.int/api"},
    )
    assert status == 200
    cid = payload["connection"]["id"]

    # Force test_connection to fail with a CREDENTIAL-bearing detail (build is looked up at
    # call time, so monkeypatching the module attribute takes effect).
    leak = "connection refused for https://u:ghp_PROBELEAK@s.int/api"
    monkeypatch.setattr(
        connection_probe,
        "build_connector",
        lambda t, s, *, timeout=None: SimpleNamespace(
            test_connection=lambda: ConnectionTest(ok=False, detail=leak)
        ),
    )
    status, payload = _post(f"{server_url}{CONN}/probe", {"id": cid})
    assert status == 200
    # (a) the probe RESPONSE carries no credential, host kept.
    assert "ghp_PROBELEAK" not in json.dumps(payload)
    assert payload["probe"]["status"] == "blocked"
    assert "s.int/api" in payload["probe"]["reason"]
    # (b) the PERSISTED-then-served health_detail on the LIST is redacted too.
    status, payload = _get(f"{server_url}{CONN}")
    detail = next(c for c in payload["connections"] if c["id"] == cid)["health_detail"]
    assert "ghp_PROBELEAK" not in detail
    assert "s.int/api" in detail

    # config_ref redaction on READ: a pre-existing credential-bearing ref (e.g. an older row
    # the current add path would reject) is redacted by the view.
    with session_factory() as session, session.begin():
        session.add(
            Connection(
                connector_type="superset",
                display_name="legacy",
                config_ref="https://u:ghp_CFGLEAK@s.int/api",
            )
        )
    status, payload = _get(f"{server_url}{CONN}")
    assert "ghp_CFGLEAK" not in json.dumps(payload)
