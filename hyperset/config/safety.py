"""The deployment-safety cross-field validation (hy-71mi, ADR-0035 section 5), applied
after the per-field schema. Two rules, both FAIL-CLOSED:

1. `auth.enabled: true` requires a COMPLETE `auth.oidc` block (issuer, audience,
   jwks_url) -- you cannot enable the gate without a verifier.
2. A NON-LOOPBACK effective bind (`server.bind: all`) REQUIRES `auth.enabled: true`
   (and therefore rule 1's complete oidc), or the config is refused -- a config must not
   expose a non-loopback API without a verifier. (`server.ui_bind` is loopback-only in
   the schema already, closing the playground UI proxy.)

Plus `auth_env_overlay`: the mapping from the validated `auth.*` config to the
`HYPERSET_AUTHZ_ENABLED` / `HYPERSET_OIDC_*` env the code reads today, so the wiring
slice can thread the toggle through without a runtime change here (this only computes
the mapping; it sets nothing).
"""

from __future__ import annotations

from hyperset.config.loader import ConfigError

_OIDC_REQUIRED = ("issuer", "audience", "jwks_url")
_OIDC_ENV = {
    "issuer": "HYPERSET_OIDC_ISSUER",
    "audience": "HYPERSET_OIDC_AUDIENCE",
    "jwks_url": "HYPERSET_OIDC_JWKS_URL",
    "roles_claim": "HYPERSET_OIDC_ROLES_CLAIM",
}


def validate_deployment_safety(settings: dict) -> None:
    """Refuse an unsafe bind/auth combination, fail-closed (raises `ConfigError`)."""
    auth = settings.get("auth") or {}
    server = settings.get("server") or {}
    enabled = bool(auth.get("enabled"))
    oidc = auth.get("oidc") or {}

    if enabled:
        missing = [key for key in _OIDC_REQUIRED if not str(oidc.get(key) or "").strip()]
        if missing:
            raise ConfigError(
                "auth.enabled is true but auth.oidc is incomplete (missing "
                f"{', '.join(missing)}); the gate cannot be enabled without a verifier"
            )

    if server.get("bind") == "all" and not enabled:
        raise ConfigError(
            "server.bind is 'all' (a non-loopback network bind) but auth.enabled is not "
            "true: a network bind must require authentication (ADR-0035 section 5). Set "
            "auth.enabled with a complete auth.oidc block, or bind loopback"
        )


def auth_env_overlay(settings: dict) -> dict[str, str]:
    """The `HYPERSET_AUTHZ_ENABLED`/`HYPERSET_OIDC_*` mapping for the validated auth
    config -- computed only, applied by the wiring slice. `HYPERSET_AUTHZ_ENABLED` is
    always present (true/false); each OIDC env appears only when its config value is set,
    so an unset field does not clobber an existing env with an empty string."""
    auth = settings.get("auth") or {}
    oidc = auth.get("oidc") or {}
    overlay = {"HYPERSET_AUTHZ_ENABLED": "true" if auth.get("enabled") else "false"}
    for key, env_name in _OIDC_ENV.items():
        value = str(oidc.get(key) or "").strip()
        if value:
            overlay[env_name] = value
    return overlay
