"""Lexical and semantic search over configured sources, ACL-fail-closed.

Slice 1 of the ACL-aware grep MVP (epic hy-01442). READ-ONLY and NON-AUTHORITATIVE:
it searches only the sources a deployment has CONFIGURED, THROUGH an adapter, never an
arbitrary shell grep of the server filesystem. `mode="grep"` remains the default;
`mode="semantic"` ranks authorized lines in the deployment's configured embedding space.

Governance is REUSED, not forked: for each candidate source the caller must be
authorized to READ it (`security.authz.authorize` over the caller's roles, the SAME
pure fail-closed decision the served-operation gate uses). A source the caller is
denied contributes ZERO hits -- it is dropped before its content is ever read, so a
denial leaks nothing, not even a count. Absent an enabled authz gate the filter is a
no-op, so behaviour is byte-identical to today until a deployment turns authz on.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from hyperset.candidates.service import configured_embedding_provider
from hyperset.embedding.provider import EmbeddingProvider, cosine
from hyperset.repositories.postgres import PostgresContextRepository
from hyperset.security.authz import (
    READ,
    Principal,
    Resource,
    authorize,
    authz_enabled,
    roles_for,
)
from hyperset.security.redaction import redact_free_text_userinfo, redact_pointer

GREP_MODE = "grep"
SEMANTIC_MODE = "semantic"
SEARCH_MODES = (GREP_MODE, SEMANTIC_MODE)

# A source with no governed domain still needs a concrete domain on its authz Resource,
# and it must be one a domain-scoped grant can NEVER cover (fail closed) -- the same NUL
# sentinel discipline the operation gate uses for an unresolvable domain. A domain-scoped
# deployment policy denies such a source; only an all-domain (reader) scope covers it.
_UNSCOPED_DOMAIN = "\x00unscoped"

# Operational, best-effort, never gates the answer. Emitted at INFO; a deployment that
# wants the hit/miss trail turns the logger up. Carries NO snippet or query text beyond
# the counts and the per-source ACL outcome, so the log itself discloses no content.
_log = logging.getLogger("hyperset.knowledge.search")


@dataclass(frozen=True)
class SourceDocument:
    """One grep-able file within a source: a path and its raw text."""

    path: str
    text: str


class SourceAdapter(Protocol):
    """The seam between a configured source and the grep. An adapter carries the
    source's IDENTITY and version metadata and yields its documents; the search core
    knows nothing about Git, Postgres, or any specific store. Slice 1 ships the Git
    context-source adapter; other configured sources implement the same Protocol."""

    source_id: str
    repository: str
    domain: str | None
    commit: str | None
    version: str | None
    staleness: dict

    def documents(self) -> Iterable[SourceDocument]: ...


@dataclass(frozen=True)
class KnowledgeHit:
    """One search match, carrying everything the epic requires per hit: which source
    (id + repository), where (path + line), at what version (commit + content version),
    the ACL decision that admitted it, the source's staleness, and the match type."""

    source_id: str
    repository: str
    domain: str | None
    path: str
    line: int
    commit: str | None
    version: str | None
    acl_decision: str
    staleness: dict
    match_type: str
    snippet: str
    signal: dict | None = None

    def to_dict(self) -> dict:
        payload = {
            "source_id": self.source_id,
            "repository": self.repository,
            "domain": self.domain,
            "path": self.path,
            "line": self.line,
            "commit": self.commit,
            "version": self.version,
            "acl_decision": self.acl_decision,
            "staleness": self.staleness,
            "match_type": self.match_type,
            "snippet": self.snippet,
        }
        # Semantic is opt-in. Keep this key absent from grep so its served hit shape is
        # byte-for-byte unchanged (hy-0unvk).
        if self.signal is not None:
            payload["signal"] = self.signal
        return payload


@dataclass(frozen=True)
class SearchKnowledgeResult:
    """The search answer: the ranked hits plus the caller's OWN searched set.

    `denied_sources` is retained as an INTERNAL field for the fail-closed isolation test,
    but is DELIBERATELY NOT SERVED (hy-r0szz): the served `to_dict` must be UNIFORM and
    NON-DISCLOSING, so a source the caller was denied is indistinguishable from one that is
    absent or disabled -- a denial leaks nothing, not even a count. Naming denied sources
    (or their count) in the response would let a caller probe which source ids exist,
    contradicting the authz non-disclosure contract."""

    query: str
    mode: str
    hits: tuple[KnowledgeHit, ...]
    searched_sources: tuple[str, ...] = ()
    denied_sources: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        # No `denied_sources` and no denial count: denied == absent == disabled must be
        # indistinguishable on the served surface (hy-r0szz). `searched_sources` names only
        # sources the caller IS authorized to read, so it discloses nothing about a denial.
        return {
            "query": self.query,
            "mode": self.mode,
            "hits": [hit.to_dict() for hit in self.hits],
            "searched_sources": list(self.searched_sources),
        }


