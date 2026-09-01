"""Read the full catalog corpus from the pinned snapshots (hy-gh-206).

The reader `list_context_catalog` would be if it did not cap: the same enabled
sources with a current snapshot, the same declared concept terms -- but WHOLE,
not cut to `INNER_LIMIT`. That difference is the feature: discovery ranks the
full declared list so a concept past the catalog's positional cap is reachable
by relevance, and a reader that inherited the cap would rank only what the
preview already showed.

Read-only and snapshot-bound: every `DomainFacts` carries the `commit_sha` and
`context_snapshot_id` it came from, so a candidate ranked from it discloses the
exact index its ranking is reproducible against.
"""

from __future__ import annotations

from hyperset.bundle.resolver import concept_terms, git_instructions
from hyperset.candidates.catalog import DomainFacts
from hyperset.repositories.postgres import PostgresContextRepository


def read_catalog_corpus(*, session_factory, workspace: str | None = None) -> list[DomainFacts]:
    """The configured governed domains, each with its FULL concept list.

    Ordered by domain, the ordering the catalog already uses, so the corpus is
    deterministic before discovery ranks it. `workspace` scopes the corpus to
    one tenant (hq-t6nx); the served discover path passes the caller's
    workspace so a tenant's discovery ranks only its own governed domains.
    """
    sources = sorted(
        (
            source
            for source in PostgresContextRepository(session_factory).list_sources(
                workspace=workspace
            )
            if source.enabled and source.current_snapshot is not None
        ),
        key=lambda item: item.current_snapshot.domain,
    )
    corpus: list[DomainFacts] = []
    for source in sources:
        snapshot = source.current_snapshot
        instructions = git_instructions(snapshot.normalized)
        corpus.append(
            DomainFacts(
                domain=snapshot.domain,
                title=snapshot.title,
                # The whole declared list. `list_context_catalog` caps this to
                # INNER_LIMIT; discovery does not, which is the point.
                concepts=tuple(concept_terms(instructions)),
                commit_sha=snapshot.commit_sha,
                context_snapshot_id=snapshot.id,
            )
        )
    return corpus
