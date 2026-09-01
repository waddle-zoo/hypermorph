import pytest

from hyperset.repositories.postgres import PostgresEvaluationRepository


@pytest.mark.postgres
def test_create_case_and_record_run(session_factory):
    repo = PostgresEvaluationRepository(session_factory)
    case = repo.create_case(
        name="net_revenue_uses_governed_metric",
        question="What was net revenue last month?",
        expected={"must_use_metric": "net_revenue"},
        domain="revenue",
    )
    run = repo.record_run(
        case_id=case.id,
        attempt_payload={"sql": "SELECT ..."},
        scorecard={"used_governed_metric": True},
        passed=True,
        context_versions_used=[{"name": "net_revenue", "version": 1}],
    )
    assert run.passed is True
    assert run.case_id == case.id

    runs = repo.list_runs(case.id)
    assert len(runs) == 1
    assert runs[0].scorecard == {"used_governed_metric": True}


@pytest.mark.postgres
def test_list_cases_filters_by_domain(session_factory):
    repo = PostgresEvaluationRepository(session_factory)
    repo.create_case(name="a", question="q1", expected={}, domain="revenue")
    repo.create_case(name="b", question="q2", expected={}, domain="marketing")
    assert len(repo.list_cases(domain="revenue")) == 1
    assert len(repo.list_cases()) == 2
