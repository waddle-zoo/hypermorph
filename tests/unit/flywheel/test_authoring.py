"""The authoring driver's boundary, without a database (hy-jg2v).

Driven by a `ScriptedRuntime` that emits the agent's tool calls, so the draft,
its validation, its attribution and its refusals are exercised deterministically
with no model and no Postgres. The DB-touching conformance -- that a proposal
persists UNAPPROVED and writes no governed table -- lives in
`tests/postgres/test_authoring.py`.
"""

from __future__ import annotations

import json
import types

from hyperset.flywheel import authoring
from hyperset.flywheel.authoring import (
    GOVERNANCE,
    LIVE_LOOKUP_ASSET,
    PRODUCER,
    PROPOSE_CONTEXT_DEFINITION,
    AuthoringExecutor,
    authoring_tool_specs,
    draft_definition,
)
from hyperset.planner.loop import RESOLVE_PATH_OPERATIONS
from hyperset.planner.runtime import ScriptedRuntime, ToolCall

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


class _CapturingReviews:
    """A stand-in for the review repository that records what it was asked to
    persist. Its presence is also the proof of criterion C: the driver reaches a
    review-task writer, never a governed-context or approval writer -- there is
    no `approve`/`propose_version` on this object to call."""

    def __init__(self) -> None:
        self.created: list[dict] = []

    def create_task(self, **kwargs) -> object:
        self.created.append(kwargs)
        return types.SimpleNamespace(id="rt-fake", status="open", **kwargs)


def _run(script, review_repository, **overrides):
    kwargs = dict(
        domain="revenue",
        undeclared=["churn"],
        question="How much did customer churn cost us last quarter?",
        gathered={"candidates": []},
        runtime=ScriptedRuntime(script=script),
        session_factory=None,  # unused: this script calls no DB-backed tool
        review_repository=review_repository,
    )
    kwargs.update(overrides)
    return draft_definition(**kwargs)


def test_a_valid_draft_persists_unapproved_with_attribution():
    reviews = _CapturingReviews()
    outcome = _run([ToolCall(PROPOSE_CONTEXT_DEFINITION, {"definition": VALID_DRAFT})], reviews)

    assert outcome.status == "drafted"
    assert len(reviews.created) == 1
    payload = reviews.created[0]["proposal_payload"]
    # Non-governed label, and nothing that reads as approved/governed/canonical.
    assert payload["governance"] == GOVERNANCE == "unapproved"
    assert "approved" not in payload and "canonical" not in payload
    # ADR 0019 floor 9: the draft names what produced it.
    assert payload["produced_by"]["producer"] == PRODUCER
    assert "model" in payload["produced_by"]
    assert set(payload["provenance"]) == {"prompt_hash", "tools_hash", "model", "runtime"}
    assert payload["definition"] == VALID_DRAFT
    # The task is a review task, opened -- not an approval.
    assert reviews.created[0]["idempotency_key"].startswith("authoring:revenue:")


def test_the_proposal_carries_the_miss_question_and_gathered_sources():
    """hy-1q9w: the expert judges a proposal against the miss and the sources it
    was drafted from, so both are PERSISTED, not left transient. The gathered
    summary stays assist-labelled (`observed`)."""
    reviews = _CapturingReviews()
    gathered = {
        "candidates": [
            {
                "rank": 1,
                "ref": "superset:dataset:abc",
                "asset_type": "dataset",
                "governance": "observed",
                "signals": [{"signal": "git_engagement"}, {"signal": "source_freshness"}],
            }
        ]
    }
    outcome = draft_definition(
        domain="revenue",
        undeclared=["churn"],
        question="How much did customer churn cost us last quarter?",
        gathered=gathered,
        runtime=ScriptedRuntime(
            script=[ToolCall(PROPOSE_CONTEXT_DEFINITION, {"definition": VALID_DRAFT})]
        ),
        session_factory=None,
        review_repository=reviews,
        resolve_miss_id="rm-123",
    )

    assert outcome.status == "drafted"
    payload = reviews.created[0]["proposal_payload"]
    assert payload["miss"]["question"] == "How much did customer churn cost us last quarter?"
    assert payload["miss"]["resolve_miss_id"] == "rm-123"
    sources = payload["gathered_sources"]
    assert sources == [
        {
            "rank": 1,
            "ref": "superset:dataset:abc",
            "asset_type": "dataset",
            "governance": "observed",
            "signals": ["git_engagement", "source_freshness"],
        }
    ]


