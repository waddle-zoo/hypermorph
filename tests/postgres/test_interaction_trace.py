"""The durable MCP interaction trace, end to end over a real DB (hy-oqevj).

Proves the migration, the write at the run_operation boundary, and the three
properties the epic requires: a search+resolve in one session reassemble into a
reconstructable chain linked by correlation id; hit/miss/denied each record with
the right status; and no matched content, snippet, or denied-source byte lands in
a row (only redacted query + opaque location ids). The behavioral matrix (status
mapping, degraded-store signal, redaction call) is in
tests/unit/transport/test_interaction_trace.py.
"""

from __future__ import annotations

import json

import pytest

from hyperset.observability.interaction import TraceContext, set_trace_context
from hyperset.repositories.postgres import (
    PostgresContextRepository,
    PostgresInteractionTraceRepository,
)
from hyperset.security import authz
from hyperset.security.authz import Principal, Role
from hyperset.transport.operations import run_operation

QUESTION = "Which source and rules should an analyst use for recognized revenue by region?"
GOVERNED = {"domains": ["revenue"], "concepts": ["recognized_revenue"]}
# A well-formed superset dataset ref the estate never observed -> a served bundle
# with a warning, a MISS for the trace, exactly as the miss-log treats it.
MISS_DIRECTIVE = {"asset_refs": ["superset:dataset:00000000-0000-0000-0000-000000000000"]}

SNIPPET = "MATCH_MARKER recognized revenue by region"


@pytest.fixture(autouse=True)
def _clear_trace_context():
    set_trace_context(None)
    yield
    set_trace_context(None)


def _make_searchable_source(session_factory):
    # A DISTINCT domain from `revenue_slice`'s governed context, so registering it does
    # not disturb the governed resolve the chain test also drives.
    repo = PostgresContextRepository(session_factory)
    source = repo.register_source(repository="git@example/searchdemo", ref="main", path="context")
    repo.record_snapshot(
        source_id=source.id,
        commit_sha="commit-searchdemo",
        committed_at=None,
        domain="searchdemo",
        title="Search Demo",
        files={"rev.md": SNIPPET},
        normalized={},
    )
    return source.id


@pytest.mark.postgres
def test_a_search_then_resolve_reassemble_into_a_correlated_chain(session_factory, revenue_slice):
    _make_searchable_source(session_factory)
    traces = PostgresInteractionTraceRepository(session_factory)

    # One session, two turns tied by a single correlation id: a search, then the
    # resolve that answers it.
    set_trace_context(
        TraceContext(
            session_id="sess-1", turn_id="turn-1", tool_call_id="call-a", correlation_id="corr-1"
        )
    )
    run_operation("search_knowledge", {"query": "recognized"}, session_factory=session_factory)

    set_trace_context(
        TraceContext(
            session_id="sess-1", turn_id="turn-2", tool_call_id="call-b", correlation_id="corr-1"
        )
    )
    resolved = run_operation(
        "resolve_analytics_context",
        {"query": QUESTION, "directive": GOVERNED},
        session_factory=session_factory,
    )
    assert resolved["resolution"]["status"] == "governed"

    chain = traces.session_chain("sess-1")
    assert [row.tool_name for row in chain] == ["search_knowledge", "resolve_analytics_context"]
    # The correlation id ties the search to the resolve that followed it.
    assert {row.correlation_id for row in chain} == {"corr-1"}
    assert [row.turn_id for row in chain] == ["turn-1", "turn-2"]
    # Both found governed context -> both hits; the resolve's answer is keyed by its bundle id.
    assert [row.status for row in chain] == ["hit", "hit"]
    assert chain[1].hit_ids == [resolved["bundle_id"]]
    # Identity is server-derived; the gate is off here so the caller is anonymous.
    assert all(row.principal_identity == "anonymous" for row in chain)


@pytest.mark.postgres
def test_a_search_with_no_match_records_a_miss(session_factory):
    _make_searchable_source(session_factory)
    traces = PostgresInteractionTraceRepository(session_factory)

    set_trace_context(TraceContext(session_id="sess-miss", correlation_id="corr-miss"))
    run_operation(
        "search_knowledge", {"query": "zzz-no-such-term"}, session_factory=session_factory
    )

    (row,) = traces.session_chain("sess-miss")
    assert row.status == "miss"
    assert row.hit_ids == []


@pytest.mark.postgres
def test_a_search_hit_records_opaque_location_ids_never_the_snippet(session_factory):
    source_id = _make_searchable_source(session_factory)
    traces = PostgresInteractionTraceRepository(session_factory)

    set_trace_context(TraceContext(session_id="sess-hit", correlation_id="corr-hit"))
    run_operation("search_knowledge", {"query": "recognized"}, session_factory=session_factory)

    (row,) = traces.session_chain("sess-hit")
    assert row.status == "hit"
    # A location id (source:path:line), never the matched line's text.
    assert row.hit_ids == [f"{source_id}:rev.md:1"]
    blob = json.dumps({"query": row.query, "hit_ids": row.hit_ids, "filters": row.filters})
    assert "MATCH_MARKER" not in blob
    assert SNIPPET not in blob


