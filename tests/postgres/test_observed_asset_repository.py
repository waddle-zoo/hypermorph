from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from hyperset.connectors.superset.connector import _HASH_BASIS
from hyperset.db.models import AssetRelationship, ObservedAssetVersion
from hyperset.repositories.errors import NotFoundError
from hyperset.repositories.hash_basis import apply_hash_basis
from hyperset.repositories.postgres import (
    PostgresConnectionRepository,
    PostgresConnectorChangeRepository,
    PostgresObservedAssetRepository,
    PostgresSyncRepository,
)
from hyperset.repositories.postgres.observed_assets import _content_hash


@pytest.fixture
def ctx(session_factory):
    connections = PostgresConnectionRepository(session_factory)
    syncs = PostgresSyncRepository(session_factory)
    connection = connections.create_or_update(connector_type="superset", display_name="Local")
    run = syncs.begin_run(connection.id, mode="full")
    return connection.id, run.id


@pytest.mark.postgres
def test_idempotent_unchanged_asset_sync(session_factory, ctx):
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    payload = {"table_name": "orders", "columns": [{"name": "id"}]}

    record1, outcome1 = repo.upsert(
        connection_id=connection_id,
        external_id="orders",
        asset_type="dataset",
        sync_run_id=run_id,
        raw_payload=payload,
        normalized={"name": "orders"},
    )
    assert outcome1 == "created"
    assert record1.current_version.version == 1

    record2, outcome2 = repo.upsert(
        connection_id=connection_id,
        external_id="orders",
        asset_type="dataset",
        sync_run_id=run_id,
        raw_payload=payload,
        normalized={"name": "orders"},
    )
    assert outcome2 == "unchanged"
    assert record2.current_version.version == 1
    assert record2.id == record1.id


@pytest.mark.postgres
def test_changed_asset_creates_exactly_one_new_version(session_factory, ctx):
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    repo.upsert(
        connection_id=connection_id,
        external_id="orders",
        asset_type="dataset",
        sync_run_id=run_id,
        raw_payload={"table_name": "orders", "row_count": 100},
    )
    record, outcome = repo.upsert(
        connection_id=connection_id,
        external_id="orders",
        asset_type="dataset",
        sync_run_id=run_id,
        raw_payload={"table_name": "orders", "row_count": 200},
    )
    assert outcome == "updated"
    assert record.current_version.version == 2

    history = repo.history(record.id)
    assert [v.version for v in history] == [1, 2]


@pytest.mark.postgres
def test_deletion_and_reappearance_preserves_history(session_factory, ctx):
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    record, _ = repo.upsert(
        connection_id=connection_id,
        external_id="orders",
        asset_type="dataset",
        sync_run_id=run_id,
        raw_payload={"table_name": "orders"},
    )

    deleted_ids = repo.mark_missing_deleted(
        connection_id=connection_id,
        asset_type="dataset",
        seen_external_ids=set(),
        sync_run_id=run_id,
    )
    assert deleted_ids == [record.id]
    after_delete = repo.get(record.id)
    assert after_delete.deleted_at is not None
    # History survives a soft delete.
    assert len(repo.history(record.id)) == 1

    reappeared, outcome = repo.upsert(
        connection_id=connection_id,
        external_id="orders",
        asset_type="dataset",
        sync_run_id=run_id,
        raw_payload={"table_name": "orders"},
    )
    assert reappeared.deleted_at is None
    assert reappeared.id == record.id
    # Unchanged payload on reappearance -- no spurious new version, but
    # the reappearance itself is what the caller is told about.
    assert outcome == "restored"
    assert len(repo.history(record.id)) == 1

    changes = PostgresConnectorChangeRepository(session_factory).list_for_asset(record.id)
    # The reappearance is announced even though it added no version: the
    # stream alone has to say the asset is alive again (hy-y8g.1).
    assert [c.change_type for c in changes] == ["created", "deleted", "restored"]
    # Absence observes no content, so the delete points only backwards.
    assert changes[1].from_version_id == repo.history(record.id)[0].id
    assert changes[1].to_version_id is None
    # So does an unchanged reappearance -- no new version to point at.
    assert changes[2].from_version_id == repo.history(record.id)[0].id
    assert changes[2].to_version_id is None
    assert changes[2].detail["content_changed"] is False


