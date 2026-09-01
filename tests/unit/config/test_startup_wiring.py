"""Wiring the layered config into serve startup (hy-ax9w, ADR-0035 section 5).

The loader/schema/safety were inert; `apply_startup_config` applies them at startup. These are
the acceptance tests the safety+wiring slice owns: a non-loopback bind without auth fails
CLOSED at startup, `auth.enabled` without a complete oidc block fails closed, a loopback bind
is unaffected (the safe default posture), and a valid auth overlay wires HYPERSET_AUTHZ_ENABLED
/ HYPERSET_OIDC_* on. Plus the never-weaken rule (config cannot turn an env-enabled gate off)
and proof that the serve command runs the wiring BEFORE it binds.
"""

from __future__ import annotations

import pytest

from hyperset.config import ConfigError
from hyperset.config.startup import apply_startup_config

_OIDC = {
    "issuer": "https://idp.example.com",
    "audience": "hyperset",
    "jwks_url": "https://idp.example.com/jwks",
}


def _overlay(tmp_path, body: str) -> dict:
    """A fake environment naming a single overlay file over the checked-in base.yaml."""
    path = tmp_path / "overlay.yaml"
    path.write_text(body)
    return {"HYPERSET_CONFIG": str(path)}


def test_the_default_base_config_leaves_the_gate_off_and_loopback_unaffected(tmp_path):
    # No overlay: base.yaml (loopback + auth off). apply_startup_config must not raise, and it
    # derives HYPERSET_AUTHZ_ENABLED=false with no OIDC env -- the server runs exactly as
    # today, unauthenticated on loopback.
    env: dict = {}
    settings = apply_startup_config(env=env)
    assert settings["server"]["bind"] == "loopback"
    assert env["HYPERSET_AUTHZ_ENABLED"] == "false"
    assert not any(key.startswith("HYPERSET_OIDC_") for key in env)


def test_a_valid_auth_overlay_wires_the_gate_and_the_verifier_on(tmp_path):
    env = _overlay(
        tmp_path,
        "auth:\n"
        "  enabled: true\n"
        "  oidc:\n"
        f"    issuer: {_OIDC['issuer']}\n"
        f"    audience: {_OIDC['audience']}\n"
        f"    jwks_url: {_OIDC['jwks_url']}\n",
    )
    apply_startup_config(env=env)
    assert env["HYPERSET_AUTHZ_ENABLED"] == "true"
    assert env["HYPERSET_OIDC_ISSUER"] == _OIDC["issuer"]
    assert env["HYPERSET_OIDC_AUDIENCE"] == _OIDC["audience"]
    assert env["HYPERSET_OIDC_JWKS_URL"] == _OIDC["jwks_url"]


def test_a_non_loopback_bind_without_auth_fails_closed_at_startup(tmp_path):
    env = _overlay(tmp_path, "server:\n  bind: all\nauth:\n  enabled: false\n")
    with pytest.raises(ConfigError) as raised:
        apply_startup_config(env=env)
    assert "server.bind" in str(raised.value) and "auth.enabled" in str(raised.value)
    # Fail closed: the gate env is NOT written when startup aborts.
    assert "HYPERSET_AUTHZ_ENABLED" not in env


def test_auth_enabled_without_a_complete_oidc_block_fails_closed_at_startup(tmp_path):
    env = _overlay(tmp_path, "auth:\n  enabled: true\n  oidc:\n    issuer: https://idp.example\n")
    with pytest.raises(ConfigError) as raised:
        apply_startup_config(env=env)
    assert "auth.oidc" in str(raised.value)
    assert "HYPERSET_AUTHZ_ENABLED" not in env


def test_config_never_turns_an_env_enabled_gate_off(tmp_path):
    # NEVER-WEAKEN: the base config does not enable auth, but the environment already has the
    # gate on (the direct-env path). Wiring the config in must keep it on, never silently
    # unauthenticate a running deployment.
    env = {"HYPERSET_AUTHZ_ENABLED": "true"}
    apply_startup_config(env=env)
    assert env["HYPERSET_AUTHZ_ENABLED"] == "true"


def test_serve_http_runs_the_wiring_before_it_binds(monkeypatch, tmp_path):
    # The serve command must apply (and fail closed on) the config BEFORE binding: an unsafe
    # config aborts startup, and the listener is never reached.
    from hyperset import cli

    monkeypatch.setenv(
        "HYPERSET_CONFIG",
        str(tmp_path / "bad.yaml"),
    )
    (tmp_path / "bad.yaml").write_text("server:\n  bind: all\nauth:\n  enabled: false\n")

    def _must_not_bind(**_kwargs):
        raise AssertionError("serve_http must not be reached when the config fails closed")

    monkeypatch.setattr(cli, "serve_http", _must_not_bind)
    monkeypatch.setattr(cli, "assert_network_bind_authenticated", _must_not_bind)
    args = type("Args", (), {"host": "127.0.0.1", "port": 8080})()
    with pytest.raises(ConfigError):
        cli.cmd_serve_http(args)
