"""The cross-field deployment-safety validation (hy-71mi, ADR-0035 section 5): a
non-loopback config bind must require an enabled, fully-configured auth gate, and the
gate cannot be enabled without a complete verifier. Plus the auth env overlay mapping the
validated config to the env the code reads today.
"""

from __future__ import annotations

import pytest

from hyperset.config import ConfigError
from hyperset.config.safety import auth_env_overlay, validate_deployment_safety

_OIDC = {
    "issuer": "https://idp.example.com",
    "audience": "hyperset",
    "jwks_url": "https://idp.example.com/jwks",
}


# --- validate_deployment_safety ----------------------------------------------


def test_loopback_bind_with_auth_off_is_accepted():
    # The safe default posture (base.yaml): loopback + auth off. No raise.
    validate_deployment_safety({"server": {"bind": "loopback"}, "auth": {"enabled": False}})


def test_non_loopback_bind_with_auth_off_is_refused():
    with pytest.raises(ConfigError) as raised:
        validate_deployment_safety({"server": {"bind": "all"}, "auth": {"enabled": False}})
    msg = str(raised.value)
    assert "server.bind" in msg and "auth.enabled" in msg


def test_auth_enabled_without_complete_oidc_is_refused():
    with pytest.raises(ConfigError) as raised:
        validate_deployment_safety({"auth": {"enabled": True, "oidc": {"issuer": "x"}}})
    msg = str(raised.value)
    assert "auth.oidc" in msg
    # It names WHICH settings are missing (audience, jwks_url), not just "incomplete".
    assert "audience" in msg and "jwks_url" in msg


def test_each_missing_oidc_field_individually_refuses_when_enabled():
    # Every one of the three is load-bearing: dropping any single field is refused.
    for drop in ("issuer", "audience", "jwks_url"):
        oidc = {k: v for k, v in _OIDC.items() if k != drop}
        with pytest.raises(ConfigError):
            validate_deployment_safety({"auth": {"enabled": True, "oidc": oidc}})


def test_non_loopback_bind_with_enabled_complete_auth_is_accepted():
    validate_deployment_safety(
        {"server": {"bind": "all"}, "auth": {"enabled": True, "oidc": dict(_OIDC)}}
    )


# --- auth_env_overlay --------------------------------------------------------


def test_overlay_always_emits_the_enabled_flag():
    assert auth_env_overlay({"auth": {"enabled": True}})["HYPERSET_AUTHZ_ENABLED"] == "true"
    assert auth_env_overlay({"auth": {"enabled": False}})["HYPERSET_AUTHZ_ENABLED"] == "false"
    # Absent auth section => default-off.
    assert auth_env_overlay({})["HYPERSET_AUTHZ_ENABLED"] == "false"


def test_overlay_maps_set_oidc_fields_and_omits_unset_ones():
    overlay = auth_env_overlay(
        {"auth": {"enabled": True, "oidc": {**_OIDC, "roles_claim": "roles"}}}
    )
    assert overlay["HYPERSET_OIDC_ISSUER"] == _OIDC["issuer"]
    assert overlay["HYPERSET_OIDC_AUDIENCE"] == _OIDC["audience"]
    assert overlay["HYPERSET_OIDC_JWKS_URL"] == _OIDC["jwks_url"]
    assert overlay["HYPERSET_OIDC_ROLES_CLAIM"] == "roles"


def test_overlay_omits_an_unset_oidc_field_rather_than_emitting_empty():
    # An unset field must NOT appear as an empty-string env that would clobber an
    # existing value -- absent means absent.
    overlay = auth_env_overlay({"auth": {"enabled": False, "oidc": {"issuer": "x"}}})
    assert overlay["HYPERSET_OIDC_ISSUER"] == "x"
    assert "HYPERSET_OIDC_AUDIENCE" not in overlay
    assert "HYPERSET_OIDC_ROLES_CLAIM" not in overlay