@pytest.mark.postgres
def test_reappearance_with_changed_content_is_one_restored_change(session_factory, ctx):
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    record, _ = repo.upsert(
        connection_id=connection_id,
        external_id="orders",
        asset_type="dataset",
        sync_run_id=run_id,
        raw_payload={"table_name": "orders", "row_count": 100},
    )
    repo.mark_missing_deleted(
        connection_id=connection_id,
        asset_type="dataset",
        seen_external_ids=set(),
        sync_run_id=run_id,
    )

    reappeared, outcome = repo.upsert(
        connection_id=connection_id,
        external_id="orders",
        asset_type="dataset",
        sync_run_id=run_id,
        raw_payload={"table_name": "orders", "row_count": 200},
    )
    assert outcome == "restored"
    assert reappeared.deleted_at is None
    assert reappeared.current_version.version == 2

    changes = PostgresConnectorChangeRepository(session_factory).list_for_asset(record.id)
    # One reappearance is one change, whether or not the content moved: the
    # new version is what "restored" points at instead of a second "updated".
    assert [c.change_type for c in changes] == ["created", "deleted", "restored"]
    assert changes[2].to_version_id == reappeared.current_version.id
    assert changes[2].detail["content_changed"] is True


@pytest.mark.postgres
def test_mark_missing_deleted_only_affects_named_asset_type(session_factory, ctx):
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    dataset, _ = repo.upsert(
        connection_id=connection_id,
        external_id="orders",
        asset_type="dataset",
        sync_run_id=run_id,
        raw_payload={"table_name": "orders"},
    )
    chart, _ = repo.upsert(
        connection_id=connection_id,
        external_id="orders_chart",
        asset_type="chart",
        sync_run_id=run_id,
        raw_payload={"slice_name": "Orders"},
    )
    repo.mark_missing_deleted(
        connection_id=connection_id,
        asset_type="dataset",
        seen_external_ids=set(),
        sync_run_id=run_id,
    )
    assert repo.get(dataset.id).deleted_at is not None
    assert repo.get(chart.id).deleted_at is None


@pytest.mark.postgres
def test_raw_jsonb_round_trip_preserves_unknown_fields(session_factory, ctx):
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    payload = {
        "table_name": "orders",
        "some_future_superset_7_field": {"deeply": {"nested": [1, 2, 3]}},
        "null_field": None,
        "bool_field": True,
    }
    record, _ = repo.upsert(
        connection_id=connection_id,
        external_id="orders",
        asset_type="dataset",
        sync_run_id=run_id,
        raw_payload=payload,
    )
    fetched = repo.get(record.id)
    assert fetched.current_version.raw_payload == payload


@pytest.mark.postgres
def test_a_read_that_discloses_no_timestamp_leaves_an_earlier_one_standing(session_factory, ctx):
    """`source_modified_at=None` means "this read did not disclose one", not
    "the source has none" (hy-y8g.3). Superset's export YAML carries no
    `changed_on` while its REST detail body does, so once one connection
    carries both read modes (hy-gh-48), overwriting unconditionally would let
    a bundle read erase a modification time a REST read proved."""
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    modified_at = datetime(2026, 7, 25, 23, 48, 50, tzinfo=UTC)
    repo.upsert(
        connection_id=connection_id,
        external_id="orders",
        asset_type="dataset",
        sync_run_id=run_id,
        raw_payload={"table_name": "orders", "changed_on": modified_at.isoformat()},
        source_modified_at=modified_at,
    )

    record, outcome = repo.upsert(
        connection_id=connection_id,
        external_id="orders",
        asset_type="dataset",
        sync_run_id=run_id,
        raw_payload={"table_name": "orders"},
        source_modified_at=None,
    )

    # A different payload is a real new version -- what must not change is the
    # timestamp the earlier read established.
    assert outcome == "updated"
    assert record.source_modified_at == modified_at


