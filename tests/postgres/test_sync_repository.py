import pytest

from hyperset.repositories.postgres import PostgresConnectionRepository, PostgresSyncRepository


@pytest.fixture
def connection_id(session_factory):
    return (
        PostgresConnectionRepository(session_factory)
        .create_or_update(connector_type="superset", display_name="Local Superset")
        .id
    )


@pytest.mark.postgres
def test_begin_and_finish_run(session_factory, connection_id):
    repo = PostgresSyncRepository(session_factory)
    run = repo.begin_run(connection_id, mode="full")
    assert run.status == "running"
    finished = repo.finish_run(run.id, counters={"created": 3, "updated": 1})
    assert finished.status == "succeeded"
    assert finished.counters == {"created": 3, "updated": 1}
    assert finished.finished_at is not None


@pytest.mark.postgres
def test_fail_run_records_errors(session_factory, connection_id):
    repo = PostgresSyncRepository(session_factory)
    run = repo.begin_run(connection_id, mode="incremental")
    failed = repo.fail_run(run.id, errors=["connection timeout"])
    assert failed.status == "failed"
    assert failed.errors == ["connection timeout"]


@pytest.mark.postgres
def test_checkpoint_round_trips_and_updates(session_factory, connection_id):
    repo = PostgresSyncRepository(session_factory)
    run = repo.begin_run(connection_id, mode="incremental")
    assert repo.get_checkpoint(connection_id) is None

    repo.set_checkpoint(connection_id, checkpoint={"cursor": "abc"}, sync_run_id=run.id)
    assert repo.get_checkpoint(connection_id) == {"cursor": "abc"}

    repo.set_checkpoint(connection_id, checkpoint={"cursor": "def"}, sync_run_id=run.id)
    assert repo.get_checkpoint(connection_id) == {"cursor": "def"}


@pytest.mark.postgres
def test_list_runs_supports_full_incremental_and_fixture_modes(session_factory, connection_id):
    repo = PostgresSyncRepository(session_factory)
    for mode in ("full", "incremental", "fixture_import"):
        repo.begin_run(connection_id, mode=mode)
    runs = repo.list_runs(connection_id)
    assert {r.mode for r in runs} == {"full", "incremental", "fixture_import"}


@pytest.mark.postgres
def test_latest_finished_run_any_picks_the_newest_completed_across_connections(session_factory):
    """The deterministic 'last sync anywhere' source for `make process` (hy-jp0gq):
    the most-recent FINISHED run over ALL connections, a run still `running`
    excluded, and None when nothing has finished."""
    from datetime import UTC, datetime, timedelta

    from hyperset.db.models import SyncRun

    connections = PostgresConnectionRepository(session_factory)
    repo = PostgresSyncRepository(session_factory)
    conn_a = connections.create_or_update(connector_type="superset", display_name="A").id
    conn_b = connections.create_or_update(connector_type="datahub", display_name="B").id

    # Nothing finished yet -> None (the caller no-ops rather than processing nothing).
    running = repo.begin_run(conn_a, mode="full")
    assert repo.latest_finished_run_any() is None

    older = repo.begin_run(conn_a, mode="full")
    repo.finish_run(older.id, counters={"n": 1})
    newer = repo.begin_run(conn_b, mode="full")
    repo.finish_run(newer.id, counters={"n": 2})

    # Force explicit, distinct finished_at across the two connections so the pick
    # does not ride on wall-clock resolution.
    base = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    with session_factory() as session, session.begin():
        session.get(SyncRun, older.id).finished_at = base
        session.get(SyncRun, newer.id).finished_at = base + timedelta(minutes=5)

    chosen = repo.latest_finished_run_any()
    assert chosen is not None
    assert chosen.id == newer.id  # newest terminal run across BOTH connections
    assert chosen.id != running.id  # the still-running run is never chosen
