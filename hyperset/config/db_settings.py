"""DB-domain settings, read from the loaded config or the legacy env (hy-2562h).

`system_db` (the REQUIRED Postgres DSN) is `db.engine.database_url_from_env`; this holds
`analytics_db` -- the OPTIONAL warehouse the readiness overview reports and the provider probe
connects to (connect-only; no warehouse SQL in v0). Both are SECRET -- the whole URL embeds a
password -- so each travels as a `${env:...}`/`${secret:...}` reference in the config and is
revealed here, never sitting in the config, a log, or an echo as plaintext.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from hyperset.config.runtime import active_settings
from hyperset.config.secrets import reveal_secret

ANALYTICS_DB_URL_ENV = "HYPERSET_ANALYTICS_DB_URL"


def analytics_db_url(env: Mapping[str, str] | None = None) -> str | None:
    """The analytics warehouse URL: the loaded `connections.analytics_db.url` (revealed), else
    the legacy `HYPERSET_ANALYTICS_DB_URL`, else None when unconfigured. Behaviour-preserving:
    once startup ran the settings object is authoritative; otherwise it is the exact env read."""
    settings = active_settings()
    if settings is not None:
        configured = settings.get("connections", {}).get("analytics_db", {}).get("url")
        return reveal_secret(configured) if configured is not None else None
    source = os.environ if env is None else env
    raw = str(source.get(ANALYTICS_DB_URL_ENV, "")).strip()
    return raw or None
