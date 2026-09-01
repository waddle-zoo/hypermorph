from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from hyperset.db.base import utcnow
from hyperset.db.models import ConnectorCheckpoint, SyncRun
from hyperset.repositories.dto import SyncRunRecord
from hyperset.repositories.errors import NotFoundError


def select_latest_finished(runs: list[SyncRunRecord]) -> SyncRunRecord | None:
    """The one FINISHED run an operator sees as "last sync", chosen deterministically.

    The SINGLE source of that choice: both the served `_get_playground_status`
    and `PostgresSyncRepository.latest_finished_run` call this, so the CLI operator
    view and the served status cannot disagree on which run is the last one, even
    on a tie (hy-9vji #404). A run still `running` is excluded -- it is not a
    terminal outcome and says nothing about a usable read.

    The order, most-significant first:
    - latest `finished_at` (the newest terminal run);
    - on a `finished_at` tie, the EARLIER-started run -- the incumbent served
      behaviour (`max` over a `started_at`-ASC list keeps the first maximal);
    - on a DOUBLE tie (`finished_at` AND `started_at` equal), the smallest run
      `id` -- a stable tertiary so the pick is deterministic. This is the ONLY
      case the tertiary changes: for any distinguishable `finished_at` or
      `started_at` the run chosen is exactly the one the incumbent chose.

    Implemented as a first-maximal `max` by `finished_at` over runs pre-sorted by
    `(started_at, id)`, which yields precisely that order.
    """
    finished = sorted(
        (run for run in runs if run.finished_at is not None),
        key=lambda run: (run.started_at, run.id),
    )
    return max(finished, key=lambda run: run.finished_at) if finished else None


def _to_record(row: SyncRun) -> SyncRunRecord:
    return SyncRunRecord(
        id=row.id,
        connection_id=row.connection_id,
        mode=row.mode,
        transport=row.transport,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        counters=row.counters,
        checkpoint=row.checkpoint,
        warnings=row.warnings,
        errors=row.errors,
    )


class PostgresSyncRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def begin_run(
        self, connection_id: str, *, mode: str, transport: str | None = None
    ) -> SyncRunRecord:
        with self._session_factory() as session, session.begin():
            row = SyncRun(
                connection_id=connection_id,
                mode=mode,
                # The transport, which `mode` cannot say: `mode` is derived
                # from it and collapses REST and GraphQL into "full" (hy-6t4).
                transport=(transport.strip().lower() if transport else None),
                status="running",
                counters={},
            )
            session.add(row)
            session.flush()
            return _to_record(row)

    def finish_run(
        self, run_id: str, *, counters: dict, warnings: list[str] | None = None
    ) -> SyncRunRecord:
        with self._session_factory() as session, session.begin():
            row = session.get(SyncRun, run_id)
            if row is None:
                raise NotFoundError(f"sync run {run_id!r} not found")
            row.status = "succeeded"
            row.counters = counters
            row.warnings = warnings or []
            row.finished_at = utcnow()
            session.flush()
            return _to_record(row)

    def fail_run(self, run_id: str, *, errors: list[str]) -> SyncRunRecord:
        with self._session_factory() as session, session.begin():
            row = session.get(SyncRun, run_id)
            if row is None:
                raise NotFoundError(f"sync run {run_id!r} not found")
            row.status = "failed"
            row.errors = errors
            row.finished_at = utcnow()
            session.flush()
            return _to_record(row)

    def get_run(self, run_id: str) -> SyncRunRecord:
        with self._session_factory() as session:
            row = session.get(SyncRun, run_id)
            if row is None:
                raise NotFoundError(f"sync run {run_id!r} not found")
            return _to_record(row)

    def list_runs(self, connection_id: str) -> list[SyncRunRecord]:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(SyncRun)
                    .where(SyncRun.connection_id == connection_id)
                    .order_by(SyncRun.started_at)
                )
                .scalars()
                .all()
            )
            return [_to_record(r) for r in rows]

    def latest_finished_status(self, connection_id: str) -> str | None:
        """The status of the run `select_latest_finished` chooses as the last
        one, or `None` when no run has finished.

        Routed through the SHARED selector (hy-9vji #404) so evidence governance
        -- `context/evidence.py::_unmeasured` reads this to decide whether a
        connector has measured the estate -- sees the SAME last-finished run the
        operator view and the served status do. One selector, all consumers: a
        `finished_at` tie can no longer make governance name a different run's
        status than the status surface. The pick differs from the old
        `finished_at`-DESC-only query ONLY on a tie, which that query resolved
        arbitrarily anyway; every distinguishable case is unchanged.

        Still not `list_runs(...)[-1]`: a run still `running` is the newest row
        and says nothing about whether the connection has ever produced a usable
        read; "never finished" and "no connection" are both `None`.
        """
        chosen = select_latest_finished(self.list_runs(connection_id))
        return chosen.status if chosen is not None else None

    def latest_finished_run(self, connection_id: str) -> SyncRunRecord | None:
        """The whole most-recent run that reached a terminal state, or `None`.

        The record behind `latest_finished_status`: the operator view (hy-9vji)
        wants the last FINISHED run's outcome, when it finished, and its counters,
        not the newest ROW -- a run still `running` is the newest row and says
        nothing about whether the connection has ever produced a usable read.

        Delegates the CHOICE to `select_latest_finished` over this connection's
        runs, the exact same function `_get_playground_status` calls, so the CLI
        operator view and the served status cannot pick different runs on any tie.
        """
        return select_latest_finished(self.list_runs(connection_id))

    def latest_finished_run_any(self) -> SyncRunRecord | None:
        """The most-recent FINISHED run across ALL connections, or `None`.

        The deterministic "last sync anywhere" source `make process` needs
        (hy-jp0gq): the operator has no connection id in hand, so the generic
        target processes whatever sync finished most recently. Routed through the
        SHARED `select_latest_finished`, so it picks the same terminal run the
        per-connection view and the served status would on any tie -- a run still
        `running` is excluded, and "never finished" and "no run at all" are both
        `None` (the caller then no-ops rather than processing nothing).
        """
        with self._session_factory() as session:
            rows = session.execute(select(SyncRun).order_by(SyncRun.started_at)).scalars().all()
        return select_latest_finished([_to_record(r) for r in rows])

    def get_checkpoint(self, connection_id: str) -> dict | None:
        with self._session_factory() as session:
            row = session.execute(
                select(ConnectorCheckpoint).where(
                    ConnectorCheckpoint.connection_id == connection_id
                )
            ).scalar_one_or_none()
            return row.checkpoint if row else None

    def set_checkpoint(self, connection_id: str, *, checkpoint: dict, sync_run_id: str) -> None:
        with self._session_factory() as session, session.begin():
            row = session.execute(
                select(ConnectorCheckpoint).where(
                    ConnectorCheckpoint.connection_id == connection_id
                )
            ).scalar_one_or_none()
            if row is None:
                row = ConnectorCheckpoint(
                    connection_id=connection_id,
                    checkpoint=checkpoint,
                    last_sync_run_id=sync_run_id,
                )
                session.add(row)
            else:
                row.checkpoint = checkpoint
                row.last_sync_run_id = sync_run_id
            session.flush()
