"""The fail-closed network-bind auth guard (hy-71mi, hardened by hy-w5ld, ADR-0035
section 5): a non-loopback listener refuses to start unless auth is fully configured OR
the narrow loopback-published topology is asserted (`HYPERSET_LOOPBACK_PUBLISHED`, Option
A). The removed blanket `HYPERSET_ALLOW_INSECURE_NETWORK_BIND` override stays gone. This is
the root fix for the admin-write exposure: `serve http --host 0.0.0.0` with authz unset
left the write-back-config and propose WRITE paths mutable-unauthenticated on a LAN bind.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from hyperset.security.authz import ENABLED_ENV
from hyperset.security.deployment import (
    InsecureBindError,
    assert_network_bind_authenticated,
    auth_is_configured,
    is_loopback_host,
)
from hyperset.security.oidc import AUDIENCE_ENV, ISSUER_ENV, JWKS_ENV

# The env name of the escape hatch the overseer REMOVED (hy-w5ld). Kept as a literal here,
# not imported, precisely because the code constant no longer exists: the regression below
# asserts that setting this env does NOT open an unauthenticated network bind.
_REMOVED_OVERRIDE_ENV = "HYPERSET_ALLOW_INSECURE_NETWORK_BIND"

_FULL_AUTH = {
    ENABLED_ENV: "true",
    ISSUER_ENV: "https://idp.example.com",
    AUDIENCE_ENV: "hyperset",
    JWKS_ENV: "https://idp.example.com/jwks",
}

COMPOSE = pathlib.Path(__file__).resolve().parents[3] / "docker-compose.yml"


# --- is_loopback_host --------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "127.5.4.3", "::1", "[::1]", "localhost", "LOCALHOST", "ip6-localhost"],
)
def test_loopback_hosts_are_recognised(host):
    assert is_loopback_host(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.0.0.5", "example.com", "", "   ", "garbage"])
def test_non_loopback_and_unparseable_hosts_are_not_loopback(host):
    # An unparseable/empty host fails CLOSED -- treated as network-exposed, never
    # assumed safe (the whole point is to require auth when we cannot prove loopback).
    assert is_loopback_host(host) is False


# --- auth_is_configured ------------------------------------------------------


def test_auth_is_configured_needs_the_flag_and_all_three_oidc_settings():
    assert auth_is_configured(_FULL_AUTH) is True
    # The gate on without a verifier authenticates nothing -> not configured.
    assert auth_is_configured({ENABLED_ENV: "true"}) is False
    # A complete verifier with the gate OFF is not "configured" either (nothing enforces).
    assert auth_is_configured({k: v for k, v in _FULL_AUTH.items() if k != ENABLED_ENV}) is False
    # Each single missing OIDC setting individually breaks it (each is load-bearing).
    for drop in (ISSUER_ENV, AUDIENCE_ENV, JWKS_ENV):
        assert auth_is_configured({k: v for k, v in _FULL_AUTH.items() if k != drop}) is False


# --- assert_network_bind_authenticated ---------------------------------------


def test_loopback_bind_is_allowed_with_no_auth():
    # Loopback is the ONLY unauthenticated path (localhost is unreachable off the host).
    assert assert_network_bind_authenticated("127.0.0.1", surface="serve http", env={}) is None


def test_non_loopback_without_auth_refuses_to_start():
    with pytest.raises(InsecureBindError) as raised:
        assert_network_bind_authenticated("0.0.0.0", surface="serve http", env={})
    msg = str(raised.value)
    assert "0.0.0.0" in msg and "serve http" in msg
    # The remedy is named (configure auth or bind loopback), not just the refusal.
    assert ENABLED_ENV in msg


def test_non_loopback_with_full_auth_is_allowed():
    assert (
        assert_network_bind_authenticated("0.0.0.0", surface="serve http", env=dict(_FULL_AUTH))
        is None
    )


def test_non_loopback_with_gate_on_but_incomplete_oidc_still_refuses():
    # The gate flag alone is NOT enough -- a verifier-less network bind is still refused.
    env = {ENABLED_ENV: "true", ISSUER_ENV: "https://idp.example.com"}
    with pytest.raises(InsecureBindError):
        assert_network_bind_authenticated("0.0.0.0", surface="serve http", env=env)


def test_the_removed_insecure_override_does_not_open_a_network_bind():
    """THE RULING (hy-w5ld): the old `HYPERSET_ALLOW_INSECURE_NETWORK_BIND` warn-and-allow
    escape hatch is GONE. Setting it -- with any truthy value -- must NOT bypass the
    fail-closed rule: a non-loopback bind without auth still raises. Loopback is the only
    break glass."""
    for value in ("1", "true", "yes", "on", "TRUE"):
        with pytest.raises(InsecureBindError):
            assert_network_bind_authenticated(
                "0.0.0.0", surface="serve http", env={_REMOVED_OVERRIDE_ENV: value}
            )


# --- HYPERSET_LOOPBACK_PUBLISHED: the narrow Option-A topology signal (hy-w5ld) -------


def test_loopback_published_signal_permits_a_container_0000_bind_without_auth():
    # Option A: the operator asserts the port is published on loopback only, so a container
    # that must bind 0.0.0.0 to serve it is safe. Permitted WITHOUT auth.
    from hyperset.security.deployment import LOOPBACK_PUBLISHED_ENV

    for value in ("1", "true", "yes", "on"):
        assert (
            assert_network_bind_authenticated(
                "0.0.0.0", surface="serve http", env={LOOPBACK_PUBLISHED_ENV: value}
            )
            is None
        )


def test_loopback_published_signal_only_the_truthy_values():
    from hyperset.security.deployment import LOOPBACK_PUBLISHED_ENV

    for value in ("", "0", "false", "no", "off"):
        with pytest.raises(InsecureBindError):
            assert_network_bind_authenticated(
                "0.0.0.0", surface="serve http", env={LOOPBACK_PUBLISHED_ENV: value}
            )


def test_default_without_the_signal_still_fails_closed():
    # THE ADVERSARY CHECK: the signal is OPT-IN. With it unset, a 0.0.0.0 bind without auth
    # still raises -- adding the signal did not weaken the default fail-closed posture.
    with pytest.raises(InsecureBindError):
        assert_network_bind_authenticated("0.0.0.0", surface="serve http", env={})


def test_the_signal_does_not_relax_the_loopback_only_ui_proxy():
    # THE ADVERSARY CHECK: the loopback-published signal is scoped to the API/MCP network
    # guard. The verifier-less UI proxy stays loopback-only -- it takes no env and cannot be
    # opened by the signal (it serves local endpoints, so a network bind is never safe).
    from hyperset.security.deployment import assert_loopback_only

    with pytest.raises(InsecureBindError):
        assert_loopback_only("0.0.0.0", surface="playground ui")
    # Even with the signal set in the process environment, the UI proxy refuses.
    import os

    os.environ["HYPERSET_LOOPBACK_PUBLISHED"] = "1"
    try:
        with pytest.raises(InsecureBindError):
            assert_loopback_only("0.0.0.0", surface="playground ui")
    finally:
        del os.environ["HYPERSET_LOOPBACK_PUBLISHED"]


# --- CLI wiring: the serve entrypoints enforce the guard before binding -------


def test_cmd_serve_http_enforces_the_guard_before_serving(monkeypatch):
    """The guard must run BEFORE `serve_http` binds -- so a refused bind never opens a
    listener. Monkeypatch the guard to raise and assert `serve_http` is never reached."""
    import argparse

    from hyperset import cli

    called = {"serve": False}
    monkeypatch.setattr(cli, "serve_http", lambda **_: called.__setitem__("serve", True))
    monkeypatch.setattr(
        cli, "_serving_session_factory", lambda: (_ for _ in ()).throw(AssertionError("too late"))
    )

    def _refuse(host, *, surface, env):
        raise InsecureBindError("refused")

    monkeypatch.setattr(cli, "assert_network_bind_authenticated", _refuse)
    with pytest.raises(InsecureBindError):
        cli.cmd_serve_http(argparse.Namespace(host="0.0.0.0", port=8080))
    assert called["serve"] is False, "serve_http bound a listener after the guard refused"


def test_cmd_serve_mcp_http_enforces_the_guard_before_serving(monkeypatch):
    import argparse

    from hyperset import cli

    called = {"serve": False}
    monkeypatch.setattr(cli, "serve_streamable_http", lambda **_: called.__setitem__("serve", True))
    monkeypatch.setattr(
        cli, "_serving_session_factory", lambda: (_ for _ in ()).throw(AssertionError("too late"))
    )

    def _refuse(host, *, surface, env):
        raise InsecureBindError("refused")

    monkeypatch.setattr(cli, "assert_network_bind_authenticated", _refuse)
    with pytest.raises(InsecureBindError):
        cli.cmd_serve_mcp(argparse.Namespace(host="0.0.0.0", port=8010, http=True))
    assert called["serve"] is False


# --- Compose: the shipped default ships NO insecure override and fails closed --


def test_shipped_compose_carries_no_insecure_override():
    """The overseer P0 (hy-w5ld): the shipped compose must NOT set the insecure override
    on its `0.0.0.0` services -- a shipped default may never bind an unauthenticated
    network service. Reds if anyone re-adds the escape hatch to make `make up` start."""
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    for name in ("api", "mcp-http"):
        env = services[name].get("environment") or {}
        assert _REMOVED_OVERRIDE_ENV not in env, (
            f"{name} re-introduces the removed insecure network-bind override; a shipped "
            "default must fail closed on a 0.0.0.0 bind, not ship an unauthenticated service"
        )


def test_shipped_compose_starts_via_the_loopback_published_signal_not_via_auth():
    """Option A (hy-w5ld): the shipped demo compose STARTS -- its `0.0.0.0`-binding
    services pass the guard because they assert the loopback-published topology, NOT
    because they configure auth. Behavioral: run the guard with each service's real env."""
    from hyperset.security.deployment import LOOPBACK_PUBLISHED_ENV

    services = yaml.safe_load(COMPOSE.read_text())["services"]
    for name in ("api", "mcp-http"):
        svc = services[name]
        command = svc["command"]
        assert "0.0.0.0" in command, f"{name} no longer binds 0.0.0.0 -- update this guard"
        host = command[command.index("--host") + 1]
        env = svc.get("environment") or {}
        # It is NOT auth that lets it start -- it is the topology assertion.
        assert auth_is_configured(env) is False, f"{name} unexpectedly ships auth configured"
        assert str(env.get(LOOPBACK_PUBLISHED_ENV, "")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        # The guard permits (returns None) -- the service starts.
        assert assert_network_bind_authenticated(host, surface=f"compose {name}", env=env) is None


def test_any_loopback_published_service_publishes_only_on_127_0_0_1():
    """THE ADVERSARY-FACING BINDING (hy-w5ld, Option A): the loopback-published signal is a
    topology ASSERTION, and this test forces the shipped compose to honor it. Any service
    that sets HYPERSET_LOOPBACK_PUBLISHED MUST publish every port on 127.0.0.1 ONLY -- never
    a bare `PORT:` or `0.0.0.0:PORT:` mapping, which would be LAN-reachable. So the signal
    can never ship alongside a LAN publish: if someone asserts loopback-published while
    exposing the port to the network, this reds. This is what makes the signal materially
    different from a blanket bypass -- the assertion is bound to the real topology."""
    from hyperset.security.deployment import LOOPBACK_PUBLISHED_ENV

    services = yaml.safe_load(COMPOSE.read_text())["services"]
    asserted = []
    for name, svc in services.items():
        env = svc.get("environment") or {}
        if str(env.get(LOOPBACK_PUBLISHED_ENV, "")).strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            continue
        asserted.append(name)
        for mapping in svc.get("ports") or []:
            # A published mapping is "HOST_IP:HOST_PORT:CONTAINER_PORT" or "HOST:CONTAINER".
            # Loopback-only requires the explicit 127.0.0.1 host-ip prefix.
            assert str(mapping).startswith("127.0.0.1:"), (
                f"{name} asserts {LOOPBACK_PUBLISHED_ENV} but publishes {mapping!r} to a "
                "non-loopback interface; a loopback-published service must bind the publish "
                "to 127.0.0.1 only, or it is LAN-reachable and the assertion is false"
            )
    # The demo services actually use the signal (guards against the test passing vacuously).
    assert set(asserted) == {"api", "mcp-http"}, (
        f"unexpected loopback-published services: {asserted}"
    )


# --- assert_loopback_only: the verifier-less UI proxy (hy-w5ld third listener) -------


def test_loopback_only_allows_loopback():
    from hyperset.security.deployment import assert_loopback_only

    assert assert_loopback_only("127.0.0.1", surface="playground ui") is None
    assert assert_loopback_only("::1", surface="playground ui") is None
    assert assert_loopback_only("localhost", surface="playground ui") is None


def test_loopback_only_refuses_a_non_loopback_bind():
    from hyperset.security.deployment import assert_loopback_only

    with pytest.raises(InsecureBindError) as raised:
        assert_loopback_only("0.0.0.0", surface="playground ui")
    assert "0.0.0.0" in str(raised.value) and "playground ui" in str(raised.value)


def test_loopback_only_is_stricter_than_the_network_guard_even_with_full_auth():
    # THE ADR-0035 Decision-4 property: unlike assert_network_bind_authenticated, NO auth
    # configuration opens a non-loopback bind for the verifier-less UI proxy. Full OIDC env
    # would satisfy the network guard, but the UI proxy still refuses -- it authenticates
    # nothing itself and serves local endpoints.
    from hyperset.security.deployment import (
        assert_loopback_only,
        assert_network_bind_authenticated,
    )

    # The network guard accepts 0.0.0.0 with full auth ...
    assert (
        assert_network_bind_authenticated("0.0.0.0", surface="serve http", env=dict(_FULL_AUTH))
        is None
    )
    # ... but the loopback-only guard takes no env at all and refuses regardless.
    with pytest.raises(InsecureBindError):
        assert_loopback_only("0.0.0.0", surface="playground ui")


def test_playground_ui_main_fails_closed_on_a_non_loopback_host(monkeypatch):
    """THE REGRESSION (hy-w5ld): `HYPERSET_UI_HOST=0.0.0.0` without a loopback bind must
    refuse to start -- the proxy's local endpoints are never exposed on a network. The
    guard runs BEFORE the server is constructed, proven by the never-called sentinel."""
    from playground.ui import app

    constructed: list = []
    monkeypatch.setattr(
        app, "ThreadingHTTPServer", lambda *a, **k: constructed.append(a) or object()
    )
    monkeypatch.setenv("HYPERSET_UI_HOST", "0.0.0.0")
    with pytest.raises(InsecureBindError):
        app.main()
    assert constructed == [], "the UI server was constructed despite a non-loopback host"


def test_playground_ui_main_binds_a_loopback_host(monkeypatch):
    # The loopback default (or an explicit 127.0.0.1) is allowed: the guard passes and the
    # server is constructed. Stub serve_forever so the test does not block.
    from playground.ui import app

    class _Server:
        def __init__(self, *a, **k):
            pass

        def serve_forever(self):
            pass

    monkeypatch.setattr(app, "ThreadingHTTPServer", _Server)
    monkeypatch.setenv("HYPERSET_UI_HOST", "127.0.0.1")
    assert app.main() is None
