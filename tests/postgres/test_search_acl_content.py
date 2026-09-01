"""FAIL-CLOSED content-before-deny, proven against a REAL postgres repository (hy-r0szz).

The unit tests drive the grep core over fake adapters; they cannot see the DATABASE fetch.
This test proves the deeper property the reviewers found: when a caller is DENIED a source,
that source's `context_snapshots.files` content is NEVER materialized from the database
before the authorize decision. The ACL-aware listing is METADATA-ONLY
(`list_source_candidates`), and file content is loaded BY ID
(`load_source_files`) only for sources that pass authorize.

MUTATION-RED: make the candidate listing eagerly load `.files` (so the denied source's
content is fetched during the listing, before authorize) and the SQL-capture assertion below
reds -- a files-selecting statement appears that is not the by-id load of the ALLOWED source.
"""

from __future__ import annotations

import json
import re

import pytest
from sqlalchemy import event

from hyperset.embedding.deterministic import DeterministicEmbeddingProvider
from hyperset.knowledge.search import search_knowledge
from hyperset.repositories.postgres import PostgresContextRepository
from hyperset.security import authz
from hyperset.security.authz import Effect, Grant, Principal, Role, Scope

_FILES_SELECT = re.compile(r"\.files\b", re.IGNORECASE)


def _make_source(repo: PostgresContextRepository, *, repository, domain, files):
    source = repo.register_source(repository=repository, ref="main", path="context")
    repo.record_snapshot(
        source_id=source.id,
        commit_sha=f"commit-{domain}",
        committed_at=None,
        domain=domain,
        title=domain.title(),
        files=files,
        normalized={},
    )
    return source.id


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


@pytest.mark.parametrize("mode", ["grep", "semantic"])
def test_a_denied_source_content_is_never_fetched_before_authorize(
    session_factory, db_engine, monkeypatch, mode
):
    repo = PostgresContextRepository(session_factory)
    allowed_id = _make_source(
        repo,
        repository="git@example/allowed",
        domain="revenue",
        files={"a.md": "ALLOWED_MARKER recognized revenue by region"},
    )
    denied_id = _make_source(
        repo,
        repository="git@example/denied",
        domain="marketing",
        files={"b.md": "DENIED_MARKER revenue attribution model"},
    )

    scoped = Role(
        name="scoped_reader",
        grants=(
            Grant(Effect.ALLOW, authz.READ, Scope()),
            Grant(Effect.DENY, authz.READ, Scope(source_ref=denied_id)),
        ),
    )
    monkeypatch.setitem(authz.ROLES, "scoped_reader", scoped)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    principal = Principal(subject="u1", issuer="https://issuer.example", roles=("scoped_reader",))

    captured: list[tuple[str, object]] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        captured.append((statement, parameters))

    event.listen(db_engine, "before_cursor_execute", _capture)
    try:
        result = search_knowledge(
            {"query": "revenue", "mode": mode},
            session_factory=session_factory,
            principal=principal,
            workspace="default",
            provider=DeterministicEmbeddingProvider() if mode == "semantic" else None,
        )
    finally:
        event.remove(db_engine, "before_cursor_execute", _capture)

    # The ACL held on the real path: only the allowed source's hits come back.
    sources = {hit["source_id"] for hit in result["hits"]}
    assert allowed_id in sources
    assert denied_id not in sources
    # And the denied source's content never reached the served payload.
    blob = json.dumps(result)
    assert "ALLOWED_MARKER" in blob
    assert "DENIED_MARKER" not in blob

    # The load-bearing DB invariant: every statement that SELECTs the `files` content column
    # is the by-id content load for the AUTHORIZED source -- the denied source id is bound in
    # NONE of them, and each names the allowed id. The metadata-only listing selects no
    # `files` column, so it never appears here. Eager-loading `.files` in the listing (the
    # mutation) adds a files-select bound to no/other source id and reds this.
    files_selects = [(s, p) for s, p in captured if _FILES_SELECT.search(s)]
    assert files_selects, "the allowed source's content must be fetched by id at least once"
    for statement, parameters in files_selects:
        values = _param_values(parameters)
        assert denied_id not in values, f"denied content fetched: {statement}"
        assert allowed_id in values, f"a files select not scoped to the allowed id: {statement}"


def test_a_credential_in_a_source_line_is_redacted_in_the_served_snippet(session_factory):
    """hy-2xdfb: search_knowledge is a SERVED op, so a raw snippet reaches an MCP client too.
    A governed file line carrying a credential URL must have its userinfo stripped at the
    source -- the readable line stays, the secret does not. Proven on the real served path
    (search_knowledge over a real postgres source), the transport an MCP client uses."""
    repo = PostgresContextRepository(session_factory)
    _make_source(
        repo,
        repository="https://svc:ghp_REPOTOKEN@github.com/acme/ctx",
        domain="revenue",
        files={"setup.md": "pull the mirror https://svc:ghp_LINETOKEN@github.com/acme/ctx now"},
    )

    principal = Principal(subject="u1", issuer="https://issuer.example", roles=("reader",))
    result = search_knowledge(
        {"query": "mirror"},
        session_factory=session_factory,
        principal=principal,
        workspace="default",
    )

    (hit,) = result["hits"]
    assert "github.com/acme/ctx" in hit["snippet"]  # authorized content still surfaced
    assert "ghp_LINETOKEN" not in hit["snippet"] and "svc:" not in hit["snippet"]
    assert "ghp_REPOTOKEN" not in hit["repository"]
    blob = json.dumps(result)
    assert "ghp_LINETOKEN" not in blob and "ghp_REPOTOKEN" not in blob
