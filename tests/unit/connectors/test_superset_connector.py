import zipfile
from pathlib import Path

import pytest
import yaml

from hyperset.connectors import ConnectorError, ConnectorSnapshot
from hyperset.connectors.superset import SupersetConnector
from hyperset.connectors.superset.connector import (
    _HASH_BASIS,
    _normalize_chart,
    _normalize_dashboard,
    _normalize_dataset,
)
from hyperset.repositories.hash_basis import apply_hash_basis
from tests.fake_superset import BASE_URL, FakeSupersetSession
from tests.unit.connectors.test_superset_rest import EmptyBodySession


def _hashed(item) -> dict:
    """What this connector's declared hash basis leaves of an asset for the
    content hash. Applied through the repository's replay function, not a
    connector-private projection, so these assertions cover the rule the
    version row actually stores."""
    return apply_hash_basis(item.raw_payload, item.hash_basis or {})


CHART_61 = {
    "slice_name": "orders_by_region",
    "uuid": "chart-1",
    "dataset_uuid": "dataset-1",
    "description": "Revenue by region.",
    "viz_type": "echarts_timeseries_bar",
    "owners": [{"username": "bsovran"}],
    "certified_by": None,
}

DASHBOARD_61 = {
    "dashboard_title": "Q3 Review",
    "uuid": "dash-1",
    "position": {
        "ROOT_ID": {"type": "ROOT", "children": ["CHART-1"]},
        "CHART-1": {"type": "CHART", "meta": {"uuid": "chart-1", "sliceName": "orders_by_region"}},
    },
    "metadata": {"filter_scopes": {}},
    "published": True,
    "owners": [{"username": "bsovran"}],
}

DATASET_61 = {
    "table_name": "Orders",
    "uuid": "dataset-1",
    "database_uuid": "db-1",
    "schema": "public",
    "description": "Customer orders.",
    "columns": [{"column_name": "order_id"}, {"column_name": "region"}],
    "metrics": [{"metric_name": "total_revenue"}],
    "some_future_superset_7_field": {"nested": True},
}


def test_normalize_chart_uses_dataset_uuid():
    result = _normalize_chart(CHART_61)
    assert result.external_id == "chart-1"
    assert result.links[0].kind == "dataset"
    assert result.links[0].target_external_id == "dataset-1"
    assert result.raw_payload == CHART_61


def test_normalize_dashboard_uses_position_chart_uuid():
    result = _normalize_dashboard(DASHBOARD_61)
    assert result.external_id == "dash-1"
    assert [link.target_external_id for link in result.links] == ["chart-1"]
    assert result.raw_payload == DASHBOARD_61


def test_normalize_dataset_uses_database_uuid():
    result = _normalize_dataset(DATASET_61)
    assert result.external_id == "dataset-1"
    assert result.links[0].kind == "database"
    assert result.links[0].target_external_id == "db-1"


def test_normalize_dataset_preserves_unknown_fields_in_raw_payload():
    result = _normalize_dataset(DATASET_61)
    assert result.raw_payload["some_future_superset_7_field"] == {"nested": True}


def test_normalize_rejects_name_only_identity():
    with pytest.raises(ConnectorError, match="stable uuid"):
        _normalize_dataset({"table_name": "orders"})


def test_connector_requires_bundle_path_or_base_url():
    with pytest.raises(ConnectorError):
        SupersetConnector()


def test_connector_rejects_two_transports_at_once(tmp_path):
    with pytest.raises(ConnectorError, match="not both"):
        SupersetConnector(bundle_path=tmp_path / "export.zip", base_url="http://superset:8088")


def test_connector_live_mode_requires_credentials():
    with pytest.raises(ConnectorError, match="username and password"):
        SupersetConnector(base_url="http://superset:8088")


