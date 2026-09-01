"""FAIL-CLOSED content-before-deny on the EXPANSION path, proven against a real postgres
repository (hy-l93sc round 1, #511 bounce).

The unit tests drive expand over a fake repository; they cannot see the DATABASE fetch. This
proves the deeper property the reviewers found: when a caller is DENIED a domain, that domain's
`context_snapshots.files` content is NEVER materialized before the authorize decision. The
expansion now classifies over METADATA (`list_source_candidates`, no `files`) and loads a
snapshot's content BY ID (`get_snapshot`) ONLY for a domain the caller is authorized for.

MUTATION-RED: revert the exact-node walk to `list_sources` (which `session.get`s every
source's current snapshot, `files` and all, before authorize) and the SQL-capture assertion
below reds -- the denied domain's snapshot id appears bound in a files-selecting statement.
"""

from __future__ import annotations

import json
import re

from sqlalchemy import event

from hyperset.bundle.expansion import expand_analytics_context
from hyperset.repositories.postgres import PostgresContextRepository

_FILES_SELECT = re.compile(r"\.files\b", re.IGNORECASE)


def _make_domain(repo, *, repository, domain, files, concept=None, parent=None):
    source = repo.register_source(repository=repository, ref="main", path="context")
    normalized = {
        "parent": parent,
        "documents": {"context_doc": {"path": "context.md"}},
        "approved_sources": [],
        "definitions": [{"term": concept, "statement": "s"}] if concept else [],
    }
    record, _created = repo.record_snapshot(
        source_id=source.id,
        commit_sha=f"commit-{domain}",
        committed_at=None,
        domain=domain,
        title=domain.title(),
        files=files,
        normalized=normalized,
    )
    return source.id, record.id


def _param_values(parameters) -> list[str]:
    if parameters is None:
        return []
    if isinstance(parameters, dict):
        items = parameters.values()
    elif isinstance(parameters, (list, tuple)):
        items = []
        for entry in parameters:
            if isinstance(entry, dict):
                items.extend(entry.values())
            elif isinstance(entry, (list, tuple)):
                items.extend(entry)
            else:
                items.append(entry)
    else:
        items = [parameters]
    return [str(value) for value in items]


def test_a_denied_domain_content_is_never_fetched_before_authorize(session_factory, db_engine):
    repo = PostgresContextRepository(session_factory)
    _allowed_src, allowed_snap = _make_domain(
        repo,
        repository="git@example/revenue",
        domain="revenue",
        files={"a.md": "ALLOWED_MARKER recognized revenue"},
        concept="recognized_revenue",
    )
    _denied_src, denied_snap = _make_domain(
        repo,
        repository="git@example/secret",
        domain="secret",
        files={"b.md": "DENIED_MARKER secret content"},
    )

    captured: list[tuple[str, object]] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        captured.append((statement, parameters))

    event.listen(db_engine, "before_cursor_execute", _capture)
    try:
        served = expand_analytics_context(
            query="revenue navigation",
            domain="revenue",
            concepts=["recognized_revenue"],
            session_factory=session_factory,
            workspace="default",
            authorize_domain=lambda d: d != "secret",
        ).to_dict()
    finally:
        event.remove(db_engine, "before_cursor_execute", _capture)

    # The walk started and the denied domain leaked nowhere in the served payload.
    assert served["start"] == "revenue"
    assert {d["domain"] for d in served["domains"] if d["available"]} == {"revenue"}
    blob = json.dumps(served)
    assert "DENIED_MARKER" not in blob
    assert "secret" not in blob  # not even its existence is disclosed on the exact-node path

    # The load-bearing DB invariant: every statement that SELECTs the `files` content column is
    # the by-id load of the AUTHORIZED domain's snapshot -- the denied snapshot id is bound in
    # NONE of them. The metadata listing selects no `files`, so it never appears here. Reverting
    # to `list_sources` (which `session.get`s the denied snapshot, files and all) reds this.
    files_selects = [(s, p) for s, p in captured if _FILES_SELECT.search(s)]
    assert files_selects, "the allowed domain's content must be fetched by id at least once"
    for statement, parameters in files_selects:
        values = _param_values(parameters)
        assert denied_snap not in values, f"denied content fetched: {statement}"
    assert any(allowed_snap in _param_values(p) for _s, p in files_selects), (
        "the allowed snapshot's content was never fetched by id"
    )
