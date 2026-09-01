"""Append-only feedback on traced answers and hits (hy-8f2r4)."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from hyperset.db.models import AnswerFeedback, McpInteractionTrace
from hyperset.repositories.dto import AnswerFeedbackRecord


def _record(row: AnswerFeedback) -> AnswerFeedbackRecord:
    return AnswerFeedbackRecord(
        id=row.id,
        workspace=row.workspace,
        principal_identity=row.principal_identity,
        trace_id=row.trace_id,
        session_id=row.session_id,
        correlation_id=row.correlation_id,
        outcome=row.outcome,
        bundle_id=row.bundle_id,
        source_ref=row.source_ref,
        review_task_id=row.review_task_id,
        notes=row.notes,
        created_at=row.created_at,
    )


def _source_matches(hit_ids: list[str], source_ref: str) -> bool:
    """Accept an exact opaque hit id or its `source_id:path` document prefix."""
    return any(hit == source_ref or hit.rpartition(":")[0] == source_ref for hit in hit_ids)


class PostgresAnswerFeedbackRepository:
    """Record and query feedback, always within one explicit workspace."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def record(
        self,
        *,
        workspace: str,
        principal_identity: str,
        session_id: str,
        correlation_id: str,
        outcome: str,
        bundle_id: str | None,
        source_ref: str | None,
        review_task_id: str | None,
        notes: str | None,
    ) -> AnswerFeedbackRecord:
        """Verify the target trace, append feedback, and back-link it atomically."""
        with self._session_factory() as session, session.begin():
            traces = (
                session.execute(
                    select(McpInteractionTrace)
                    .where(
                        McpInteractionTrace.workspace == workspace,
                        McpInteractionTrace.session_id == session_id,
                        McpInteractionTrace.correlation_id == correlation_id,
                    )
                    .with_for_update()
                    .order_by(McpInteractionTrace.created_at.desc(), McpInteractionTrace.id.desc())
                )
                .scalars()
                .all()
            )
            bundle_matches = (
                [trace for trace in traces if trace.answer_bundle_id == bundle_id]
                if bundle_id is not None
                else []
            )
            source_matches = (
                [trace for trace in traces if _source_matches(trace.hit_ids or [], source_ref)]
                if source_ref is not None
                else []
            )
            # Every supplied linkage must independently exist in this chain. An
            # OR here would let a valid source smuggle a fabricated bundle id
            # (or vice versa) into the durable row.
            if (bundle_id is not None and not bundle_matches) or (
                source_ref is not None and not source_matches
            ):
                raise ValueError("feedback target does not match this session/correlation trace")
            trace = (source_matches or bundle_matches)[0]
            row = AnswerFeedback(
                workspace=workspace,
                principal_identity=principal_identity,
                trace_id=trace.id,
                session_id=session_id,
                correlation_id=correlation_id,
                outcome=outcome,
                bundle_id=bundle_id,
                source_ref=source_ref,
                review_task_id=review_task_id,
                notes=notes,
            )
            session.add(row)
            session.flush()
            trace.feedback_ids = [*(trace.feedback_ids or []), row.id]
            return _record(row)

    def lookup(
        self,
        *,
        workspace: str,
        session_id: str | None = None,
        correlation_id: str | None = None,
        source_ref: str | None = None,
        review_task_id: str | None = None,
        limit: int = 100,
    ) -> list[AnswerFeedbackRecord]:
        stmt = select(AnswerFeedback).where(AnswerFeedback.workspace == workspace)
        for column, value in (
            (AnswerFeedback.session_id, session_id),
            (AnswerFeedback.correlation_id, correlation_id),
            (AnswerFeedback.source_ref, source_ref),
            (AnswerFeedback.review_task_id, review_task_id),
        ):
            if value is not None:
                stmt = stmt.where(column == value)
        stmt = stmt.order_by(AnswerFeedback.created_at.asc(), AnswerFeedback.id.asc()).limit(limit)
        with self._session_factory() as session:
            return [_record(row) for row in session.execute(stmt).scalars().all()]

    def blocking_ids(
        self,
        *,
        workspace: str,
        review_task_id: str | None = None,
        correlation_id: str | None = None,
    ) -> list[str]:
        """Return every current negative feedback id for one proposal chain.

        This is intentionally unbounded by the display-page limit: the proposal gate must
        not miss a negative decision merely because it is the 21st record.  The caller
        supplies the task and/or its originating correlation, and the workspace predicate
        is always applied.
        """
        if review_task_id is None and correlation_id is None:
            return []
        stmt = select(AnswerFeedback.id).where(
            AnswerFeedback.workspace == workspace,
            AnswerFeedback.outcome.in_(("reject", "ignore", "needs_review")),
        )
        links = []
        if review_task_id is not None:
            links.append(AnswerFeedback.review_task_id == review_task_id)
        if correlation_id is not None:
            links.append(AnswerFeedback.correlation_id == correlation_id)
        stmt = stmt.where(or_(*links)).order_by(
            AnswerFeedback.created_at.asc(), AnswerFeedback.id.asc()
        )
        with self._session_factory() as session:
            return list(session.execute(stmt).scalars().all())