def _write_bundle_zip(tmp_path, *, databases, datasets, charts, dashboards) -> Path:
    src = tmp_path / "src"
    (src / "databases").mkdir(parents=True)
    (src / "datasets" / "warehouse" / "public").mkdir(parents=True)
    (src / "charts").mkdir(parents=True)
    (src / "dashboards").mkdir(parents=True)
    (src / "metadata.yaml").write_text(yaml.safe_dump({"version": "1.0.0", "type": "Slice"}))
    for name, payload in databases.items():
        (src / "databases" / f"{name}.yaml").write_text(yaml.safe_dump(payload))
    for name, payload in datasets.items():
        (src / "datasets" / "warehouse" / "public" / f"{name}.yaml").write_text(
            yaml.safe_dump(payload)
        )
    for name, payload in charts.items():
        (src / "charts" / f"{name}.yaml").write_text(yaml.safe_dump(payload))
    for name, payload in dashboards.items():
        (src / "dashboards" / f"{name}.yaml").write_text(yaml.safe_dump(payload))

    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for file_path in src.rglob("*.yaml"):
            archive.write(file_path, arcname=file_path.relative_to(src))
    return zip_path


def test_snapshot_and_normalize_end_to_end(tmp_path):
    zip_path = _write_bundle_zip(
        tmp_path,
        databases={"warehouse": {"database_name": "Warehouse", "uuid": "db-1"}},
        datasets={"orders": DATASET_61},
        charts={"orders_by_region": CHART_61},
        dashboards={"q3_review": DASHBOARD_61},
    )
    connector = SupersetConnector(bundle_path=zip_path)

    test_result = connector.test_connection()
    assert test_result.ok is True

    snapshot = connector.snapshot()
    assert isinstance(snapshot, ConnectorSnapshot)
    assert snapshot.source_version is None
    assert any("does not disclose" in warning for warning in snapshot.warnings)

    items = list(connector.normalize(snapshot))
    asset_types = {item.asset_type for item in items}
    assert asset_types == {"database", "dataset", "chart", "dashboard"}
    dataset_item = next(i for i in items if i.asset_type == "dataset")
    assert dataset_item.raw_payload == DATASET_61


def test_test_connection_reports_missing_path(tmp_path):
    connector = SupersetConnector(bundle_path=tmp_path / "does-not-exist.zip")
    result = connector.test_connection()
    assert result.ok is False
    assert "does not exist" in result.detail


def test_snapshot_warns_on_unsupported_asset_type_instead_of_silent_loss(tmp_path):
    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("datasets/warehouse/public/orders.yaml", yaml.safe_dump(DATASET_61))
        archive.writestr("queries/saved_query.yaml", yaml.safe_dump({"label": "Ad-hoc SQL"}))

    connector = SupersetConnector(bundle_path=zip_path)
    snapshot = connector.snapshot()

    assert any("queries" in w and "skipped" in w for w in snapshot.warnings)


# --- live REST mode (hy-gh-27 Phase C) ---------------------------------------
#
# Exercised against the recorded pinned-6.1.0 REST evidence, so the assertions
# below are about real response shapes, not hand-written ones.


def _rest_connector(session) -> SupersetConnector:
    return SupersetConnector(
        base_url=BASE_URL, username="admin", password="s3cret", session=session
    )


def test_rest_transport_is_reported_separately_from_export():
    assert _rest_connector(FakeSupersetSession()).transport == "rest"


def test_rest_test_connection_reports_the_authenticated_instance():
    result = _rest_connector(FakeSupersetSession()).test_connection()

    assert result.ok is True
    assert "1 database(s)" in result.detail


def test_rest_test_connection_fails_closed_on_bad_credentials():
    result = _rest_connector(FakeSupersetSession(login_status=401)).test_connection()

    assert result.ok is False
    assert "401" in result.detail


def test_rest_test_connection_fails_closed_on_a_body_that_will_not_parse():
    """The symptom hy-ozhz was filed for. `_test_rest_connection` catches
    `ConnectorError` only, so before the transport named this shape the
    `JSONDecodeError` escaped and the connection test crashed -- the one call
    whose entire job is to answer "can I read this instance" ended in the
    traceback of a caller that had asked politely.
    """
    result = _rest_connector(EmptyBodySession(204)).test_connection()

    assert result.ok is False
    assert "204" in result.detail


def test_rest_snapshot_discloses_transport_coverage_and_unknown_source_version():
    snapshot = _rest_connector(FakeSupersetSession()).snapshot()

    assert snapshot.transport == "rest"
    assert snapshot.source_version is None
    assert snapshot.covered_asset_types == ("database", "dataset", "chart", "dashboard")
    assert snapshot.source_capabilities["api_version"] == "v1"
    assert snapshot.source_capabilities["application_version_disclosed"] is False
    assert any("does not disclose its application version" in w for w in snapshot.warnings)
    # What the transport still fails to disclose is named; what it merely did
    # not read is no longer a category (hy-rt4v).
    assert any("chart detail bodies disclose no `changed_on`" in w for w in snapshot.warnings)
    assert any("changed_on_humanized, created_on_humanized" in w for w in snapshot.warnings)


