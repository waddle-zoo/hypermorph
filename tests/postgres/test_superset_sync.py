import json
import zipfile
from pathlib import Path

import pytest
import yaml

from hyperset.connectors import run_sync
from hyperset.connectors.superset import SupersetConnector
from hyperset.repositories.errors import NotFoundError
from hyperset.repositories.postgres import (
    PostgresConnectionRepository,
    PostgresConnectorChangeRepository,
    PostgresGovernedContextRepository,
    PostgresObservedAssetRepository,
)
from tests.denominators import EstablishesDenominators


class _WarrantsDashboards(EstablishesDenominators, SupersetConnector):
    """Establishes a dashboard denominator, so the deletion pass may run
    (hy-6nit). No shipped connector establishes one yet."""

    _warranted = ("dashboard",)


def _write_bundle_zip(tmp_path, name, *, datasets=None, charts=None, dashboards=None) -> str:
    src = tmp_path / name
    (src / "datasets" / "warehouse" / "public").mkdir(parents=True)
    (src / "charts").mkdir(parents=True)
    (src / "dashboards").mkdir(parents=True)
    (src / "metadata.yaml").write_text(yaml.safe_dump({"version": "1.0.0", "type": "Slice"}))
    for fname, payload in (datasets or {}).items():
        (src / "datasets" / "warehouse" / "public" / f"{fname}.yaml").write_text(
            yaml.safe_dump(payload)
        )
    for fname, payload in (charts or {}).items():
        (src / "charts" / f"{fname}.yaml").write_text(yaml.safe_dump(payload))
    for fname, payload in (dashboards or {}).items():
        (src / "dashboards" / f"{fname}.yaml").write_text(yaml.safe_dump(payload))

    zip_path = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for file_path in src.rglob("*.yaml"):
            archive.write(file_path, arcname=file_path.relative_to(src))
    return str(zip_path)


@pytest.fixture
def connection_id(session_factory):
    return (
        PostgresConnectionRepository(session_factory)
        .create_or_update(connector_type="superset", display_name="Local Superset")
        .id
    )


@pytest.mark.postgres
def test_full_sync_persists_assets_losslessly(session_factory, connection_id, tmp_path):
    payload = {"table_name": "orders", "uuid": "dataset-1", "database_uuid": "db-1", "columns": []}
    zip_path = _write_bundle_zip(tmp_path, "bundle1", datasets={"orders": payload})
    connector = SupersetConnector(bundle_path=zip_path)

    result = run_sync(
        connector=connector, connection_id=connection_id, session_factory=session_factory
    )

    assert result.created == ["dataset-1"]
    assert result.counters == {
        "created": 1,
        "updated": 0,
        "restored": 0,
        "unchanged": 0,
        "deleted": 0,
    }

    assets = PostgresObservedAssetRepository(session_factory)
    record = assets.get_by_external_id(
        connection_id=connection_id, external_id="dataset-1", asset_type="dataset"
    )
    assert record.current_version.version == 1
    assert record.current_version.raw_payload["table_name"] == "orders"


@pytest.mark.postgres
def test_repeated_sync_of_unchanged_bundle_is_idempotent(session_factory, connection_id, tmp_path):
    payload = {"table_name": "orders", "uuid": "dataset-1", "database_uuid": "db-1", "columns": []}
    zip_path = _write_bundle_zip(tmp_path, "bundle1", datasets={"orders": payload})
    connector = SupersetConnector(bundle_path=zip_path)

    run_sync(connector=connector, connection_id=connection_id, session_factory=session_factory)
    result2 = run_sync(
        connector=connector, connection_id=connection_id, session_factory=session_factory
    )

    assert result2.created == []
    assert result2.unchanged == ["dataset-1"]

    assets = PostgresObservedAssetRepository(session_factory)
    record = assets.get_by_external_id(
        connection_id=connection_id, external_id="dataset-1", asset_type="dataset"
    )
    assert record.current_version.version == 1


