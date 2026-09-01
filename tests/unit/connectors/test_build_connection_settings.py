"""The live-connector build path reads its credentials through the settings object, not a second
raw os.environ path (hy-py62a -- no split-brain).

`connectors/build.py` is the ONE place that maps a connector_type + reference to a connector; it
and the live-lookup transports must resolve the SAME credential. These pin that the loaded config
drives the built connector, that the legacy env still works with no startup, and that the
absent-credential guard is preserved.
"""

from __future__ import annotations

import pytest

from hyperset.config.runtime import set_active_settings
from hyperset.connectors.build import build_datahub_connector, build_superset_connector


def test_superset_build_uses_the_loaded_credentials(monkeypatch):
    monkeypatch.setenv("HYPERSET_SUPERSET_USERNAME", "should-be-ignored")
    monkeypatch.setenv("HYPERSET_SUPERSET_PASSWORD", "should-be-ignored")
    set_active_settings(
        {"connections": {"superset": {"username": "cfg-user", "password": "cfg-pw"}}}
    )
    conn = build_superset_connector("http://superset.example")
    assert conn._client._username == "cfg-user"
    assert conn._client._password == "cfg-pw"


def test_superset_build_without_startup_uses_the_legacy_env(monkeypatch):
    monkeypatch.setenv("HYPERSET_SUPERSET_USERNAME", "env-user")
    monkeypatch.setenv("HYPERSET_SUPERSET_PASSWORD", "env-pw")
    conn = build_superset_connector("http://superset.example")
    assert conn._client._username == "env-user"
    assert conn._client._password == "env-pw"


def test_superset_build_still_refuses_when_credentials_are_absent(monkeypatch):
    for var in ("HYPERSET_SUPERSET_USERNAME", "HYPERSET_SUPERSET_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    set_active_settings({"connections": {}})  # active, but no credentials
    with pytest.raises(ValueError, match="HYPERSET_SUPERSET_USERNAME"):
        build_superset_connector("http://superset.example")


def test_build_keeps_the_30s_sync_default_when_no_timeout_is_given(monkeypatch):
    # The SYNC path passes no override; the connector must keep its 30s batch default unchanged
    # (hq-hnrf area 2 -- only the interactive probe gets the short bound).
    monkeypatch.setenv("HYPERSET_SUPERSET_USERNAME", "u")
    monkeypatch.setenv("HYPERSET_SUPERSET_PASSWORD", "p")
    monkeypatch.setenv("HYPERSET_DATAHUB_TOKEN", "t")
    set_active_settings({"connections": {}})
    assert build_superset_connector("http://superset.example")._client._timeout == 30
    assert build_datahub_connector("http://datahub.example")._client._timeout == 30


def test_build_threads_an_explicit_timeout_override_into_the_connector(monkeypatch):
    # The live admin probe passes a short bound; it must reach the connector's HTTP client.
    monkeypatch.setenv("HYPERSET_SUPERSET_USERNAME", "u")
    monkeypatch.setenv("HYPERSET_SUPERSET_PASSWORD", "p")
    monkeypatch.setenv("HYPERSET_DATAHUB_TOKEN", "t")
    set_active_settings({"connections": {}})
    assert build_superset_connector("http://superset.example", timeout=0.5)._client._timeout == 0.5
    assert build_datahub_connector("http://datahub.example", timeout=0.5)._client._timeout == 0.5


def test_datahub_build_uses_the_loaded_token(monkeypatch):
    monkeypatch.setenv("HYPERSET_DATAHUB_TOKEN", "should-be-ignored")
    set_active_settings({"connections": {"datahub": {"token": "cfg-token"}}})
    conn = build_datahub_connector("http://datahub.example")
    assert conn._client._session.headers.get("Authorization") == "Bearer cfg-token"


def test_datahub_build_without_a_token_sends_no_auth_header(monkeypatch):
    monkeypatch.delenv("HYPERSET_DATAHUB_TOKEN", raising=False)
    set_active_settings({"connections": {}})
    conn = build_datahub_connector("http://datahub.example")
    assert "Authorization" not in conn._client._session.headers
