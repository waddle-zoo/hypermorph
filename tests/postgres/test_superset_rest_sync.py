"""Live-REST sync against real Postgres (hy-gh-27 Phase C acceptance).

Payloads are the recorded pinned-Superset-6.1.0 REST captures
(`tests/fixtures/superset/6.1.0/revenue/{baseline,drift,restored}`), served
through `tests.fake_superset`, so persisted identity, versions, and change
records are asserted against real source shapes and the manifest's declared
UUIDs. `tests/compose/test_superset_live_sync.py` runs the same code against
the running instance.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from hyperset.connectors import run_sync
from hyperset.connectors.superset import SupersetConnector
from hyperset.connectors.types import UnresolvedLink
from hyperset.repositories.hash_basis import apply_hash_basis
from hyperset.repositories.postgres import (
    PostgresConnectionRepository,
    PostgresConnectorChangeRepository,
    PostgresGovernedContextRepository,
    PostgresObservedAssetRepository,
    PostgresSyncRepository,
)
from hyperset.repositories.postgres.observed_assets import _content_hash
from hyperset.repositories.scope import ALL_WORKSPACES
from tests.denominators import EstablishesDenominators
from tests.fake_superset import BASE_URL, FakeSupersetSession
from tests.postgres.test_superset_sync import _write_bundle_zip

_MANIFEST = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "superset"
        / "6.1.0"
        / "revenue"
        / "manifest.json"
    ).read_text()
)
_DATABASE_UUID = _MANIFEST["source_contract"]["database"]["native_uuid"]
_APPROVED_DATASET_UUID = next(
    asset["native_uuid"]
    for asset in _MANIFEST["source_contract"]["datasets"]
    if asset["role"] == "approved_candidate"
)
_OTHER_DATASET_UUID = next(
    asset["native_uuid"]
    for asset in _MANIFEST["source_contract"]["datasets"]
    if asset["native_uuid"] != _APPROVED_DATASET_UUID
)


@pytest.fixture
def connection_id(session_factory):
    return (
        PostgresConnectionRepository(session_factory)
        .create_or_update(
            connector_type="superset", display_name="Local Superset (REST)", config_ref=BASE_URL
        )
        .id
    )


def _connector(session) -> SupersetConnector:
    return SupersetConnector(
        base_url=BASE_URL, username="admin", password="s3cret", session=session
    )


def _sync(session, connection_id, session_factory, **kwargs):
    return run_sync(
        connector=_connector(session),
        connection_id=connection_id,
        session_factory=session_factory,
        **kwargs,
    )


@pytest.mark.postgres
def test_live_sync_persists_the_canonical_revenue_identities(session_factory, connection_id):
    result = _sync(FakeSupersetSession(), connection_id, session_factory)

    assert result.transport == "rest"
    assert result.counters == {
        "created": 4,
        "updated": 0,
        "restored": 0,
        "unchanged": 0,
        "deleted": 0,
    }
    assert _DATABASE_UUID in result.created
    assert _APPROVED_DATASET_UUID in result.created

    assets = PostgresObservedAssetRepository(session_factory)
    dataset = assets.get_by_external_id(
        connection_id=connection_id, external_id=_APPROVED_DATASET_UUID, asset_type="dataset"
    )
    assert dataset.current_version.version == 1
    assert dataset.current_version.raw_payload["table_name"] == "finance_orders_daily"
    # The whole REST detail body is retained, relative times included.
    assert dataset.current_version.raw_payload["created_on_humanized"] == "now"
    assert dataset.source_modified_at is not None

    run = PostgresSyncRepository(session_factory).get_run(result.sync_run_id)
    assert run.mode == "full"
    assert run.status == "succeeded"
    assert any("does not disclose its application version" in w for w in run.warnings)


@pytest.mark.postgres
def test_relationship_is_linked_by_native_uuid_not_display_name(session_factory, connection_id):
    result = _sync(FakeSupersetSession(), connection_id, session_factory)

    # Every dataset's parent database resolved, so no unresolved-link warning.
    assert not [w for w in result.warnings if w.startswith("unresolved link")]


@pytest.mark.postgres
def test_the_references_the_pinned_instance_declares_are_persisted(session_factory, connection_id):
    """Real pinned 6.1.0 REST captures: three datasets, one database, three
    `belongs_to` references. Before hy-d7xh these resolved and were then
    dropped, so nothing downstream could read the reference graph the source
    had already proved."""
    result = _sync(FakeSupersetSession(), connection_id, session_factory)

    assets = PostgresObservedAssetRepository(session_factory)
    database = assets.get_by_external_id(
        connection_id=connection_id, external_id=_DATABASE_UUID, asset_type="database"
    )
    incoming = assets.list_relationships(to_asset_id=database.id)

    assert result.relationships == 3
    assert {row.relation for row in incoming} == {"belongs_to"}
    datasets = {
        asset.id: asset.external_id
        for asset in assets.list_all(
            connection_id=connection_id, asset_type="dataset", workspace=ALL_WORKSPACES
        )
    }
    assert {datasets[row.from_asset_id] for row in incoming} == set(datasets.values())
    assert _APPROVED_DATASET_UUID in {datasets[row.from_asset_id] for row in incoming}


@pytest.mark.postgres
def test_a_resync_neither_duplicates_nor_renumbers_the_references(session_factory, connection_id):
    """The projection must be idempotent for the same reason the version chain
    is: a count over these rows is meant to say how many references the source
    declares, not how many times it was read."""
    first = _sync(FakeSupersetSession(), connection_id, session_factory)
    assets = PostgresObservedAssetRepository(session_factory)
    database = assets.get_by_external_id(
        connection_id=connection_id, external_id=_DATABASE_UUID, asset_type="database"
    )
    before = {row.id for row in assets.list_relationships(to_asset_id=database.id)}

    second = _sync(FakeSupersetSession(), connection_id, session_factory)

    assert (first.relationships, second.relationships) == (3, 3)
    assert {row.id for row in assets.list_relationships(to_asset_id=database.id)} == before


@pytest.mark.postgres
def test_the_reported_count_is_the_runs_own_assets_not_the_whole_table(
    session_factory, connection_id
):
    """`SyncResult.relationships` is scoped to the assets THIS run read, and
    every earlier test was blind to the difference because in all of them the
    run's assets were every asset (hy-ponl): a refactor to `SELECT count(*)`
    would have reported the table and passed the suite.

    The discriminating run is one where a dataset vanishes from the source. Its
    outgoing row is deliberately left standing -- `replace_relationships` never
    retracts references it did not re-read -- so the table holds three while
    the run reports two. Which is also why the count falling 3 -> 2 means "one
    fewer asset was read", not "a reference was retracted".
    """
    first = _sync(FakeSupersetSession(), connection_id, session_factory)
    assets = PostgresObservedAssetRepository(session_factory)
    database = assets.get_by_external_id(
        connection_id=connection_id, external_id=_DATABASE_UUID, asset_type="database"
    )
    vanished_uuid = _OTHER_DATASET_UUID

    class _WithoutOneDataset(EstablishesDenominators, SupersetConnector):
        # Establishes a dataset denominator, because the discriminating run
        # here is one where the vanished dataset IS soft-deleted, and after
        # hy-6nit nothing is soft-deleted without one. No shipped connector
        # establishes one yet.
        _warranted = ("dataset",)

        def normalize(self, snapshot):
            for item in super().normalize(snapshot):
                if item.external_id != vanished_uuid:
                    yield item

    second = run_sync(
        connector=_WithoutOneDataset(
            base_url=BASE_URL, username="admin", password="s3cret", session=FakeSupersetSession()
        ),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    assert (first.relationships, second.relationships) == (3, 2)
    # The table still holds three, and the vanished asset is soft-deleted with
    # its reference intact.
    assert len(assets.list_relationships(to_asset_id=database.id)) == 3
    vanished = assets.get_by_external_id(
        connection_id=connection_id, external_id=vanished_uuid, asset_type="dataset"
    )
    assert second.deleted == [vanished.id]
    assert vanished.deleted_at is not None
    assert len(assets.list_relationships(from_asset_id=vanished.id)) == 1
    # And the reader that wants "references from assets still live" says so,
    # rather than joining `deleted_at` itself row by row (hy-z21y).
    assert (
        len(assets.list_relationships(to_asset_id=database.id, include_deleted=False))
        == second.relationships
    )


@pytest.mark.postgres
def test_one_asset_served_twice_projects_its_last_payload_not_the_union(
    session_factory, connection_id
):
    """A source that serves the same asset twice in one snapshot: the version
    chain gives that asset's payload last-one-wins, so the projection must too.
    Accumulating both yields made the row for the first target claim a
    reference the newest payload does not make, contradicting the docstrings on
    `AssetRelationship` and `replace_relationships` (hy-3h7f).

    Nothing in the shipped connectors produces a duplicate yield today and
    nothing stops one: `DataHubGraphQLClient.scroll_entities` appends every
    `{urn, type}` from every page with no dedupe, and a scroll over a live
    search index can serve one URN on two pages (hy-dtpj). Fixed in the
    orchestration rather than in one client, so the projection's claim holds
    whatever a connector yields.
    """
    first = _sync(FakeSupersetSession(), connection_id, session_factory)
    assert first.relationships == 3

    class _ServesOneDatasetTwice(SupersetConnector):
        def normalize(self, snapshot):
            for item in super().normalize(snapshot):
                if item.external_id == _APPROVED_DATASET_UUID:
                    # First: a reference to another dataset. Then the real
                    # payload again, declaring only its parent database.
                    yield replace(
                        item,
                        links=[
                            UnresolvedLink(
                                kind="dataset",
                                target_external_id=_OTHER_DATASET_UUID,
                                relation="derived_from",
                            )
                        ],
                    )
                yield item

    second = run_sync(
        connector=_ServesOneDatasetTwice(
            base_url=BASE_URL, username="admin", password="s3cret", session=FakeSupersetSession()
        ),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    assets = PostgresObservedAssetRepository(session_factory)
    dataset = assets.get_by_external_id(
        connection_id=connection_id, external_id=_APPROVED_DATASET_UUID, asset_type="dataset"
    )
    outgoing = assets.list_relationships(from_asset_id=dataset.id)

    assert [row.relation for row in outgoing] == ["belongs_to"]
    assert second.relationships == 3
    # The dropped yield's target is not claimed by anyone.
    other = assets.get_by_external_id(
        connection_id=connection_id, external_id=_OTHER_DATASET_UUID, asset_type="dataset"
    )
    assert assets.list_relationships(to_asset_id=other.id) == []


@pytest.mark.postgres
def test_resync_of_unchanged_source_creates_no_version_despite_rerendered_times(
    session_factory, connection_id
):
    _sync(FakeSupersetSession(humanized="now"), connection_id, session_factory)
    # Same assets, later request: Superset re-renders every relative time.
    result = _sync(FakeSupersetSession(humanized="4 hours ago"), connection_id, session_factory)

    assert result.created == []
    assert result.updated == []
    assert len(result.unchanged) == 4

    assets = PostgresObservedAssetRepository(session_factory)
    dataset = assets.get_by_external_id(
        connection_id=connection_id, external_id=_APPROVED_DATASET_UUID, asset_type="dataset"
    )
    assert dataset.current_version.version == 1
    assert len(assets.history(dataset.id)) == 1
    # No version, no change: a no-op resync announces nothing downstream.
    assert PostgresConnectorChangeRepository(session_factory).list_for_run(result.sync_run_id) == []


@pytest.mark.postgres
def test_controlled_metric_drift_creates_exactly_one_new_version(session_factory, connection_id):
    _sync(FakeSupersetSession("baseline"), connection_id, session_factory)
    result = _sync(FakeSupersetSession("drift"), connection_id, session_factory)

    assert result.updated == [_APPROVED_DATASET_UUID]
    assert result.counters == {
        "created": 0,
        "updated": 1,
        "restored": 0,
        "unchanged": 3,
        "deleted": 0,
    }

    assets = PostgresObservedAssetRepository(session_factory)
    dataset = assets.get_by_external_id(
        connection_id=connection_id, external_id=_APPROVED_DATASET_UUID, asset_type="dataset"
    )
    history = assets.history(dataset.id)
    assert [version.version for version in history] == [1, 2]

    def expression(version):
        return next(
            metric["expression"]
            for metric in version.raw_payload["metrics"]
            if metric["metric_name"] == "recognized_revenue"
        )

    assert expression(history[0]) == _MANIFEST["controlled_drift"]["baseline_value"]
    assert expression(history[1]) == _MANIFEST["controlled_drift"]["drift_value"]
    # The superseded version is retained, not rewritten.
    assert history[0].sync_run_id != history[1].sync_run_id

    # One controlled source change is one immutable version AND one
    # ConnectorChange -- the durable record, not just the in-memory counters.
    changes = PostgresConnectorChangeRepository(session_factory)
    run_changes = changes.list_for_run(result.sync_run_id)
    assert len(run_changes) == 1
    change = run_changes[0]
    assert change.change_type == "updated"
    assert change.asset_id == dataset.id
    assert change.from_version_id == history[0].id
    assert change.to_version_id == history[1].id
    assert change.detail["external_id"] == _APPROVED_DATASET_UUID
    assert change.detail["from_content_hash"] == history[0].content_hash
    assert change.detail["to_content_hash"] == history[1].content_hash
    # The asset's own change history covers both the create and the update.
    assert [c.change_type for c in changes.list_for_asset(dataset.id)] == ["created", "updated"]


@pytest.mark.postgres
def test_checkpoint_survives_restart_and_is_resumed_by_the_next_sync(
    session_factory, connection_id
):
    first = _sync(FakeSupersetSession(), connection_id, session_factory)

    # "Restart": nothing but Postgres carries state into the next sync.
    persisted = PostgresSyncRepository(session_factory).get_checkpoint(connection_id)
    assert persisted == first.checkpoint
    # Four keys, two of them zero: this capture's instance holds no chart or
    # dashboard, and a covered type with none of it is still a type that was
    # read (hy-rt4v).
    assert persisted["asset_counts"] == {
        "database": 1,
        "dataset": 3,
        "chart": 0,
        "dashboard": 0,
    }

    second = _sync(FakeSupersetSession(), connection_id, session_factory)
    assert (
        second.checkpoint["resumed_from_high_watermark"] == persisted["high_watermark_changed_on"]
    )

    # Identity is stable across the restart: no new asset rows, no new versions.
    assets = PostgresObservedAssetRepository(session_factory)
    assert len(assets.list_all(connection_id=connection_id, workspace=ALL_WORKSPACES)) == 4
    assert second.created == []


@pytest.mark.postgres
def test_claiming_a_type_is_what_makes_it_eligible_for_deletion_checking(
    session_factory, connection_id, tmp_path
):
    """The cost of hy-rt4v's coverage, taken deliberately and asserted rather
    than discovered later.

    This test used to say the opposite -- a dashboard seen through an export
    bundle survived a REST sync, because REST did not look for dashboards.
    That protection was never about dashboards. It is `sync.py`'s rule that a
    type absent from `covered_asset_types` is never deletion-checked, and it
    now applies to Superset REST for nothing, because REST looks for all four.

    Then main moved and the answer moved with it. hy-6nit's deny half
    (`2d3bf86`) made coverage NECESSARY AND NOT SUFFICIENT: a full sync
    soft-deletes only the types whose snapshot carries an
    `EstablishedDenominator` and declines the rest out loud, and no shipped
    connector establishes one. So this test is its own third version and it
    asserts both halves, because either alone reads as the whole rule:

    1. the shipped REST connector covers dashboards, deletes nothing, and says
       why -- which is the state a live estate is in today;
    2. with a denominator established, the dashboard IS soft-deleted, and
       coverage is what selects it. Without that half the test would pass
       against a gate hard-coded to refuse (`tests/denominators.py`).

    What makes (2) safe rather than reckless is that a partial look cannot
    happen -- any non-200 raises before the deletion pass, which
    `test_failed_read_fails_the_run_and_implies_no_deletion` pins.

    The coverage rule itself is still exercised where a connector really does
    cover less than it could: DataHub excludes charts (`tests/unit/connectors/
    test_datahub_graphql.py`).
    """
    import zipfile

    import yaml

    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(
            "dashboards/q3.yaml",
            yaml.safe_dump({"dashboard_title": "Q3 Review", "uuid": "dash-1", "position": {}}),
        )
    run_sync(
        connector=SupersetConnector(bundle_path=zip_path),
        connection_id=connection_id,
        session_factory=session_factory,
    )
    assets = PostgresObservedAssetRepository(session_factory)

    declined = _sync(FakeSupersetSession(), connection_id, session_factory)

    dashboard = assets.get_by_external_id(
        connection_id=connection_id, external_id="dash-1", asset_type="dashboard"
    )
    assert declined.deleted == []
    assert dashboard.deleted_at is None
    assert [
        warning
        for warning in declined.warnings
        if "deletion declined" in warning and "'dashboard'" in warning
    ], declined.warnings

    class _EstablishesDashboards(EstablishesDenominators, SupersetConnector):
        _warranted = ("dashboard",)

    established = run_sync(
        connector=_EstablishesDashboards(
            base_url=BASE_URL, username="admin", password="s3cret", session=FakeSupersetSession()
        ),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    dashboard = assets.get_by_external_id(
        connection_id=connection_id, external_id="dash-1", asset_type="dashboard"
    )
    assert established.deleted == [dashboard.id]
    assert dashboard.deleted_at is not None
    # Soft, and reversible by the source reappearing: the row and its version
    # history stay, which is what makes a restore a restore rather than a
    # second first observation.
    assert dashboard.external_id == "dash-1"


@pytest.mark.postgres
def test_a_type_absent_from_covered_asset_types_is_not_deletion_checked_even_when_warranted(
    session_factory, connection_id, tmp_path
):
    """The coverage gate on the deletion pass, pinned where nothing else pins it (hy-k78j).

    `run_sync` seeds one seen-set per `covered_asset_types` entry, and THAT seeding
    is the whole reason a type the connector never read this run stays out of the
    deletion pass -- an uncovered type is never a key, so `mark_missing_deleted` is
    never called for it. #175 renamed the arm that used to hold this line (a
    dashboard seen through an export bundle surviving a REST sync that did not cover
    dashboards) because REST now covers all four types, so that scenario is
    unreachable through the shipped connector. The RULE it depended on lost its test.

    The hazard is real, not hypothetical: a widening of the seed to
    `{asset_type: set() for asset_type in (*covered_asset_types, "database",
    "dataset", "chart", "dashboard")}` -- covered types still seeded, four never-read
    types added -- passes the entire postgres + unit suite unchanged. The first
    connector to warrant a type it did not read that run would then soft-delete live
    assets of it.

    So this isolates the seeding gate as the ONLY thing standing between an uncovered
    type and deletion, by warranting its denominator. hy-6nit's default-deny is not
    the guard here: the denominator IS established, so if coverage did not gate the
    pass the dashboard would be deleted. The two connectors differ in exactly one
    axis -- whether `covered_asset_types` includes "dashboard" -- and that axis
    decides deletion:

    1. warranted but NOT covered (and never read): dash-1 survives;
    2. warranted AND covered: dash-1 is soft-deleted.

    Half (2) is the positive control. Without it, half (1) would also pass against a
    denominator double that silently established nothing, or against a mutant that
    never reaches the pass for any reason -- the survival would prove non-coverage
    only if a covered run with the same warrant deletes.
    """
    import zipfile

    import yaml

    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(
            "dashboards/q3.yaml",
            yaml.safe_dump({"dashboard_title": "Q3 Review", "uuid": "dash-1", "position": {}}),
        )
    run_sync(
        connector=SupersetConnector(bundle_path=zip_path),
        connection_id=connection_id,
        session_factory=session_factory,
    )
    assets = PostgresObservedAssetRepository(session_factory)

    class _WarrantsDashboardsButDoesNotCoverThem(EstablishesDenominators, SupersetConnector):
        """Warrants a dashboard denominator for a type it neither covers nor reads.

        The shipped REST connector covers all four types, so this combination cannot
        arise from it -- it is built here on purpose to leave the seeding gate as the
        single reason the dashboard is spared. `_warranted` establishes the
        denominator; the override drops "dashboard" from coverage AND empties the
        dashboards the snapshot carries, so `normalize` yields none and the type
        never becomes a seen key that way either.
        """

        _warranted = ("dashboard",)

        def snapshot(self, checkpoint=None):
            base = super().snapshot(checkpoint)
            return replace(
                base,
                covered_asset_types=tuple(t for t in base.covered_asset_types if t != "dashboard"),
                bundle={**base.bundle, "dashboards": []},
            )

    uncovered = run_sync(
        connector=_WarrantsDashboardsButDoesNotCoverThem(
            base_url=BASE_URL, username="admin", password="s3cret", session=FakeSupersetSession()
        ),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    dashboard = assets.get_by_external_id(
        connection_id=connection_id, external_id="dash-1", asset_type="dashboard"
    )
    # The load-bearing assertion: a widened seed would have deletion-checked the
    # warranted dashboard against an empty seen-set and soft-deleted dash-1.
    assert dashboard.deleted_at is None
    assert dashboard.id not in uncovered.deleted
    # A type not in the pass is not "declined for want of a denominator" either --
    # it is not considered at all, which is a different state than default-deny.
    assert not [
        warning
        for warning in uncovered.warnings
        if "deletion declined" in warning and "'dashboard'" in warning
    ], uncovered.warnings

    class _CoversAndWarrantsDashboards(EstablishesDenominators, SupersetConnector):
        _warranted = ("dashboard",)

    covered = run_sync(
        connector=_CoversAndWarrantsDashboards(
            base_url=BASE_URL, username="admin", password="s3cret", session=FakeSupersetSession()
        ),
        connection_id=connection_id,
        session_factory=session_factory,
    )

    dashboard = assets.get_by_external_id(
        connection_id=connection_id, external_id="dash-1", asset_type="dashboard"
    )
    assert covered.deleted == [dashboard.id]
    assert dashboard.deleted_at is not None


@pytest.mark.postgres
def test_failed_read_fails_the_run_and_implies_no_deletion(session_factory, connection_id):
    _sync(FakeSupersetSession(), connection_id, session_factory)

    with pytest.raises(Exception, match="403"):
        _sync(
            FakeSupersetSession(status_overrides={"/api/v1/dataset/": 403}),
            connection_id,
            session_factory,
        )

    assets = PostgresObservedAssetRepository(session_factory)
    dataset = assets.get_by_external_id(
        connection_id=connection_id, external_id=_APPROVED_DATASET_UUID, asset_type="dataset"
    )
    assert dataset.deleted_at is None
    runs = PostgresSyncRepository(session_factory).list_runs(connection_id)
    assert runs[-1].status == "failed"
    assert any("403" in error for error in runs[-1].errors)


@pytest.mark.postgres
def test_raise_after_the_read_fails_the_run_instead_of_leaving_it_running(
    session_factory, connection_id
):
    """A read that succeeds and a persist step that raises must still finish
    the run: a `SyncRun` stuck in "running" is indistinguishable from one in
    flight, and hy-gh-49's retry semantics read that status (hy-y8g.4)."""

    class _RaisingConnector(SupersetConnector):
        def normalize(self, snapshot):
            for index, item in enumerate(super().normalize(snapshot)):
                if index == 1:
                    raise RuntimeError("upsert loop exploded")
                yield item

    connector = _RaisingConnector(
        base_url=BASE_URL, username="admin", password="s3cret", session=FakeSupersetSession()
    )
    with pytest.raises(RuntimeError, match="upsert loop exploded"):
        run_sync(connector=connector, connection_id=connection_id, session_factory=session_factory)

    run = PostgresSyncRepository(session_factory).list_runs(connection_id)[-1]
    assert run.status == "failed"
    assert run.finished_at is not None
    assert any("upsert loop exploded" in error for error in run.errors)