@pytest.mark.postgres
def test_a_version_stored_before_the_shared_rule_is_not_rehashed_by_it(session_factory, ctx):
    """The upgrade case for hy-y8g.3, at the level where the migration
    actually happens (hy-sv7).

    Applying the connector's declared basis to export payloads is only safe
    because it changes no stored hash. A version row written before that
    change carries `{}` and its hash covers the whole payload; the very next
    sync passes `_HASH_BASIS`. If those two ever disagree -- because the rule
    grew to drop something export YAML does carry -- every stored bundle
    version re-hashes and the next sync appends a version nobody earned,
    across every deployment that had already synced.

    Not covered by `test_repeated_sync_of_unchanged_bundle_is_idempotent`:
    both of its syncs run the same connector at the same code version, so both
    write the same basis and no old row ever meets a new rule.
    """
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    # Export-shaped: what Superset's YAML carries, with no `*_humanized` key.
    payload = {
        "table_name": "orders",
        "uuid": "dataset-1",
        "schema": "public",
        "columns": [{"column_name": "order_id"}],
    }

    stored, created = repo.upsert(
        connection_id=connection_id,
        external_id="dataset-1",
        asset_type="dataset",
        sync_run_id=run_id,
        raw_payload=payload,
        hash_basis=None,  # the pre-hy-y8g.3 export read: no declared rule
    )
    assert created == "created"
    assert stored.current_version.hash_basis == {}

    record, outcome = repo.upsert(
        connection_id=connection_id,
        external_id="dataset-1",
        asset_type="dataset",
        sync_run_id=run_id,
        raw_payload=payload,
        hash_basis=_HASH_BASIS,  # the same read after the change
    )

    assert outcome == "unchanged"
    assert record.current_version.version == 1
    assert len(repo.history(record.id)) == 1
    # The whole stream for this run, not a slice of it: the first read's
    # "created" and nothing the rule change added.
    changes = PostgresConnectorChangeRepository(session_factory).list_for_run(run_id)
    assert [change.change_type for change in changes] == ["created"]


@pytest.mark.postgres
def test_get_by_external_id_missing_raises_not_found(session_factory, ctx):
    connection_id, _ = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    with pytest.raises(NotFoundError):
        repo.get_by_external_id(
            connection_id=connection_id, external_id="nope", asset_type="dataset"
        )


@pytest.mark.postgres
def test_search_finds_by_normalized_text(session_factory, ctx):
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    repo.upsert(
        connection_id=connection_id,
        external_id="orders",
        asset_type="dataset",
        sync_run_id=run_id,
        raw_payload={"table_name": "orders"},
        normalized={"name": "orders", "description": "Finance orders table"},
    )
    repo.upsert(
        connection_id=connection_id,
        external_id="customers",
        asset_type="dataset",
        sync_run_id=run_id,
        raw_payload={"table_name": "customers"},
        normalized={"name": "customers", "description": "Customer dimension"},
    )
    hits = repo.search("finance")
    assert len(hits) == 1
    assert hits[0].external_id == "orders"


@pytest.mark.postgres
def test_a_version_records_the_basis_its_hash_was_computed_under(session_factory, ctx):
    """hy-y8g finding 2: a narrowed hash used to be unverifiable, because the
    projection lived in connector memory and only the whole payload was
    stored. The rule is now on the row, so the hash is recomputable from
    stored state alone -- which is what incremental scanning will lean on."""
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    payload = {"table_name": "orders", "changed_on_humanized": "now"}
    basis = {"drop_key_suffixes": ["_humanized"]}

    record, _ = repo.upsert(
        connection_id=connection_id,
        external_id="orders",
        asset_type="dataset",
        sync_run_id=run_id,
        raw_payload=payload,
        hash_basis=basis,
    )

    version = record.current_version
    assert version.hash_basis == basis
    assert version.raw_payload == payload  # still whole, nothing dropped from storage
    assert _content_hash(apply_hash_basis(version.raw_payload, version.hash_basis)) == (
        version.content_hash
    )
    # Load-bearing: without the stored basis the hash cannot be reproduced.
    assert _content_hash(version.raw_payload) != version.content_hash


