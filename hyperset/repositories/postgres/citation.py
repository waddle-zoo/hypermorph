"""Durable citation<->answer + human-decision linkage (hy-cpkvu, epic hy-01442 slice 3).

Two audit-only stores that reuse the #503 trace correlation and the #502 review task:

- `PostgresAnswerCitationRepository` records WHICH citations supplied a governed
  answer (keyed by correlation_id + bundle_id), enumerable in BOTH directions.
- `PostgresCitationDecisionRepository` records a human include/exclude/approve/reject
  on a citation, idempotent by SUPERSEDE (latest-wins): a re-submit marks the prior
  live row superseded and inserts a new one, so exactly one decision per item is live.

Neither decides authority (ADR 0012); both store opaque ids/refs, never snippets.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from hyperset.db.base import new_id
from hyperset.db.models import AnswerCitation, CitationDecision
from hyperset.repositories.dto import AnswerCitationRecord, CitationDecisionRecord


def _citation_record(row: AnswerCitation) -> AnswerCitationRecord:
    return AnswerCitationRecord(
        id=row.id,
        workspace=row.workspace,
        correlation_id=row.correlation_id,
        bundle_id=row.bundle_id,
        citation_ref=row.citation_ref,
        citation_kind=row.citation_kind,
        source_ref=row.source_ref,
        created_at=row.created_at,
    )


def _decision_record(row: CitationDecision) -> CitationDecisionRecord:
    return CitationDecisionRecord(
        id=row.id,
        workspace=row.workspace,
        principal_identity=row.principal_identity,
        decision=row.decision,
        citation_ref=row.citation_ref,
        source_ref=row.source_ref,
        review_task_id=row.review_task_id,
        correlation_id=row.correlation_id,
        notes=row.notes,
        superseded_by=row.superseded_by,
        created_at=row.created_at,
    )


class PostgresAnswerCitationRepository:
    """Write + both-direction read of the citation<->answer link. Audit only."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def record(
        self,
        *,
        workspace: str,
        correlation_id: str,
        bundle_id: str,
        citation_ref: str,
        citation_kind: str,
        source_ref: str | None,
    ) -> AnswerCitationRecord:
        """Persist one answer->citation link. Idempotent on (workspace, bundle_id,
        citation_ref, citation_kind): re-recording the same answer's citation returns
        the existing row rather than duplicating it."""
        with self._session_factory() as session, session.begin():
            existing = (
                session.execute(
                    select(AnswerCitation).where(
                        AnswerCitation.workspace == workspace,
                        AnswerCitation.bundle_id == bundle_id,
                        AnswerCitation.correlation_id == correlation_id,
                        AnswerCitation.citation_ref == citation_ref,
                        AnswerCitation.citation_kind == citation_kind,
                    )
                )
                .scalars()
                .first()
            )
            if existing is not None:
                return _citation_record(existing)
            row = AnswerCitation(
                workspace=workspace,
                correlation_id=correlation_id,
                bundle_id=bundle_id,
                citation_ref=citation_ref,
                citation_kind=citation_kind,
                source_ref=source_ref,
            )
            session.add(row)
            session.flush()
            return _citation_record(row)

    def for_answer(self, *, workspace: str, bundle_id: str) -> list[AnswerCitationRecord]:
        """Every citation that supplied one answer -- the answer->citations direction.

        `workspace` is REQUIRED and every query filters by it (hy-cpkvu): bundle_id is
        DETERMINISTIC/content-addressed, so two DIFFERENT workspaces asking an equivalent
        question compute the SAME bundle_id -- an unscoped read would mix tenants
        STRUCTURALLY. The keyword is mandatory so an omitted scope fails closed at the call."""
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(AnswerCitation)
                    .where(
                        AnswerCitation.workspace == workspace,
                        AnswerCitation.bundle_id == bundle_id,
                    )
                    .order_by(AnswerCitation.created_at.asc(), AnswerCitation.id.asc())
                )
                .scalars()
                .all()
            )
            return [_citation_record(r) for r in rows]

    def for_citation(
        self,
        *,
        workspace: str,
        citation_ref: str | None = None,
        source_ref: str | None = None,
    ) -> list[AnswerCitationRecord]:
        """Every answer a citation (or source) supported -- the citation->answers
        direction. Provide `citation_ref` or `source_ref`. `workspace` is REQUIRED and
        every query filters by it (hy-cpkvu): a shared deterministic bundle_id makes an
        unscoped read cross-tenant by construction."""
        if citation_ref is None and source_ref is None:
            raise ValueError("for_citation requires citation_ref or source_ref")
        stmt = select(AnswerCitation).where(AnswerCitation.workspace == workspace)
        if citation_ref is not None:
            stmt = stmt.where(AnswerCitation.citation_ref == citation_ref)
        if source_ref is not None:
            stmt = stmt.where(AnswerCitation.source_ref == source_ref)
        stmt = stmt.order_by(AnswerCitation.created_at.asc(), AnswerCitation.id.asc())
        with self._session_factory() as session:
            rows = session.execute(stmt).scalars().all()
            return [_citation_record(r) for r in rows]