@pytest.mark.postgres
def test_caller_secrets_never_land_in_the_durable_row(session_factory, monkeypatch):
    # The durable, queryable row must strip caller credentials UNCONDITIONALLY (no
    # HYPERSET_PII_GUARD set): a secret in the query, the intent, or a filters key lands
    # NOTHING of itself in the persisted row. Mutation: drop the redaction/narrowing in
    # _record_interaction_trace and a secret reappears here.
    monkeypatch.delenv("HYPERSET_PII_GUARD", raising=False)
    _make_searchable_source(session_factory)
    traces = PostgresInteractionTraceRepository(session_factory)

    set_trace_context(
        TraceContext(
            session_id="sess-secret",
            correlation_id="corr-secret",
            intent="context https://user:i_secret@evil/x",
        )
    )
    run_operation(
        "search_knowledge",
        {
            "query": "clone https://bob:s3cr3ttoken@git.example/repo",
            "filters": {"path": "docs/", "token": "https://u:f_secret@h"},
        },
        session_factory=session_factory,
    )

    (row,) = traces.session_chain("sess-secret")
    blob = json.dumps(
        {
            "query": row.query,
            "intent": row.intent,
            "filters": row.filters,
            "hit_ids": row.hit_ids,
        }
    )
    for secret in ("s3cr3ttoken", "i_secret", "f_secret"):
        assert secret not in blob, f"the durable row leaked {secret}"
    # The narrow filter projection kept only the validated path field, dropped the raw key.
    assert row.filters == {"path": "docs/"}
    # Redaction stripped only the userinfo, leaving the rest of the query intact.
    assert "git.example/repo" in row.query


@pytest.mark.postgres
def test_a_denied_call_records_status_and_correlation_but_no_content(session_factory, monkeypatch):
    # A source the caller will be denied, holding a distinctive marker.
    repo = PostgresContextRepository(session_factory)
    source = repo.register_source(repository="git@example/secret", ref="main", path="context")
    repo.record_snapshot(
        source_id=source.id,
        commit_sha="commit-secret",
        committed_at=None,
        domain="secret",
        title="Secret",
        files={"s.md": "DENIED_MARKER classified revenue"},
        normalized={},
    )

    # A role with NO grants: authorize fail-closed denies READ, so search_knowledge is
    # refused at the gate before it runs.
    monkeypatch.setitem(authz.ROLES, "denier", Role(name="denier", grants=()))
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    principal = Principal(subject="u1", issuer="https://issuer.example", roles=("denier",))

    from hyperset.transport.operations import OperationError

    set_trace_context(TraceContext(session_id="sess-deny", correlation_id="corr-deny"))
    with pytest.raises(OperationError):
        run_operation(
            "search_knowledge",
            {"query": "revenue"},
            session_factory=session_factory,
            principal=principal,
        )

    traces = PostgresInteractionTraceRepository(session_factory)
    (row,) = traces.session_chain("sess-deny")
    assert row.status == "denied"
    assert row.correlation_id == "corr-deny"
    assert row.hit_ids == []  # nothing found, nothing to key
    # The protected source's content never touched the row.
    blob = json.dumps(
        {"q": row.query, "hits": row.hit_ids, "filters": row.filters, "intent": row.intent}
    )
    assert "DENIED_MARKER" not in blob
    # Server-derived identity is recorded (opaque subject@issuer), not a caller field.
    assert row.principal_identity == "u1@https://issuer.example"


@pytest.mark.postgres
def test_a_resolve_trace_status_matches_the_served_resolution(session_factory, revenue_slice):
    traces = PostgresInteractionTraceRepository(session_factory)

    set_trace_context(TraceContext(session_id="sess-rmiss", correlation_id="corr-rmiss"))
    served = run_operation(
        "resolve_analytics_context",
        {"query": QUESTION, "directive": MISS_DIRECTIVE},
        session_factory=session_factory,
    )

    (row,) = traces.session_chain("sess-rmiss")
    # Independent oracle: the recorded status is the mapping of the SERVED status --
    # a no_match/observed_only resolve found nothing (miss, no hit ids), anything
    # governed is a hit keyed by the bundle id.
    served_status = served["resolution"]["status"]
    if served_status in ("no_match", "observed_only"):
        assert row.status == "miss"
        assert row.hit_ids == []
    else:
        assert row.status == "hit"
        assert row.hit_ids == [served["bundle_id"]]
