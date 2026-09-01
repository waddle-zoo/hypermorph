"""The `playground` domain, read from the loaded config or the legacy env (hy-lk83s, slice 3d).

Fourth domain migrated off scattered `os.environ.get` (config slice 3). The playground UI
process (`playground/ui/app.py`) and the server's `_playground_enabled` gate read the SAME
resolved values, so the demo surface cannot disagree with itself. Behaviour-preserving: once
startup loaded a config the settings object is authoritative (a mapped legacy var was folded
into it); otherwise it is the exact prior env read.

`enabled` folds through the legacy shim NORMALIZED to `true`/`false` (LEGACY_BOOL_ENV_MAP): its
schema home is `Bool`, so the lenient legacy read (`1`/`true`/`yes`) is canonicalized before it
reaches validation. Folding is REQUIRED, not optional: config/base.yaml sets
`playground.enabled: false`, so once startup loaded the config the accessor would see
configured-false and a non-folded `HYPERSET_PLAYGROUND_ENABLED=yes` could never re-enable the
playground (hy-lk83s adversary). The accessor's own lenient env parse still covers the
no-startup path (a CLI/test that never ran apply_startup_config).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping

from hyperset.config.runtime import active_settings

PLAYGROUND_ENABLED_ENV = "HYPERSET_PLAYGROUND_ENABLED"
PLAYGROUND_AGENTS_JSON_ENV = "HYPERSET_PLAYGROUND_AGENTS_JSON"
PLAYGROUND_MODELS_JSON_ENV = "HYPERSET_PLAYGROUND_MODELS_JSON"
PLAYGROUND_DEFAULT_AGENT_ENV = "HYPERSET_PLAYGROUND_DEFAULT_AGENT"
PLAYGROUND_DEFAULT_MODEL_ENV = "HYPERSET_PLAYGROUND_DEFAULT_MODEL"
# The UI proxy's upstream Hyperset API target (proposal section 9 maps HYPERSET_HTTP_BASE_URL
# to playground.upstream_base_url).
UPSTREAM_BASE_URL_ENV = "HYPERSET_HTTP_BASE_URL"

_ENABLED_TRUE = {"1", "true", "yes"}


def _settings_get(*path):
    """A value at a nested settings path, or None -- only when startup has loaded a config."""
    settings = active_settings()
    if settings is None:
        return None
    node = settings
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
        if node is None:
            return None
    return node


def _env(source: Mapping[str, str] | None):
    return os.environ if source is None else source


def playground_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the demo playground surface is served: the loaded `playground.enabled` (a bool,
    which after startup includes a normalized `HYPERSET_PLAYGROUND_ENABLED` folded over the
    base default), else -- with no startup -- the lenient legacy `HYPERSET_PLAYGROUND_ENABLED`
    (`1`/`true`/`yes`). See the module docstring for why folding is required."""
    configured = _settings_get("playground", "enabled")
    if configured is not None:
        return bool(configured)
    return str(_env(env).get(PLAYGROUND_ENABLED_ENV, "")).strip().lower() in _ENABLED_TRUE


def _json_blob(path_key: str, env_var: str, env: Mapping[str, str] | None) -> str:
    """A JSON blob the caller `json.loads`: the loaded `playground.<path_key>` (a string as
    folded from the legacy env, or a native structure re-serialized), else the raw legacy env
    string, else ''. Behaviour-preserving for the env path; a YAML-native value is serialized
    so the caller's `json.loads` still consumes it."""
    configured = _settings_get("playground", path_key)
    if configured is not None:
        return configured if isinstance(configured, str) else json.dumps(configured)
    return str(_env(env).get(env_var, "")).strip()


def playground_agents_json(env: Mapping[str, str] | None = None) -> str:
    return _json_blob("agents", PLAYGROUND_AGENTS_JSON_ENV, env)


def playground_models_json(env: Mapping[str, str] | None = None) -> str:
    return _json_blob("models", PLAYGROUND_MODELS_JSON_ENV, env)


def playground_default_agent(env: Mapping[str, str] | None = None) -> str:
    configured = _settings_get("playground", "default_agent")
    if configured is not None:
        return str(configured)
    return str(_env(env).get(PLAYGROUND_DEFAULT_AGENT_ENV, "")).strip()


def playground_default_model(env: Mapping[str, str] | None = None) -> str:
    configured = _settings_get("playground", "default_model")
    if configured is not None:
        return str(configured)
    return str(_env(env).get(PLAYGROUND_DEFAULT_MODEL_ENV, "")).strip()


def playground_upstream_base_url(env: Mapping[str, str] | None = None) -> str | None:
    """The UI proxy's upstream Hyperset API target: the loaded `playground.upstream_base_url`,
    else the legacy `HYPERSET_HTTP_BASE_URL`, else None (ABSENT) so the caller applies its
    default. A PRESENT-but-empty legacy value returns '' -- NOT None -- because the prior read
    (`os.environ.get(name, default)`) used the default only when the var was ABSENT; a
    present-empty value produced an empty base, and that is preserved (hy-lk83s adversary)."""
    configured = _settings_get("playground", "upstream_base_url")
    if configured is not None:
        return str(configured)
    source = _env(env)
    if UPSTREAM_BASE_URL_ENV in source:
        return str(source[UPSTREAM_BASE_URL_ENV])
    return None