@pytest.mark.postgres
def test_an_unnarrowed_version_records_that_its_hash_covers_the_whole_payload(session_factory, ctx):
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    payload = {"table_name": "orders", "columns": [{"name": "id"}]}

    record, _ = repo.upsert(
        connection_id=connection_id,
        external_id="orders",
        asset_type="dataset",
        sync_run_id=run_id,
        raw_payload=payload,
    )

    version = record.current_version
    assert version.hash_basis == {}  # not NULL: "nothing narrowed", not "unrecorded"
    assert _content_hash(apply_hash_basis(version.raw_payload, version.hash_basis)) == (
        version.content_hash
    )


@pytest.mark.postgres
def test_a_change_within_one_transport_is_still_detected(session_factory, ctx):
    """The property comparing-within-a-mode must not break while fixing the
    other one (hy-6t4): two reads by the SAME mode still see a real edit."""
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)

    def observe(payload):
        return repo.upsert(
            connection_id=connection_id,
            external_id="orders",
            asset_type="dataset",
            sync_run_id=run_id,
            raw_payload=payload,
            transport="rest",
        )

    observe({"table_name": "orders", "sql": "SELECT 1"})
    record, outcome = observe({"table_name": "orders", "sql": "SELECT 2"})

    assert outcome == "updated"
    assert [version.version for version in repo.history(record.id)] == [1, 2]
    assert [version.transport for version in repo.history(record.id)] == ["rest", "rest"]


@pytest.mark.postgres
def test_rows_written_before_the_transport_column_settle_after_one_version(session_factory, ctx):
    """The upgrade case, and it is expected rather than suppressed (hy-6t4).

    Every version stored before this column existed has `transport` NULL, and
    guessing one would be false -- a run's `mode` collapses REST and GraphQL
    into "full". So the first sync after the migration appends one version per
    asset, ONCE, and settles. Suppressing that first version would be
    indistinguishable from failing to notice a real change.
    """
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    payload = {"table_name": "orders", "columns": [{"column_name": "id"}]}

    def observe(transport):
        return repo.upsert(
            connection_id=connection_id,
            external_id="orders",
            asset_type="dataset",
            sync_run_id=run_id,
            raw_payload=payload,
            transport=transport,
        )

    observe(None)  # a row from before the column existed
    record, first = observe("rest")
    assert first == "updated"

    for _ in range(3):
        _, outcome = observe("rest")
        assert outcome == "unchanged"

    history = repo.history(record.id)
    assert [version.version for version in history] == [1, 2]
    assert [version.transport for version in history] == [None, "rest"]


@pytest.mark.postgres
def test_a_transport_spelt_differently_does_not_fork_the_lineage(session_factory, ctx):
    """Normalised where it is written, not only where it is stored (hy-6t4).

    A transport that differs by case or by a trailing space would start a
    SECOND lineage: the next observation finds nothing to compare against,
    appends a version, and reports first-sight -- forever, and every symptom
    of it looks exactly like the defect comparing-within-a-transport fixes.
    The CHECK constraint would reject these outright; normalising first means
    a stray space does not fail a sync it obviously meant to succeed.
    """
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    payload = {"table_name": "orders"}

    def observe(transport):
        return repo.upsert(
            connection_id=connection_id,
            external_id="orders",
            asset_type="dataset",
            sync_run_id=run_id,
            raw_payload=payload,
            transport=transport,
        )

    record, created = observe("rest")
    assert created == "created"

    for spelling in (" rest", "REST ", "Rest"):
        _, outcome = observe(spelling)
        assert outcome == "unchanged", spelling

    history = repo.history(record.id)
    assert [version.version for version in history] == [1]
    assert [version.transport for version in history] == ["rest"]


