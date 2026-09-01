"""The `context` domain's settings, read from the loaded config or the legacy env (hy-tc4o).

The FIRST domain migrated off scattered `os.environ.get` to the settings object (config slice
3). Behaviour-preserving: when startup loaded a config the value comes from it (a legacy env
var was folded in as a deprecation-warned override); when startup has not run -- a unit test, a
non-serving CLI call -- it is the exact old env read, so the same value resolves across
set / unset / default.
"""

from __future__ import annotations

import os
from pathlib import Path

from hyperset.config.runtime import active_settings

CACHE_DIR_ENV = "HYPERSET_CONTEXT_CACHE_DIR"


def _default_cache_dir() -> str:
    # The home-based default is dynamic, so it is computed here rather than declared as a
    # static literal in base.yaml.
    return str(Path.home() / ".cache" / "hyperset" / "git-context")


def context_cache_dir() -> str:
    """The git-context cache directory: the loaded `context.cache_dir`, else the legacy
    `HYPERSET_CONTEXT_CACHE_DIR`, else the home-based default."""
    settings = active_settings()
    if settings is not None:
        configured = settings.get("context", {}).get("cache_dir")
        return configured or _default_cache_dir()
    return os.environ.get(CACHE_DIR_ENV) or _default_cache_dir()
