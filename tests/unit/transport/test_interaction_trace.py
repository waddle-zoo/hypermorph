"""The durable interaction trace at the serving boundary (hy-oqevj).

The write is a property of `run_operation`: these assert the status mapping
(hit/miss for search and resolve, denied at the gate), that a broken store is
reported as DEGRADED rather than silently dropped, that the caller's query is
REDACTED before it lands, that a denied call never reaches the search body (so
no protected content is read), and that a correlation id is minted when the
caller supplies none. The real persistence + migration are proven in
tests/postgres/test_interaction_trace.py; here the repository is a spy.
"""

from __future__ import annotations

import logging

import pytest

from hyperset.observability.interaction import TraceContext, set_trace_context
from hyperset.security.authz import Principal, Role
from hyperset.transport import operations
from hyperset.transport.operations import OperationError
from tests.unit.transport.conftest import DIRECTIVE, QUESTION, governed_bundle


class _SpyTraceRepo:
    """Captures record() calls; one is constructed per _record_interaction_trace."""

    made: list[_SpyTraceRepo] = []

    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory
        self.calls: list[dict] = []
        _SpyTraceRepo.made.append(self)

    def record(self, **kwargs):
        self.calls.append(kwargs)
        return None


@pytest.fixture
def spy_trace_repo(monkeypatch):
    _SpyTraceRepo.made = []
    monkeypatch.setattr(operations, "PostgresInteractionTraceRepository", _SpyTraceRepo)
    return _SpyTraceRepo


@pytest.fixture(autouse=True)
def _clear_trace_context():
    set_trace_context(None)
    yield
    set_trace_context(None)


def _stub_search(monkeypatch, payload):
    monkeypatch.setattr(operations, "_search_knowledge", lambda *a, **k: payload)


def _run_search(session_factory, **kwargs):
    return operations.run_operation(
        "search_knowledge",
        {"query": "revenue", **kwargs.pop("params", {})},
        session_factory=session_factory,
        **kwargs,
    )


def _hit_payload():
    return {
        "query": "revenue",
        "mode": "grep",
        "hits": [
            {"source_id": "src-1", "path": "a.md", "line": 3, "snippet": "secret line"},
        ],
        "searched_sources": ["src-1"],
    }


def _miss_payload():
    return {"query": "revenue", "mode": "grep", "hits": [], "searched_sources": ["src-1"]}


def test_a_search_hit_is_traced_by_opaque_location_id(spy_trace_repo, session_factory, monkeypatch):
    _stub_search(monkeypatch, _hit_payload())
    set_trace_context(TraceContext(session_id="s", turn_id="t", correlation_id="c"))

    _run_search(session_factory)

    (repo,) = spy_trace_repo.made
    (call,) = repo.calls
    assert call["status"] == "hit"
    assert call["hit_ids"] == ["src-1:a.md:3"]
    assert call["tool_name"] == "search_knowledge"
    assert call["session_id"] == "s"
    assert call["correlation_id"] == "c"
    assert call["duration_ms"] >= 0
    assert call["source_staleness"] == {}
    assert call["miss"] is None
    assert call["answer_bundle_id"] is None
    # The matched snippet never crosses into the trace.
    assert "secret line" not in repr(call)


def test_a_search_with_no_hits_is_traced_as_a_miss(spy_trace_repo, session_factory, monkeypatch):
    _stub_search(monkeypatch, _miss_payload())
    set_trace_context(TraceContext(session_id="s", correlation_id="c"))

    _run_search(session_factory)

    (call,) = spy_trace_repo.made[0].calls
    assert call["status"] == "miss"
    assert call["hit_ids"] == []
    assert call["miss"] == {
        "operation": "search_knowledge",
        "searched_sources": ["src-1"],
    }