@pytest.mark.postgres
def test_the_database_refuses_a_transport_outside_the_vocabulary(session_factory, ctx):
    """The backstop behind the normalisation, and the reason the constraint
    landed with the column rather than after it: added later it would have
    needed a data audit for lineages that had already forked."""
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    record, _ = repo.upsert(
        connection_id=connection_id,
        external_id="orders",
        asset_type="dataset",
        sync_run_id=run_id,
        raw_payload={"table_name": "orders"},
        transport="rest",
    )

    with pytest.raises(IntegrityError, match="valid_version_transport"):
        with session_factory() as session, session.begin():
            session.add(
                ObservedAssetVersion(
                    asset_id=record.id,
                    sync_run_id=run_id,
                    version=99,
                    raw_payload={},
                    normalized={},
                    content_hash="deadbeef",
                    transport="carrier-pigeon",
                )
            )


# -- declared references (hy-d7xh) ------------------------------------------
#
# `asset_relationships` shipped in the initial schema and nothing wrote a row
# for the whole of v0, so hy-gh-124 had no observed reference graph to count
# over. These pin the projection's contract: what the source's newest payload
# declared, nothing derived, and nothing left standing after the source stops
# declaring it.


def _observed(repo, connection_id, run_id, external_id, asset_type):
    record, _ = repo.upsert(
        connection_id=connection_id,
        external_id=external_id,
        asset_type=asset_type,
        sync_run_id=run_id,
        raw_payload={"external_id": external_id},
    )
    return record


@pytest.mark.postgres
def test_declared_reference_survives_a_round_trip_through_the_store(session_factory, ctx):
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    chart = _observed(repo, connection_id, run_id, "chart-1", "chart")
    dataset = _observed(repo, connection_id, run_id, "dataset-1", "dataset")

    written = repo.replace_relationships(declared={chart.id: [("queries", dataset.id)]})

    assert [(row.from_asset_id, row.relation, row.to_asset_id) for row in written] == [
        (chart.id, "queries", dataset.id)
    ]
    # Read back through the other side of the projection: what points AT the
    # dataset is the access path a reference count needs.
    incoming = repo.list_relationships(to_asset_id=dataset.id)
    assert [(row.from_asset_id, row.relation) for row in incoming] == [(chart.id, "queries")]
    assert incoming[0].id == written[0].id


@pytest.mark.postgres
def test_redeclaring_the_same_reference_keeps_the_row_id(session_factory, ctx):
    """Re-observing an unchanged source must not churn the projection: a
    delete-and-reinsert would hand every reader a new id for a reference that
    never changed, and a count of rows would still be right while nothing
    could be tracked across syncs."""
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    chart = _observed(repo, connection_id, run_id, "chart-1", "chart")
    dataset = _observed(repo, connection_id, run_id, "dataset-1", "dataset")

    first = repo.replace_relationships(declared={chart.id: [("queries", dataset.id)]})
    second = repo.replace_relationships(declared={chart.id: [("queries", dataset.id)]})

    assert [row.id for row in second] == [row.id for row in first]
    assert len(repo.list_relationships(from_asset_id=chart.id)) == 1


@pytest.mark.postgres
def test_a_reference_the_source_stopped_declaring_is_removed(session_factory, ctx):
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    dashboard = _observed(repo, connection_id, run_id, "dash-1", "dashboard")
    kept = _observed(repo, connection_id, run_id, "chart-1", "chart")
    dropped = _observed(repo, connection_id, run_id, "chart-2", "chart")
    repo.replace_relationships(
        declared={dashboard.id: [("contains", kept.id), ("contains", dropped.id)]}
    )

    remaining = repo.replace_relationships(declared={dashboard.id: [("contains", kept.id)]})

    assert [row.to_asset_id for row in remaining] == [kept.id]
    assert [row.to_asset_id for row in repo.list_relationships(from_asset_id=dashboard.id)] == [
        kept.id
    ]
    assert repo.list_relationships(to_asset_id=dropped.id) == []


