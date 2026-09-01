"""Flywheel step 4 conformance, on real Postgres and the real estate (hy-jg2v).

The authoring agent drafts one candidate definition; it is persisted UNAPPROVED
and it consumes asset bodies through a read-only live-lookup. Proven here:
- a proposed draft writes a ReviewTask and NONE of the three governed tables;
- the only approval stays the human `approve` call (every governed/approval
  writer armed to raise, and the run reaches none);
- the live-lookup body-read returns the held body when the observation carries
  it, and reads through an injected read-only transport when it does not --
  never warehouse SQL.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from hyperset.bundle.gather import gather
from hyperset.flywheel.authoring import (
    LIVE_LOOKUP_ASSET,
    PROPOSE_CONTEXT_DEFINITION,
    AuthoringExecutor,
    draft_definition,
)
from hyperset.flywheel.live_lookup import body_for_reference
from hyperset.planner.runtime import ScriptedRuntime, ToolCall
from hyperset.repositories.postgres import (
    PostgresGovernedContextRepository,
    PostgresObservedAssetRepository,
    PostgresReviewRepository,
)

CHURN_QUESTION = "How much did customer churn cost us last quarter?"
VALID_DRAFT = {
    "definitions": [{"term": "churn", "statement": "customers lost in a period"}],
    "approved_sources": [{"ref": "table:postgres:analytics.public.churn", "role": "primary"}],
    "fields": [
        {
            "name": "churn_rate",
            "source_ref": "table:postgres:analytics.public.churn",
            "expression": "lost / total",
        }
    ],
}


def _gathered(session_factory) -> dict:
    section = gather(domain="revenue", undeclared=["churn"], session_factory=session_factory)
    assert section is not None and section["candidates"]
    return section


def _counts(session_factory) -> dict[str, int]:
    with session_factory() as session:
        return {
            table: session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in (
                "governed_context",
                "governed_context_versions",
                "review_decisions",
                "review_tasks",
            )
        }


@pytest.mark.postgres
def test_a_proposed_draft_persists_unapproved_and_touches_no_governed_table(
    session_factory, revenue_slice
):
    before = _counts(session_factory)
    outcome = draft_definition(
        domain="revenue",
        undeclared=["churn"],
        question=CHURN_QUESTION,
        gathered=_gathered(session_factory),
        runtime=ScriptedRuntime(
            script=[ToolCall(PROPOSE_CONTEXT_DEFINITION, {"definition": VALID_DRAFT})]
        ),
        session_factory=session_factory,
    )

    assert outcome.status == "drafted"
    after = _counts(session_factory)
    # The draft is an UNAPPROVED review task and nothing else: no governed
    # context, no version, no decision -- the same boundary the processor path
    # holds (test_no_finding_creates_governed_context_or_a_decision).
    assert after["governed_context"] == before["governed_context"]
    assert after["governed_context_versions"] == before["governed_context_versions"]
    assert after["review_decisions"] == before["review_decisions"]
    assert after["review_tasks"] == before["review_tasks"] + 1

    task = PostgresReviewRepository(session_factory).get_task(outcome.task.id)
    assert task.status == "open"
    assert task.proposal_payload["governance"] == "unapproved"
    assert task.proposal_payload["definition"] == VALID_DRAFT
    assert task.proposal_payload["produced_by"]["producer"] == "authoring/1"


@pytest.mark.postgres
def test_the_only_approval_stays_the_human_review_call(session_factory, revenue_slice, monkeypatch):
    def forbidden(name):
        def _stub(*args, **kwargs):
            raise AssertionError(f"authoring reached the forbidden writer {name!r}")

        return _stub

    monkeypatch.setattr(PostgresReviewRepository, "approve", forbidden("approve"))
    monkeypatch.setattr(
        PostgresGovernedContextRepository, "propose_version", forbidden("propose_version")
    )
    for method in ("upsert", "mark_missing_deleted", "replace_relationships"):
        monkeypatch.setattr(PostgresObservedAssetRepository, method, forbidden(method))

    outcome = draft_definition(
        domain="revenue",
        undeclared=["churn"],
        question=CHURN_QUESTION,
        gathered=_gathered(session_factory),
        runtime=ScriptedRuntime(
            script=[ToolCall(PROPOSE_CONTEXT_DEFINITION, {"definition": VALID_DRAFT})]
        ),
        session_factory=session_factory,
    )
    assert outcome.status == "drafted"


@pytest.mark.postgres
def test_a_live_lookup_returns_the_held_body_without_a_source_call(session_factory, revenue_slice):
    """The v0 connectors ingest whole, so a gathered candidate's body is already
    held: the lookup reads the store and issues NO live call. A transport that
    raises if built proves no source call was made."""
    section = _gathered(session_factory)
    candidate = section["candidates"][0]

    def _no_transport(_connection):
        raise AssertionError("a held body must not build a live transport")

    executor = AuthoringExecutor(
        session_factory=session_factory, gathered=section, transport_factory=_no_transport
    )
    result = executor.call(LIVE_LOOKUP_ASSET, {"ref": candidate["ref"]})

    assert not result.refused
    assert result.payload["held"] is True
    assert result.payload["source"] == "observed"
    assert result.payload["body"]


@pytest.mark.postgres
def test_a_reference_only_asset_is_read_live_through_a_read_only_transport(
    session_factory, revenue_slice
):
    """The fetch branch (ADR 0024 dec 2): an observation that does NOT hold the
    body in full is read live, through an injected read-only transport that only
    fetches -- exercising the path a reference-not-ingest observation (hy-op9p)
    will take, with no warehouse SQL anywhere."""
    assets = PostgresObservedAssetRepository(session_factory)
    # A reference-only observation: identity present, body absent.
    assets.upsert(
        connection_id=revenue_slice["connection_id"],
        external_id="reference-only-dataset",
        asset_type="dataset",
        sync_run_id=revenue_slice["baseline_sync_run_id"],
        raw_payload={},
    )

    class _FakeReadOnly:
        def __init__(self):
            self.calls = []

        def fetch_body(self, *, asset_type, external_id, raw_payload):
            self.calls.append((asset_type, external_id))
            return {"definition": "live body", "read_only": True}

    fake = _FakeReadOnly()
    result = body_for_reference(
        connection_id=revenue_slice["connection_id"],
        external_id="reference-only-dataset",
        asset_type="dataset",
        session_factory=session_factory,
        transport_factory=lambda _connection: fake,
    )

    assert result["held"] is False
    assert result["source"] == "live_lookup"
    assert result["body"] == {"definition": "live body", "read_only": True}
    assert fake.calls == [("dataset", "reference-only-dataset")]