def test_a_search_argument_carries_intent_and_mints_per_call_ids(
    spy_trace_repo, session_factory, monkeypatch
):
    _stub_search(monkeypatch, _miss_payload())
    set_trace_context(TraceContext(session_id="s", correlation_id="c"))

    _run_search(session_factory, params={"intent": "answer the revenue question"})

    (call,) = spy_trace_repo.made[0].calls
    assert call["session_id"] == "s"
    assert call["correlation_id"] == "c"
    assert call["intent"] == "answer the revenue question"
    assert call["turn_id"]
    assert call["tool_call_id"]


def test_a_broken_trace_store_reports_degraded_not_silent(session_factory, monkeypatch, caplog):
    class _Exploding:
        def __init__(self, _sf):
            pass

        def record(self, **_kwargs):
            raise RuntimeError("the trace store fell over")

    monkeypatch.setattr(operations, "PostgresInteractionTraceRepository", _Exploding)
    _stub_search(monkeypatch, _hit_payload())
    set_trace_context(TraceContext(session_id="s", correlation_id="c-degraded"))

    with caplog.at_level(logging.WARNING, logger="hyperset.transport.operations"):
        result = _run_search(session_factory)

    # The served answer is unaffected -- the trace never gates it.
    assert result["hits"], "the search answer must still be served"
    # ...but the failure is EXPLICITLY reported as degraded, not swallowed. A mutation
    # that silently drops the write (no warning) reds this.
    degraded = [r for r in caplog.records if "interaction trace degraded" in r.getMessage()]
    assert degraded, "a failed trace write must be reported as degraded"
    assert "c-degraded" in degraded[0].getMessage()


def test_a_denied_call_traces_denied_and_never_reads_content(
    spy_trace_repo, session_factory, monkeypatch
):
    def _must_not_run(*_a, **_k):
        raise AssertionError("the search body ran on a denied call -- content was read")

    monkeypatch.setattr(operations, "_search_knowledge", _must_not_run)

    from hyperset.security import authz

    monkeypatch.setitem(authz.ROLES, "denier", Role(name="denier", grants=()))
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    principal = Principal(subject="u1", issuer="https://issuer.example", roles=("denier",))
    set_trace_context(TraceContext(session_id="s", correlation_id="c-deny"))

    with pytest.raises(OperationError):
        _run_search(session_factory, principal=principal)

    (call,) = spy_trace_repo.made[0].calls
    assert call["status"] == "denied"
    assert call["hit_ids"] == []
    assert call["correlation_id"] == "c-deny"
    # Server-derived identity, not a caller field.
    assert call["principal_identity"] == "u1@https://issuer.example"


def test_query_and_intent_are_redacted_unconditionally(
    spy_trace_repo, session_factory, monkeypatch
):
    # No HYPERSET_PII_GUARD set: the durable trace must STILL strip caller credentials
    # (dual-block fix 2). A URL-userinfo secret in the query and the intent is gone.
    monkeypatch.delenv("HYPERSET_PII_GUARD", raising=False)
    monkeypatch.setattr(
        operations,
        "_search_knowledge",
        lambda *a, **k: {"query": "q", "mode": "grep", "hits": [], "searched_sources": []},
    )
    set_trace_context(TraceContext(correlation_id="c", intent="see https://user:s3cret@evil/x"))

    operations.run_operation(
        "search_knowledge",
        {"query": "clone https://bob:tok3n@git.example/repo"},
        session_factory=session_factory,
    )

    (call,) = spy_trace_repo.made[0].calls
    assert "tok3n" not in call["query"]
    assert "s3cret" not in call["intent"]
    # The redaction stripped only the userinfo, not the whole field.
    assert "git.example/repo" in call["query"]


def test_search_filters_persist_only_narrow_redacted_path_fields(
    spy_trace_repo, session_factory, monkeypatch
):
    # A caller dict with an arbitrary secret-bearing key must land NOTHING of it; only the
    # validated path/path_prefix survive, redacted (dual-block fix 1).
    _stub_search(monkeypatch, _miss_payload())
    set_trace_context(TraceContext(correlation_id="c"))

    operations.run_operation(
        "search_knowledge",
        {
            "query": "revenue",
            "filters": {
                "path": "docs/",
                "path_prefix": "https://u:secret@host/team",
                "token": "https://u:secret@host",
            },
        },
        session_factory=session_factory,
    )

    (call,) = spy_trace_repo.made[0].calls
    assert set(call["filters"]) <= {"path", "path_prefix"}
    assert "token" not in call["filters"]
    assert "secret" not in repr(call["filters"])
    assert call["filters"]["path"] == "docs/"