@pytest.mark.postgres
def test_declaring_nothing_retracts_that_assets_references(session_factory, ctx):
    """An empty list is an observation, not a no-op: the source was read and
    declared no reference, so the previous row is a claim the source no longer
    makes."""
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    chart = _observed(repo, connection_id, run_id, "chart-1", "chart")
    dataset = _observed(repo, connection_id, run_id, "dataset-1", "dataset")
    repo.replace_relationships(declared={chart.id: [("queries", dataset.id)]})

    assert repo.replace_relationships(declared={chart.id: []}) == []
    assert repo.list_relationships(to_asset_id=dataset.id) == []


@pytest.mark.postgres
def test_an_asset_the_run_never_read_keeps_its_references(session_factory, ctx):
    """The `partial=True` case at the projection level: a snapshot that never
    covered charts must not retract chart references, exactly as a partial
    sync never implies deletion."""
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    chart = _observed(repo, connection_id, run_id, "chart-1", "chart")
    dataset = _observed(repo, connection_id, run_id, "dataset-1", "dataset")
    database = _observed(repo, connection_id, run_id, "db-1", "database")
    repo.replace_relationships(declared={chart.id: [("queries", dataset.id)]})

    repo.replace_relationships(declared={dataset.id: [("belongs_to", database.id)]})

    assert [row.relation for row in repo.list_relationships(from_asset_id=chart.id)] == ["queries"]


@pytest.mark.postgres
def test_references_outlive_a_soft_deleted_endpoint(session_factory, ctx):
    """The reference really was observed while both assets existed, and
    `mark_missing_deleted` keeps history rather than erasing it. Counting
    *live* references is the reader's join, which is why the row is still
    returned here."""
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    chart = _observed(repo, connection_id, run_id, "chart-1", "chart")
    dataset = _observed(repo, connection_id, run_id, "dataset-1", "dataset")
    repo.replace_relationships(declared={chart.id: [("queries", dataset.id)]})

    deleted = repo.mark_missing_deleted(
        connection_id=connection_id,
        asset_type="chart",
        seen_external_ids=set(),
        sync_run_id=run_id,
    )

    assert deleted == [chart.id]
    assert [row.from_asset_id for row in repo.list_relationships(to_asset_id=dataset.id)] == [
        chart.id
    ]
    assert repo.get(chart.id).deleted_at is not None


@pytest.mark.postgres
def test_live_only_references_are_answered_without_a_read_per_row(session_factory, ctx):
    """`include_deleted=False` is the read hy-gh-124 performs, and it is the
    store's join rather than the caller's loop: `AssetRelationshipRecord`
    carries no `deleted_at`, so a caller filtering for itself pays one `get()`
    per row -- an N+1 answering the very read
    `ix_asset_relationships_to_relation` was added for (hy-z21y)."""
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    live = _observed(repo, connection_id, run_id, "chart-live", "chart")
    gone = _observed(repo, connection_id, run_id, "chart-gone", "chart")
    dataset = _observed(repo, connection_id, run_id, "dataset-1", "dataset")
    repo.replace_relationships(
        declared={live.id: [("queries", dataset.id)], gone.id: [("queries", dataset.id)]}
    )
    repo.mark_missing_deleted(
        connection_id=connection_id,
        asset_type="chart",
        seen_external_ids={"chart-live"},
        sync_run_id=run_id,
    )

    assert len(repo.list_relationships(to_asset_id=dataset.id)) == 2
    assert [
        row.from_asset_id
        for row in repo.list_relationships(to_asset_id=dataset.id, include_deleted=False)
    ] == [live.id]

    # A soft-deleted TARGET disqualifies the reference too: "live" is a claim
    # about the reference, and a reference into an asset the source dropped is
    # no more live than one out of it.
    repo.mark_missing_deleted(
        connection_id=connection_id,
        asset_type="dataset",
        seen_external_ids=set(),
        sync_run_id=run_id,
    )
    assert repo.list_relationships(from_asset_id=live.id, include_deleted=False) == []
    assert len(repo.list_relationships(from_asset_id=live.id)) == 1


