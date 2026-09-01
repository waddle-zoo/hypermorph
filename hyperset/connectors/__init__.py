"""Read-only source connectors: Superset over export bundles and REST
(hy-gh-27), DataHub over GraphQL (hy-gh-17).

With two real sources in hand, the only abstraction they proved they share
is `Connector` in `hyperset.connectors.types` -- snapshot, normalize, name
the transport. A shared connector SDK stays out of v0: auth, discovery,
identity, and link shapes generalized between these two not at all.
"""

from hyperset.connectors.errors import BundleParseError, ConnectorAuthError, ConnectorError
from hyperset.connectors.sync import SyncResult, run_sync
from hyperset.connectors.types import (
    ConnectionTest,
    Connector,
    ConnectorSnapshot,
    EstablishedDenominator,
    ObservedAssetInput,
    UnresolvedLink,
)

__all__ = [
    "ConnectorError",
    "ConnectorAuthError",
    "BundleParseError",
    "ConnectionTest",
    "Connector",
    "ConnectorSnapshot",
    "EstablishedDenominator",
    "ObservedAssetInput",
    "UnresolvedLink",
    "SyncResult",
    "run_sync",
]
