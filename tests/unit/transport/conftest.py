"""One governed bundle and one catalog, without a database (hy-oih, hy-x7f).

The transports' own rules -- routing, decoding, protocol framing -- are
independent of where the bundle came from, so these tests stub the resolver
and let `tests/postgres/test_transport_parity.py` prove that HTTP and MCP
serve the real one identically.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime

import pytest

from hyperset.bundle import ContextBundle, ContextCatalog
from hyperset.transport import operations

PRIMARY = "superset:dataset:finance_orders_daily"
QUESTION = "Which source and rules should an analyst use for recognized revenue by region?"
# What a planner sends after reading the catalog: exact names, nothing
# inferred, and the coverage claim the named domain must declare (hy-9lct).
DIRECTIVE = {"domains": ["revenue"], "concepts": ["recognized_revenue"]}

INSTRUCTIONS = {
    "definitions": [],
    "approved_sources": [{"ref": PRIMARY, "role": "primary", "reason": "Completed orders."}],
    "prohibited_sources": [],
    "fields": [
        {"name": "recognized_revenue", "source_ref": PRIMARY, "expression": "SUM(net_amount)"}
    ],
    "joins": [],
    "filters": [],
    "grain": "order_date",
    "caveats": [],
    "validations": [],
}


def governed_bundle(**overrides) -> ContextBundle:
    payload = {
        "request": {
            "query": QUESTION,
            "directive": {**DIRECTIVE, "asset_refs": []},
        },
        "resolution": {"status": "governed", "summary": "Revenue guidance.", "warnings": []},
        "context_authority": {
            "type": "git",
            "commit_sha": "abc123",
            "context_snapshot_id": "ctxsnap-1",
        },
        "instructions": INSTRUCTIONS,
        "linked_evidence": {"observed_assets": [], "findings": [], "conflicts": []},
        "domain_graph": {"nodes": [], "edges": []},
        "provenance_refs": ["git_context:ctxsnap-1@abc123"],
        "resolved_at": datetime(2026, 7, 28, tzinfo=UTC),
    }
    payload.update(overrides)
    return ContextBundle(**payload)


@pytest.fixture
def resolved(monkeypatch) -> list[dict]:
    """Stub the resolver; records the arguments each transport passed it."""
    calls: list[dict] = []

    def _resolve(*, query, directive, session_factory, workspace=None):
        calls.append(
            {
                "query": query,
                "session_factory": session_factory,
                "directive": directive,
                "workspace": workspace,
            }
        )
        return governed_bundle()

    monkeypatch.setattr(operations, "resolve_analytics_context", _resolve)
    return calls


def catalog() -> ContextCatalog:
    return ContextCatalog(
        domains=[{"domain": "revenue", "concepts": ["recognized revenue"]}],
        observed=[{"connector": "superset", "asset_type": "dataset", "live_count": 2}],
        page={
            "limit": 50,
            "offset": 0,
            "domain_count": 1,
            "next_offset": None,
            "truncated": [],
            "recovery": None,
        },
        generated_at=datetime(2026, 7, 28, tzinfo=UTC),
    )


@pytest.fixture
def listed(monkeypatch) -> list[dict]:
    """Stub the catalog service; records what each transport passed it."""
    calls: list[dict] = []

    def _list(*, session_factory, limit, offset, workspace=None):
        calls.append(
            {
                "session_factory": session_factory,
                "limit": limit,
                "offset": offset,
                "workspace": workspace,
            }
        )
        return catalog()

    monkeypatch.setattr(operations, "list_context_catalog", _list)
    return calls


class _StubSession:
    """A do-nothing session/transaction. The transport unit tests fake the repositories,
    so the real ORM is never touched; a handler that opens a transaction to couple a
    mutation to its audit append (hy-gh-75 round 2) only needs a context manager that
    supports `session.begin()`. Exceptions raised inside the `with` still propagate, so a
    faked audit repo that raises makes the handler reject exactly as against a real DB."""

    def __enter__(self) -> _StubSession:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def begin(self) -> contextlib.AbstractContextManager:
        return contextlib.nullcontext()


@pytest.fixture
def session_factory():
    """A stand-in sessionmaker: called, it yields a stub session context manager. The
    transports mostly pass it through untouched; the admin write paths call it to open a
    transaction, so it must be callable and produce a `with`-able session."""

    def _factory() -> _StubSession:
        return _StubSession()

    return _factory


class ResolverExploded(RuntimeError):
    """Stands in for the failures nobody wrote a branch for: Postgres gone,
    a driver timeout, a bug in the resolver."""


@pytest.fixture
def broken_resolver(monkeypatch) -> None:
    def _explode(**_kwargs):
        raise ResolverExploded("connection to server at 'postgres' failed: no route to host")

    monkeypatch.setattr(operations, "resolve_analytics_context", _explode)
