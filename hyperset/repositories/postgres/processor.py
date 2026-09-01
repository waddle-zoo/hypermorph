from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from hyperset.db.base import utcnow
from hyperset.db.models import Finding, ProcessorRun
from hyperset.repositories.dto import FindingRecord, ProcessorRunRecord
from hyperset.repositories.errors import NotFoundError

# The subject of `uq_findings_current_subject` (hyperset.db.models.Finding):
# one rule holds at most one *current* finding about one asset under one Git
# commit. Naming the index here lets an insert defer to it, rather than to a
# SELECT another transaction can invalidate a microsecond later.
_CURRENT_SUBJECT = ("finding_type", "affected_asset_id", "affected_context_snapshot_id")
_CURRENT_STATE = text("state = 'current'")


def _run_record(row: ProcessorRun) -> ProcessorRunRecord:
    return ProcessorRunRecord(
        id=row.id,
        trigger_type=row.trigger_type,
        trigger_ref=row.trigger_ref,
        rule_versions=row.rule_versions,
        started_at=row.started_at,
        completed_at=row.completed_at,
        status=row.status,
        counters=row.counters,
        retries=row.retries,
        errors=row.errors,
        warnings=row.warnings,
    )


def _finding_record(row: Finding) -> FindingRecord:
    return FindingRecord(
        id=row.id,
        finding_type=row.finding_type,
        rule_version=row.rule_version,
        processor_run_id=row.processor_run_id,
        affected_asset_id=row.affected_asset_id,
        affected_context_snapshot_id=row.affected_context_snapshot_id,
        affected_context_id=row.affected_context_id,
        severity=row.severity,
        confidence=row.confidence,
        explanation=row.explanation,
        evidence=row.evidence,
        proposed_reviewer=row.proposed_reviewer,
        proposed_action=row.proposed_action,
        state=row.state,
        review_task_id=row.review_task_id,
        created_at=row.created_at,
    )


class PostgresProcessorRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def _claim(
        self, *, trigger_type: str, trigger_ref: str | None, rule_versions: dict
    ) -> ProcessorRunRecord | None:
        with self._session_factory() as session:
            try:
                with session.begin():
                    retries = session.execute(
                        select(func.count())
                        .select_from(ProcessorRun)
                        .where(
                            ProcessorRun.trigger_type == trigger_type,
                            ProcessorRun.trigger_ref == trigger_ref,
                        )
                    ).scalar_one()
                    row = ProcessorRun(
                        trigger_type=trigger_type,
                        trigger_ref=trigger_ref,
                        rule_versions=rule_versions,
                        status="running",
                        retries=retries,
                    )
                    session.add(row)
            except IntegrityError:
                return None
            return _run_record(row)

    def claim_run(
        self, *, trigger_type: str, trigger_ref: str | None = None, rule_versions: dict
    ) -> ProcessorRunRecord | None:
        return self._claim(
            trigger_type=trigger_type, trigger_ref=trigger_ref, rule_versions=rule_versions
        )

    def retry_run(self, run_id: str, *, rule_versions: dict) -> ProcessorRunRecord:
        prior = self.get_run(run_id)
        claimed = self._claim(
            trigger_type=prior.trigger_type,
            trigger_ref=prior.trigger_ref,
            rule_versions=rule_versions,
        )
        if claimed is None:
            raise NotFoundError(
                f"cannot retry {run_id!r}: a run for trigger "
                f"({prior.trigger_type!r}, {prior.trigger_ref!r}) is already running"
            )
        return claimed

    def finish_run(
        self, run_id: str, *, counters: dict, warnings: list[str] | None = None
    ) -> ProcessorRunRecord:
        with self._session_factory() as session, session.begin():
            row = session.get(ProcessorRun, run_id)
            if row is None:
                raise NotFoundError(f"processor run {run_id!r} not found")
            row.status = "succeeded"
            row.counters = counters
            row.warnings = warnings or []
            row.completed_at = utcnow()
            session.flush()
            return _run_record(row)

    def fail_run(self, run_id: str, *, errors: list[str]) -> ProcessorRunRecord:
        with self._session_factory() as session, session.begin():
            row = session.get(ProcessorRun, run_id)
            if row is None:
                raise NotFoundError(f"processor run {run_id!r} not found")
            row.status = "failed"
            row.errors = errors
            row.completed_at = utcnow()
            session.flush()
            return _run_record(row)

    def get_run(self, run_id: str) -> ProcessorRunRecord:
        with self._session_factory() as session:
            row = session.get(ProcessorRun, run_id)
            if row is None:
                raise NotFoundError(f"processor run {run_id!r} not found")
            return _run_record(row)

    def record_finding(
        self,
        *,
        processor_run_id: str,
        finding_type: str,
        rule_version: int,
        severity: str,
        explanation: str,
        evidence: dict,
        affected_asset_id: str | None = None,
        affected_context_snapshot_id: str | None = None,
        affected_context_id: str | None = None,
        confidence: float | None = None,
        proposed_reviewer: str | None = None,
        proposed_action: dict | None = None,
        review_task_id: str | None = None,
    ) -> FindingRecord:
        with self._session_factory() as session, session.begin():
            row = Finding(
                processor_run_id=processor_run_id,
                finding_type=finding_type,
                rule_version=rule_version,
                severity=severity,
                explanation=explanation,
                evidence=evidence,
                affected_asset_id=affected_asset_id,
                affected_context_snapshot_id=affected_context_snapshot_id,
                affected_context_id=affected_context_id,
                confidence=confidence,
                proposed_reviewer=proposed_reviewer,
                proposed_action=proposed_action or {},
                review_task_id=review_task_id,
            )
            session.add(row)
            session.flush()
            return _finding_record(row)

    def record_current_finding(
        self,
        *,
        processor_run_id: str,
        finding_type: str,
        rule_version: int,
        affected_asset_id: str,
        affected_context_snapshot_id: str,
        severity: str,
        explanation: str,
        evidence: dict,
        proposed_action: dict | None = None,
        proposed_reviewer: str | None = None,
        review_task_id: str | None = None,
    ) -> tuple[FindingRecord, bool]:
        """Record one finding at most once per (rule, asset, Git commit).

        Returns `(record, created)`. An identical rerun returns the existing
        finding untouched, so a human is not asked the same question twice.
        A finding for the *same* rule and asset under an older commit is
        superseded rather than left beside the new one: the question the
        reviewer must answer is the one about the current commit, and both
        rows staying current would put two of them in the queue.

        One transaction, so the supersede and the insert cannot half-happen.
        """
        with self._session_factory() as session, session.begin():
            existing = (
                session.execute(
                    select(Finding).where(
                        Finding.finding_type == finding_type,
                        Finding.affected_asset_id == affected_asset_id,
                        Finding.state == "current",
                    )
                )
                .scalars()
                .all()
            )
            for row in existing:
                if row.affected_context_snapshot_id == affected_context_snapshot_id:
                    return _finding_record(row), False
                row.state = "superseded"
            session.flush()

            insert_current = (
                pg_insert(Finding)
                .values(
                    processor_run_id=processor_run_id,
                    finding_type=finding_type,
                    rule_version=rule_version,
                    affected_asset_id=affected_asset_id,
                    affected_context_snapshot_id=affected_context_snapshot_id,
                    severity=severity,
                    explanation=explanation,
                    evidence=evidence,
                    proposed_action=proposed_action or {},
                    proposed_reviewer=proposed_reviewer,
                    review_task_id=review_task_id,
                )
                .on_conflict_do_nothing(
                    index_elements=list(_CURRENT_SUBJECT), index_where=_CURRENT_STATE
                )
                .returning(Finding.id)
            )
            # `claim_run` only serializes one sync run against itself, so two
            # sync runs processed at once can both evaluate this asset, both
            # read no current finding above, and both insert. Postgres holds
            # the loser at the index until the winner commits; DO NOTHING then
            # turns that into a re-read, instead of the IntegrityError that
            # used to escape `run_sync_processing` (hy-nnq). The index, not
            # this transaction's SELECT, is what keeps the subject single.
            for _ in range(2):
                inserted_id = session.execute(insert_current).scalar_one_or_none()
                if inserted_id is not None:
                    return _finding_record(session.get(Finding, inserted_id)), True
                winner = session.execute(
                    select(Finding).where(
                        Finding.finding_type == finding_type,
                        Finding.affected_asset_id == affected_asset_id,
                        Finding.affected_context_snapshot_id == affected_context_snapshot_id,
                        Finding.state == "current",
                    )
                ).scalar_one_or_none()
                if winner is not None:
                    return _finding_record(winner), False
                # The winner stopped being current between the insert and the
                # read, so the subject is free again -- take it this time.
            raise RuntimeError(
                f"could not record a current {finding_type!r} finding for asset "
                f"{affected_asset_id!r}: the subject kept changing hands"
            )

    def list_findings(
        self,
        *,
        processor_run_id: str | None = None,
        state: str | None = None,
        finding_type: str | None = None,
        affected_asset_id: str | None = None,
    ) -> list[FindingRecord]:
        with self._session_factory() as session:
            stmt = select(Finding)
            if processor_run_id is not None:
                stmt = stmt.where(Finding.processor_run_id == processor_run_id)
            if state is not None:
                stmt = stmt.where(Finding.state == state)
            if finding_type is not None:
                stmt = stmt.where(Finding.finding_type == finding_type)
            if affected_asset_id is not None:
                stmt = stmt.where(Finding.affected_asset_id == affected_asset_id)
            rows = session.execute(stmt.order_by(Finding.created_at)).scalars().all()
            return [_finding_record(r) for r in rows]

    def resolve_finding(self, finding_id: str, *, state: str = "resolved") -> FindingRecord:
        with self._session_factory() as session, session.begin():
            row = session.get(Finding, finding_id)
            if row is None:
                raise NotFoundError(f"finding {finding_id!r} not found")
            row.state = state
            session.flush()
            return _finding_record(row)
