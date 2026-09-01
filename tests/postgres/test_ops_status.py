"""The operator view's sync-health surface over a real store (hy-9vji, hy-gh-72 S1).

Read-only: it reports the last FINISHED run per connection. Built here against a
real Postgres because the readers it composes are repository readers.
"""

from __future__ import annotations

from datetime import UTC

import pytest

from hyperset.ops.status import read_sync_health
from hyperset.repositories.postgres import PostgresConnectionRepository, PostgresSyncRepository
from hyperset.repositories.scope import ALL_WORKSPACES


def _connection(session_factory, *, name="Ops Test", connector_type="superset"):
    return (
        PostgresConnectionRepository(session_factory)
        .create_or_update(connector_type=connector_type, display_name=name)
        .id
    )


def _health_for(session_factory, connection_id):
    return next(c for c in read_sync_health(session_factory) if c.connection_id == connection_id)


def test_a_succeeded_sync_is_surfaced_as_the_last_outcome(session_factory):
    connection_id = _connection(session_factory)
    syncs = PostgresSyncRepository(session_factory)
    run = syncs.begin_run(connection_id, mode="full")
    syncs.finish_run(run.id, counters={"created": 3})

    health = _health_for(session_factory, connection_id)
    assert health.outcome == "succeeded"
    assert health.last_sync is not None
    assert health.last_sync.status == "succeeded"
    assert health.last_sync.counters == {"created": 3}
    assert health.last_sync.finished_at is not None


def test_a_failed_sync_is_surfaced_not_hidden(session_factory):
    connection_id = _connection(session_factory)
    syncs = PostgresSyncRepository(session_factory)
    run = syncs.begin_run(connection_id, mode="full")
    syncs.fail_run(run.id, errors=["connection refused"])

    health = _health_for(session_factory, connection_id)
    assert health.outcome == "failed"
    assert health.last_sync is not None
    assert health.last_sync.errors == ["connection refused"]


def test_a_connection_that_never_finished_a_run_reads_as_never(session_factory):
    connection_id = _connection(session_factory)
    # A run left RUNNING is not health: it says nothing about a usable read.
    PostgresSyncRepository(session_factory).begin_run(connection_id, mode="full")

    health = _health_for(session_factory, connection_id)
    assert health.outcome == "never"
    assert health.last_sync is None


def test_the_latest_finished_run_wins_over_an_earlier_one(session_factory):
    connection_id = _connection(session_factory)
    syncs = PostgresSyncRepository(session_factory)
    first = syncs.begin_run(connection_id, mode="full")
    syncs.fail_run(first.id, errors=["transient"])
    second = syncs.begin_run(connection_id, mode="full")
    syncs.finish_run(second.id, counters={"created": 1})

    health = _health_for(session_factory, connection_id)
    assert health.outcome == "succeeded"
    assert health.last_sync.id == second.id


def test_every_registered_connection_appears_even_with_no_runs(session_factory):
    connection_id = _connection(session_factory, name="No Runs Yet")
    ids = {c.connection_id for c in read_sync_health(session_factory)}
    assert connection_id in ids


def test_ops_and_served_status_pick_the_same_run_on_a_finished_at_tie(session_factory):
    # hy-9vji #404 bounce: latest_finished_run must match the SERVED
    # _get_playground_status pick on a finished_at tie. Served does
    # max(finished, key=finished_at) over runs ordered by started_at ASC, and
    # `max` keeps the FIRST maximal -> the EARLIER-started run. The reader must
    # agree, or CLI and served status diverge.
    from datetime import datetime, timedelta

    from hyperset.db.models import SyncRun

    connection_id = _connection(session_factory)
    syncs = PostgresSyncRepository(session_factory)
    earlier = syncs.begin_run(connection_id, mode="full")
    syncs.finish_run(earlier.id, counters={"n": 1})
    later = syncs.begin_run(connection_id, mode="full")
    syncs.finish_run(later.id, counters={"n": 2})

    # Force a finished_at TIE with distinct started_at (earlier vs later start).
    tie = datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)
    with session_factory() as session, session.begin():
        row_earlier = session.get(SyncRun, earlier.id)
        row_later = session.get(SyncRun, later.id)
        row_earlier.started_at = tie - timedelta(minutes=5)
        row_earlier.finished_at = tie
        row_later.started_at = tie - timedelta(minutes=1)
        row_later.finished_at = tie

    # Independent oracle: the served algorithm, replicated (not the subject's SQL).
    runs = syncs.list_runs(connection_id)
    finished = [run for run in runs if run.finished_at is not None]
    served_pick = max(finished, key=lambda run: run.finished_at)
    assert served_pick.id == earlier.id  # the tie goes to the earlier-started run

    assert syncs.latest_finished_run(connection_id).id == served_pick.id
    assert _health_for(session_factory, connection_id).last_sync.id == served_pick.id


