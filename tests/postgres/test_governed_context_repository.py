import pytest

from hyperset.repositories.errors import NotFoundError
from hyperset.repositories.postgres import PostgresGovernedContextRepository


@pytest.mark.postgres
def test_propose_version_creates_identity_and_version_one(session_factory):
    repo = PostgresGovernedContextRepository(session_factory)
    v1 = repo.propose_version(
        context_type="governed_metric",
        domain="revenue",
        name="net_revenue",
        title="Net Revenue",
        definition={"expression": "a - b"},
    )
    assert v1.version == 1
    context = repo.get(v1.context_id)
    assert context.lifecycle == "candidate"
    assert context.current_version.version == 1


@pytest.mark.postgres
def test_second_candidate_appends_version_without_granting_approval(session_factory):
    repo = PostgresGovernedContextRepository(session_factory)
    v1 = repo.propose_version(
        context_type="governed_metric",
        domain="revenue",
        name="net_revenue",
        title="Net Revenue",
        definition={"expression": "a - b"},
    )
    v2 = repo.propose_version(
        context_type="governed_metric",
        domain="revenue",
        name="net_revenue",
        title="Net Revenue v2",
        definition={"expression": "a - b - c"},
    )
    assert v2.version == 2
    context = repo.get(v1.context_id)
    assert context.current_version.version == 2
    assert context.lifecycle == "candidate"

    # v1 is retrievable unchanged -- immutable version history.
    history_v1 = repo.get_version(v1.context_id, 1)
    assert history_v1.definition == {"expression": "a - b"}
    assert history_v1.title == "Net Revenue"


@pytest.mark.postgres
def test_candidate_cannot_replace_approved_pointer(session_factory):
    contexts = PostgresGovernedContextRepository(session_factory)
    from hyperset.repositories.postgres import PostgresReviewRepository

    first = contexts.propose_version(
        context_type="governed_metric",
        domain="revenue",
        name="net_revenue",
        title="Net Revenue",
        definition={"expression": "a - b"},
    )
    reviews = PostgresReviewRepository(session_factory)
    task = reviews.create_task(
        reason="initial approval",
        idempotency_key="approve-net-revenue",
        affected_context_id=first.context_id,
    )
    approved = reviews.approve(
        task.id,
        decided_by="reviewer",
        title="Net Revenue",
        definition={"expression": "a - b"},
        expected_version=task.row_version,
    )
    proposal = contexts.propose_version(
        context_type="governed_metric",
        domain="revenue",
        name="net_revenue",
        title="Unreviewed change",
        definition={"expression": "a"},
    )

    context = contexts.get(first.context_id)
    assert proposal.version == approved.context_version.version + 1
    assert context.lifecycle == "approved"
    assert context.current_version.id == approved.context_version.id


@pytest.mark.postgres
def test_history_returns_all_versions_oldest_first(session_factory):
    repo = PostgresGovernedContextRepository(session_factory)
    v1 = repo.propose_version(
        context_type="governed_metric",
        domain="revenue",
        name="m",
        title="M1",
        definition={"expression": "a"},
    )
    repo.propose_version(
        context_type="governed_metric",
        domain="revenue",
        name="m",
        title="M2",
        definition={"expression": "b"},
    )
    repo.propose_version(
        context_type="governed_metric",
        domain="revenue",
        name="m",
        title="M3",
        definition={"expression": "c"},
    )
    history = repo.history(v1.context_id)
    assert [v.version for v in history] == [1, 2, 3]
    assert [v.title for v in history] == ["M1", "M2", "M3"]


@pytest.mark.postgres
def test_get_by_name_and_missing_raises(session_factory):
    repo = PostgresGovernedContextRepository(session_factory)
    repo.propose_version(
        context_type="dataset_guidance",
        domain="revenue",
        name="orders",
        title="Orders",
        definition={},
    )
    found = repo.get_by_name(context_type="dataset_guidance", domain="revenue", name="orders")
    assert found.name == "orders"
    with pytest.raises(NotFoundError):
        repo.get_by_name(context_type="dataset_guidance", domain="revenue", name="does-not-exist")


@pytest.mark.postgres
def test_search_by_domain_and_context_type(session_factory):
    repo = PostgresGovernedContextRepository(session_factory)
    repo.propose_version(
        context_type="governed_metric",
        domain="revenue",
        name="net_revenue",
        title="Net Revenue",
        definition={"business_definition": "recognized minus refunded"},
    )
    repo.propose_version(
        context_type="governed_metric",
        domain="marketing",
        name="cac",
        title="Customer Acquisition Cost",
        definition={"business_definition": "spend divided by new customers"},
    )
    hits = repo.search("refunded", domain="revenue")
    assert len(hits) == 1
    assert hits[0].name == "net_revenue"

    no_hits = repo.search("refunded", domain="marketing")
    assert no_hits == []