@pytest.mark.postgres
def test_live_sync_never_creates_governed_context_or_persists_credentials(
    session_factory, connection_id
):
    _sync(FakeSupersetSession(), connection_id, session_factory)

    assert PostgresGovernedContextRepository(session_factory).search("finance_orders_daily") == []

    assets = PostgresObservedAssetRepository(session_factory)
    payloads = json.dumps(
        [
            record.current_version.raw_payload
            for record in assets.list_all(connection_id=connection_id, workspace=ALL_WORKSPACES)
        ]
    )
    assert "s3cret" not in payloads
    assert "test-access-token" not in payloads
    connection = PostgresConnectionRepository(session_factory).get(connection_id)
    assert connection.config_ref == BASE_URL  # base URL only, never a credential


@pytest.mark.postgres
def test_a_rest_version_stores_the_hash_basis_that_produced_its_content_hash(
    session_factory, connection_id
):
    """The REST transport narrows change detection past `*_humanized`, so the
    stored hash covers less than the stored payload. The narrowing rule is
    persisted with the version, so a later reader (hy-gh-48's incremental
    scanning) can re-verify the hash from Postgres alone rather than trusting
    the connector build that wrote it (hy-y8g finding 2)."""
    result = _sync(FakeSupersetSession(), connection_id, session_factory)
    assert any("created_on_humanized" in warning for warning in result.warnings)

    assets = PostgresObservedAssetRepository(session_factory)
    dataset = assets.get_by_external_id(
        connection_id=connection_id, external_id=_APPROVED_DATASET_UUID, asset_type="dataset"
    )
    version = dataset.current_version

    assert version.hash_basis == {"drop_key_suffixes": ["_humanized"]}
    assert version.raw_payload["created_on_humanized"]  # the real payload is stored whole
    assert _content_hash(apply_hash_basis(version.raw_payload, version.hash_basis)) == (
        version.content_hash
    )
    assert _content_hash(version.raw_payload) != version.content_hash


