import pytest

from hyperset.repositories.errors import NotFoundError
from hyperset.repositories.postgres import PostgresProcessorRepository


@pytest.mark.postgres
def test_claim_rejects_concurrent_same_trigger(session_factory):
    repo = PostgresProcessorRepository(session_factory)
    assert repo.claim_run(trigger_type="manual", rule_versions={}) is not None
    assert repo.claim_run(trigger_type="manual", rule_versions={}) is None


@pytest.mark.postgres
def test_finished_trigger_can_run_again(session_factory):
    repo = PostgresProcessorRepository(session_factory)
    first = repo.claim_run(trigger_type="manual", rule_versions={})
    repo.finish_run(first.id, counters={})
    second = repo.claim_run(trigger_type="manual", rule_versions={})
    assert second is not None
    assert second.retries == 1


@pytest.mark.postgres
def test_failed_run_can_retry(session_factory):
    repo = PostgresProcessorRepository(session_factory)
    run = repo.claim_run(trigger_type="manual", rule_versions={})
    failed = repo.fail_run(run.id, errors=["boom"])
    retried = repo.retry_run(run.id, rule_versions={})
    assert failed.errors == ["boom"]
    assert retried.status == "running"
    assert retried.retries == 1


@pytest.mark.postgres
def test_running_trigger_cannot_retry(session_factory):
    repo = PostgresProcessorRepository(session_factory)
    run = repo.claim_run(trigger_type="manual", rule_versions={})
    with pytest.raises(NotFoundError):
        repo.retry_run(run.id, rule_versions={})
