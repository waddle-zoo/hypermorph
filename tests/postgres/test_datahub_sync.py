"""DataHub GraphQL sync against real Postgres (hy-gh-17 acceptance).

Payloads are the recorded pinned-DataHub-v1.6.0 GraphQL captures
(`tests/fixtures/datahub/v1.6.0/revenue/{baseline,drift}`), served through
`tests.fake_datahub`, so persisted identity, versions, relationships, and
change records are asserted against real source shapes and the URNs the
instance actually served. `tests/compose/test_datahub_live_sync.py` runs the
same code against the running instance.
"""

from __future__ import annotations

import pytest

from hyperset.connectors import run_sync
from hyperset.connectors.datahub import DataHubConnector
from hyperset.connectors.errors import ConnectorAuthError, ConnectorError
from hyperset.repositories.errors import NotFoundError
from hyperset.repositories.postgres import (
    PostgresConnectionRepository,
    PostgresConnectorChangeRepository,
    PostgresGovernedContextRepository,
    PostgresObservedAssetRepository,
    PostgresSyncRepository,
)
from tests.fake_datahub import BASE_URL, FakeDataHubSession, manifest

_MANIFEST = manifest("baseline")
_WAREHOUSE_URN = next(
    urn for urn in _MANIFEST["urns_by_asset_type"]["dataset"] if "dataPlatform:postgres" in urn
)
_BI_URN = next(
    urn for urn in _MANIFEST["urns_by_asset_type"]["dataset"] if "dataPlatform:superset" in urn
)
_DOMAIN_URN = _MANIFEST["urns_by_asset_type"]["domain"][0]
_TERM_URN = _MANIFEST["urns_by_asset_type"]["glossary_term"][0]
_OWNER_URN = next(
    urn for urn in _MANIFEST["urns_by_asset_type"]["corp_user"] if urn.endswith("revenue_owner")
)
_TOTAL_ASSETS = sum(len(urns) for urns in _MANIFEST["urns_by_asset_type"].values())


@pytest.fixture
def connection_id(session_factory):
    return (
        PostgresConnectionRepository(session_factory)
        .create_or_update(
            connector_type="datahub", display_name="Local DataHub (GraphQL)", config_ref=BASE_URL
        )
        .id
    )


def _sync(session, connection_id, session_factory, **kwargs):
    return run_sync(
        connector=DataHubConnector(base_url=BASE_URL, session=session),
        connection_id=connection_id,
        session_factory=session_factory,
        **kwargs,
    )


def _asset(session_factory, connection_id, external_id, asset_type):
    return PostgresObservedAssetRepository(session_factory).get_by_external_id(
        connection_id=connection_id, external_id=external_id, asset_type=asset_type
    )


def _version(session_factory, connection_id, external_id, asset_type):
    return _asset(session_factory, connection_id, external_id, asset_type).current_version


# -- identity and evidence --------------------------------------------------


def test_fresh_seed_syncs_through_the_production_connector_path(session_factory, connection_id):
    result = _sync(FakeDataHubSession(), connection_id, session_factory)

    assert result.transport == "graphql"
    # DataHub, unlike Superset, does disclose its application version.
    assert result.source_version == "v1.6.0"
    assert result.counters == {
        "created": _TOTAL_ASSETS,
        "updated": 0,
        "restored": 0,
        "unchanged": 0,
        "deleted": 0,
    }
    # A GraphQL read is a full refresh of the source, not a fixture import.
    run = PostgresSyncRepository(session_factory).get_run(result.sync_run_id)
    assert run.mode == "full"
    assert run.status == "succeeded"


def test_urns_are_the_persisted_identity_verbatim(session_factory, connection_id):
    _sync(FakeDataHubSession(), connection_id, session_factory)

    assets = PostgresObservedAssetRepository(session_factory)
    for external_id, asset_type in (
        (_WAREHOUSE_URN, "dataset"),
        (_BI_URN, "dataset"),
        (_DOMAIN_URN, "domain"),
        (_TERM_URN, "glossary_term"),
        (_OWNER_URN, "corp_user"),
    ):
        record = assets.get_by_external_id(
            connection_id=connection_id, external_id=external_id, asset_type=asset_type
        )
        # No re-derived key: the URN the source served is the identity stored.
        assert record.external_id == external_id