def test_a_double_tie_finished_and_started_is_deterministic(session_factory):
    # hy-9vji #404 last item: finished_at AND started_at both equal. Without a
    # stable tertiary the pick is order-dependent and CLI vs served could diverge.
    # The shared select_latest_finished breaks it by smallest id, so every path
    # -- the reader, the health surface, and the served selection -- agrees.
    from datetime import datetime

    from hyperset.db.models import SyncRun
    from hyperset.repositories.postgres import select_latest_finished

    connection_id = _connection(session_factory)
    syncs = PostgresSyncRepository(session_factory)
    a = syncs.begin_run(connection_id, mode="full")
    syncs.finish_run(a.id, counters={"n": 1})
    b = syncs.begin_run(connection_id, mode="full")
    syncs.finish_run(b.id, counters={"n": 2})

    same = datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)
    with session_factory() as session, session.begin():
        for run_id in (a.id, b.id):
            row = session.get(SyncRun, run_id)
            row.started_at = same
            row.finished_at = same

    # Independent oracle: the smallest id, computed without the subject's logic.
    expected = min(a.id, b.id)
    assert syncs.latest_finished_run(connection_id).id == expected
    assert _health_for(session_factory, connection_id).last_sync.id == expected
    # The served path calls the SAME function, so it picks the same run.
    assert select_latest_finished(syncs.list_runs(connection_id)).id == expected


def test_evidence_governance_and_status_surface_agree_on_a_finished_at_tie(session_factory):
    # hy-9vji #404 round 3: latest_finished_status feeds evidence governance
    # (context/evidence.py::_unmeasured). It must route through the SAME shared
    # selector as the status surface, or on a finished_at tie governance could
    # name a different run's status than the operator view.
    #
    # Construct a tie where the shared pick MATTERS to the outcome: the
    # earlier-started run FAILED, the later-started run SUCCEEDED, same
    # finished_at. The shared selector keeps the earlier-started (failed) run, so
    # governance must see "failed" -- the same run the status surface sees.
    from datetime import datetime, timedelta

    from hyperset.context.evidence import ObservedEvidenceResolver
    from hyperset.db.models import SyncRun

    connection_id = _connection(session_factory, connector_type="superset")
    syncs = PostgresSyncRepository(session_factory)
    earlier = syncs.begin_run(connection_id, mode="full")
    syncs.fail_run(earlier.id, errors=["down"])
    later = syncs.begin_run(connection_id, mode="full")
    syncs.finish_run(later.id, counters={"created": 1})

    tie = datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)
    with session_factory() as session, session.begin():
        row_earlier = session.get(SyncRun, earlier.id)
        row_later = session.get(SyncRun, later.id)
        row_earlier.started_at = tie - timedelta(minutes=5)
        row_earlier.finished_at = tie
        row_later.started_at = tie - timedelta(minutes=1)
        row_later.finished_at = tie

    # The status surface keeps the earlier-started, failed run.
    assert syncs.latest_finished_run(connection_id).id == earlier.id
    assert syncs.latest_finished_run(connection_id).status == "failed"
    # The seam evidence governance reads picks the SAME run's status.
    assert syncs.latest_finished_status(connection_id) == "failed"
    # And governance itself marks the connector unmeasured for that failed sync.
    unmeasured = ObservedEvidenceResolver(session_factory, workspace=ALL_WORKSPACES)._unmeasured(
        {"superset"}, {"superset": [connection_id]}
    )
    assert "superset" in unmeasured
    assert "last sync failed" in unmeasured["superset"]


def test_evidence_governance_delegates_to_the_shared_selector(session_factory, monkeypatch):
    # hy-9vji #404 round 4: the MUTATION-STRONG governance guard. The tie test
    # above is behaviourally right but its mutant -- reverting the seam to
    # `finished_at DESC LIMIT 1` -- can return the failed row by arbitrary
    # equal-key order and pass anyway. This proves DELEGATION structurally and
    # deterministically: replace the shared selector so it names the EARLIER,
    # FAILED run, while the private DESC query would name the LATER, SUCCEEDED run
    # (a STRICTLY later finished_at -- no tie, so the mutant is deterministic).
    # Governance must reflect the selector's choice; the mutant reflects the
    # query's and reds.
    import hyperset.repositories.postgres.sync as sync_module
    from hyperset.context.evidence import ObservedEvidenceResolver

    connection_id = _connection(session_factory, connector_type="superset")
    syncs = PostgresSyncRepository(session_factory)
    failed = syncs.begin_run(connection_id, mode="full")
    syncs.fail_run(failed.id, errors=["down"])
    later_ok = syncs.begin_run(connection_id, mode="full")
    syncs.finish_run(later_ok.id, counters={"created": 1})  # strictly later finished_at

    # A non-delegating seam (finished_at DESC LIMIT 1) returns later_ok -> succeeded.
    assert syncs.latest_finished_run(connection_id).id == later_ok.id

    sentinel = syncs.get_run(failed.id)  # status == "failed"
    calls: list = []

    def spy(runs):
        calls.append(runs)
        return sentinel

    monkeypatch.setattr(sync_module, "select_latest_finished", spy)

    unmeasured = ObservedEvidenceResolver(session_factory, workspace=ALL_WORKSPACES)._unmeasured(
        {"superset"}, {"superset": [connection_id]}
    )
    assert calls, "evidence governance did not route through select_latest_finished"
    # Governance reflects the SELECTOR's failed run, not the query's succeeded one.
    assert "superset" in unmeasured
    assert "last sync failed" in unmeasured["superset"]


@pytest.mark.usefixtures("session_factory")
def test_read_sync_health_returns_empty_when_no_connections(session_factory):
    # A fresh estate: no connections, no crash, empty list.
    assert read_sync_health(session_factory) == []