def test_rest_snapshot_records_a_resumable_checkpoint():
    connector = _rest_connector(FakeSupersetSession())

    first = connector.snapshot()
    assert first.checkpoint["read_mode"] == "full_refresh"
    # Zeroes, not absences: this capture's instance had no chart or dashboard,
    # and a covered type with none of it is still a type that was read.
    assert first.checkpoint["asset_counts"] == {
        "database": 1,
        "dataset": 3,
        "chart": 0,
        "dashboard": 0,
    }
    assert first.checkpoint["resumed_from_high_watermark"] is None

    second = connector.snapshot(first.checkpoint)
    assert (
        second.checkpoint["resumed_from_high_watermark"]
        == first.checkpoint["high_watermark_changed_on"]
    )


def test_rest_normalize_uses_native_uuid_identity_and_nested_database_link():
    connector = _rest_connector(FakeSupersetSession())
    items = {item.external_id: item for item in connector.normalize(connector.snapshot())}

    approved = items["ae48881d-334f-54a7-94e8-1ffcc73866e2"]  # finance_orders_daily
    assert approved.asset_type == "dataset"
    assert approved.normalized["name"] == "finance_orders_daily"
    assert {"name": "recognized_revenue", "expression": "SUM(gross_amount - tax_amount)"} in (
        approved.normalized["metrics"]
    )
    assert approved.links[0].kind == "database"
    assert approved.links[0].target_external_id == "191e8838-4a5c-5f3f-9d53-71f52f56f7f8"
    assert "191e8838-4a5c-5f3f-9d53-71f52f56f7f8" in items  # the database itself


def test_rest_normalize_keeps_the_whole_detail_payload_and_narrows_only_the_hash_input():
    connector = _rest_connector(FakeSupersetSession())
    dataset = next(
        item for item in connector.normalize(connector.snapshot()) if item.asset_type == "dataset"
    )

    # Volatile fields are retained in the stored payload...
    assert "changed_on_delta_humanized" not in dataset.raw_payload  # detail body has none
    assert dataset.raw_payload["created_on_humanized"] == "now"
    # ...and excluded from what the hash covers only.
    assert dataset.hash_basis == _HASH_BASIS
    basis = _hashed(dataset)
    assert "created_on_humanized" not in basis
    assert basis["uuid"] == dataset.raw_payload["uuid"]
    assert basis["metrics"] == dataset.raw_payload["metrics"]


def test_rest_hash_basis_is_stable_across_rerendered_relative_times():
    first = next(
        item
        for item in (lambda c: c.normalize(c.snapshot()))(
            _rest_connector(FakeSupersetSession(humanized="now"))
        )
        if item.asset_type == "dataset"
    )
    later = next(
        item
        for item in (lambda c: c.normalize(c.snapshot()))(
            _rest_connector(FakeSupersetSession(humanized="3 hours ago"))
        )
        if item.asset_type == "dataset"
    )

    assert first.raw_payload != later.raw_payload
    assert _hashed(first) == _hashed(later)


def test_rest_drift_capture_changes_the_hash_basis_projection():
    """The recorded controlled drift (`recognized_revenue`'s expression) is a
    real source change, so it must move the hash input -- unlike a
    re-rendered relative time. Restoring the expression returns the metric to
    its baseline value but not the whole basis: Superset's own `changed_on`
    advanced with the second edit, which is genuine source state and stays in
    change detection."""

    def dataset(capture):
        connector = _rest_connector(FakeSupersetSession(capture))
        return next(
            item
            for item in connector.normalize(connector.snapshot())
            if item.external_id == "ae48881d-334f-54a7-94e8-1ffcc73866e2"
        )

    baseline, drift, restored = dataset("baseline"), dataset("drift"), dataset("restored")

    def expression(item):
        return next(
            m["expression"]
            for m in _hashed(item)["metrics"]
            if m["metric_name"] == "recognized_revenue"
        )

    assert _hashed(baseline) != _hashed(drift)
    assert expression(baseline) == "SUM(gross_amount - tax_amount)"
    assert expression(drift) == "SUM(gross_amount)"
    assert expression(restored) == expression(baseline)
    assert _hashed(restored) != _hashed(baseline)
    assert restored.source_modified_at > baseline.source_modified_at


