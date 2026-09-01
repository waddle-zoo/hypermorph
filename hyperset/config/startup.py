"""Wire the layered deployment config into the serving process (hy-ax9w, ADR-0035 section 5).

The loader/schema/safety (config slice 1, hy-7h00, plus the `auth.*` section and the
bind/auth safety rules) were INERT -- constructed by tests, not the server. This applies them
at STARTUP: every network listener (`serve http` and `serve mcp --http`) loads the ONE
validated config, FAILS CLOSED on an unsafe bind/auth combination, and derives the
`HYPERSET_AUTHZ_ENABLED` / `HYPERSET_OIDC_*` environment the authz gate and OIDC verifier
already read -- so a valid auth overlay wires the gate on, and a config that would expose a
non-loopback API without a verifier cannot boot.

NEVER-WEAKEN, fail-closed: applying the config can only turn the gate ON or leave it as it is.
If the config does not enable auth but the environment already has it on, the on value is
kept -- wiring the config in must never silently unauthenticate a running deployment. The
GLOBAL DEFAULT stays OFF (`base.yaml` `auth.enabled: false`, loopback); flipping it on for a
production deployment is a separate, human-gated decision (hy-nt89 / hy-ia9n), not this slice.
"""

from __future__ import annotations

import os

from hyperset.config import load_settings, resolve_secrets
from hyperset.config.runtime import set_active_settings
from hyperset.config.safety import auth_env_overlay
from hyperset.security.authz import ENABLED_ENV

# Matches `authz_enabled()`'s parsing, so "already on" here means exactly what the gate reads.
_TRUTHY = {"1", "true", "yes", "on"}


def apply_startup_config(*, env: dict | None = None) -> dict:
    """Load + validate the layered config and apply the auth env overlay to `env`, or FAIL
    CLOSED. Returns the validated settings.

    Raises `ConfigError` (aborting startup, never a warning) on any schema failure, a missing
    named overlay, or an unsafe bind/auth combination -- a non-loopback `server.bind` without
    `auth.enabled` + a complete `auth.oidc` block, or `auth.enabled` without one. The overlay
    maps `auth.*` to the env the code reads today; it NEVER turns the gate off (see module
    docstring), and it only sets an OIDC env when the config carries a value, so an unset field
    cannot clobber an env an operator set directly."""
    env = os.environ if env is None else env
    settings = load_settings(env=env)
    # Resolve secret references into write-only in-memory Secrets (slice 2, hy-ogg5) so a
    # migrated secret-bearing read reveals a value from the settings object instead of reading
    # a plaintext env var (hy-2562h). A config with no secret ref -- the default -- resolves to
    # itself; an unresolved ref is fatal here, fail-closed, before anything binds.
    settings = resolve_secrets(settings, env=env)
    # Record the loaded settings as the process's active config (hy-tc4o) so migrated domain
    # accessors read the layered config instead of a scattered os.environ.get. Set once, here,
    # at the same point the serve entrypoints run before binding.
    set_active_settings(settings)
    overlay = auth_env_overlay(settings)
    if overlay.get(ENABLED_ENV) != "true" and _authz_already_on(env):
        # The config did not enable the gate, but the environment already has it on. Keep it
        # on: wiring the config in must not weaken an authenticated deployment.
        overlay[ENABLED_ENV] = str(env[ENABLED_ENV])
    env.update(overlay)
    return settings


def _authz_already_on(env: dict) -> bool:
    return str(env.get(ENABLED_ENV, "")).strip().lower() in _TRUTHY
