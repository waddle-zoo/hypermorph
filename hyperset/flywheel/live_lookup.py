"""A live-lookup body-read: a referenced asset's body, read-only (hy-jg2v).

The deferred half of ADR 0024 decision 2. When the authoring agent needs an
asset it does not already hold in full, it reads that asset's DEFINITION/METADATA
body -- never runs the asset's query against the warehouse. The read boundary is
structural, not careful:

- The only outbound calls this path can make are `SupersetRestClient.detail`
  (an HTTP GET) and `DataHubGraphQLClient.entity` (a GraphQL query POST with no
  mutation method on the client). No warehouse connection is constructed here,
  and there is no method on either transport that runs SQL. So
  `execution.performed_by_hyperset` -- a `ContextBundle` field this path never
  builds -- cannot be flipped true (v0-foundation invariant 6).
- The body-read happens ONLY when the held observation does not already carry
  the body in full. On a full-payload observation (v0's connectors ingest
  whole) the body is returned from the store and NO live call is issued: "need
  = consumption" is a property of this code path, not agent discretion.

Writing a freshly-read body back as a new `ObservedAssetVersion` (ADR 0024
decision 2's "observation refresh") is NOT done here: that goes through the
connector's sole-writer `run_sync`/`upsert` path and is estate-scale
observation machinery owned by hy-op9p. This function only READS.
"""

from __future__ import annotations

from typing import Protocol

from hyperset.repositories.dto import ConnectionRecord
from hyperset.repositories.postgres import (
    PostgresConnectionRepository,
    PostgresObservedAssetRepository,
)


class ReadOnlyTransport(Protocol):
    """What a live-lookup needs of a connector: fetch one asset's body, read
    only. A conforming transport exposes no writer -- the type is the boundary."""

    def fetch_body(
        self, *, asset_type: str, external_id: str, raw_payload: dict
    ) -> dict | None: ...


def body_for_reference(
    *,
    connection_id: str,
    external_id: str,
    asset_type: str,
    session_factory,
    transport_factory=None,
) -> dict:
    """The referenced asset's body, held or freshly read.

    Returns `{"held": bool, "source": "observed"|"live_lookup", "body": dict}`.
    `held` is True when the stored observation already carries the full
    `raw_payload`; then no live call is made. Otherwise the body is read from the
    source through a read-only transport. `transport_factory(connection)` is the
    injection seam for tests; the default builds a read-only connector client.
    """
    assets = PostgresObservedAssetRepository(session_factory)
    asset = assets.get_by_external_id(
        connection_id=connection_id, external_id=external_id, asset_type=asset_type
    )
    version = asset.current_version
    held = version.raw_payload if version is not None else {}
    if held:
        # Already consumed: the store holds the body in full, so reading it is
        # the whole lookup and no source call is warranted.
        return {"held": True, "source": "observed", "body": held}

    connection = PostgresConnectionRepository(session_factory).get(connection_id)
    build = transport_factory or _default_transport
    transport = build(connection)
    body = transport.fetch_body(asset_type=asset_type, external_id=external_id, raw_payload=held)
    return {"held": False, "source": "live_lookup", "body": body}


def _default_transport(connection: ConnectionRecord) -> ReadOnlyTransport:
    """A read-only transport for one connection, built from its base URL and the
    same environment credentials the CLI sync uses -- never from anything
    persisted on the connection (the secret-ref boundary). Its concrete type is
    one of the connectors' read-only clients, so it CANNOT run SQL."""
    if connection.connector_type == "superset":
        return _SupersetReadOnly(connection)
    if connection.connector_type == "datahub":
        return _DataHubReadOnly(connection)
    raise ValueError(f"connector_type {connection.connector_type!r} has no live-lookup transport")


class _SupersetReadOnly:
    """Superset detail-read, GET only. Built lazily so construction needs no
    credentials until a body is actually fetched."""

    def __init__(self, connection: ConnectionRecord) -> None:
        self._connection = connection

    def fetch_body(self, *, asset_type: str, external_id: str, raw_payload: dict) -> dict | None:
        from hyperset.config.connection_settings import superset_password, superset_username
        from hyperset.connectors.superset.rest import SupersetRestClient

        username = superset_username()
        password = superset_password()
        if not username or not password:
            raise ValueError(
                "a live Superset lookup needs HYPERSET_SUPERSET_USERNAME and "
                "HYPERSET_SUPERSET_PASSWORD; credentials are never persisted on the connection"
            )
        client = SupersetRestClient(
            base_url=self._connection.config_ref, username=username, password=password
        )
        # Superset's detail endpoint is keyed by the numeric id, which the
        # reference carries in its stored locator; `external_id` is the stable
        # uuid used for identity.
        native_id = raw_payload.get("id", external_id)
        return client.detail(asset_type, native_id)


class _DataHubReadOnly:
    """DataHub entity-read, a GraphQL query only. The connector owns the per-type
    query documents, so this defers to it rather than restating them."""

    def __init__(self, connection: ConnectionRecord) -> None:
        self._connection = connection

    def fetch_body(self, *, asset_type: str, external_id: str, raw_payload: dict) -> dict | None:
        from hyperset.config.connection_settings import datahub_token
        from hyperset.connectors.datahub.connector import projections
        from hyperset.connectors.datahub.graphql import DataHubGraphQLClient

        client = DataHubGraphQLClient(base_url=self._connection.config_ref, token=datahub_token())
        for _entity_type, a_type, root_field, query in projections():
            if a_type == asset_type:
                return client.entity(query, root_field, urn=external_id)
        raise ValueError(f"datahub has no live-lookup projection for {asset_type!r}")