def test_the_hash_basis_is_the_connectors_rule_and_not_the_transports(tmp_path):
    """One payload, both read modes, one rule (hy-y8g.3).

    A connection carries a second read mode the moment hy-gh-48 lands. If the
    bundle path hashed the whole payload while REST hashed it minus the
    server-rendered relative times, every switch between them would append an
    immutable version produced by the rule changing rather than by the source
    changing -- and the stored basis would disagree with the previous
    version's for an asset nobody edited.
    """
    payload = _rest_connector(FakeSupersetSession()).snapshot().bundle["datasets"][0]

    def read_as(connector, transport):
        snapshot = ConnectorSnapshot(
            source_version=None, bundle={"datasets": [payload]}, transport=transport
        )
        return next(iter(connector.normalize(snapshot)))

    by_bundle = read_as(SupersetConnector(bundle_path=tmp_path / "export.zip"), "export_bundle")
    by_rest = read_as(_rest_connector(FakeSupersetSession()), "rest")

    assert "created_on_humanized" in payload  # the rule has something to exclude
    assert by_bundle.hash_basis == by_rest.hash_basis == _HASH_BASIS
    assert _hashed(by_bundle) == _hashed(by_rest)
    assert "created_on_humanized" not in _hashed(by_bundle)
    assert by_bundle.source_modified_at == by_rest.source_modified_at is not None


def test_the_shared_rule_leaves_an_export_payload_whole():
    """The rule only drops `*_humanized` keys, which export YAML does not
    carry, so a bundle asset's hash still covers its payload exactly -- the
    point is that it stays that way by the rule rather than by which branch
    ran. Export YAML discloses no `changed_on` either, so the read reports no
    modification time instead of inventing one."""
    connector = SupersetConnector(bundle_path=Path("unused.zip"))
    snapshot = ConnectorSnapshot(source_version=None, bundle={"datasets": [DATASET_61]})

    item = next(iter(connector.normalize(snapshot)))

    # By the rule, not by the absence of one (hy-sv7): before hy-y8g.3 the
    # export branch declared no basis at all, so the payload was hashed whole
    # by default. Asserting the rule is what makes this test red there, and it
    # is the distinction the change was about.
    assert item.hash_basis == _HASH_BASIS
    assert _hashed(item) == item.raw_payload == DATASET_61
    assert item.source_modified_at is None


def test_rest_normalize_reads_source_modified_at_as_utc():
    connector = _rest_connector(FakeSupersetSession())
    dataset = next(
        item for item in connector.normalize(connector.snapshot()) if item.asset_type == "dataset"
    )

    assert dataset.source_modified_at is not None
    assert dataset.source_modified_at.tzinfo is not None
    assert dataset.source_modified_at.isoformat().endswith("+00:00")


# --- live REST reads every type the build serves (hy-rt4v) -------------------
#
# The chart and dashboard bodies come from the `usage` capture of the same
# pinned instance, whose seed adds them over the datasets `revenue/` recorded.
# So these assertions are about real 6.1.0 REST shapes too, and the field names
# they turn on -- `datasource_uuid`, `position_json` -- are the ones that made
# this normalization work rather than wiring.

_APPROVED_DATASET = "ae48881d-334f-54a7-94e8-1ffcc73866e2"
_DASHBOARD = "655abff1-3c4f-5921-96ba-b67616cda208"
_CHARTS = {
    "6f0af5cf-146c-56c7-a57d-0912550108f9",
    "4d82dc9e-fe4b-5989-b5cb-3f20eb7c1643",
    "73995395-7d07-58ea-90b7-65330d4f3b22",
}


def _referencing_connector() -> SupersetConnector:
    return _rest_connector(FakeSupersetSession(references="usage"))


def _rest_items(connector) -> dict:
    return {item.external_id: item for item in connector.normalize(connector.snapshot())}


