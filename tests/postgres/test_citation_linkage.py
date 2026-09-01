"""Citation<->answer + human decision linkage, end to end over a real DB (hy-cpkvu).

Proves slice 3: an answer's citations are enumerable + correct over a REAL governed
bundle (both directions); a human include/exclude/approve/reject is recorded and linked
to principal + citation + review task; an unauthorized decision is rejected (mutation-red
on the service gate); notes are redacted; a re-submitted decision supersedes latest-wins.
The behavioral matrix (extraction, degraded, redaction call) is in
tests/unit/transport/test_citation_decision.py.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from hyperset.db.models import CitationDecision
from hyperset.observability.interaction import TraceContext, set_trace_context
from hyperset.repositories.postgres import (
    PostgresAnswerCitationRepository,
    PostgresCitationDecisionRepository,
    PostgresInteractionTraceRepository,
    PostgresReviewRepository,
)
from hyperset.security import authz
from hyperset.security.authz import Principal, Role
from hyperset.transport.operations import OperationError, _decide_citation, run_operation

QUESTION = "Which source and rules should an analyst use for recognized revenue by region?"
GOVERNED = {"domains": ["revenue"], "concepts": ["recognized_revenue"]}


@pytest.fixture(autouse=True)
def _clear_trace_context():
    set_trace_context(None)
    yield
    set_trace_context(None)


@pytest.mark.postgres
def test_a_governed_answers_citations_are_enumerable_both_directions(
    session_factory, revenue_slice
):
    set_trace_context(TraceContext(session_id="s", correlation_id="corr-cite"))
    answer = run_operation(
        "resolve_analytics_context",
        {"query": QUESTION, "directive": GOVERNED},
        session_factory=session_factory,
    )
    assert answer["resolution"]["status"] == "governed"
    bundle_id = answer["bundle_id"]

    citations = PostgresAnswerCitationRepository(session_factory).for_answer(
        workspace="default", bundle_id=bundle_id
    )
    assert citations, "a governed answer must record its citations"
    # The recorded provenance citations are EXACTLY the bundle's served provenance_refs.
    provenance = {c.citation_ref for c in citations if c.citation_kind == "provenance"}
    assert provenance == set(answer["provenance_refs"])
    # The approved-source citations are exactly the served instructions.approved_sources.
    approved_refs = {s["ref"] for s in answer["instructions"]["approved_sources"]}
    recorded_approved = {c.citation_ref for c in citations if c.citation_kind == "approved_source"}
    assert recorded_approved == approved_refs
    # Every citation carries the answer's correlation id (ties to the #503 trace).
    assert {c.correlation_id for c in citations} == {"corr-cite"}

    # Reverse direction: from a source ref back to the answer that cited it.
    if approved_refs:
        one = next(iter(approved_refs))
        answers = PostgresAnswerCitationRepository(session_factory).for_citation(
            workspace="default", source_ref=one
        )
        assert bundle_id in {a.bundle_id for a in answers}

        decision = _decide_citation(
            {
                "decision": "include",
                "citation_ref": one,
                "correlation_id": "corr-cite",
            },
            session_factory=session_factory,
            principal=None,
        )
        (trace,) = PostgresInteractionTraceRepository(session_factory).session_chain("s")
        assert trace.decision_ids == [decision["decision"]["id"]]


@pytest.mark.postgres
def test_a_human_decision_is_recorded_and_linked(session_factory):
    task = PostgresReviewRepository(session_factory).create_task(
        reason="gap", idempotency_key="idem-cdec-1"
    )
    result = _decide_citation(
        {
            "decision": "include",
            "citation_ref": "git_context:snap-1@abc",
            "source_ref": "superset:dataset:orders",
            "review_task_id": task.id,
            "correlation_id": "corr-1",
            "notes": "looks right",
        },
        session_factory=session_factory,
        principal=None,  # authz gate off -> loopback dev, decided_by anonymous
    )
    view = result["decision"]
    assert view["decision"] == "include"
    assert view["review_task_id"] == task.id
    assert view["decided_by"] == "anonymous"

    (row,) = PostgresCitationDecisionRepository(session_factory).for_task(
        workspace="default", review_task_id=task.id
    )
    assert row.citation_ref == "git_context:snap-1@abc"
    assert row.source_ref == "superset:dataset:orders"
    assert row.correlation_id == "corr-1"
    assert row.superseded_by is None  # the live decision


@pytest.mark.postgres
def test_caller_secrets_never_land_in_the_durable_decision_row(session_factory, monkeypatch):
    # The durable decision row strips caller credentials UNCONDITIONALLY (no
    # HYPERSET_PII_GUARD): a secret in notes or the refs lands nothing of itself.
    monkeypatch.delenv("HYPERSET_PII_GUARD", raising=False)
    task = PostgresReviewRepository(session_factory).create_task(
        reason="gap", idempotency_key="idem-cdec-secret"
    )
    _decide_citation(
        {
            "decision": "approve",
            "citation_ref": "https://u:c_secret@host/ref",
            "source_ref": "https://u:s_secret@host/ds",
            "review_task_id": task.id,
            "notes": "contact https://u:n_secret@h",
        },
        session_factory=session_factory,
        principal=None,
    )
    (row,) = PostgresCitationDecisionRepository(session_factory).for_task(
        workspace="default", review_task_id=task.id
    )
    import json as _json

    blob = _json.dumps(
        {
            "citation_ref": row.citation_ref,
            "source_ref": row.source_ref,
            "notes": row.notes,
            "correlation_id": row.correlation_id,
        }
    )
    for secret in ("c_secret", "s_secret", "n_secret"):
        assert secret not in blob, f"the durable decision row leaked {secret}"
    # Redaction stripped only the userinfo, leaving the rest of the ref intact.
    assert "host/ref" in row.citation_ref


@pytest.mark.postgres
def test_an_unauthorized_decision_is_rejected_and_persists_nothing(session_factory, monkeypatch):
    task = PostgresReviewRepository(session_factory).create_task(
        reason="gap", idempotency_key="idem-cdec-deny"
    )
    # authz ON + a role with no REVIEW grant: the service gate fail-closed denies. A
    # mutation that drops the gate would let this persist -> the empty-log assertion reds.
    monkeypatch.setitem(authz.ROLES, "reader_only", Role(name="reader_only", grants=()))
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    principal = Principal(subject="u1", issuer="https://issuer.example", roles=("reader_only",))

    with pytest.raises(OperationError) as exc:
        _decide_citation(
            {
                "decision": "approve",
                "citation_ref": "git_context:snap@x",
                "review_task_id": task.id,
            },
            session_factory=session_factory,
            principal=principal,
        )
    assert exc.value.code == "unauthorized"
    assert (
        PostgresCitationDecisionRepository(session_factory).for_task(
            workspace="default", review_task_id=task.id
        )
        == []
    )


@pytest.mark.postgres
def test_a_resubmitted_decision_supersedes_latest_wins(session_factory):
    task = PostgresReviewRepository(session_factory).create_task(
        reason="gap", idempotency_key="idem-cdec-supersede"
    )
    args = {"decision": "include", "citation_ref": "cit-1", "review_task_id": task.id}
    _decide_citation(dict(args), session_factory=session_factory, principal=None)
    _decide_citation(
        {**args, "decision": "exclude"}, session_factory=session_factory, principal=None
    )

    repo = PostgresCitationDecisionRepository(session_factory)
    history = repo.for_task(workspace="default", review_task_id=task.id)
    assert len(history) == 2  # both kept as history
    live = [r for r in history if r.superseded_by is None]
    assert len(live) == 1  # exactly one live decision
    assert live[0].decision == "exclude"  # latest wins
    current = repo.current(
        workspace="default",
        citation_ref="cit-1",
        principal_identity="anonymous",
        review_task_id=task.id,
    )
    assert current is not None
    assert current.decision == "exclude"


@pytest.mark.postgres
def test_a_read_is_scoped_to_its_workspace_despite_a_shared_bundle_id(session_factory):
    """Blocker 1: bundle_id is DETERMINISTIC/content-addressed, so two DIFFERENT workspaces
    asking an equivalent question compute the SAME bundle_id. `for_answer` MUST filter by a
    REQUIRED workspace, so a read scoped to workspace A returns ONLY A's citations, never B's,
    even though both share the id. An omitted scope fails closed at the call boundary (the
    keyword is mandatory). Mutation-red: drop the workspace filter -> A sees B's citation."""
    repo = PostgresAnswerCitationRepository(session_factory)
    shared_bundle = "bundle-shared-deterministic"
    repo.record(
        workspace="ws-a",
        correlation_id="corr-a",
        bundle_id=shared_bundle,
        citation_ref="cit-a",
        citation_kind="provenance",
        source_ref=None,
    )
    repo.record(
        workspace="ws-b",
        correlation_id="corr-b",
        bundle_id=shared_bundle,
        citation_ref="cit-b",
        citation_kind="provenance",
        source_ref=None,
    )

    a_only = repo.for_answer(workspace="ws-a", bundle_id=shared_bundle)
    assert {c.citation_ref for c in a_only} == {"cit-a"}  # never cit-b: no cross-tenant leak
    assert {c.workspace for c in a_only} == {"ws-a"}

    # An omitted scope is a structural fail-closed: the keyword is REQUIRED.
    with pytest.raises(TypeError):
        repo.for_answer(bundle_id=shared_bundle)  # type: ignore[call-arg]


@pytest.mark.postgres
def test_two_bare_citation_decisions_cannot_both_be_live(session_factory):
    """Blocker 2: review_task_id is NULLABLE (a bare-citation decision has no task), and
    Postgres treats NULLs as DISTINCT. A plain nullable partial-unique index would let bare
    decisions have MANY live rows -- the one-live invariant would NOT hold under concurrent or
    repeated inserts. The COALESCE-normalized index collapses NULL to a fixed sentinel, so two
    LIVE bare decisions for one (workspace, citation, principal) collide. Mutation-red: revert
    the index to the plain nullable column -> both insert -> no IntegrityError -> this reds."""

    def _bare_live() -> CitationDecision:
        return CitationDecision(
            workspace="default",
            principal_identity="anonymous",
            decision="include",
            citation_ref="cit-bare",
            source_ref=None,
            review_task_id=None,  # bare citation: no task
            correlation_id=None,
            notes=None,
        )

    with session_factory() as session, session.begin():
        session.add(_bare_live())
    # A second LIVE bare decision for the same item must violate the unique index.
    with pytest.raises(IntegrityError):
        with session_factory() as session, session.begin():
            session.add(_bare_live())