@pytest.mark.postgres
def test_changed_metric_expression_creates_one_new_version(
    session_factory, connection_id, tmp_path
):
    zip_1 = _write_bundle_zip(
        tmp_path,
        "bundle1",
        datasets={
            "orders": {
                "table_name": "orders",
                "uuid": "dataset-1",
                "database_uuid": "db-1",
                "columns": [],
            }
        },
    )
    run_sync(
        connector=SupersetConnector(bundle_path=zip_1),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    zip_2 = _write_bundle_zip(
        tmp_path,
        "bundle2",
        datasets={
            "orders": {
                "table_name": "orders",
                "uuid": "dataset-1",
                "database_uuid": "db-1",
                "columns": [],
                "metrics": [{"metric_name": "total_revenue", "expression": "SUM(amount * 2)"}],
            }
        },
    )
    result = run_sync(
        connector=SupersetConnector(bundle_path=zip_2),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    assert result.updated == ["dataset-1"]
    assets = PostgresObservedAssetRepository(session_factory)
    record = assets.get_by_external_id(
        connection_id=connection_id, external_id="dataset-1", asset_type="dataset"
    )
    assert record.current_version.version == 2
    history = assets.history(record.id)
    assert len(history) == 2


@pytest.mark.postgres
def test_deleted_dashboard_marked_missing_after_full_sync(session_factory, connection_id, tmp_path):
    """Soft-deletion still works, and now says what licensed it: the second
    sync carries an `EstablishedDenominator` for dashboards. A plain
    `SupersetConnector` carries none and would delete nothing (hy-6nit), which
    `test_deletion_needs_a_denominator` asserts on this same sequence."""
    zip_1 = _write_bundle_zip(
        tmp_path,
        "bundle1",
        dashboards={"q3": {"dashboard_title": "Q3 Review", "uuid": "dash-1", "position": {}}},
    )
    run_sync(
        connector=SupersetConnector(bundle_path=zip_1),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    zip_2 = _write_bundle_zip(tmp_path, "bundle2")  # empty bundle -- dashboard no longer present
    result = run_sync(
        connector=_WarrantsDashboards(bundle_path=zip_2),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    assets = PostgresObservedAssetRepository(session_factory)
    record = assets.get_by_external_id(
        connection_id=connection_id, external_id="dash-1", asset_type="dashboard"
    )
    assert record.deleted_at is not None
    assert record.id in result.deleted
    # History is retained, not deleted.
    assert len(assets.history(record.id)) == 1


@pytest.mark.postgres
def test_reappearance_counts_as_restored_not_unchanged(session_factory, connection_id, tmp_path):
    dashboards = {"q3": {"dashboard_title": "Q3 Review", "uuid": "dash-1", "position": {}}}
    zip_1 = _write_bundle_zip(tmp_path, "bundle1", dashboards=dashboards)
    zip_2 = _write_bundle_zip(tmp_path, "bundle2")  # empty -- the dashboard disappears
    zip_3 = _write_bundle_zip(tmp_path, "bundle3", dashboards=dashboards)  # and comes back
    for bundle in (zip_1, zip_2):
        # The disappearance has to be a real soft-delete for a reappearance to
        # be observable at all, so these runs carry a dashboard denominator
        # (hy-6nit).
        run_sync(
            connector=_WarrantsDashboards(bundle_path=bundle),
            connection_id=connection_id,
            session_factory=session_factory,
        )

    result = run_sync(
        connector=SupersetConnector(bundle_path=zip_3),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    # The payload is byte-identical to the one observed before the deletion,
    # so the old tally called this "unchanged" while the change stream
    # announced a reappearance (hy-y8g.6). Counters follow the stream.
    assert result.restored == ["dash-1"]
    assert result.unchanged == []
    assert result.counters == {
        "created": 0,
        "updated": 0,
        "restored": 1,
        "unchanged": 0,
        "deleted": 0,
    }
    changes = PostgresConnectorChangeRepository(session_factory).list_for_run(result.sync_run_id)
    assert [c.change_type for c in changes] == ["restored"]


@pytest.mark.postgres
def test_partial_sync_never_implies_deletion(session_factory, connection_id, tmp_path):
    zip_1 = _write_bundle_zip(
        tmp_path,
        "bundle1",
        dashboards={"q3": {"dashboard_title": "Q3 Review", "uuid": "dash-1", "position": {}}},
    )
    run_sync(
        connector=SupersetConnector(bundle_path=zip_1),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    zip_2 = _write_bundle_zip(tmp_path, "bundle2")  # empty, but this run is declared partial
    result = run_sync(
        connector=SupersetConnector(bundle_path=zip_2),
        connection_id=connection_id,
        session_factory=session_factory,
        partial=True,
    )

    assert result.deleted == []
    assets = PostgresObservedAssetRepository(session_factory)
    record = assets.get_by_external_id(
        connection_id=connection_id, external_id="dash-1", asset_type="dashboard"
    )
    assert record.deleted_at is None


@pytest.mark.postgres
def test_sync_never_creates_governed_context(session_factory, connection_id, tmp_path):
    zip_path = _write_bundle_zip(
        tmp_path,
        "bundle1",
        datasets={"orders": {"table_name": "orders", "uuid": "dataset-1", "database_uuid": "db-1"}},
    )
    run_sync(
        connector=SupersetConnector(bundle_path=zip_path),
        connection_id=connection_id,
        session_factory=session_factory,
    )
    contexts = PostgresGovernedContextRepository(session_factory)
    hits = contexts.search("orders")
    assert hits == []


@pytest.mark.postgres
def test_sync_persists_counters_and_warnings_on_sync_run(session_factory, connection_id, tmp_path):
    from hyperset.repositories.postgres import PostgresSyncRepository

    zip_path = _write_bundle_zip(
        tmp_path,
        "bundle1",
        datasets={"orders": {"table_name": "orders", "uuid": "dataset-1", "database_uuid": "db-1"}},
    )
    result = run_sync(
        connector=SupersetConnector(bundle_path=zip_path),
        connection_id=connection_id,
        session_factory=session_factory,
    )
    run = PostgresSyncRepository(session_factory).get_run(result.sync_run_id)
    assert run.status == "succeeded"
    assert run.counters == {"created": 1, "updated": 0, "restored": 0, "unchanged": 0, "deleted": 0}


@pytest.mark.postgres
def test_no_plaintext_secret_in_persisted_payload(session_factory, connection_id, tmp_path):
    # Bundle-mode connectors never see connection credentials at all --
    # only database export metadata (sqlalchemy_uri is already sanitized
    # by Superset's own export, this test just proves nothing is ever
    # added to raw_payload/normalized beyond what the source emitted).
    zip_path = _write_bundle_zip(
        tmp_path,
        "bundle1",
        datasets={"orders": {"table_name": "orders", "uuid": "dataset-1", "database_uuid": "db-1"}},
    )
    run_sync(
        connector=SupersetConnector(bundle_path=zip_path),
        connection_id=connection_id,
        session_factory=session_factory,
    )
    connection = PostgresConnectionRepository(session_factory).get(connection_id)
    assert not hasattr(connection, "config_encrypted")


# -- declared references (hy-d7xh, hy-vzk8) ---------------------------------
#
# The real captured evidence for the two references that make a dataset's
# popularity countable -- chart --queries--> dataset and dashboard --contains-->
# chart -- is the usage fixture, synced below from the unmodified export ZIP the
# pinned 6.1.0 instance produced.
#
# The bundles built here stay for what one capture of a live instance cannot
# show: a reference the source stopped declaring, and a reference whose target
# was never observed. Their layout and position tree are the real 6.1.0 export
# shapes; the persistence under test is source-neutral.

_DATASET = {"table_name": "orders", "uuid": "dataset-1", "database_uuid": "db-1", "columns": []}
_CHART = {"slice_name": "Orders by day", "uuid": "chart-1", "dataset_uuid": "dataset-1"}


def _dashboard(*chart_uuids: str) -> dict:
    return {
        "dashboard_title": "Q3 Review",
        "uuid": "dash-1",
        "position": {
            f"CHART-{uuid}": {"type": "CHART", "meta": {"uuid": uuid}} for uuid in chart_uuids
        },
    }


@pytest.mark.postgres
def test_chart_and_dashboard_references_are_persisted(session_factory, connection_id, tmp_path):
    zip_path = _write_bundle_zip(
        tmp_path,
        "bundle1",
        datasets={"orders": _DATASET},
        charts={"orders_by_day": _CHART},
        dashboards={"q3": _dashboard("chart-1")},
    )

    result = run_sync(
        connector=SupersetConnector(bundle_path=zip_path),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    assets = PostgresObservedAssetRepository(session_factory)
    by_external_id = {
        external_id: assets.get_by_external_id(
            connection_id=connection_id, external_id=external_id, asset_type=asset_type
        )
        for external_id, asset_type in (
            ("dataset-1", "dataset"),
            ("chart-1", "chart"),
            ("dash-1", "dashboard"),
        )
    }
    # The countable signal: what references this dataset, read from the store
    # rather than re-derived from a payload.
    querying = assets.list_relationships(to_asset_id=by_external_id["dataset-1"].id)

    # The dataset's own parent database is not in this bundle, so that link
    # stays a warning; the two references under test both resolved.
    assert [w for w in result.warnings if w.startswith("unresolved link")] == [
        "unresolved link: dataset/dataset-1 -> database/db-1 (target not observed)"
    ]
    assert result.relationships == 2
    assert [(row.from_asset_id, row.relation) for row in querying] == [
        (by_external_id["chart-1"].id, "queries")
    ]
    assert [
        (row.relation, row.to_asset_id)
        for row in assets.list_relationships(from_asset_id=by_external_id["dash-1"].id)
    ] == [("contains", by_external_id["chart-1"].id)]


@pytest.mark.postgres
def test_a_chart_removed_from_a_dashboard_stops_being_a_declared_reference(
    session_factory, connection_id, tmp_path
):
    charts = {"orders_by_day": _CHART}
    with_chart = _write_bundle_zip(
        tmp_path,
        "bundle1",
        datasets={"orders": _DATASET},
        charts=charts,
        dashboards={"q3": _dashboard("chart-1")},
    )
    without_chart = _write_bundle_zip(
        tmp_path,
        "bundle2",
        datasets={"orders": _DATASET},
        charts=charts,
        dashboards={"q3": _dashboard()},
    )
    run_sync(
        connector=SupersetConnector(bundle_path=with_chart),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    result = run_sync(
        connector=SupersetConnector(bundle_path=without_chart),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    assets = PostgresObservedAssetRepository(session_factory)
    chart = assets.get_by_external_id(
        connection_id=connection_id, external_id="chart-1", asset_type="chart"
    )
    dashboard = assets.get_by_external_id(
        connection_id=connection_id, external_id="dash-1", asset_type="dashboard"
    )
    # The chart still exists and still queries the dataset; only the
    # dashboard's claim on it is gone.
    assert assets.list_relationships(to_asset_id=chart.id) == []
    assert [row.relation for row in assets.list_relationships(from_asset_id=chart.id)] == [
        "queries"
    ]
    assert assets.list_relationships(from_asset_id=dashboard.id) == []
    assert result.relationships == 1


@pytest.mark.postgres
def test_an_unresolvable_reference_warns_and_persists_nothing(
    session_factory, connection_id, tmp_path
):
    """A chart whose dataset was never observed has no second endpoint to
    point at, so it stays a warning (hy-gh-38 rule 7) instead of becoming a
    row -- the projection never invents an identity."""
    zip_path = _write_bundle_zip(tmp_path, "bundle1", charts={"orders_by_day": _CHART})

    result = run_sync(
        connector=SupersetConnector(bundle_path=zip_path),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    assets = PostgresObservedAssetRepository(session_factory)
    chart = assets.get_by_external_id(
        connection_id=connection_id, external_id="chart-1", asset_type="chart"
    )
    assert any("unresolved link: chart/chart-1 -> dataset/dataset-1" in w for w in result.warnings)
    assert assets.list_relationships(from_asset_id=chart.id) == []
    assert result.relationships == 0


_USAGE_FIXTURE = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "superset" / "6.1.0" / "usage"
)
_USAGE_MANIFEST = json.loads((_USAGE_FIXTURE / "manifest.json").read_text(encoding="utf-8"))


@pytest.mark.postgres
def test_the_real_captured_export_persists_the_references_it_declares(
    session_factory, connection_id
):
    """One sync of the unmodified 6.1.0 dashboard export, checked against what
    the capture's manifest says the source declared (hy-vzk8).

    Nothing here is asserted from a literal: the expected references come from
    the manifest, and the manifest's own reference claims are verified against
    the captured bytes by `tests/integration/test_usage_reference_evidence.py`.
    """
    expected = _USAGE_MANIFEST["expected_observed_references"]

    result = run_sync(
        connector=SupersetConnector(bundle_path=str(_USAGE_FIXTURE / "official-export.zip")),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    assets = PostgresObservedAssetRepository(session_factory)

    def asset_id(external_id: str, asset_type: str) -> int:
        return assets.get_by_external_id(
            connection_id=connection_id, external_id=external_id, asset_type=asset_type
        ).id

    chart_targets = {
        chart_uuid: assets.list_relationships(from_asset_id=asset_id(chart_uuid, "chart"))
        for chart_uuid in expected["chart_queries_dataset"]
    }
    for chart_uuid, dataset_uuid in expected["chart_queries_dataset"].items():
        assert [(row.relation, row.to_asset_id) for row in chart_targets[chart_uuid]] == [
            ("queries", asset_id(dataset_uuid, "dataset"))
        ]

    for dashboard_uuid, chart_uuids in expected["dashboard_contains_chart"].items():
        rows = assets.list_relationships(from_asset_id=asset_id(dashboard_uuid, "dashboard"))
        assert {row.relation for row in rows} == {"contains"}
        assert sorted(row.to_asset_id for row in rows) == sorted(
            asset_id(chart_uuid, "chart") for chart_uuid in chart_uuids
        )

    # The countable signal, read back per dataset the way a ranking would read
    # it. Datasets no chart queries are not in this export at all, so the
    # capture's own count of zero is all this transport can show for them.
    counts = expected["dataset_reference_counts"]
    uncovered = set(expected["uncovered_by_this_capture"]["dataset_uuids"])
    for dataset_uuid, count in counts.items():
        if dataset_uuid in uncovered:
            with pytest.raises(NotFoundError):
                asset_id(dataset_uuid, "dataset")
            continue
        querying = assets.list_relationships(to_asset_id=asset_id(dataset_uuid, "dataset"))
        assert len([row for row in querying if row.relation == "queries"]) == count

    # Every reference in the archive: three charts, three dashboard placements,
    # and each exported dataset's own parent database.
    assert result.relationships == sum(counts.values()) + sum(
        len(charts) for charts in expected["dashboard_contains_chart"].values()
    ) + len(counts) - len(uncovered)
    assert [w for w in result.warnings if w.startswith("unresolved link")] == []