@pytest.mark.postgres
def test_a_second_transport_settles_after_one_version_and_then_stops(
    session_factory, connection_id, tmp_path
):
    """The hy-gh-48 path, pinned before hy-gh-48 exists (hy-y8g.3, hy-6t4).

    A connection carries one `config_ref` today; hy-gh-48's whole point is to
    give it a second read mode. When that lands, an asset observed over REST
    gets read again through a bundle, and change detection compares against
    the most recent version FROM THE SAME MODE (hy-6t4). So:

    - the bundle's first sight of the asset appends exactly one version, which
      is correct and is not suppressed -- suppressing it would be
      indistinguishable from failing to notice a real change;
    - every read after that, in either mode, appends nothing, however the
      schedule alternates. That is the churn this rule exists to stop, and it
      is asserted over several cycles rather than one because one cycle cannot
      tell "settled" from "alternating".

    The payload is deliberately the same bytes through both transports: what
    is under test is the comparison, not the content. `partial=True` on the
    bundle runs because a bundle covering only datasets must not be read as
    evidence that the REST-observed databases are gone.
    """
    assets = PostgresObservedAssetRepository(session_factory)
    changes = PostgresConnectorChangeRepository(session_factory)
    _sync(FakeSupersetSession(), connection_id, session_factory)
    observed = assets.get_by_external_id(
        connection_id=connection_id, external_id=_APPROVED_DATASET_UUID, asset_type="dataset"
    )
    bundle_path = _write_bundle_zip(
        tmp_path,
        "rest-payload-as-export",
        datasets={"finance": observed.current_version.raw_payload},
    )

    def bundle_sync():
        return run_sync(
            connector=SupersetConnector(bundle_path=bundle_path),
            connection_id=connection_id,
            session_factory=session_factory,
            partial=True,
        )

    first_bundle = bundle_sync()

    assert first_bundle.counters == {
        "created": 0,
        "updated": 1,
        "restored": 0,
        "unchanged": 0,
        "deleted": 0,
    }
    # Updated, not created: the asset has been there since the REST run, and a
    # change stream saying "created" would announce an appearance that did not
    # happen. The change row says which lineage the claim was made within.
    (announced,) = changes.list_for_run(first_bundle.sync_run_id)
    assert announced.change_type == "updated"
    assert announced.detail["transport"] == "export_bundle"

    # The export path discloses what the shared rule excluded, same as REST.
    # Real export YAML carries no `*_humanized` key, so this bundle -- built
    # from a REST payload -- is the only place that branch can be observed at
    # all (hy-sv7).
    assert any("created_on_humanized" in warning for warning in first_bundle.warnings)

    # Now alternate. Nothing the source did changed, so nothing should be
    # appended or announced again, in either mode.
    for _ in range(3):
        for result in (bundle_sync(), _sync(FakeSupersetSession(), connection_id, session_factory)):
            assert result.updated == []
            assert result.created == []
            assert changes.list_for_run(result.sync_run_id) == []

    settled = assets.get_by_external_id(
        connection_id=connection_id, external_id=_APPROVED_DATASET_UUID, asset_type="dataset"
    )
    history = assets.history(settled.id)
    assert [version.version for version in history] == [1, 2]
    assert [version.transport for version in history] == ["rest", "export_bundle"]