def test_the_model_receives_the_domain_gathered_refs_and_feedback():
    seen = {}

    class _Runtime:
        def tools(self):
            return []

        def run(self, question, *, on_message, call_tool):
            seen.update(json.loads(question))
            call_tool(ToolCall(PROPOSE_CONTEXT_DEFINITION, {"definition": VALID_DRAFT}))

        def provenance(self):
            return {"runtime": "capturing", "model": "test"}

        def close(self):
            pass

    gathered = {
        "candidates": [
            {
                "rank": 1,
                "ref": "superset:dataset:abc",
                "asset_type": "dataset",
                "governance": "observed",
                "signals": [{"signal": "source_freshness"}],
            }
        ]
    }
    draft_definition(
        domain="revenue",
        undeclared=["churn"],
        question="What is churn?",
        gathered=gathered,
        runtime=_Runtime(),
        session_factory=None,
        review_repository=_CapturingReviews(),
        feedback="Use the approved period.",
    )

    assert seen == {
        "domain": "revenue",
        "undeclared_concepts": ["churn"],
        "question": "What is churn?",
        "gathered_sources": [
            {
                "rank": 1,
                "ref": "superset:dataset:abc",
                "asset_type": "dataset",
                "governance": "observed",
                "signals": ["source_freshness"],
            }
        ],
        "expert_feedback": "Use the approved period.",
    }


def test_an_invalid_draft_is_refused_and_never_persisted():
    reviews = _CapturingReviews()
    bad = {
        "definitions": [{"term": "churn", "statement": "x"}],
        "approved_sources": [{"ref": "table:postgres:a.b.c", "role": "primary"}],
        # reads a source it did not approve
        "fields": [{"name": "f", "source_ref": "table:postgres:z.z.z", "expression": "e"}],
    }
    outcome = _run([ToolCall(PROPOSE_CONTEXT_DEFINITION, {"definition": bad})], reviews)

    assert outcome.status == "invalid"
    assert any("not an approved source" in reason for reason in outcome.reasons)
    assert reviews.created == [], "an invalid draft must not become even an unapproved artifact"


def test_a_run_that_emits_no_draft_persists_nothing():
    reviews = _CapturingReviews()
    outcome = _run(["I could not draft a definition."], reviews)

    assert outcome.status == "no_draft"
    assert reviews.created == []


def test_the_authoring_tools_are_not_the_resolve_path_surface():
    """Criterion: the authoring run drives the loop with its OWN declarations, so
    it cannot move `tools_hash` or the pinned #25 recordings. The tool names are
    disjoint from the locked resolve-path allowlist."""
    names = {spec["name"] for spec in authoring_tool_specs()}
    assert names.isdisjoint(set(RESOLVE_PATH_OPERATIONS))
    assert PROPOSE_CONTEXT_DEFINITION in names


def test_the_executor_captures_exactly_one_draft():
    executor = AuthoringExecutor(session_factory=None, gathered={"candidates": []})
    executor.call(PROPOSE_CONTEXT_DEFINITION, {"definition": {"a": 1}})
    executor.call(PROPOSE_CONTEXT_DEFINITION, {"definition": VALID_DRAFT})
    # A second proposal replaces the first: one candidate per miss.
    assert executor.draft == VALID_DRAFT


def test_a_live_lookup_of_an_ungathered_ref_is_refused():
    executor = AuthoringExecutor(session_factory=None, gathered={"candidates": []})
    result = executor.call(LIVE_LOOKUP_ASSET, {"ref": "superset:dataset:not-gathered"})
    assert result.refused
    assert result.error.code == "authoring_tool_error"


def test_the_executor_has_no_governed_or_warehouse_writer():
    """Criterion C/D, structurally: the executor exposes no approval, no governed
    version writer, and no SQL/execute method. The boundary is the type."""
    executor = AuthoringExecutor(session_factory=None, gathered={"candidates": []})
    for forbidden in ("approve", "propose_version", "execute", "run_sql", "upsert"):
        assert not hasattr(executor, forbidden)
    # The module imports no governed-context or review-approval writer into the
    # executor's reach.
    assert not hasattr(authoring, "PostgresGovernedContextRepository")