@dataclass(frozen=True)
class GitContextSourceAdapter:
    """A configured Git context source, adapted for grep from its current snapshot.

    The grep-able content is the snapshot's `files` (relative path -> raw Git text at a
    commit); the identity and version metadata come off the source/snapshot records. A
    source with no current snapshot yields nothing -- there is no content to search.

    CONTENT IS FETCHED LAZILY, BY ID (hy-r0szz): the adapter is built from a metadata-only
    candidate (identity + version, NO file bytes) and holds a `_load_files` closure that
    reads the snapshot content FROM THE DATABASE only when `documents()` is called. The
    search core calls `documents()` strictly AFTER the per-source authorize, so a denied
    source's content is fetched by neither the listing nor the grep -- the deny decision is
    made before any byte of it leaves the database (authorize-before-documents)."""

    source_id: str
    repository: str
    domain: str | None
    commit: str | None
    version: str | None
    staleness: dict
    _load_files: Callable[[], dict[str, str]] | None = None

    def documents(self) -> Iterable[SourceDocument]:
        # Fetch the snapshot's file CONTENT here, not during the listing: this runs only
        # after the caller has been authorized for this source (hy-r0szz).
        files = self._load_files() if self._load_files is not None else {}
        for path, text in (files or {}).items():
            yield SourceDocument(path=path, text=text)

    @classmethod
    def from_candidate(
        cls, candidate, *, load_files: Callable[[], dict[str, str]]
    ) -> GitContextSourceAdapter:
        staleness = {
            "last_attempt_status": candidate.last_attempt_status,
            "last_attempt_at": _isoformat(candidate.last_attempt_at),
            "synced_at": _isoformat(candidate.synced_at),
            "committed_at": _isoformat(candidate.committed_at),
            # A source whose last sync FAILED keeps serving its last valid snapshot, so a
            # hit from it is stale-but-real; flag it rather than hide it.
            "stale": candidate.last_attempt_status == "failed",
        }
        # Identity + version metadata only; the file bytes stay in the database until an
        # authorized documents() call invokes load_files (hy-r0szz).
        return cls(
            source_id=str(candidate.id),
            repository=candidate.repository,
            domain=candidate.domain,
            commit=candidate.commit_sha,
            version=candidate.content_hash,
            staleness=staleness,
            _load_files=load_files,
        )


def _isoformat(value) -> str | None:
    return value.isoformat() if value is not None else None


def _path_allowed(path: str, filters: dict) -> bool:
    """Metadata/path filtering: a `path` substring, or a `path_prefix`. Both optional;
    absent means no narrowing. Lexical, like the search itself -- no glob engine."""
    prefix = filters.get("path_prefix")
    if prefix and not path.startswith(str(prefix)):
        return False
    contains = filters.get("path")
    if contains and str(contains) not in path:
        return False
    return True


def search_over_adapters(
    query: str,
    adapters: Sequence[SourceAdapter],
    *,
    principal: Principal | None,
    filters: dict | None = None,
    limit: int = 50,
    mode: str = GREP_MODE,
    provider: EmbeddingProvider | None = None,
) -> SearchKnowledgeResult:
    """The search core, over already-built adapters -- the DB-free seam tests drive.

    ACL FIRST, per source, before any content is read: a denied source is dropped and
    never searched or embedded, so it contributes zero hits AND its denial is decided
    without touching its bytes. With the authz gate off the filter is a no-op."""
    filters = filters or {}
    if mode not in SEARCH_MODES:
        raise ValueError(f"search_knowledge mode must be one of {SEARCH_MODES!r}, not {mode!r}")
    if mode == SEMANTIC_MODE and provider is None:
        raise ValueError("semantic search requires an embedding provider")

    needle = query.casefold()
    gate_on = authz_enabled()
    registry = roles_for(principal)

    searched: list[str] = []
    denied: list[str] = []
    hits: list[KnowledgeHit] = []
    semantic_plan: list[KnowledgeHit] = []

    for adapter in adapters:
        if gate_on:
            resource = Resource(
                domain=adapter.domain or _UNSCOPED_DOMAIN,
                source_ref=adapter.source_id,
                workspace=getattr(principal, "workspace", None),
            )
            if not authorize(principal, READ, resource, registry).allowed:
                denied.append(adapter.source_id)
                continue
        searched.append(adapter.source_id)
        for document in adapter.documents():
            if not _path_allowed(document.path, filters):
                continue
            for line_number, line in enumerate(document.text.splitlines(), start=1):
                snippet = redact_free_text_userinfo(line.strip())
                if mode == GREP_MODE and needle and needle in line.casefold():
                    hits.append(_knowledge_hit(adapter, document.path, line_number, snippet))
                elif mode == SEMANTIC_MODE and snippet:
                    # ACL has already admitted this source. Redaction happens before a line
                    # reaches a possibly hosted provider, not merely before it is served.
                    semantic_plan.append(
                        _knowledge_hit(
                            adapter,
                            document.path,
                            line_number,
                            snippet,
                            match_type=SEMANTIC_MODE,
                        )
                    )

    if semantic_plan:
        assert provider is not None  # established above; narrows the Protocol for typing
        # The query is intentionally sent to the configured embedding provider for
        # semantic search, but URL userinfo is never a meaningful search signal and is
        # a credential-bearing value. Strip it before a hosted provider sees the text;
        # the caller-facing result and local trace retain their existing contracts.
        query_vector = provider.embed_query(redact_free_text_userinfo(query) or "")
        document_vectors = provider.embed_documents([hit.snippet for hit in semantic_plan])
        space = provider.space.as_metadata()
        hits = [
            replace(hit, signal={"score": cosine(query_vector, vector)} | space)
            for hit, vector in zip(semantic_plan, document_vectors, strict=True)
        ]
        hits.sort(key=lambda hit: (-hit.signal["score"], hit.source_id, hit.path, hit.line))

    bounded = hits[: max(0, int(limit))]
    # Best-effort operational trail, never gates the answer (hy-r0szz): counts and the
    # per-source ACL outcome only, so the log discloses no matched content.
    _log.info(
        "search_knowledge %s: searched=%d denied=%d hits=%d returned=%d",
        mode,
        len(searched),
        len(denied),
        len(hits),
        len(bounded),
    )
    return SearchKnowledgeResult(
        query=query,
        mode=mode,
        hits=tuple(bounded),
        searched_sources=tuple(searched),
        denied_sources=tuple(denied),
    )