def test_domain_owner_glossary_and_lineage_evidence_is_persisted(session_factory, connection_id):
    _sync(FakeDataHubSession(), connection_id, session_factory)

    warehouse = _version(session_factory, connection_id, _WAREHOUSE_URN, "dataset")
    assert warehouse.normalized["domain_urn"] == _DOMAIN_URN
    assert warehouse.normalized["owner_urns"] == [_OWNER_URN]
    assert warehouse.normalized["glossary_term_urns"] == [_TERM_URN]
    assert warehouse.normalized["column_names"] == [
        "order_id",
        "customer_id",
        "order_date",
        "status",
        "gross_amount",
        "tax_amount",
        "currency",
    ]

    # Lineage is read only from the edge DataHub actually asserted.
    bi = _version(session_factory, connection_id, _BI_URN, "dataset")
    assert bi.normalized["upstream_dataset_urns"] == [_WAREHOUSE_URN]

    term = _version(session_factory, connection_id, _TERM_URN, "glossary_term")
    assert term.normalized["name"] == "Recognized Revenue"
    assert "non-refunded orders" in term.normalized["definition"]

    domain = _version(session_factory, connection_id, _DOMAIN_URN, "domain")
    assert domain.normalized["name"] == "Revenue"


def test_the_whole_graphql_response_is_retained_in_the_raw_payload(session_factory, connection_id):
    _sync(FakeDataHubSession(), connection_id, session_factory)

    warehouse = _version(session_factory, connection_id, _WAREHOUSE_URN, "dataset")
    # Fields the normalizer never reads survive verbatim for the curator.
    assert warehouse.raw_payload["schemaMetadata"]["fields"][0]["nativeDataType"] == "string"
    assert warehouse.raw_payload["properties"]["customProperties"] == [
        {"key": "warehouse_schema", "value": "public"},
        {"key": "warehouse_table", "value": "finance_orders_daily"},
    ]


def test_a_field_graphql_returned_as_null_is_never_inferred(session_factory, connection_id):
    _sync(FakeDataHubSession(), connection_id, session_factory)

    bi = _version(session_factory, connection_id, _BI_URN, "dataset")
    # The pinned instance served no Status aspect and no schema for the
    # Superset-side dataset. `removed` stays None rather than becoming False,
    # and no column list is invented.
    assert bi.raw_payload["status"] is None
    assert bi.normalized["removed"] is None
    assert bi.raw_payload["schemaMetadata"] is None
    assert bi.normalized["column_names"] == []
    # `lastModified.time` is 0 upstream, which is not a modification time.
    assert bi.raw_payload["properties"]["lastModified"]["time"] == 0
    assert _asset(session_factory, connection_id, _BI_URN, "dataset").source_modified_at is None


def test_cross_source_identifiers_are_observed_but_never_merged(session_factory, connection_id):
    result = _sync(FakeDataHubSession(), connection_id, session_factory)

    bi = _version(session_factory, connection_id, _BI_URN, "dataset")
    # The Superset UUID is recorded as source evidence...
    assert bi.normalized["custom_properties"]["superset_dataset_uuid"] == (
        "ae48881d-334f-54a7-94e8-1ffcc73866e2"
    )
    # ...and the connector says out loud that it did not resolve it.
    assert any("cross-source mapping is not performed here" in w for w in result.warnings)
    # Observation only: no governed context was created by a sync.
    assert PostgresGovernedContextRepository(session_factory).list_all() == []


# -- relationships ----------------------------------------------------------


def test_explicit_relationships_resolve_without_unresolved_link_warnings(
    session_factory, connection_id
):
    result = _sync(FakeDataHubSession(), connection_id, session_factory)

    assert [w for w in result.warnings if "unresolved link" in w] == []


