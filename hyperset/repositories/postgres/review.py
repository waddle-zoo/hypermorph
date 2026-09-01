from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from hyperset.db.base import new_id, utcnow
from hyperset.db.models import (
    REVIEW_TASK_STATUSES,
    GovernedContext,
    GovernedContextVersion,
    ReviewDecision,
    ReviewTask,
)

# Re-exported so the transport layer can enumerate accepted `status` filters
# without importing the SQLAlchemy table module directly (hy-gh-281 item 5). The
# CheckConstraint on `ReviewTask.status` is defined from the same tuple, so the
# served enum and the stored constraint cannot drift.
__all__ = ["PostgresReviewRepository", "REVIEW_TASK_STATUSES", "RECONCILABLE_LIFECYCLE_STATES"]
from hyperset.repositories.dto import ReviewApprovalResult, ReviewDecisionRecord, ReviewTaskRecord
from hyperset.repositories.errors import NotFoundError, OptimisticConcurrencyError
from hyperset.repositories.postgres.governed_context import _search_text, _version_columns
from hyperset.repositories.postgres.governed_context import (
    _version_record as _context_version_record,
)

# The `pr_lifecycle.state` values (flywheel.lifecycle) that still warrant a
# reconcile sweep: a PR still open, or a transient 'unknown' to retry. Terminal
# states are excluded from the sweep selection, so a re-run is a no-op (hq-3ta2).
RECONCILABLE_LIFECYCLE_STATES = ("open", "unknown")
PROPOSAL_LEASE_SECONDS = 15 * 60


def _task_record(row: ReviewTask) -> ReviewTaskRecord:
    return ReviewTaskRecord(
        id=row.id,
        workspace=getattr(row, "workspace", "default"),
        reason=row.reason,
        priority=row.priority,
        affected_asset_ids=row.affected_asset_ids,
        affected_context_id=row.affected_context_id,
        proposal_payload=row.proposal_payload,
        processor_evidence=row.processor_evidence,
        evaluation_impact=row.evaluation_impact,
        assignee=row.assignee,
        status=row.status,
        idempotency_key=row.idempotency_key,
        row_version=row.row_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        proposal_in_flight=getattr(row, "proposal_in_flight", False),
        proposal_lease_id=getattr(row, "proposal_lease_id", None),
        proposal_lease_expires_at=getattr(row, "proposal_lease_expires_at", None),
    )


def _decision_record(row: ReviewDecision) -> ReviewDecisionRecord:
    return ReviewDecisionRecord(
        id=row.id,
        review_task_id=row.review_task_id,
        decision=row.decision,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        resulting_context_version_id=row.resulting_context_version_id,
        notes=row.notes,
    )


def _reclaim_expired_proposal(row: ReviewTask, *, now=None) -> bool:
    """Clear a crashed writer's expired lease while the caller holds the row lock.

    Returning ``True`` only means the reservation was reclaimed; the caller still performs
    its own mutation/version bump. A missing expiry is treated as non-reclaimable so rows
    written by an older migration cannot be silently taken over without an explicit lease.
    """
    if not row.proposal_in_flight:
        return False
    expires_at = getattr(row, "proposal_lease_expires_at", None)
    if expires_at is None or expires_at > (now or utcnow()):
        return False
    row.proposal_in_flight = False
    row.proposal_lease_id = None
    row.proposal_lease_expires_at = None
    return True


