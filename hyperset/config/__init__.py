"""Layered deployment configuration (hy-7h00, ADR-0035).

`load_settings()` reads the checked-in `config/base.yaml` (ALWAYS loaded, implicit,
never named in `HYPERSET_CONFIG`), deep-merges the ordered `HYPERSET_CONFIG` overlay(s)
over it (a missing named overlay is FATAL, never skipped), and validates the merged
result against the explicit-typed schema, FAIL-CLOSED on any unknown key / wrong type /
missing required / plaintext-in-a-secret-ref / absent overlay. There is NO silent
fallback to demo defaults: the demo is an explicit `config/demo.yaml` overlay a
deployment selects (`HYPERSET_CONFIG=config/demo.yaml`).

Scope of this slice: the loader, merge, and pre-resolution schema validation. Secret
references are validated for SHAPE but not resolved (slice 2, hy-ogg5); no existing call
site is rewired (slice 3, hy-tc4o); the allowlisted env-override layer's full map and
its reject-unknown-`HYPERSET_*` rule are hy-tc4o's; the non-loopback auth-safety rule is
hy-ax9w's. Nothing here changes runtime behaviour until those land -- it is inert,
constructed by tests, not yet by the server.
"""

from __future__ import annotations

import os
from pathlib import Path

from hyperset.config.connection_settings import (
    datahub_token,
    superset_password,
    superset_username,
)
from hyperset.config.context_settings import context_cache_dir
from hyperset.config.db_settings import analytics_db_url
from hyperset.config.feature_settings import (
    pii_action,
    pii_entities,
    pii_guard,
    pii_spacy_model,
)
from hyperset.config.legacy import legacy_env_overlay
from hyperset.config.loader import ConfigError, read_config_file
from hyperset.config.merge import deep_merge
from hyperset.config.playground_settings import (
    playground_agents_json,
    playground_default_agent,
    playground_default_model,
    playground_enabled,
    playground_models_json,
    playground_upstream_base_url,
)
from hyperset.config.provider_settings import (
    embedding_api_key,
    embedding_base_url,
    embedding_dimensions,
    embedding_model,
    embedding_provider,
    model_provider,
    ollama_base_url,
    ollama_max_tokens,
    ollama_model,
    openai_api_key,
    openai_base_url,
    openai_max_completion_tokens,
    openai_model,
    openai_reasoning_effort,
)
from hyperset.config.runtime import (
    active_settings,
    clear_active_settings,
    set_active_settings,
)
from hyperset.config.safety import auth_env_overlay, validate_deployment_safety
from hyperset.config.schema import validate
from hyperset.config.secrets import (
    REDACTED,
    DirSecretProvider,
    Secret,
    SecretProvider,
    redact_settings,
    resolve_secrets,
)

__all__ = [
    "ConfigError",
    "load_settings",
    "overlay_paths",
    "auth_env_overlay",
    "validate_deployment_safety",
    "CONFIG_ENV",
    "BASE_CONFIG",
    "resolve_secrets",
    "redact_settings",
    "Secret",
    "SecretProvider",
    "DirSecretProvider",
    "REDACTED",
    "active_settings",
    "set_active_settings",
    "clear_active_settings",
    "legacy_env_overlay",
    "context_cache_dir",
    "analytics_db_url",
    "model_provider",
    "embedding_provider",
    "embedding_base_url",
    "embedding_model",
    "embedding_api_key",
    "embedding_dimensions",
    "ollama_base_url",
    "ollama_model",
    "ollama_max_tokens",
    "openai_base_url",
    "openai_model",
    "openai_reasoning_effort",
    "openai_max_completion_tokens",
    "openai_api_key",
    "playground_enabled",
    "playground_agents_json",
    "playground_models_json",
    "playground_default_agent",
    "playground_default_model",
    "playground_upstream_base_url",
    "superset_username",
    "superset_password",
    "datahub_token",
    "pii_guard",
    "pii_action",
    "pii_entities",
    "pii_spacy_model",
]

# The env var that names the ORDERED, `:`-separated overlay files layered over base.
# Base is never named here; unset = base only (proposal section 3, #372 one-model).
CONFIG_ENV = "HYPERSET_CONFIG"

BASE_CONFIG = Path(__file__).resolve().parents[2] / "config" / "base.yaml"


def overlay_paths(env: dict) -> list[Path]:
    raw = env.get(CONFIG_ENV, "").strip()
    if not raw:
        return []
    return [Path(name.strip()) for name in raw.split(":") if name.strip()]


def load_settings(*, base_path: Path | None = None, env: dict | None = None) -> dict:
    """The merged, validated settings, or a fatal `ConfigError`."""
    env = os.environ if env is None else env
    merged = read_config_file(BASE_CONFIG if base_path is None else base_path)
    for overlay in overlay_paths(env):
        merged = deep_merge(merged, read_config_file(overlay))
    # Layer 3 (proposal section 3): a MIGRATED legacy `HYPERSET_*` var is honored as an
    # allowlisted override, folded in over the overlays with a one-time deprecation warning
    # (hy-tc4o). Empty until a domain is migrated, so an unmigrated config is unchanged.
    merged = deep_merge(merged, legacy_env_overlay(env))
    settings = validate(merged)
    # Cross-field deployment safety (hy-71mi, ADR-0035 section 5): a non-loopback bind
    # requires auth, and enabling auth requires a complete OIDC verifier. Fail-closed.
    validate_deployment_safety(settings)
    return settings