@pytest.mark.postgres
def test_the_references_datahub_declares_are_persisted(session_factory, connection_id):
    """The pinned v1.6.0 captures declare domain, ownership, glossary-term and
    one lineage edge explicitly, and until hy-d7xh every one of them was
    resolved by `run_sync` and then dropped.

    Asserted per dataset rather than in aggregate, because which dataset
    declares which reference is the whole point: lineage runs BI -> warehouse
    and not the other way, and only the warehouse side carries an owner."""
    result = _sync(FakeDataHubSession(), connection_id, session_factory)

    assets = PostgresObservedAssetRepository(session_factory)

    def observed(external_id: str, asset_type: str) -> str:
        return assets.get_by_external_id(
            connection_id=connection_id, external_id=external_id, asset_type=asset_type
        ).id

    warehouse_targets: dict[str, set[str]] = {}
    for row in assets.list_relationships(from_asset_id=observed(_WAREHOUSE_URN, "dataset")):
        warehouse_targets.setdefault(row.relation, set()).add(row.to_asset_id)

    assert result.relationships > 0
    assert warehouse_targets["in_domain"] == {observed(_DOMAIN_URN, "domain")}
    assert warehouse_targets["has_glossary_term"] == {observed(_TERM_URN, "glossary_term")}
    assert warehouse_targets["owned_by"] == {observed(_OWNER_URN, "corp_user")}
    # Nothing upstream of the warehouse table was asserted, so nothing is
    # claimed: the projection carries the edge DataHub declared and no reverse.
    assert "derived_from" not in warehouse_targets
    # Lineage proximity comes from the source's own upstream declaration, never
    # from name similarity: the BI dataset is derived from the warehouse one.
    assert [
        (row.relation, row.to_asset_id)
        for row in assets.list_relationships(from_asset_id=observed(_BI_URN, "dataset"))
    ] == [
        ("derived_from", observed(_WAREHOUSE_URN, "dataset")),
        ("in_domain", observed(_DOMAIN_URN, "domain")),
    ]


# -- idempotence and change detection ---------------------------------------


def test_unchanged_resync_is_a_no_op(session_factory, connection_id):
    _sync(FakeDataHubSession(), connection_id, session_factory)
    result = _sync(FakeDataHubSession(), connection_id, session_factory)

    assert result.counters == {
        "created": 0,
        "updated": 0,
        "restored": 0,
        "unchanged": _TOTAL_ASSETS,
        "deleted": 0,
    }
    assert PostgresConnectorChangeRepository(session_factory).list_for_run(result.sync_run_id) == []


def test_one_controlled_change_produces_one_version_and_one_connector_change(
    session_factory, connection_id
):
    _sync(FakeDataHubSession("baseline"), connection_id, session_factory)
    result = _sync(FakeDataHubSession("drift"), connection_id, session_factory)

    assert result.updated == [_TERM_URN]
    assert result.counters["unchanged"] == _TOTAL_ASSETS - 1
    assert result.counters["deleted"] == 0

    changes = PostgresConnectorChangeRepository(session_factory).list_for_run(result.sync_run_id)
    assert len(changes) == 1
    assert (
        changes[0].asset_id == _asset(session_factory, connection_id, _TERM_URN, "glossary_term").id
    )
    assert changes[0].change_type == "updated"

    # The prior observation is still there: versions are append-only.
    term = _version(session_factory, connection_id, _TERM_URN, "glossary_term")
    assert term.version == 2
    assert "including refunded orders" in term.normalized["definition"]

    # Datasets reference the term by URN only, so editing its definition
    # upstream did not restate the two datasets that carry it.
    assert _version(session_factory, connection_id, _WAREHOUSE_URN, "dataset").version == 1


