"""Soft-deletion is default-deny: a full sync deletes only what a snapshot
holds an `EstablishedDenominator` for, and says so out loud when it declines
(hy-6nit).

Both arms live here on purpose. With no connector producing a denominator
yet, every natural test in this suite asserts "nothing was deleted" -- and
that whole suite would pass unchanged against a gate hard-coded to refuse.
So the discriminating test is the one that hands a run a denominator for one
asset type and withholds it for another, in the same sync: the warranted type
must be soft-deleted and the unwarranted one must survive. A gate whose
permitting branch no test reaches is not known to be a gate.

The rule is connector-agnostic and is exercised on both v0 connectors, because
neither can establish a denominator today. DataHub's `total` is capped at 10000
with the cap invisible to the client; Superset's listing measures completeness
in rows, not identities (hy-fc01).
"""

from __future__ import annotations

import pytest

from hyperset.connectors import EstablishedDenominator, run_sync
from hyperset.connectors.datahub import DataHubConnector
from hyperset.connectors.superset import SupersetConnector
from hyperset.repositories.postgres import (
    PostgresConnectionRepository,
    PostgresObservedAssetRepository,
)
from tests.denominators import EstablishesDenominators
from tests.fake_datahub import BASE_URL as DATAHUB_URL
from tests.fake_datahub import FakeDataHubSession, manifest
from tests.postgres.test_superset_sync import _write_bundle_zip

_TERM_URN = manifest("baseline")["urns_by_asset_type"]["glossary_term"][0]

_DASHBOARD = {"q3": {"dashboard_title": "Q3 Review", "uuid": "dash-1", "position": {}}}
_DATASET = {
    "orders": {"table_name": "orders", "uuid": "dataset-1", "database_uuid": "db-1", "columns": []}
}


def _superset_connection(session_factory) -> str:
    return (
        PostgresConnectionRepository(session_factory)
        .create_or_update(connector_type="superset", display_name="Local Superset")
        .id
    )


def _datahub_connection(session_factory) -> str:
    return (
        PostgresConnectionRepository(session_factory)
        .create_or_update(
            connector_type="datahub", display_name="Local DataHub (GraphQL)", config_ref=DATAHUB_URL
        )
        .id
    )


def _declines(warnings: list[str], asset_type: str) -> list[str]:
    return [w for w in warnings if w.startswith("deletion declined:") and f"'{asset_type}'" in w]


class _WarrantsDatasetsOnly(EstablishesDenominators, SupersetConnector):
    _warranted = ("dataset",)


@pytest.mark.postgres
def test_a_full_superset_sync_declines_to_delete_and_names_what_is_missing(
    session_factory, tmp_path
):
    """The export-bundle connector gets no exemption for being a static file:
    it establishes no denominator, so it deletes nothing. Before hy-6nit this
    same sequence soft-deleted dash-1."""
    connection_id = _superset_connection(session_factory)
    run_sync(
        connector=SupersetConnector(
            bundle_path=_write_bundle_zip(tmp_path, "bundle1", dashboards=_DASHBOARD)
        ),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    result = run_sync(
        connector=SupersetConnector(bundle_path=_write_bundle_zip(tmp_path, "bundle2")),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    record = PostgresObservedAssetRepository(session_factory).get_by_external_id(
        connection_id=connection_id, external_id="dash-1", asset_type="dashboard"
    )
    assert result.deleted == []
    assert record.deleted_at is None

    # And the refusal is loud, not an empty result: silent non-deletion is its
    # own hazard and it is the one that hides for months.
    (declined,) = _declines(result.warnings, "dashboard")
    assert "SupersetConnector" in declined
    assert "'export_bundle'" in declined
    assert "ConnectorSnapshot.established_denominators" in declined
    assert "stays live" in declined
    # One per COVERED asset type, not per type that happened to hold an asset,
    # so no covered type declines silently. An export bundle covers four.
    assert [w for w in result.warnings if w.startswith("deletion declined:")] == [
        _declines(result.warnings, asset_type)[0]
        for asset_type in ("database", "dataset", "chart", "dashboard")
    ]


@pytest.mark.postgres
def test_a_full_datahub_sync_declines_to_delete_and_names_what_is_missing(session_factory):
    """Same rule, other connector, other transport. The URN the index lists
    but GMS will not serve is exactly the shape a short read has, which is why
    the deletion may not run on it."""
    connection_id = _datahub_connection(session_factory)
    run_sync(
        connector=DataHubConnector(base_url=DATAHUB_URL, session=FakeDataHubSession()),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    result = run_sync(
        connector=DataHubConnector(
            base_url=DATAHUB_URL,
            session=FakeDataHubSession(missing_urns=frozenset({_TERM_URN})),
        ),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    term = PostgresObservedAssetRepository(session_factory).get_by_external_id(
        connection_id=connection_id, external_id=_TERM_URN, asset_type="glossary_term"
    )
    assert result.deleted == []
    assert term.deleted_at is None

    (declined,) = _declines(result.warnings, "glossary_term")
    assert "DataHubConnector" in declined
    assert "'graphql'" in declined
    assert "ConnectorSnapshot.established_denominators" in declined


@pytest.mark.postgres
def test_the_deletion_pass_runs_for_a_warranted_type_and_refuses_beside_it(
    session_factory, tmp_path
):
    """The discriminating arm. One sync, two asset types, one denominator: the
    dataset is soft-deleted and the dashboard is not, so neither branch can be
    passing by accident. Both plausible wrong gates are caught at
    `result.deleted`, in opposite directions: hard-coded to refuse it is empty,
    and with the gate removed it carries the dashboard too."""
    connection_id = _superset_connection(session_factory)
    full = _write_bundle_zip(tmp_path, "bundle1", datasets=_DATASET, dashboards=_DASHBOARD)
    empty = _write_bundle_zip(tmp_path, "bundle2")
    run_sync(
        connector=SupersetConnector(bundle_path=full),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    result = run_sync(
        connector=_WarrantsDatasetsOnly(bundle_path=empty),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    assets = PostgresObservedAssetRepository(session_factory)
    dataset = assets.get_by_external_id(
        connection_id=connection_id, external_id="dataset-1", asset_type="dataset"
    )
    dashboard = assets.get_by_external_id(
        connection_id=connection_id, external_id="dash-1", asset_type="dashboard"
    )
    assert result.deleted == [dataset.id]
    assert dataset.deleted_at is not None
    assert dashboard.deleted_at is None
    # A warrant for one type licenses that type only, and the types without
    # one still say so.
    assert _declines(result.warnings, "dataset") == []
    assert len(_declines(result.warnings, "dashboard")) == 1


def test_a_denominator_cannot_be_established_without_naming_its_instrument():
    """An unset token and a token set by an unknown path must not read alike,
    so the unnamed one cannot be constructed at all."""
    with pytest.raises(ValueError, match="requires a producer"):
        EstablishedDenominator(producer="  ")