@pytest.mark.postgres
def test_the_projection_is_never_read_whole(session_factory, ctx):
    """Both endpoints defaulting to `None` meant "every row in the table,
    across every connection" -- an accident of the signature that no call site
    wanted, on a repository whose every other read is scoped to a connection
    or an id (hy-z21y)."""
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    chart = _observed(repo, connection_id, run_id, "chart-1", "chart")
    dataset = _observed(repo, connection_id, run_id, "dataset-1", "dataset")
    repo.replace_relationships(declared={chart.id: [("queries", dataset.id)]})

    with pytest.raises(ValueError, match="from_asset_id or to_asset_id"):
        repo.list_relationships()


@pytest.mark.postgres
def test_reconciling_many_assets_costs_a_fixed_number_of_round_trips(
    session_factory, db_engine, ctx
):
    """The reconciliation is row by row -- that is what preserves row ids --
    but it never needed a query per asset (hy-iv4y). Reading the existing rows
    per asset made a link-free sync of 200 assets issue 201 selects inside one
    transaction, so transaction duration scaled with asset count rather than
    link count. Pinned as a statement count on `asset_relationships`, because
    the defect was invisible to every behavioural assertion.
    """
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    dataset = _observed(repo, connection_id, run_id, "dataset-1", "dataset")

    def statements_for(count):
        declared = {
            _observed(repo, connection_id, run_id, f"chart-{index}", "chart").id: [
                ("queries", dataset.id)
            ]
            for index in range(count)
        }
        seen = []

        def record(conn, cursor, statement, parameters, context, many):
            if "asset_relationships" in statement.lower():
                seen.append(statement)

        event.listen(db_engine, "before_cursor_execute", record)
        try:
            repo.replace_relationships(declared=declared)
        finally:
            event.remove(db_engine, "before_cursor_execute", record)
        return [s for s in seen if s.lstrip().upper().startswith("SELECT")]

    one = statements_for(1)
    many = statements_for(40)

    assert len(one) == len(many) == 2  # the existing-row read, and the returned set
    # The second call re-declares `chart-0`, so it exercises both reconciliation
    # paths -- one row kept, thirty-nine inserted -- off that one batched read.
    assert len(repo.list_relationships(to_asset_id=dataset.id)) == 40


@pytest.mark.postgres
def test_the_database_refuses_a_duplicate_declared_reference(session_factory, ctx):
    """The backstop behind reconciliation: a second writer, or a future
    bulk-insert path, cannot turn one declared reference into two rows and
    make a reference count mean "times synced"."""
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    chart = _observed(repo, connection_id, run_id, "chart-1", "chart")
    dataset = _observed(repo, connection_id, run_id, "dataset-1", "dataset")
    repo.replace_relationships(declared={chart.id: [("queries", dataset.id)]})

    with pytest.raises(IntegrityError, match="uq_asset_relationship"):
        with session_factory() as session, session.begin():
            session.add(
                AssetRelationship(
                    from_asset_id=chart.id, to_asset_id=dataset.id, relation="queries"
                )
            )


