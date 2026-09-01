"""The `features` / PII domain, read from the loaded config or the legacy env (hy-e16vx,
config slice 3f).

Sixth domain migrated off scattered `os.environ.get` (config slice 3). The PII guard's master
switch and its action/entities/spaCy-model settings read the settings object, so the two
write-back boundaries the guard sits on resolve one configuration. Behaviour-preserving: once
startup loaded a config the settings object is authoritative (a mapped legacy var was folded in);
otherwise it is the exact prior env read.

`pii_guard` folds NORMALIZED to `true`/`false` (via legacy.py's LEGACY_BOOL_ENV_MAP) because its
schema home is `Bool` and config/base.yaml sets `features.pii_guard: false` -- an unfolded env
could never ENGAGE the guard once startup loaded the config. The historical read is lenient
(`on`/`1`/`true`/`yes`), so `PII_GUARD_ENGAGED_VALUES` is the single source shared by the fold and
this module's no-startup parse.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from hyperset.config.runtime import active_settings

PII_GUARD_ENV = "HYPERSET_PII_GUARD"
PII_ACTION_ENV = "HYPERSET_PII_ACTION"
PII_ENTITIES_ENV = "HYPERSET_PII_ENTITIES"
PII_SPACY_MODEL_ENV = "HYPERSET_PII_SPACY_MODEL"

# The values that ENGAGE the guard, matching the historical env read. Shared with legacy.py's
# normalized fold so the active-startup-path and the no-startup reads agree on what "on" means.
PII_GUARD_ENGAGED_VALUES = frozenset({"on", "1", "true", "yes"})


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


def pii_guard(env: Mapping[str, str] | None = None) -> bool:
    """Whether the PII guard is ENGAGED: the loaded `features.pii_guard` (a bool, which after
    startup includes a normalized `HYPERSET_PII_GUARD` folded over the base default), else --
    with no startup -- the lenient legacy read (`on`/`1`/`true`/`yes`)."""
    configured = _settings_get("features", "pii_guard")
    if configured is not None:
        return bool(configured)
    return str(_env(env).get(PII_GUARD_ENV, "")).strip().lower() in PII_GUARD_ENGAGED_VALUES


def pii_action(env: Mapping[str, str] | None = None) -> str | None:
    """The PII action (`block`/`redact`) selector: the loaded `features.pii.action`, else the
    legacy `HYPERSET_PII_ACTION`. Mirrors os.environ.get: None when ABSENT, the value when
    present; the caller lower-cases and compares to 'block' exactly as before."""
    configured = _settings_get("features", "pii", "action")
    if configured is not None:
        return str(configured)
    return _env(env).get(PII_ACTION_ENV)


def pii_entities(env: Mapping[str, str] | None = None) -> str | None:
    """The comma-separated PII entity list: the loaded `features.pii.entities`, else the legacy
    `HYPERSET_PII_ENTITIES` (None when absent); the caller splits it exactly as before."""
    configured = _settings_get("features", "pii", "entities")
    if configured is not None:
        return str(configured)
    return _env(env).get(PII_ENTITIES_ENV)


def pii_spacy_model(env: Mapping[str, str] | None = None) -> str | None:
    """The spaCy model override: the loaded `features.pii.spacy_model`, else the legacy
    `HYPERSET_PII_SPACY_MODEL` (None when absent); the caller applies the pinned default."""
    configured = _settings_get("features", "pii", "spacy_model")
    if configured is not None:
        return str(configured)
    return _env(env).get(PII_SPACY_MODEL_ENV)
