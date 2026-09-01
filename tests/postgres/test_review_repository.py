from datetime import timedelta

import pytest
from sqlalchemy import update

from hyperset.db.base import utcnow
from hyperset.db.models import ReviewTask
from hyperset.repositories.errors import NotFoundError, OptimisticConcurrencyError
from hyperset.repositories.postgres import (
    PostgresGovernedContextRepository,
    PostgresReviewRepository,
)


@pytest.fixture
def context_id(session_factory):
    return (
        PostgresGovernedContextRepository(session_factory)
        .propose_version(
            context_type="governed_metric",
            domain="revenue",
            name="net_revenue",
            title="Net Revenue",
            definition={"expression": "a - b"},
        )
        .context_id
    )


@pytest.mark.postgres
def test_deduplicated_task_creation(session_factory, context_id):
    repo = PostgresReviewRepository(session_factory)
    first = repo.create_task(
        reason="new dataset drift detected", idempotency_key="drift-orders-2026-07-25"
    )
    second = repo.create_task(
        reason="new dataset drift detected", idempotency_key="drift-orders-2026-07-25"
    )
    assert first.id == second.id
    assert len(repo.list_tasks()) == 1


@pytest.mark.postgres
def test_transactional_approve_creates_version_and_decision_together(session_factory, context_id):
    reviews = PostgresReviewRepository(session_factory)
    contexts = PostgresGovernedContextRepository(session_factory)

    task = reviews.create_task(
        reason="proposed refinement", idempotency_key="k1", affected_context_id=context_id
    )
    result = reviews.approve(
        task.id,
        decided_by="brandon",
        title="Net Revenue",
        definition={"expression": "a - b - c", "business_definition": "refined"},
        expected_version=task.row_version,
    )
    assert result.decision.decision == "approve"
    assert result.context_version.version == 2

    context = contexts.get(context_id)
    assert context.lifecycle == "approved"
    assert context.current_version.version == 2

    resolved_task = reviews.get_task(task.id)
    assert resolved_task.status == "resolved"
    assert resolved_task.row_version == 2


@pytest.mark.postgres
def test_edit_decision_is_recorded_distinctly_from_approve(session_factory, context_id):
    reviews = PostgresReviewRepository(session_factory)
    task = reviews.create_task(
        reason="proposed refinement", idempotency_key="k2", affected_context_id=context_id
    )
    result = reviews.approve(
        task.id,
        decided_by="brandon",
        title="Net Revenue",
        definition={"expression": "a - b - c"},
        expected_version=task.row_version,
        edited=True,
    )
    assert result.decision.decision == "edit"


@pytest.mark.postgres
def test_reject_dismisses_task_without_versioning_context(session_factory, context_id):
    reviews = PostgresReviewRepository(session_factory)
    contexts = PostgresGovernedContextRepository(session_factory)
    task = reviews.create_task(
        reason="proposed refinement", idempotency_key="k3", affected_context_id=context_id
    )
    decision = reviews.reject(
        task.id,
        decided_by="brandon",
        expected_version=task.row_version,
        notes="not needed",
    )
    assert decision.decision == "reject"
    assert reviews.get_task(task.id).status == "dismissed"
    # Rejecting must not touch the governed context at all.
    assert contexts.get(context_id).current_version.version == 1


@pytest.mark.postgres
def test_optimistic_concurrency_conflict_on_stale_version(session_factory, context_id):
    reviews = PostgresReviewRepository(session_factory)
    task = reviews.create_task(
        reason="proposed refinement", idempotency_key="k4", affected_context_id=context_id
    )
    assert task.row_version == 1

    # First reviewer approves, bumping row_version to 2.
    reviews.approve(
        task.id,
        decided_by="brandon",
        title="Net Revenue",
        definition={"expression": "a - b - c"},
        expected_version=task.row_version,
    )

    # A second reviewer, holding a stale copy of the task (row_version=1),
    # tries to reject the same task -- must fail, not silently overwrite.
    with pytest.raises(OptimisticConcurrencyError):
        reviews.reject(task.id, decided_by="someone-else", expected_version=1)


@pytest.mark.postgres
def test_approve_requires_existing_affected_context(session_factory):
    reviews = PostgresReviewRepository(session_factory)
    task = reviews.create_task(reason="orphan proposal", idempotency_key="k5")
    with pytest.raises(NotFoundError):
        reviews.approve(
            task.id,
            decided_by="brandon",
            title="X",
            definition={},
            expected_version=task.row_version,
        )


@pytest.mark.postgres
def test_resolved_task_cannot_be_decided_twice(session_factory, context_id):
    reviews = PostgresReviewRepository(session_factory)
    task = reviews.create_task(
        reason="proposed refinement", idempotency_key="k6", affected_context_id=context_id
    )
    reviews.reject(task.id, decided_by="brandon", expected_version=task.row_version)

    with pytest.raises(OptimisticConcurrencyError):
        reviews.reject(task.id, decided_by="second", expected_version=task.row_version + 1)


@pytest.mark.postgres
def test_an_expired_proposal_lease_is_reclaimed_but_an_old_writer_cannot_clear_new_lease(
    session_factory,
):
    reviews = PostgresReviewRepository(session_factory)
    task = reviews.create_task(reason="proposal", idempotency_key="lease-recovery")

    first = reviews.reserve_proposal(task.id, expected_version=task.row_version)
    assert first.proposal_lease_id

    with session_factory() as session, session.begin():
        session.execute(
            update(ReviewTask)
            .where(ReviewTask.id == task.id)
            .values(proposal_lease_expires_at=utcnow() - timedelta(seconds=1))
        )

    second = reviews.reserve_proposal(task.id, expected_version=first.row_version)
    assert second.proposal_lease_id and second.proposal_lease_id != first.proposal_lease_id

    with pytest.raises(OptimisticConcurrencyError):
        reviews.release_proposal(task.id, lease_id=first.proposal_lease_id)

    released = reviews.release_proposal(task.id, lease_id=second.proposal_lease_id)
    assert released.proposal_in_flight is False
    assert released.proposal_lease_id is None


@pytest.mark.postgres
def test_an_expired_proposal_lease_fails_the_pre_side_effect_fence(session_factory):
    reviews = PostgresReviewRepository(session_factory)
    task = reviews.create_task(reason="proposal", idempotency_key="lease-fence")
    reserved = reviews.reserve_proposal(task.id, expected_version=task.row_version)

    with session_factory() as session, session.begin():
        session.execute(
            update(ReviewTask)
            .where(ReviewTask.id == task.id)
            .values(proposal_lease_expires_at=utcnow() - timedelta(seconds=1))
        )

    with pytest.raises(OptimisticConcurrencyError):
        reviews.assert_proposal_lease(task.id, lease_id=reserved.proposal_lease_id)
