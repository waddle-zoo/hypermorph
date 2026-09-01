"""Build a live connector from a stored connection's non-secret reference (hq-jedd).

A connection row stores only a `config_ref` (a bundle path or a base URL) -- never a
credential. The credential is read from the SERVER ENVIRONMENT at build time and lives
only for the operation, exactly as the sync path does (hy-gh-43). This module is the ONE
place that maps a `connector_type` + reference to a connector instance, so the CLI sync
and the admin connection probe cannot construct connectors two different ways.
"""

from __future__ import annotations

from hyperset.config.connection_settings import (
    datahub_token,
    superset_password,
    superset_username,
)
from hyperset.connectors.datahub import DataHubConnector
from hyperset.connectors.superset import SupersetConnector


def build_superset_connector(source: str, *, timeout: float | None = None) -> SupersetConnector:
    """A Superset connector from a bundle path or a base URL. A URL needs
    HYPERSET_SUPERSET_USERNAME/PASSWORD in the environment; the credential is never
    persisted on the connection. `timeout` overrides the connector's HTTP deadline: the live
    admin probe passes a short bound (hq-hnrf area 2); sync passes None and keeps the 30s
    default. A bundle path reads a local file, so the timeout does not apply."""
    if not source.startswith(("http://", "https://")):
        return SupersetConnector(bundle_path=source)
    username = superset_username()
    password = superset_password()
    if not username or not password:
        raise ValueError(
            "live Superset sync needs HYPERSET_SUPERSET_USERNAME and "
            "HYPERSET_SUPERSET_PASSWORD in the environment; credentials are never "
            "persisted on the connection"
        )
    kwargs = {} if timeout is None else {"timeout": timeout}
    return SupersetConnector(base_url=source, username=username, password=password, **kwargs)


def build_datahub_connector(source: str, *, timeout: float | None = None) -> DataHubConnector:
    """A DataHub connector from a GMS base URL. The optional GMS token is read from
    HYPERSET_DATAHUB_TOKEN in the environment (the pinned local instance runs with auth
    off); a missing token surfaces as the connector's 401/403, not as absent metadata.
    `timeout` overrides the HTTP deadline (short for the live admin probe; None keeps the 30s
    sync default)."""
    if not source.startswith(("http://", "https://")):
        raise ValueError(
            f"DataHub sync needs a GMS base URL, got {source!r}; DataHub has no export-bundle "
            "transport in v0"
        )
    kwargs = {} if timeout is None else {"timeout": timeout}
    return DataHubConnector(base_url=source, token=datahub_token(), **kwargs)


# The connector types this build layer supports, and the builder for each. The admin add
# path validates a `connector_type` against these keys, and the probe/sync build through
# them -- so "which types exist" has one definition, not one per call site.
CONNECTOR_BUILDERS = {
    "superset": build_superset_connector,
    "datahub": build_datahub_connector,
}


def build_connector(connector_type: str, source: str, *, timeout: float | None = None):
    """The connector for a stored connection, or ValueError for an unsupported type or a
    reference its builder rejects. Reused by CLI sync (no `timeout`, keeps the 30s default) and
    the admin probe (a short explicit `timeout` so an interactive probe never hangs, hq-hnrf
    area 2, hq-jedd)."""
    builder = CONNECTOR_BUILDERS.get(connector_type)
    if builder is None:
        raise ValueError(f"connector_type {connector_type!r} has no connector support yet")
    return builder(source, timeout=timeout)