def test_rest_covers_every_type_the_pinned_build_serves():
    """The measured defect (hy-rt4v): the connector's warning said charts and
    dashboards were unread because their REST shapes were unproven. hy-vzk8
    proved them. Unread is what they were."""
    snapshot = _referencing_connector().snapshot()

    assert snapshot.covered_asset_types == ("database", "dataset", "chart", "dashboard")
    assert snapshot.checkpoint["asset_counts"] == {
        "database": 1,
        "dataset": 3,
        "chart": 3,
        "dashboard": 1,
    }
    # Gone rather than reworded a third time.
    assert not any("were not read" in warning for warning in snapshot.warnings)


def test_a_rest_chart_links_to_the_dataset_it_queries():
    """`datasource_uuid`, which is the REST name for what the export calls
    `dataset_uuid`. Reading the export name only would have yielded a chart
    with no link at all -- silently, which is worse than not reading it."""
    chart = _rest_items(_referencing_connector())["6f0af5cf-146c-56c7-a57d-0912550108f9"]

    assert chart.asset_type == "chart"
    assert chart.normalized["name"] == "Recognized revenue by status"
    assert [(link.kind, link.target_external_id, link.relation) for link in chart.links] == [
        ("dataset", _APPROVED_DATASET, "queries")
    ]


def test_a_rest_dashboard_links_to_its_charts_through_the_layout_string():
    """`position_json` is the same layout tree the export carries as a
    mapping, served as a JSON string. Both name the chart by `meta.uuid`."""
    dashboard = _rest_items(_referencing_connector())[_DASHBOARD]

    assert dashboard.asset_type == "dashboard"
    assert {link.target_external_id for link in dashboard.links} == _CHARTS
    assert {link.relation for link in dashboard.links} == {"contains"}


def test_the_two_transports_produce_the_same_links_from_the_same_source_fact():
    """What the slice actually owes (hy-rt4v item 2). ADR 0003 keeps the two
    upstream contracts separate, which argues against normalizing the payload
    -- not against reading both field names for one source fact. Asserted over
    the real export archive and the real REST body for the same three charts
    and the same dashboard, so it is the shapes that agree, not two fixtures
    somebody wrote to agree."""
    export = SupersetConnector(
        bundle_path=Path(__file__).resolve().parents[3]
        / "tests/fixtures/superset/6.1.0/usage/official-export.zip"
    )
    by_export = {item.external_id: item for item in export.normalize(export.snapshot())}
    by_rest = _rest_items(_referencing_connector())

    def links(items, external_id):
        return sorted(
            (link.kind, link.target_external_id, link.relation) for link in items[external_id].links
        )

    for external_id in _CHARTS | {_DASHBOARD}:
        assert links(by_export, external_id) == links(by_rest, external_id), external_id
    # Not vacuous in either direction: both sides declared references.
    assert links(by_rest, _DASHBOARD) and links(by_rest, next(iter(_CHARTS)))


def test_a_chart_built_on_something_that_is_not_a_dataset_declares_no_dataset_link():
    """REST says which kind of datasource a chart reads; a chart can be built
    on a saved query. Calling that a dataset reference would put a link in the
    projection that no dataset can ever resolve, and the reference count is a
    ranking input (hy-g1y8) -- a wrong one is worse than a missing one.

    The export contract carries no `datasource_type` at all, so absence has to
    keep meaning dataset or every export chart loses its link.
    """
    on_query = _normalize_chart(
        {
            "uuid": "chart-9",
            "slice_name": "ad hoc",
            "datasource_uuid": "q-1",
            "datasource_type": "query",
        }
    )
    on_table = _normalize_chart(
        {
            "uuid": "chart-8",
            "slice_name": "table",
            "datasource_uuid": "d-1",
            "datasource_type": "table",
        }
    )

    assert on_query.links == []
    assert [link.target_external_id for link in on_table.links] == ["d-1"]
    assert [link.target_external_id for link in _normalize_chart(CHART_61).links] == ["dataset-1"]


def test_a_read_that_fails_halfway_claims_no_coverage_at_all():
    """The condition that makes claiming coverage safe (hy-rt4v item 4).
    Coverage is not by itself what soft-deletes anything -- since hy-6nit that
    takes an `EstablishedDenominator` this connector does not set -- but a
    partial read that claimed a whole type would soft-delete live assets the
    day one is established. It cannot happen: any non-200 raises, so the run
    fails and no deletion pass runs."""
    connector = _rest_connector(
        FakeSupersetSession(references="usage", status_overrides={"/api/v1/chart/": 502})
    )

    with pytest.raises(ConnectorError, match="502"):
        connector.snapshot()