_EXPORT_BUNDLE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "superset"
    / "6.1.0"
    / "revenue"
    / "baseline"
    / "official-export.zip"
)


@pytest.mark.postgres
def test_the_real_two_shapes_stop_appending_versions_after_the_first(
    session_factory, connection_id
):
    """The churn hy-6t4 is actually about, with the real payloads.

    The test above uses one payload through two transports, which isolates the
    comparison. This one uses what the two transports really serve: the pinned
    6.1.0 export bundle and the REST detail bodies for the same three
    datasets. They disagree -- 23 keys against 42 -- and they always will,
    because each carries server bookkeeping the other cannot see, so under a
    single cross-mode comparison every alternation appended an immutable
    version for assets nobody edited.

    Comparing within a read mode settles it: each mode's first sight of an
    asset appends one version, and then nothing, however long the schedule
    alternates.
    """
    assets = PostgresObservedAssetRepository(session_factory)
    changes = PostgresConnectorChangeRepository(session_factory)

    def bundle_sync():
        return run_sync(
            connector=SupersetConnector(bundle_path=str(_EXPORT_BUNDLE)),
            connection_id=connection_id,
            session_factory=session_factory,
            partial=True,
        )

    _sync(FakeSupersetSession(), connection_id, session_factory)
    bundle_sync()  # the bundle's first sight of these assets: one version each

    for _ in range(3):
        for result in (_sync(FakeSupersetSession(), connection_id, session_factory), bundle_sync()):
            assert result.created == []
            assert result.updated == []
            assert changes.list_for_run(result.sync_run_id) == []

    dataset = assets.get_by_external_id(
        connection_id=connection_id, external_id=_APPROVED_DATASET_UUID, asset_type="dataset"
    )
    history = assets.history(dataset.id)
    assert [version.version for version in history] == [1, 2]
    assert [version.transport for version in history] == ["rest", "export_bundle"]
    # Both payloads kept whole, which is what makes the two hashes differ and
    # is the thing a common projection would have had to throw away.
    assert len(history[0].raw_payload) == 42
    assert len(history[1].raw_payload) == 23