@pytest.mark.postgres
def test_live_references_are_read_for_the_whole_candidate_set_in_one_statement(
    session_factory, ctx
):
    """The set-at-a-time form ranking needs, and the reason it exists (hy-g1y8).

    `list_relationships(to_asset_id=...)` already answers this per asset, so a
    ranking over every dataset in the estate would issue one statement per
    candidate. The statement count is asserted, not assumed: it is the whole
    reason this method was added rather than looped over.
    """
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    chart_a = _observed(repo, connection_id, run_id, "chart-a", "chart")
    chart_b = _observed(repo, connection_id, run_id, "chart-b", "chart")
    dashboard = _observed(repo, connection_id, run_id, "dash-1", "dashboard")
    primary = _observed(repo, connection_id, run_id, "dataset-primary", "dataset")
    quiet = _observed(repo, connection_id, run_id, "dataset-quiet", "dataset")
    repo.replace_relationships(
        declared={
            chart_a.id: [("queries", primary.id)],
            chart_b.id: [("queries", primary.id)],
            dashboard.id: [("contains", primary.id)],
        }
    )

    with _statements(session_factory) as recorded:
        rows = repo.list_live_references(to_asset_ids=[primary.id, quiet.id])

    assert len(recorded) == 1, f"one statement for the whole set, got {len(recorded)}"

    assert [(row.to_asset_id, row.relation, row.from_external_id) for row in rows] == [
        (primary.id, "contains", "dash-1"),
        (primary.id, "queries", "chart-a"),
        (primary.id, "queries", "chart-b"),
    ]
    # The referring asset's own identity travels with the row: a rank that says
    # "three things reference this" without naming them cannot be checked.
    assert {row.from_asset_type for row in rows} == {"chart", "dashboard"}
    assert {row.from_connection_id for row in rows} == {connection_id}
    # A dataset nothing references is absent rather than present-and-empty, and
    # the caller distinguishes the two by asking for it.
    assert [row for row in rows if row.to_asset_id == quiet.id] == []


@pytest.mark.postgres
def test_live_references_exclude_a_soft_deleted_endpoint_on_either_side(session_factory, ctx):
    """The join the bead requires, on both endpoints (hy-g1y8).

    The projection deliberately keeps rows whose endpoints were soft-deleted, so
    a count that included them would rank a deleted chart's dataset above a live
    one. Both directions are asserted because only one of them is the obvious
    one: a deleted REFERRER should not count, and a deleted TARGET should not
    collect references either.
    """
    connection_id, run_id = ctx
    repo = PostgresObservedAssetRepository(session_factory)
    live = _observed(repo, connection_id, run_id, "chart-live", "chart")
    gone = _observed(repo, connection_id, run_id, "chart-gone", "chart")
    dataset = _observed(repo, connection_id, run_id, "dataset-1", "dataset")
    doomed = _observed(repo, connection_id, run_id, "dataset-doomed", "dataset")
    repo.replace_relationships(
        declared={
            live.id: [("queries", dataset.id), ("queries", doomed.id)],
            gone.id: [("queries", dataset.id)],
        }
    )

    repo.mark_missing_deleted(
        connection_id=connection_id,
        asset_type="chart",
        seen_external_ids={"chart-live"},
        sync_run_id=run_id,
    )
    repo.mark_missing_deleted(
        connection_id=connection_id,
        asset_type="dataset",
        seen_external_ids={"dataset-1"},
        sync_run_id=run_id,
    )

    rows = repo.list_live_references(to_asset_ids=[dataset.id, doomed.id])

    assert [(row.to_asset_id, row.from_external_id) for row in rows] == [(dataset.id, "chart-live")]
    # The rows themselves survive, which is what makes the filter a filter.
    assert len(repo.list_relationships(to_asset_id=dataset.id)) == 2


@pytest.mark.postgres
def test_live_references_for_no_assets_asks_the_database_nothing(session_factory, ctx):
    """`IN ()` is a statement whose answer is already known, and the ranking
    calls this with whatever `list_all` returned -- which is empty on a fresh
    estate."""
    repo = PostgresObservedAssetRepository(session_factory)

    with _statements(session_factory) as recorded:
        assert repo.list_live_references(to_asset_ids=[]) == []

    assert recorded == []


@contextmanager
def _statements(session_factory):
    """Every SELECT the block issues on this factory's connection.

    Counting statements is the only way to assert the property
    `list_live_references` exists for: a per-asset loop and a set-at-a-time read
    return the same rows, so nothing about the RESULT distinguishes them. The
    filter to SELECT keeps a savepoint the fixture's outer transaction issues
    from being counted as a read (hy-g1y8).
    """
    recorded: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            recorded.append(statement)

    engine = session_factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield recorded
    finally:
        event.remove(engine, "before_cursor_execute", record)
