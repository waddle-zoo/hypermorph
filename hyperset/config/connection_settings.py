"""The `connections` domain credentials, read from the loaded config or the legacy env
(hy-py62a, config slice 3e).

Fifth domain migrated off scattered `os.environ.get` (config slice 3). The live-connector
build path (`connectors/build.py`) and the live detail-lookup (`flywheel/live_lookup.py`) read
the SAME resolved credential, so a sync and a lookup cannot authenticate two different ways.

ALL three are SECRET (the schema types `connections.superset.username`/`.password` and
`connections.datahub.token` as `Ref`): each travels as a `${env:...}`/`${secret:...}` reference
in the config and is revealed HERE, never sitting in the config, a log, or an echo as plaintext.
Behaviour-preserving: once startup ran the settings object is authoritative (a migrated legacy
var was folded into it as a reference); otherwise it is the exact prior env read.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from hyperset.config.runtime import active_settings
from hyperset.config.secrets import reveal_secret

SUPERSET_USERNAME_ENV = "HYPERSET_SUPERSET_USERNAME"
SUPERSET_PASSWORD_ENV = "HYPERSET_SUPERSET_PASSWORD"
DATAHUB_TOKEN_ENV = "HYPERSET_DATAHUB_TOKEN"


def _settings_secret(*path):
    """The revealed value at a nested settings path, or None -- only when startup loaded a
    config and the (secret-typed) path is set. `reveal_secret` unwraps the write-only `Secret`;
    a plain value or None passes through."""
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
    return reveal_secret(node)


def _env(source: Mapping[str, str] | None):
    return os.environ if source is None else source


def superset_username(env: Mapping[str, str] | None = None) -> str | None:
    """The Superset username: the loaded `connections.superset.username` (revealed), else the
    legacy `HYPERSET_SUPERSET_USERNAME`, else None."""
    configured = _settings_secret("connections", "superset", "username")
    if configured is not None:
        return configured
    # Mirror the prior os.environ.get(NAME): None when ABSENT, the value (even "") when present.
    return _env(env).get(SUPERSET_USERNAME_ENV)


def superset_password(env: Mapping[str, str] | None = None) -> str | None:
    """The Superset password: the loaded `connections.superset.password` (revealed), else the
    legacy `HYPERSET_SUPERSET_PASSWORD`, else None."""
    configured = _settings_secret("connections", "superset", "password")
    if configured is not None:
        return configured
    return _env(env).get(SUPERSET_PASSWORD_ENV)


def datahub_token(env: Mapping[str, str] | None = None) -> str | None:
    """The DataHub GMS token: the loaded `connections.datahub.token` (revealed), else the legacy
    `HYPERSET_DATAHUB_TOKEN`, else None (the pinned local instance runs with auth off)."""
    configured = _settings_secret("connections", "datahub", "token")
    if configured is not None:
        return configured
    return _env(env).get(DATAHUB_TOKEN_ENV)
