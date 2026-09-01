"""The lexical grep core + fail-closed per-source ACL (hy-r0szz), DB-free.

Drives `search_over_adapters` with fake source adapters, so the grep, the filtering, the
per-hit identity, and -- the load-bearing one -- the ACL isolation are exercised without a
database. The ACL isolation is MUTATION-RED: flipping the per-source authorize in
`search.py` (dropping the guard) makes the denied source's hits appear here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import pytest

from hyperset.embedding.deterministic import DeterministicEmbeddingProvider
from hyperset.knowledge.search import (
    GitContextSourceAdapter,
    SourceDocument,
    search_over_adapters,
)
from hyperset.security import authz
from hyperset.security.authz import Effect, Grant, Principal, Role, Scope

READ = authz.READ


@dataclass(frozen=True)
class FakeAdapter:
    """A source adapter with hand-built content and metadata -- the DB-free seam."""

    source_id: str
    repository: str
    domain: str | None
    commit: str | None
    version: str | None
    staleness: dict
    docs: tuple[SourceDocument, ...]

    def documents(self):
        return self.docs


def _adapter(
    source_id, *, domain, docs, repository="git@example/repo", commit="c0ffee", version="v1"
):
    return FakeAdapter(
        source_id=source_id,
        repository=repository,
        domain=domain,
        commit=commit,
        version=version,
        staleness={"last_attempt_status": "synced", "stale": False},
        docs=tuple(docs),
    )


def _reader(*roles):
    return Principal(subject="u1", issuer="https://issuer.example", roles=roles or ("reader",))


def test_cross_source_grep_returns_hits_from_more_than_one_source_with_identity():
    """The MVP's headline: grep spans configured sources, and each hit is attributable.

    A hit that could not name its source, path, line, and version would be useless for the
    resolve-then-answer flow the epic describes -- so every one of those is asserted, from
    both sources, not just that some hit came back.
    """
    a = _adapter(
        "src-a",
        domain="revenue",
        docs=[
            SourceDocument("docs/grain.md", "line one\nrecognized revenue by region\nline three")
        ],
        commit="aaa111",
        version="va",
    )
    b = _adapter(
        "src-b",
        domain="marketing",
        docs=[SourceDocument("catalog/notes.md", "churn\nrevenue attribution model")],
        commit="bbb222",
        version="vb",
    )

    result = search_over_adapters("revenue", [a, b], principal=_reader())

    by_source = {hit.source_id: hit for hit in result.hits}
    assert set(by_source) == {"src-a", "src-b"}, "a cross-source grep returns hits from BOTH"
    assert set(result.searched_sources) == {"src-a", "src-b"}
    assert result.denied_sources == ()
    # Per-hit identity + location + version, from source A.
    hit_a = by_source["src-a"]
    assert hit_a.repository == "git@example/repo"
    assert hit_a.path == "docs/grain.md"
    assert hit_a.line == 2, "the 1-based line of the match, not the file"
    assert hit_a.commit == "aaa111" and hit_a.version == "va"
    assert hit_a.match_type == "lexical" and hit_a.acl_decision == "allowed"
    assert "recognized revenue by region" in hit_a.snippet
    assert by_source["src-b"].commit == "bbb222"
    assert all("signal" not in hit for hit in result.to_dict()["hits"])


def test_semantic_search_ranks_lines_and_discloses_the_embedding_signal():
    provider = DeterministicEmbeddingProvider()
    adapter = _adapter(
        "src-a",
        domain="revenue",
        docs=[
            SourceDocument(
                "docs/meaning.md",
                "weekly support staffing\nrecognized revenue by region\nwarehouse retention policy",
            )
        ],
    )

    result = search_over_adapters(
        "recognized revenue",
        [adapter],
        principal=_reader(),
        mode="semantic",
        provider=provider,
    )

    assert result.mode == "semantic"
    assert result.hits[0].snippet == "recognized revenue by region"
    assert result.hits[0].match_type == "semantic"
    scores = [hit.signal["score"] for hit in result.hits]
    assert scores == sorted(scores, reverse=True)
    for hit in result.to_dict()["hits"]:
        assert set(hit["signal"]) == {
            "score",
            "space_id",
            "provider",
            "model",
            "dimensions",
            "input_projection_version",
        }
        assert hit["signal"]["provider"] == "deterministic"
        assert hit["signal"]["dimensions"] == provider.space.dimensions


def test_semantic_acl_denial_happens_before_content_read_or_embedding(monkeypatch):
    scoped = Role(
        name="semantic_reader",
        grants=(
            Grant(Effect.ALLOW, READ, Scope()),
            Grant(Effect.DENY, READ, Scope(source_ref="src-denied")),
        ),
    )
    monkeypatch.setitem(authz.ROLES, "semantic_reader", scoped)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")

    allowed = _adapter(
        "src-allowed",
        domain="revenue",
        docs=[SourceDocument("allowed.md", "recognized revenue")],
    )

    def _read_denied():
        raise AssertionError("denied content must never be fetched")

    denied = GitContextSourceAdapter(
        source_id="src-denied",
        repository="git@example/denied",
        domain="revenue",
        commit="bad",
        version="bad",
        staleness={"stale": False},
        _load_files=_read_denied,
    )

    result = search_over_adapters(
        "recognized revenue",
        [allowed, denied],
        principal=_reader("semantic_reader"),
        mode="semantic",
        provider=DeterministicEmbeddingProvider(),
    )

    assert {hit.source_id for hit in result.hits} == {"src-allowed"}
    assert result.denied_sources == ("src-denied",)
    assert "src-denied" not in json.dumps(result.to_dict())


def test_semantic_search_redacts_credentials_before_hosted_provider_input():
    class RecordingProvider:
        def __init__(self):
            self.delegate = DeterministicEmbeddingProvider()
            self.queries = []
            self.documents = []

        @property
        def space(self):
            return self.delegate.space

        def embed_query(self, text):
            self.queries.append(text)
            return self.delegate.embed_query(text)

        def embed_documents(self, texts):
            self.documents = list(texts)
            return self.delegate.embed_documents(texts)

    provider = RecordingProvider()
    adapter = _adapter(
        "src-a",
        domain="revenue",
        docs=[
            SourceDocument(
                "setup.md",
                "read https://alice:ghp_PROVIDERSECRET@example.com/revenue definitions",
            )
        ],
    )

    result = search_over_adapters(
        "revenue https://alice:ghp_QUERYSECRET@example.com/definitions",
        [adapter],
        principal=_reader(),
        mode="semantic",
        provider=provider,
    )

    assert provider.queries == ["revenue https://example.com/definitions"]
    assert provider.documents == ["read https://example.com/revenue definitions"]
    assert "ghp_PROVIDERSECRET" not in json.dumps(result.to_dict())


def test_a_reader_denied_a_source_sees_none_of_its_hits(monkeypatch):
    """FAIL-CLOSED per-source ACL, the mutation-red isolation test (hy-r0szz).

    A deployment scopes a reader to deny one source (an ALLOW-all with a source_ref DENY --
    the model already supports it, no shipped role uses it yet). That source must contribute
    ZERO hits, and its content is never even read. Dropping the per-source authorize in
    `search_over_adapters` makes src-b's hits appear here -> this reds. The reader is NOT
    denied src-a, so this is isolation, not a blanket deny: src-a's hits still come back.
    """
    scoped = Role(
        name="scoped_reader",
        grants=(
            Grant(Effect.ALLOW, READ, Scope()),
            Grant(Effect.DENY, READ, Scope(source_ref="src-b")),
        ),
    )
    monkeypatch.setitem(authz.ROLES, "scoped_reader", scoped)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")

    a = _adapter("src-a", domain="revenue", docs=[SourceDocument("a.md", "revenue here")])
    b = _adapter("src-b", domain="revenue", docs=[SourceDocument("b.md", "revenue there")])

    result = search_over_adapters("revenue", [a, b], principal=_reader("scoped_reader"))

    source_ids = {hit.source_id for hit in result.hits}
    assert "src-a" in source_ids, "the source the reader IS allowed still returns hits"
    assert "src-b" not in source_ids, "the DENIED source contributes ZERO hits"
    assert result.denied_sources == ("src-b",)
    assert result.searched_sources == ("src-a",)


def test_the_gate_off_searches_every_source(monkeypatch):
    """The control on the ACL test: with authz OFF the per-source filter is a no-op.

    Without this, the isolation above could pass because grep never reached src-b at all
    rather than because the ACL dropped it. Same scoped-deny role, gate OFF -> src-b's hits
    come back, proving the deny above is the ACL and not an accident of the fixture.
    """
    scoped = Role(
        name="scoped_reader",
        grants=(Grant(Effect.DENY, READ, Scope(source_ref="src-b")),),
    )
    monkeypatch.setitem(authz.ROLES, "scoped_reader", scoped)
    monkeypatch.delenv("HYPERSET_AUTHZ_ENABLED", raising=False)

    a = _adapter("src-a", domain="revenue", docs=[SourceDocument("a.md", "revenue")])
    b = _adapter("src-b", domain="revenue", docs=[SourceDocument("b.md", "revenue")])

    result = search_over_adapters("revenue", [a, b], principal=_reader("scoped_reader"))

    assert {hit.source_id for hit in result.hits} == {"src-a", "src-b"}
    assert result.denied_sources == ()


def test_a_path_filter_narrows_results():
    """Metadata/path filtering: a path_prefix keeps only the matching documents."""
    a = _adapter(
        "src-a",
        domain="revenue",
        docs=[
            SourceDocument("docs/grain.md", "revenue definition"),
            SourceDocument("src/model.py", "revenue = amount - tax"),
        ],
    )

    result = search_over_adapters(
        "revenue", [a], principal=_reader(), filters={"path_prefix": "docs/"}
    )

    paths = {hit.path for hit in result.hits}
    assert paths == {"docs/grain.md"}, "the src/ document is filtered out by the path_prefix"


def test_hit_miss_logging_is_emitted(caplog):
    """Operational hit/miss logging is emitted (best-effort, counts only, no content)."""
    a = _adapter("src-a", domain="revenue", docs=[SourceDocument("a.md", "revenue")])

    with caplog.at_level(logging.INFO, logger="hyperset.knowledge.search"):
        search_over_adapters("revenue", [a], principal=_reader())

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "search_knowledge grep" in message and "hits=1" in message for message in messages
    ), messages


def test_the_limit_bounds_the_returned_hits():
    a = _adapter(
        "src-a",
        domain="revenue",
        docs=[SourceDocument("a.md", "\n".join("revenue" for _ in range(10)))],
    )

    result = search_over_adapters("revenue", [a], principal=_reader(), limit=3)

    assert len(result.hits) == 3, "the limit bounds the returned hits"


def test_the_served_response_does_not_disclose_a_denied_source(monkeypatch):
    """NON-DISCLOSURE on the SERVED surface (hy-r0szz, blocker 1).

    The served response is `to_dict`. It must not name denied sources nor a denial count, so
    a caller cannot distinguish a configured-but-DENIED source from an absent/disabled one --
    that distinction would leak source existence. src-b is denied here; its id must appear
    NOWHERE in the served payload, and there must be no `denied_sources` key (nor any 'denied'
    key). Re-adding `denied_sources` to `to_dict` reds this (mutation on the disclosure).
    """
    scoped = Role(
        name="scoped_reader",
        grants=(
            Grant(Effect.ALLOW, READ, Scope()),
            Grant(Effect.DENY, READ, Scope(source_ref="src-b")),
        ),
    )
    monkeypatch.setitem(authz.ROLES, "scoped_reader", scoped)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")

    a = _adapter("src-a", domain="revenue", docs=[SourceDocument("a.md", "revenue")])
    b = _adapter("src-b", domain="revenue", docs=[SourceDocument("b.md", "revenue")])

    result = search_over_adapters("revenue", [a, b], principal=_reader("scoped_reader"))
    # The internal field still records the denial (the isolation test relies on it)...
    assert result.denied_sources == ("src-b",)
    # ...but the SERVED dict discloses neither the denied id nor a count nor any 'denied' key.
    served = result.to_dict()
    assert "denied_sources" not in served
    assert not any("denied" in key for key in served)
    blob = json.dumps(served)
    assert "src-b" not in blob, "the denied source id must not appear anywhere in the payload"
    assert "src-a" in blob, "the allowed source's hits are still served"


def test_from_candidate_fetches_content_only_when_documents_is_called():
    """AUTHORIZE-BEFORE-CONTENT ordering, adapter level (hy-r0szz, blocker 2).

    The adapter is built from a metadata-only candidate and a `load_files` closure; the
    closure (which, in production, hits the database) must be invoked ONLY inside
    `documents()`, never at build time. The search core calls `documents()` strictly after
    the per-source authorize, so a denied source's `load_files` is never called.

    This is the adapter-level property; the load-bearing proof that the LISTING itself
    fetches no content is the real postgres test
    `tests/postgres/test_search_acl_content.py::test_a_denied_source_content_is_never_fetched`.
    """

    class _Candidate:
        id = "src-a"
        repository = "git@example/repo"
        enabled = True
        current_snapshot_id = "snap-a"
        domain = "revenue"
        commit_sha = "c1"
        content_hash = "v1"
        last_attempt_status = "synced"
        last_attempt_at = None
        synced_at = None
        committed_at = None

    calls = {"n": 0}

    def _load_files():
        calls["n"] += 1
        return {"a.md": "revenue here"}

    adapter = GitContextSourceAdapter.from_candidate(_Candidate(), load_files=_load_files)

    # Build carries identity/version metadata but calls the content loader ZERO times.
    assert adapter.source_id == "src-a" and adapter.domain == "revenue"
    assert adapter.commit == "c1" and adapter.version == "v1"
    assert calls["n"] == 0, "adapter build must not fetch content"

    # Content is fetched only now, when documents() is called (post-authorize in the core).
    docs = list(adapter.documents())
    assert calls["n"] == 1
    assert [d.path for d in docs] == ["a.md"]


def test_search_knowledge_validates_the_request():
    """The op body refuses a malformed request with ValueError (translated to a 400 at the
    boundary), before it ever touches the source repository."""
    from hyperset.knowledge.search import search_knowledge

    with pytest.raises(ValueError, match="query"):
        search_knowledge({"query": "  "}, session_factory=None)
    with pytest.raises(ValueError, match="semantic"):
        search_knowledge({"query": "x", "mode": "hybrid"}, session_factory=None)
    with pytest.raises(ValueError, match="limit"):
        search_knowledge({"query": "x", "limit": 0}, session_factory=None)


def test_a_credential_bearing_source_line_is_redacted_in_the_served_snippet():
    """hy-2xdfb: the snippet is a raw source line and search_knowledge is a SERVED op, so a
    governed line carrying `https://user:token@host` would leak the token to EVERY consumer
    (an MCP client, not only the DOM #514 fixed). Redacted at the source: the userinfo is
    stripped and the readable line + host stay. The source repository pointer is redacted too.
    MUTATION-RED: drop the source-side redaction and the token appears in the served hit."""
    adapter = _adapter(
        "src-cred",
        domain="revenue",
        docs=[
            SourceDocument(
                "docs/setup.md",
                "clone the mirror at https://alice:ghp_LINESECRET@github.com/acme/ctx here",
            )
        ],
        repository="https://bob:ghp_REPOSECRET@github.com/acme/ctx",
    )

    result = search_over_adapters("mirror", [adapter], principal=_reader())

    (hit,) = result.to_dict()["hits"]
    # The line is still surfaced (authorized content), host intact, credential stripped.
    assert "github.com/acme/ctx" in hit["snippet"]
    assert "ghp_LINESECRET" not in hit["snippet"]
    assert "alice:" not in hit["snippet"]
    # And the source repository pointer is redacted in the same served hit.
    assert "ghp_REPOSECRET" not in hit["repository"]
    assert "bob:" not in hit["repository"]
    # Nothing anywhere in the served result carries either secret.
    import json

    blob = json.dumps(result.to_dict())
    assert "ghp_LINESECRET" not in blob and "ghp_REPOSECRET" not in blob
