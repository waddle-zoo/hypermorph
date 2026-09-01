"""Fail-closed deployment safety: a NETWORK bind requires authentication (hy-71mi,
ADR-0035 section 5). This is the root fix for the exposure the admin-write audit named
-- docker-compose runs `serve http --host 0.0.0.0` with authz unset, leaving every
route (including the write-back-config and propose WRITE paths) mutable-unauthenticated
on a network bind. Binding network exposure to the auth flag closes ALL of them at once:
a non-loopback listener that is not authenticated REFUSES TO START.

The rule (overseer ruling on hy-w5ld, hardening hq-l4g2): an actual LOOPBACK bind is the
ONLY way to run unauthenticated. A non-loopback effective bind REQUIRES the authz gate
enabled AND a complete OIDC verifier config (issuer, audience, JWKS URL) at startup, or
the process is FATAL -- there is NO env override that opens an unauthenticated network
bind. An earlier cut carried a `HYPERSET_ALLOW_INSECURE_NETWORK_BIND` warn-and-allow
escape hatch and shipped it in the compose defaults; the overseer removed it, because a
shipped default that binds `0.0.0.0` unauthenticated is exactly the P0 exposure. Loopback
is the break glass; there is no other. A deployment that needs a network bind configures
auth -- full stop.
"""

from __future__ import annotations

import ipaddress

from hyperset.security.authz import ENABLED_ENV
from hyperset.security.oidc import AUDIENCE_ENV, ISSUER_ENV, JWKS_ENV

_TRUTHY = {"1", "true", "yes", "on"}
_LOOPBACK_NAMES = {"localhost", "ip6-localhost"}

# The NARROW, honestly-named topology assertion (Option A, mayor ruling on hy-w5ld,
# overseer notified). A container MUST bind `0.0.0.0` to answer a published port at all
# (hy-voes), so a Docker demo cannot bind loopback in-container. This signal lets the
# operator ASSERT that the port is PUBLISHED ON LOOPBACK ONLY -- the effective exposure is
# loopback even though the in-container bind is `0.0.0.0` -- which is exactly the
# overseer's rule (hq-l4g2: loopback/local = auth-off OK) enforced at the host-publish
# layer. It is MATERIALLY DIFFERENT from the removed blanket `HYPERSET_ALLOW_INSECURE_NETWORK_BIND`:
# (1) it asserts a specific no-LAN-exposure topology rather than "allow insecure anything";
# (2) it does NOT itself publish or expose anything -- whether a port reaches the LAN is
# decided entirely by the compose `ports:` line, which this does not touch;
# (3) the shipped compose that sets it is bound by a test to a `127.0.0.1:`-only publish,
# so the assertion cannot silently diverge from the topology; and
# (4) it applies ONLY to the API/MCP network guard, NEVER to the verifier-less UI proxy,
# which stays loopback-only (`assert_loopback_only` takes no env and ignores this).
LOOPBACK_PUBLISHED_ENV = "HYPERSET_LOOPBACK_PUBLISHED"


class InsecureBindError(RuntimeError):
    """A non-loopback listener asked to start without authentication. Fatal, fail-closed
    -- the process refuses rather than expose an unauthenticated network service."""


def is_loopback_host(host: str) -> bool:
    """Whether `host` binds LOOPBACK only. A hostname `localhost` and every address in
    127.0.0.0/8 or `::1` is loopback; `0.0.0.0`/`::`/a routable address is NOT. An
    unparseable or empty host is treated as NON-loopback -- fail closed toward requiring
    auth rather than assuming safety."""
    cleaned = (host or "").strip().strip("[]").lower()
    if not cleaned:
        return False
    if cleaned in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(cleaned).is_loopback
    except ValueError:
        # A non-loopback hostname (or garbage): fail closed, treat as network-exposed.
        return False


def auth_is_configured(env: dict) -> bool:
    """Whether the authz gate is enabled AND the OIDC verifier is fully configured --
    the precondition for accepting authenticated traffic on a network bind. All three
    OIDC settings must be present; the gate without a verifier can authenticate nothing,
    so it does not satisfy the rule."""
    if env.get(ENABLED_ENV, "").strip().lower() not in _TRUTHY:
        return False
    return all(env.get(name, "").strip() for name in (ISSUER_ENV, AUDIENCE_ENV, JWKS_ENV))


def assert_network_bind_authenticated(host: str, *, surface: str, env: dict) -> None:
    """Refuse to start a NON-LOOPBACK listener that is not authenticated (fail-closed).

    - loopback bind: allowed (the ONLY unconditional unauthenticated path -- localhost
      cannot be reached off the host).
    - non-loopback + authz enabled + full OIDC: allowed.
    - non-loopback + `HYPERSET_LOOPBACK_PUBLISHED` asserted: allowed WITHOUT auth, because
      the operator asserts the port is published on loopback only (a container that must
      bind `0.0.0.0` to answer a host-loopback-published port). This is a topology
      assertion, not a blanket bypass (see `LOOPBACK_PUBLISHED_ENV`).
    - non-loopback + none of the above: `InsecureBindError`, FATAL. The default still
      fails closed -- an unauthenticated LAN bind never ships (overseer ruling hy-w5ld).
    """
    if is_loopback_host(host):
        return
    if auth_is_configured(env):
        return
    if env.get(LOOPBACK_PUBLISHED_ENV, "").strip().lower() in _TRUTHY:
        # The operator asserts a loopback-published topology (Option A). The in-container
        # bind is `0.0.0.0` only so Docker can forward a `127.0.0.1:`-published port; the
        # effective exposure is loopback. This does not relax the UI-proxy loopback-only
        # rule, and it never controls the publish interface itself.
        return
    raise InsecureBindError(
        f"{surface} refuses to bind the non-loopback host {host!r} without "
        f"authentication: set {ENABLED_ENV}=true with {ISSUER_ENV}/{AUDIENCE_ENV}/"
        f"{JWKS_ENV}, bind a loopback host, or -- only when the port is published on "
        f"loopback ONLY -- set {LOOPBACK_PUBLISHED_ENV}. A network bind must not answer "
        "unauthenticated (ADR-0035 section 5, hy-w5ld)."
    )


def assert_loopback_only(host: str, *, surface: str) -> None:
    """Refuse ANY non-loopback bind for a surface that has NO verifier of its own -- the
    playground UI proxy (ADR-0035 section 5, Decision 4; hy-w5ld third listener).

    This is STRICTER than `assert_network_bind_authenticated`: there is no auth
    configuration that makes a non-loopback bind acceptable here, because the proxy does
    not authenticate anything itself. It forwards to the API *and serves local endpoints
    of its own* (the SPA, `/demo/*`), so exposing it on a network answers those local
    endpoints UNAUTHENTICATED no matter what the API behind it enforces. Loopback is the
    only allowed bind; a non-loopback `HYPERSET_UI_HOST` is always fatal."""
    if is_loopback_host(host):
        return
    raise InsecureBindError(
        f"{surface} refuses the non-loopback host {host!r}: it authenticates nothing of "
        "its own and serves local endpoints, so it is LOOPBACK-ONLY. Bind a loopback host "
        "(127.0.0.1). There is no auth configuration that opens it (ADR-0035 section 5)."
    )