class PostgresCitationDecisionRepository:
    """Write + read of the human citation-decision log. Audit only.

    Idempotent by supersede: the CURRENT decision for an item is the single row with
    `superseded_by IS NULL`; `record` retires any prior live row before inserting."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def record(
        self,
        *,
        workspace: str,
        principal_identity: str,
        decision: str,
        citation_ref: str,
        source_ref: str | None,
        review_task_id: str | None,
        correlation_id: str | None,
        notes: str | None,
    ) -> CitationDecisionRecord:
        """Record a decision, latest-wins: retire the prior LIVE decision for the same
        (workspace, review_task, citation, principal), then insert this one. `notes`
        must already be redacted."""
        # Pre-mint the new row's id so the prior live decision can be retired to point at
        # it BEFORE the insert. Retiring first is load-bearing: the partial-unique live
        # index forbids two live rows for one item, so inserting the new row while the old
        # one is still live would collide. `superseded_by` is a plain column (no FK), so
        # pointing it at a not-yet-inserted id is fine.
        new_row_id = new_id("cdec")
        with self._session_factory() as session, session.begin():
            session.execute(
                update(CitationDecision)
                .where(
                    CitationDecision.workspace == workspace,
                    CitationDecision.review_task_id.is_(review_task_id)
                    if review_task_id is None
                    else CitationDecision.review_task_id == review_task_id,
                    CitationDecision.citation_ref == citation_ref,
                    CitationDecision.principal_identity == principal_identity,
                    CitationDecision.superseded_by.is_(None),
                )
                .values(superseded_by=new_row_id)
            )
            row = CitationDecision(
                id=new_row_id,
                workspace=workspace,
                principal_identity=principal_identity,
                decision=decision,
                citation_ref=citation_ref,
                source_ref=source_ref,
                review_task_id=review_task_id,
                correlation_id=correlation_id,
                notes=notes,
            )
            session.add(row)
            session.flush()
            return _decision_record(row)

    def current(
        self,
        *,
        workspace: str,
        citation_ref: str,
        principal_identity: str,
        review_task_id: str | None,
    ) -> CitationDecisionRecord | None:
        """The single LIVE decision for one item, or None."""
        with self._session_factory() as session:
            stmt = select(CitationDecision).where(
                CitationDecision.workspace == workspace,
                CitationDecision.citation_ref == citation_ref,
                CitationDecision.principal_identity == principal_identity,
                CitationDecision.superseded_by.is_(None),
                CitationDecision.review_task_id.is_(review_task_id)
                if review_task_id is None
                else CitationDecision.review_task_id == review_task_id,
            )
            row = session.execute(stmt).scalars().first()
            return _decision_record(row) if row is not None else None

    def for_task(self, *, workspace: str, review_task_id: str) -> list[CitationDecisionRecord]:
        """Every decision (live or superseded) recorded against one review task, oldest
        first -- the full human-decision history for the loop. `workspace` is REQUIRED and
        the query filters by it (hy-cpkvu), so a read never crosses tenants."""
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(CitationDecision)
                    .where(
                        CitationDecision.workspace == workspace,
                        CitationDecision.review_task_id == review_task_id,
                    )
                    .order_by(CitationDecision.created_at.asc(), CitationDecision.id.asc())
                )
                .scalars()
                .all()
            )
            return [_decision_record(r) for r in rows]

    def for_correlation(
        self, *, workspace: str, correlation_id: str
    ) -> list[CitationDecisionRecord]:
        """Every decision linked to one traced answer interaction.

        A citation decision may be recorded before a review task exists, so
        correlation linkage is a first-class path rather than an optional
        decoration. Workspace remains mandatory to keep one tenant's feedback
        out of another tenant's proposal gate.
        """
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(CitationDecision)
                    .where(
                        CitationDecision.workspace == workspace,
                        CitationDecision.correlation_id == correlation_id,
                    )
                    .order_by(CitationDecision.created_at.asc(), CitationDecision.id.asc())
                )
                .scalars()
                .all()
            )
            return [_decision_record(r) for r in rows]