def test_a_correlation_id_is_minted_when_the_caller_supplies_none(
    spy_trace_repo, session_factory, monkeypatch
):
    _stub_search(monkeypatch, _miss_payload())
    # No trace context at all: the autouse fixture cleared it.

    _run_search(session_factory)

    (call,) = spy_trace_repo.made[0].calls
    assert call["correlation_id"], "a traced call must always carry a correlation id"
    assert call["session_id"] is None


def test_a_resolve_hit_is_traced(spy_trace_repo, session_factory, monkeypatch):
    from hyperset.bundle import schema

    bundle = governed_bundle(
        resolution={"status": "governed", "summary": "revenue", "warnings": []}
    )
    monkeypatch.setattr(operations, "resolve_analytics_context", lambda **_k: bundle)
    set_trace_context(TraceContext(session_id="s", correlation_id="c"))

    operations.run_operation(
        "resolve_analytics_context",
        {"query": QUESTION, "directive": DIRECTIVE},
        session_factory=session_factory,
    )

    trace_calls = [c for repo in spy_trace_repo.made for c in repo.calls]
    (call,) = trace_calls
    assert call["status"] == "hit"
    assert call["hit_ids"] == [bundle.bundle_id]
    assert call["answer_bundle_id"] == bundle.bundle_id
    assert call["duration_ms"] >= 0
    assert schema.NO_MATCH not in call["hit_ids"]


def test_a_resolve_writes_the_full_linkage_and_principal_when_supplied(
    spy_trace_repo, session_factory, monkeypatch
):
    # hy-z7bsw: the live Luna rows landed with blank session/turn/tool_call/intent and an
    # anonymous principal because the CALLER supplied no linkage -- the backend write itself
    # carries every field the context holds. With a full TraceContext AND a verified principal,
    # the durable row is NON-NULL on all five: session_id, turn_id, tool_call_id, intent
    # (server-redacted) and the server-derived principal_identity.
    bundle = governed_bundle(
        resolution={"status": "governed", "summary": "revenue", "warnings": []}
    )
    monkeypatch.setattr(operations, "resolve_analytics_context", lambda **_k: bundle)
    set_trace_context(
        TraceContext(
            session_id="sess-1",
            turn_id="turn-1",
            tool_call_id="call-1",
            correlation_id="corr-1",
            intent="answer the revenue question",
        )
    )
    principal = Principal(subject="analyst", issuer="https://issuer.example", roles=("reader",))

    operations.run_operation(
        "resolve_analytics_context",
        {"query": QUESTION, "directive": DIRECTIVE},
        session_factory=session_factory,
        principal=principal,
    )

    (call,) = [c for repo in spy_trace_repo.made for c in repo.calls]
    assert call["session_id"] == "sess-1"
    assert call["turn_id"] == "turn-1"
    assert call["tool_call_id"] == "call-1"
    assert call["intent"] == "answer the revenue question"
    assert call["principal_identity"] == "analyst@https://issuer.example"
    # None of the linkage fields the bug reported blank are null.
    assert all(
        call[field] is not None for field in ("session_id", "turn_id", "tool_call_id", "intent")
    )


def test_a_resolve_no_match_is_traced_as_a_miss(spy_trace_repo, session_factory, monkeypatch):
    from hyperset.bundle import schema

    bundle = governed_bundle(
        resolution={"status": schema.NO_MATCH, "summary": "nothing", "warnings": []}
    )
    monkeypatch.setattr(operations, "resolve_analytics_context", lambda **_k: bundle)
    set_trace_context(TraceContext(session_id="s", correlation_id="c"))

    operations.run_operation(
        "resolve_analytics_context",
        {"query": QUESTION, "directive": DIRECTIVE},
        session_factory=session_factory,
    )

    trace_calls = [c for repo in spy_trace_repo.made for c in repo.calls]
    (call,) = trace_calls
    assert call["status"] == "miss"
    assert call["hit_ids"] == []
    assert call["answer_bundle_id"] == bundle.bundle_id
    assert call["miss"] == {
        "operation": "resolve_analytics_context",
        "domains": ["revenue"],
        "concepts": ["recognized_revenue"],
    }