def _knowledge_hit(
    adapter: SourceAdapter,
    path: str,
    line: int,
    snippet: str,
    *,
    match_type: str = "lexical",
) -> KnowledgeHit:
    """Build the shared governed/ACL/staleness envelope for either search mode."""
    return KnowledgeHit(
        source_id=adapter.source_id,
        repository=redact_pointer(adapter.repository),
        domain=adapter.domain,
        path=path,
        line=line,
        commit=adapter.commit,
        version=adapter.version,
        acl_decision="allowed",
        staleness=adapter.staleness,
        match_type=match_type,
        snippet=snippet,
    )


def _configured_adapters(
    session_factory, *, workspace: str | None, wanted: Sequence[str] | None
) -> list[SourceAdapter]:
    """Build a Git adapter for every ENABLED, snapshotted configured source in the
    caller's workspace, optionally narrowed to the ids the caller named in `sources[]`.

    Only configured/governed sources, only through the repository -- there is no path from
    a caller's `sources[]` to an arbitrary server file. The listing is METADATA-ONLY
    (`list_source_candidates`, which never fetches `context_snapshots.files`); each adapter
    carries a `load_source_files` closure that fetches the snapshot content FROM THE DATABASE
    BY ID only when `documents()` is called, which the search core does strictly AFTER the
    per-source authorize. So a source the caller will be denied never has its file bytes read
    from the database -- authorize-before-content, not read-then-deny (hy-r0szz)."""
    repository = PostgresContextRepository(session_factory)
    candidates = repository.list_source_candidates(workspace=workspace)
    names = {str(item) for item in wanted} if wanted else None
    adapters: list[SourceAdapter] = []
    for candidate in candidates:
        if not candidate.enabled or candidate.current_snapshot_id is None:
            continue
        if (
            names is not None
            and str(candidate.id) not in names
            and candidate.repository not in names
        ):
            continue

        # Bind the content loader to THIS source id; it fetches files only when an
        # authorized documents() call invokes it (hy-r0szz). Default-arg binds the id per
        # iteration so every closure loads its own source, not the loop's last.
        def _load_files(source_id: str = str(candidate.id)) -> dict[str, str]:
            return repository.load_source_files(source_id, workspace=workspace)

        adapters.append(GitContextSourceAdapter.from_candidate(candidate, load_files=_load_files))
    return adapters


def search_knowledge(
    params: dict,
    *,
    session_factory,
    principal: Principal | None = None,
    workspace: str | None = None,
    provider: EmbeddingProvider | None = None,
) -> dict:
    """The served `search_knowledge` operation body. Validates the request, builds the
    configured-source adapters, and searches them ACL-fail-closed."""
    query = params.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("search_knowledge requires a non-empty 'query' string")
    mode = params.get("mode", GREP_MODE)
    if mode not in SEARCH_MODES:
        raise ValueError(f"search_knowledge mode must be one of {SEARCH_MODES!r}, not {mode!r}")
    sources = params.get("sources")
    if sources is not None and not isinstance(sources, list):
        raise ValueError("'sources' must be a list of configured source identifiers")
    filters = params.get("filters") or {}
    if not isinstance(filters, dict):
        raise ValueError("'filters' must be an object")
    limit = params.get("limit", 50)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("'limit' must be a positive integer")

    adapters = _configured_adapters(session_factory, workspace=workspace, wanted=sources)
    embedding_provider = (
        configured_embedding_provider() if mode == SEMANTIC_MODE and provider is None else provider
    )
    result = search_over_adapters(
        query,
        adapters,
        principal=principal,
        filters=filters,
        limit=limit,
        mode=mode,
        provider=embedding_provider,
    )
    return result.to_dict()