class PostgresReviewRepository:
    """`approve()` is the one method in this package that spans two
    identity families (GovernedContextVersion + ReviewDecision) in a single
    transaction -- hy-gh-26 "transactional approval must create a
    governed-context version and decision audit entry together"."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_task(
        self,
        *,
        reason: str,
        idempotency_key: str,
        workspace: str = "default",
        priority: int = 2,
        affected_asset_ids: list[str] | None = None,
        affected_context_id: str | None = None,
        proposal_payload: dict | None = None,
        processor_evidence: dict | None = None,
        evaluation_impact: dict | None = None,
    ) -> ReviewTaskRecord:
        with self._session_factory() as session, session.begin():
            existing = session.execute(
                select(ReviewTask).where(
                    ReviewTask.workspace == workspace,
                    ReviewTask.idempotency_key == idempotency_key,
                )
            ).scalar_one_or_none()
            if existing is not None:
                return _task_record(existing)
            row = ReviewTask(
                workspace=workspace,
                reason=reason,
                idempotency_key=idempotency_key,
                priority=priority,
                affected_asset_ids=affected_asset_ids or [],
                affected_context_id=affected_context_id,
                proposal_payload=proposal_payload or {},
                processor_evidence=processor_evidence or {},
                evaluation_impact=evaluation_impact,
                status="open",
            )
            session.add(row)
            session.flush()
            return _task_record(row)

    def get_task(self, task_id: str, *, workspace: str = "default") -> ReviewTaskRecord:
        with self._session_factory() as session:
            row = session.execute(
                select(ReviewTask).where(
                    ReviewTask.id == task_id,
                    ReviewTask.workspace == workspace,
                )
            ).scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"review task {task_id!r} not found")
            return _task_record(row)

    def set_proposal_payload(
        self,
        task_id: str,
        proposal_payload: dict,
        *,
        workspace: str = "default",
        session: Session | None = None,
        allow_in_flight: bool = False,
    ) -> ReviewTaskRecord:
        """Replace the assist draft carried on a task (hy-murb).

        The one write the interactive review makes to a task: the expert's
        edited draft, an agent re-run's replacement, or the reconcile's PR
        lifecycle (hq-ci92). It touches `proposal_payload` only -- it does not
        resolve, approve, or advance the task's status, and it writes no governed
        row. A draft stays a draft. With `session` given the change is made in
        THAT transaction and not committed here, so the caller can couple it to an
        audit append (hq-ci92)."""

        def _apply(sess: Session) -> ReviewTaskRecord:
            row = sess.execute(
                select(ReviewTask)
                .where(
                    ReviewTask.id == task_id,
                    ReviewTask.workspace == workspace,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"review task {task_id!r} not found")
            if row.status not in {"open", "in_progress"}:
                raise OptimisticConcurrencyError(
                    f"review task {task_id!r} is already {row.status!r}"
                )
            if row.proposal_in_flight and not allow_in_flight:
                _reclaim_expired_proposal(row)
            if row.proposal_in_flight and not allow_in_flight:
                raise OptimisticConcurrencyError(
                    f"review task {task_id!r} has a proposal in flight"
                )
            row.proposal_payload = proposal_payload
            row.row_version += 1
            row.updated_at = utcnow()
            sess.flush()
            return _task_record(row)

        if session is not None:
            return _apply(session)
        with self._session_factory() as owned, owned.begin():
            return _apply(owned)

    def set_assignee(
        self,
        task_id: str,
        assignee: str | None,
        *,
        workspace: str = "default",
        session: Session | None = None,
    ) -> ReviewTaskRecord:
        """Set or clear the owner assigned to a review task (hy-s8a6).

        Touches `assignee` only -- an opaque identity label, `None` to unassign. It does
        not resolve, approve, or advance the task's status, and writes no governed row:
        assignment is metadata, not an approval or an access grant. With `session` given
        the change rides THAT transaction and is not committed here, mirroring
        `set_proposal_payload` so a caller can couple it to an audit append later."""

        def _apply(sess: Session) -> ReviewTaskRecord:
            row = sess.execute(
                select(ReviewTask)
                .where(
                    ReviewTask.id == task_id,
                    ReviewTask.workspace == workspace,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"review task {task_id!r} not found")
            if row.status not in {"open", "in_progress"}:
                raise OptimisticConcurrencyError(
                    f"review task {task_id!r} is already {row.status!r}"
                )
            if row.proposal_in_flight:
                _reclaim_expired_proposal(row)
            if row.proposal_in_flight:
                raise OptimisticConcurrencyError(
                    f"review task {task_id!r} has a proposal in flight"
                )
            row.assignee = assignee
            row.row_version += 1
            row.updated_at = utcnow()
            sess.flush()
            return _task_record(row)

        if session is not None:
            return _apply(session)
        with self._session_factory() as owned, owned.begin():
            return _apply(owned)

    def list_tasks(
        self, *, status: str | None = None, workspace: str = "default"
    ) -> list[ReviewTaskRecord]:
        with self._session_factory() as session:
            stmt = select(ReviewTask).where(ReviewTask.workspace == workspace)
            if status is not None:
                stmt = stmt.where(ReviewTask.status == status)
            rows = session.execute(stmt.order_by(ReviewTask.created_at)).scalars().all()
            return [_task_record(r) for r in rows]

    def list_reconcilable(
        self, *, limit: int, workspace: str = "default"
    ) -> list[ReviewTaskRecord]:
        """Tasks whose proposal PR is still OPEN and worth another reconcile sweep
        (hq-3ta2), OLDEST-first, capped at `limit`.

        Selected by a JSONB predicate, so the sweep scans only what it will act on:
        a task must carry a proposal PR (`review_routing.backlink` present) and be
        NON-TERMINAL -- its `pr_lifecycle.state` is absent (never reconciled) or one
        of `RECONCILABLE_LIFECYCLE_STATES` ('open', a still-open PR; 'unknown', a
        transient to retry). Terminal states ('synced', 'merged', 'closed_unmerged',
        'target_changed', 'no_pr') are excluded, so a re-run over them is a no-op --
        the sweep is idempotent by SELECTION, not by mutating already-done tasks."""
        backlink = ReviewTask.proposal_payload["review_routing"]["backlink"].astext
        state = ReviewTask.proposal_payload["pr_lifecycle"]["state"].astext
        with self._session_factory() as session:
            stmt = (
                select(ReviewTask)
                .where(ReviewTask.workspace == workspace)
                .where(backlink.isnot(None))
                .where(or_(state.is_(None), state.in_(RECONCILABLE_LIFECYCLE_STATES)))
                .order_by(ReviewTask.created_at)
                .limit(limit)
            )
            return [_task_record(r) for r in session.execute(stmt).scalars().all()]

    def approve(
        self,
        task_id: str,
        *,
        decided_by: str,
        title: str,
        definition: dict,
        expected_version: int,
        edited: bool = False,
        notes: str | None = None,
        workspace: str = "default",
    ) -> ReviewApprovalResult:
        with self._session_factory() as session, session.begin():
            task = session.execute(
                select(ReviewTask)
                .where(
                    ReviewTask.id == task_id,
                    ReviewTask.workspace == workspace,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if task is None:
                raise NotFoundError(f"review task {task_id!r} not found")
            if task.row_version != expected_version:
                raise OptimisticConcurrencyError(
                    f"review task {task_id!r}: expected row_version {expected_version}, "
                    f"found {task.row_version} (concurrently modified)"
                )
            if task.status not in {"open", "in_progress"}:
                raise OptimisticConcurrencyError(
                    f"review task {task_id!r} is already {task.status!r}"
                )
            if task.proposal_in_flight:
                _reclaim_expired_proposal(task)
            if task.proposal_in_flight:
                raise OptimisticConcurrencyError(
                    f"review task {task_id!r} has a proposal in flight"
                )
            if task.affected_context_id is None:
                raise NotFoundError(
                    f"review task {task_id!r} has no affected_context_id; approve requires "
                    "an existing GovernedContext identity to version"
                )
            context = session.execute(
                select(GovernedContext).where(
                    GovernedContext.id == task.affected_context_id,
                    GovernedContext.workspace == workspace,
                )
            ).scalar_one_or_none()
            if context is None:
                raise NotFoundError(f"governed context {task.affected_context_id!r} not found")

            latest_version = session.execute(
                select(func.max(GovernedContextVersion.version)).where(
                    GovernedContextVersion.context_id == context.id
                )
            ).scalar_one()
            next_version = (latest_version or 0) + 1

            version_row = GovernedContextVersion(
                context_id=context.id,
                version=next_version,
                title=title,
                definition=definition,
                reviewer=decided_by,
                created_by=decided_by,
                **_version_columns(definition),
            )
            session.add(version_row)
            session.flush()

            decision_row = ReviewDecision(
                review_task_id=task.id,
                decision="edit" if edited else "approve",
                decided_by=decided_by,
                resulting_context_version_id=version_row.id,
                notes=notes,
            )
            session.add(decision_row)
            session.flush()

            version_row.approval_decision_id = decision_row.id
            context.current_version_id = version_row.id
            context.lifecycle = "approved"
            context.updated_at = utcnow()
            task.status = "resolved"
            task.row_version += 1
            task.updated_at = utcnow()
            session.flush()

            session.execute(
                update(GovernedContextVersion)
                .where(GovernedContextVersion.id == version_row.id)
                .values(
                    search_vector=func.to_tsvector("english", _search_text(definition) or title)
                )
            )

            return ReviewApprovalResult(
                decision=_decision_record(decision_row),
                context_version=_context_version_record(version_row),
            )

    def reject(
        self,
        task_id: str,
        *,
        decided_by: str,
        expected_version: int,
        notes: str | None = None,
        workspace: str = "default",
    ) -> ReviewDecisionRecord:
        with self._session_factory() as session, session.begin():
            task = session.execute(
                select(ReviewTask)
                .where(
                    ReviewTask.id == task_id,
                    ReviewTask.workspace == workspace,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if task is None:
                raise NotFoundError(f"review task {task_id!r} not found")
            if task.row_version != expected_version:
                raise OptimisticConcurrencyError(
                    f"review task {task_id!r}: expected row_version {expected_version}, "
                    f"found {task.row_version} (concurrently modified)"
                )
            if task.status not in {"open", "in_progress"}:
                raise OptimisticConcurrencyError(
                    f"review task {task_id!r} is already {task.status!r}"
                )
            if task.proposal_in_flight:
                _reclaim_expired_proposal(task)
            if task.proposal_in_flight:
                raise OptimisticConcurrencyError(
                    f"review task {task_id!r} has a proposal in flight"
                )
            decision_row = ReviewDecision(
                review_task_id=task.id, decision="reject", decided_by=decided_by, notes=notes
            )
            session.add(decision_row)
            task.status = "dismissed"
            task.row_version += 1
            task.updated_at = utcnow()
            session.flush()
            return _decision_record(decision_row)

    def reserve_proposal(
        self,
        task_id: str,
        *,
        workspace: str = "default",
        expected_version: int,
        session: Session | None = None,
    ) -> ReviewTaskRecord:
        """Atomically reserve an open task before a remote PR side effect.

        The row lock plus the expected version means a terminal decision or another
        proposal attempt wins deterministically; no caller can pass a stale preflight
        and still reach the remote writer.
        """

        def _apply(sess: Session) -> ReviewTaskRecord:
            row = sess.execute(
                select(ReviewTask)
                .where(
                    ReviewTask.id == task_id,
                    ReviewTask.workspace == workspace,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"review task {task_id!r} not found")
            if row.row_version != expected_version:
                raise OptimisticConcurrencyError(
                    f"review task {task_id!r}: expected row_version {expected_version}, "
                    f"found {row.row_version} (concurrently modified)"
                )
            if row.status not in {"open", "in_progress"}:
                raise OptimisticConcurrencyError(
                    f"review task {task_id!r} is already {row.status!r}"
                )
            if row.proposal_in_flight:
                _reclaim_expired_proposal(row)
            if row.proposal_in_flight:
                raise OptimisticConcurrencyError(
                    f"review task {task_id!r} already has a proposal in flight"
                )
            now = utcnow()
            row.proposal_in_flight = True
            row.proposal_lease_id = new_id("lease")
            row.proposal_lease_expires_at = now + timedelta(seconds=PROPOSAL_LEASE_SECONDS)
            row.row_version += 1
            row.updated_at = now
            sess.flush()
            return _task_record(row)

        if session is not None:
            return _apply(session)
        with self._session_factory() as owned, owned.begin():
            return _apply(owned)

    def assert_proposal_lease(
        self,
        task_id: str,
        *,
        workspace: str = "default",
        lease_id: str,
        session: Session | None = None,
    ) -> ReviewTaskRecord:
        """Verify lease ownership immediately before a remote proposal side effect.

        Reservation prevents concurrent starts, while this second fence prevents a writer
        that was paused through lease expiry or task takeover from pushing an obsolete PR.
        The check is deliberately separate from ``finish_proposal``: finalization is too late
        to prevent the remote side effect itself.
        """

        def _apply(sess: Session) -> ReviewTaskRecord:
            row = sess.execute(
                select(ReviewTask)
                .where(
                    ReviewTask.id == task_id,
                    ReviewTask.workspace == workspace,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"review task {task_id!r} not found")
            if (
                not row.proposal_in_flight
                or row.proposal_lease_id != lease_id
                or row.proposal_lease_expires_at is None
                or row.proposal_lease_expires_at <= utcnow()
            ):
                raise OptimisticConcurrencyError(
                    f"review task {task_id!r} proposal reservation is no longer owned by this "
                    "writer"
                )
            return _task_record(row)

        if session is not None:
            return _apply(session)
        with self._session_factory() as owned, owned.begin():
            return _apply(owned)

    def finish_proposal(
        self,
        task_id: str,
        proposal_payload: dict,
        *,
        workspace: str = "default",
        lease_id: str,
        session: Session | None = None,
    ) -> ReviewTaskRecord:
        """Persist a successful proposal and release its local reservation."""
        return self._complete_proposal(
            task_id,
            proposal_payload,
            workspace=workspace,
            lease_id=lease_id,
            session=session,
            release=True,
        )

    def release_proposal(
        self,
        task_id: str,
        *,
        workspace: str = "default",
        lease_id: str,
        session: Session | None = None,
    ) -> ReviewTaskRecord:
        """Release a reservation after a remote writer failed."""
        return self._complete_proposal(
            task_id,
            None,
            workspace=workspace,
            lease_id=lease_id,
            session=session,
            release=True,
        )

    def _complete_proposal(
        self,
        task_id: str,
        proposal_payload: dict | None,
        *,
        workspace: str,
        lease_id: str,
        session: Session | None,
        release: bool,
    ) -> ReviewTaskRecord:
        def _apply(sess: Session) -> ReviewTaskRecord:
            row = sess.execute(
                select(ReviewTask)
                .where(
                    ReviewTask.id == task_id,
                    ReviewTask.workspace == workspace,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise NotFoundError(f"review task {task_id!r} not found")
            if not row.proposal_in_flight:
                raise OptimisticConcurrencyError(
                    f"review task {task_id!r} has no proposal reservation"
                )
            if row.proposal_lease_id != lease_id:
                raise OptimisticConcurrencyError(
                    f"review task {task_id!r} proposal reservation is no longer owned by this "
                    "writer"
                )
            if proposal_payload is not None:
                row.proposal_payload = proposal_payload
            row.proposal_in_flight = False
            row.proposal_lease_id = None
            row.proposal_lease_expires_at = None
            row.row_version += 1
            row.updated_at = utcnow()
            sess.flush()
            return _task_record(row)

        if session is not None:
            return _apply(session)
        with self._session_factory() as owned, owned.begin():
            return _apply(owned)