def test_search_trace_carries_only_served_source_staleness(
    spy_trace_repo, session_factory, monkeypatch
):
    payload = _hit_payload()
    payload["hits"][0]["staleness"] = {
        "last_attempt_status": "failed",
        "synced_at": "2026-08-28T00:00:00+00:00",
        "stale": True,
        "unrelated": "not persisted",
    }
    _stub_search(monkeypatch, payload)

    _run_search(session_factory)

    (call,) = spy_trace_repo.made[0].calls
    assert call["source_staleness"] == {
        "src-1": {
            "last_attempt_status": "failed",
            "synced_at": "2026-08-28T00:00:00+00:00",
            "stale": True,
        }
    }


def test_credential_bearing_linkage_headers_are_dropped_not_persisted():
    # A crafted credential URL in a linkage header is not a well-formed opaque token, so it
    # is DROPPED before it can reach the durable row (dual-block fix 3). A clean token is kept.
    from hyperset.observability.interaction import opaque_token, trace_context_from_headers

    headers = {
        "mcp-session-id": "https://user:secret@host/evil",
        "x-hyperset-turn-id": "turn-42",
        "x-correlation-id": "id with spaces and /slash",
        "x-hyperset-intent": "see https://u:tok@evil",
    }
    context = trace_context_from_headers(lambda name: headers.get(name))
    assert context.session_id is None  # credential URL rejected
    assert context.turn_id == "turn-42"  # clean token kept
    assert context.correlation_id is None  # spaces/slash rejected
    # intent is free text, carried through here (it is redacted at the persist boundary).
    assert context.intent == "see https://u:tok@evil"
    # The validator itself: clean tokens pass, credential/whitespace ones are dropped.
    assert opaque_token("sess-1.a_b~c:d") == "sess-1.a_b~c:d"
    assert opaque_token("https://u:secret@host") is None


def test_the_degraded_log_leaks_no_caller_secret(session_factory, monkeypatch, caplog):
    # The degraded WARNING must carry ONLY the opaque correlation id -- never the caller's
    # secret-bearing query/intent/filters/headers (dual-block fixes 3+4, log-egress).
    class _Exploding:
        def __init__(self, _sf):
            pass

        def record(self, **_kwargs):
            raise RuntimeError("store down")

    from hyperset.observability.interaction import trace_context_from_headers

    monkeypatch.setattr(operations, "PostgresInteractionTraceRepository", _Exploding)
    monkeypatch.setattr(
        operations,
        "_search_knowledge",
        lambda *a, **k: {"query": "q", "mode": "grep", "hits": [], "searched_sources": []},
    )
    # Drive the correlation via a CREDENTIAL-bearing header: validation must drop it (so the
    # degraded log falls back to a minted, opaque id), never log the raw secret. A mutation
    # that skips token validation would let the header's secret reach the log -> reds.
    headers = {
        "x-correlation-id": "https://u:c_secret@evil",
        "x-hyperset-intent": "https://u:i_secret@h",
    }
    set_trace_context(trace_context_from_headers(lambda name: headers.get(name)))

    with caplog.at_level(logging.WARNING, logger="hyperset.transport.operations"):
        operations.run_operation(
            "search_knowledge",
            {
                "query": "https://bob:q_secret@git/x",
                "filters": {"token": "https://u:f_secret@h"},
            },
            session_factory=session_factory,
        )

    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert blob, "a failed durable write must be reported as degraded"
    for secret in ("q_secret", "i_secret", "f_secret", "c_secret"):
        assert secret not in blob, f"degraded log leaked {secret}"