def test_an_instance_that_has_no_charts_still_covers_the_type():
    """Coverage is what was looked at, not what was found. A capture with no
    chart is exactly the state the covered-type pre-seed in `sync.py` exists
    for: without the type in `covered_asset_types`, charts deleted upstream
    would never be checked."""
    snapshot = _rest_connector(FakeSupersetSession()).snapshot()

    assert snapshot.covered_asset_types == ("database", "dataset", "chart", "dashboard")
    assert snapshot.checkpoint["asset_counts"]["chart"] == 0
    assert snapshot.bundle["charts"] == []


class _LayoutServed(FakeSupersetSession):
    """The `usage` capture's real dashboard body, served with one field
    replaced. The disclosure runs inside `_rest_snapshot`, so a hand-built
    `ConnectorSnapshot` cannot reach it -- the previous version of this test
    built one and therefore asserted nothing about the warning at all. Every
    other byte of the recorded body is untouched, which is the point: the
    layout string is the only thing under test."""

    _ABSENT = object()

    def __init__(self, layout: object) -> None:
        super().__init__(references="usage")
        self._layout = layout

    def _reference_rest(self, name: str):
        payload = super()._reference_rest(name)
        if name == "dashboard-detail.json":
            if self._layout is self._ABSENT:
                payload["result"].pop("position_json", None)
            else:
                payload["result"]["position_json"] = self._layout
        return payload


def _layout_warnings(layout: object) -> list[str]:
    snapshot = _rest_connector(_LayoutServed(layout)).snapshot()
    return [warning for warning in snapshot.warnings if "layout" in warning]


def test_a_layout_that_cannot_be_parsed_is_disclosed_rather_than_read_as_no_charts():
    """A dashboard whose `position_json` is not JSON yields no links, and a
    dashboard that genuinely contains nothing yields no links either. Those
    are different facts and the projection cannot tell them apart, so the
    snapshot says which one happened.

    Asserted on the warning `_rest_snapshot` actually emits. Killing
    `_unparseable_layout_disclosure`'s body has to red this test; nothing else
    in the suite notices its removal (#175 bounce)."""
    control = _rest_connector(FakeSupersetSession(references="usage")).snapshot()
    assert not any("layout" in warning for warning in control.warnings)
    # Canary: the recorded body carries a layout that parses to the three
    # charts, so replacing it below is not a no-op against an already-empty
    # field.
    assert {
        link.target_external_id for link in _rest_items(_referencing_connector())[_DASHBOARD].links
    } == _CHARTS

    disclosed = _layout_warnings("{not json")

    assert disclosed == [
        "dashboard layout could not be parsed, so the charts these dashboards contain are "
        f"recorded as none rather than as unknown: {_DASHBOARD}"
    ]
    broken = ConnectorSnapshot(
        source_version=None,
        bundle={"dashboards": [{"uuid": _DASHBOARD, "position_json": "{not json"}]},
        transport="rest",
    )
    assert next(iter(_referencing_connector().normalize(broken))).links == []


def test_a_dashboard_that_was_never_laid_out_is_not_called_unreadable():
    """The decision the bounce asked for, stated: an empty `position_json` is
    not a read failure. Superset 6.1.0 is the authority and it is unambiguous
    -- `superset/utils/json.py:179` exempts a falsy value from `validate_json`,
    so its own API accepts `""` on POST and PUT; `models/dashboard.py:295`,
    `commands/report/base.py:111` and
    `mcp_service/dashboard/tool/add_chart_to_existing_dashboard.py:432` all
    read `position_json or "{}"`; and `commands/dashboard/importers/v0.py:239`
    writes the comment "position_json can be empty for dashboards".

    Read from the pinned image, not from documentation:
    `docker run --rm hyperset/superset-pinned:6.1.0`.

    A warning that fires on a dashboard nobody has laid out yet is a warning
    reviewers learn to skip, which costs the real one its meaning.
    """
    assert _layout_warnings("") == []
    assert _layout_warnings(None) == []
    assert _layout_warnings(_LayoutServed._ABSENT) == []
    # And it is still not silent about the shapes that are genuinely unreadable
    # -- including `"null"`, which parses to no mapping at all.
    assert _layout_warnings("null")
    assert _layout_warnings("[]")
    assert _layout_warnings("{}") == []