def test_restoring_the_definition_is_observed_as_a_further_version_not_a_rollback(
    session_factory, connection_id
):
    _sync(FakeDataHubSession("baseline"), connection_id, session_factory)
    _sync(FakeDataHubSession("drift"), connection_id, session_factory)
    # Restoring upstream produced a response identical to the baseline.
    result = _sync(FakeDataHubSession("baseline"), connection_id, session_factory)

    assert result.updated == [_TERM_URN]
    term = _version(session_factory, connection_id, _TERM_URN, "glossary_term")
    assert term.version == 3
    assert (
        len(PostgresConnectorChangeRepository(session_factory).list_for_run(result.sync_run_id))
        == 1
    )


# -- failure never implies deletion or approval -----------------------------


def test_an_unauthorized_read_fails_the_run_and_deletes_nothing(session_factory, connection_id):
    _sync(FakeDataHubSession(), connection_id, session_factory)

    session = FakeDataHubSession(status_overrides={"hypersetScrollEntities": 403})
    with pytest.raises(ConnectorAuthError):
        _sync(session, connection_id, session_factory)

    assets = PostgresObservedAssetRepository(session_factory)
    warehouse = assets.get_by_external_id(
        connection_id=connection_id, external_id=_WAREHOUSE_URN, asset_type="dataset"
    )
    assert warehouse.deleted_at is None
    runs = PostgresSyncRepository(session_factory).list_runs(connection_id)
    assert [run.status for run in runs] == ["succeeded", "failed"]


def test_a_graphql_level_error_is_a_failure_not_absent_metadata(session_factory, connection_id):
    _sync(FakeDataHubSession(), connection_id, session_factory)

    session = FakeDataHubSession(graphql_errors={"hypersetGlossaryTerm": "boom"})
    with pytest.raises(ConnectorError):
        _sync(session, connection_id, session_factory)

    # HTTP 200 with an `errors` array must not be read as "the term is gone".
    term = PostgresObservedAssetRepository(session_factory).get_by_external_id(
        connection_id=connection_id, external_id=_TERM_URN, asset_type="glossary_term"
    )
    assert term.deleted_at is None


def test_a_partial_sync_never_implies_deletion(session_factory, connection_id):
    _sync(FakeDataHubSession(), connection_id, session_factory)

    session = FakeDataHubSession(missing_urns=frozenset({_TERM_URN}))
    result = _sync(session, connection_id, session_factory, partial=True)

    assert result.deleted == []
    term = PostgresObservedAssetRepository(session_factory).get_by_external_id(
        connection_id=connection_id, external_id=_TERM_URN, asset_type="glossary_term"
    )
    assert term.deleted_at is None


def test_an_entity_the_index_lists_but_gms_will_not_serve_is_reported_not_observed(
    session_factory, connection_id
):
    result = _sync(
        FakeDataHubSession(missing_urns=frozenset({_TERM_URN})), connection_id, session_factory
    )

    assert any("not served by GMS at read time" in w and _TERM_URN in w for w in result.warnings)
    with pytest.raises(NotFoundError):
        PostgresObservedAssetRepository(session_factory).get_by_external_id(
            connection_id=connection_id, external_id=_TERM_URN, asset_type="glossary_term"
        )


# -- checkpoint -------------------------------------------------------------


def test_checkpoint_records_the_projection_contract_and_survives_restart(
    session_factory, connection_id
):
    first = _sync(FakeDataHubSession(), connection_id, session_factory)
    fingerprint = first.checkpoint["projection_fingerprint"]
    assert first.checkpoint["read_mode"] == "full_refresh"
    assert first.checkpoint["source_version"] == "v1.6.0"
    assert first.checkpoint["asset_counts"]["dataset"] == 2
    assert first.checkpoint["resumed_from_projection_fingerprint"] is None

    # A fresh connector instance -- as after a container restart -- resumes
    # from the persisted checkpoint and keeps the same identities.
    second = _sync(FakeDataHubSession(), connection_id, session_factory)
    assert second.checkpoint["resumed_from_projection_fingerprint"] == fingerprint
    assert second.counters["unchanged"] == _TOTAL_ASSETS
    assert (
        PostgresSyncRepository(session_factory).get_checkpoint(connection_id) == second.checkpoint
    )
