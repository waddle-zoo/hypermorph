from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from hyperset.db.models import EvaluationCase, EvaluationRun
from hyperset.repositories.dto import EvaluationCaseRecord, EvaluationRunRecord
from hyperset.repositories.errors import NotFoundError


def _case_record(row: EvaluationCase) -> EvaluationCaseRecord:
    return EvaluationCaseRecord(
        id=row.id,
        name=row.name,
        version=row.version,
        question=row.question,
        expected=row.expected,
        domain=row.domain,
        created_at=row.created_at,
    )


def _run_record(row: EvaluationRun) -> EvaluationRunRecord:
    return EvaluationRunRecord(
        id=row.id,
        case_id=row.case_id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        attempt_payload=row.attempt_payload,
        scorecard=row.scorecard,
        passed=row.passed,
        context_versions_used=row.context_versions_used,
        created_at=row.created_at,
    )


class PostgresEvaluationRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_case(
        self,
        *,
        name: str,
        question: str,
        expected: dict,
        domain: str | None = None,
        version: int = 1,
    ) -> EvaluationCaseRecord:
        with self._session_factory() as session, session.begin():
            row = EvaluationCase(
                name=name, version=version, question=question, expected=expected, domain=domain
            )
            session.add(row)
            session.flush()
            return _case_record(row)

    def get_case(self, case_id: str) -> EvaluationCaseRecord:
        with self._session_factory() as session:
            row = session.get(EvaluationCase, case_id)
            if row is None:
                raise NotFoundError(f"evaluation case {case_id!r} not found")
            return _case_record(row)

    def list_cases(self, *, domain: str | None = None) -> list[EvaluationCaseRecord]:
        with self._session_factory() as session:
            stmt = select(EvaluationCase)
            if domain is not None:
                stmt = stmt.where(EvaluationCase.domain == domain)
            rows = session.execute(stmt.order_by(EvaluationCase.name)).scalars().all()
            return [_case_record(r) for r in rows]

    def record_run(
        self,
        *,
        case_id: str,
        attempt_payload: dict,
        scorecard: dict,
        passed: bool | None,
        context_versions_used: list | None = None,
        finished_at: datetime | None = None,
    ) -> EvaluationRunRecord:
        with self._session_factory() as session, session.begin():
            row = EvaluationRun(
                case_id=case_id,
                attempt_payload=attempt_payload,
                scorecard=scorecard,
                passed=passed,
                context_versions_used=context_versions_used or [],
                finished_at=finished_at,
            )
            session.add(row)
            session.flush()
            return _run_record(row)

    def list_runs(self, case_id: str) -> list[EvaluationRunRecord]:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(EvaluationRun)
                    .where(EvaluationRun.case_id == case_id)
                    .order_by(EvaluationRun.started_at)
                )
                .scalars()
                .all()
            )
            return [_run_record(r) for r in rows]
