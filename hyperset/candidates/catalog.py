"""Rank catalog domains and concepts by relevance to a question (hy-gh-206).

Pure: values in, candidates out, the shape `bundle.discovery.candidate_sources`
keeps so the ranking is tested at full width without a database. The wiring
that reads the full corpus from the pinned snapshots, the served operation, and
the byte-identical-resolve guard are the next slice; this one is the ranking
and the candidate, and it consumes the merged `EmbeddingProvider` boundary.

WHY A FULL CORPUS AND NOT THE CATALOG PREVIEW. `list_context_catalog` caps each
domain's concept list at `INNER_LIMIT` positionally, and a concept past that
cap is invisible to a planner reading the preview -- the failure this feature
exists to remove. So discovery ranks the FULL, uncapped concept list a domain
declares; relevance, not position, decides what a planner sees. A relevant
concept is reachable wherever it sits in the declared order.

WHAT A CANDIDATE MAY CARRY. A Git-declared domain name, a Git-declared concept
term, and the signal that ranked it. Not an observed-asset ref: assist output
has no slot for the declared ref that would make a ranking into a link (ADR
0019). Not a resolution or a status: ranking a domain highly says a planner
should look there, never that an answer is governed. Not a governed label: the
provenance is `assist_ranking`, always.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from hyperset.bundle.schema import canonical_json
from hyperset.embedding.provider import EmbeddingProvider, IndexVersion, cosine
from hyperset.security.redaction import redact_free_text_userinfo

# How many candidates a discovery returns, most-relevant first. Bounded for the
# reason the catalog is bounded -- a discovery whose size is a property of the
# corpus is not a discovery surface -- but ordered by relevance rather than
# position, which is the whole difference from the catalog it sits ahead of.
CANDIDATE_LIMIT = 20

# The one provenance an assist candidate carries. Never `governed`, `approved`,
# `canonical`, or `trusted` (ADR 0019 floor 1): a candidate is evidence about
# where to look, and no ranking makes a business definition approved.
ASSIST_RANKING = "assist_ranking"

DOMAIN = "domain"
CONCEPT = "concept"


@dataclass(frozen=True)
class DomainFacts:
    """One domain's full, uncapped facts for ranking.

    `concepts` is the whole declared list, not the catalog's capped preview:
    reachability past the cap is the point. `commit_sha` and
    `context_snapshot_id` pin the snapshot these facts came from so each
    candidate can disclose the exact index its ranking is reproducible against.
    """

    domain: str
    title: str
    concepts: tuple[str, ...]
    commit_sha: str
    context_snapshot_id: str


@dataclass(frozen=True)
class Candidate:
    """A ranked domain or concept, and the signal that ranked it.

    Frozen and shaped so it CANNOT hold what assist may not produce: there is
    no field for an observed-asset ref, a resolution status, or a governed
    label. `domain` and `term` are Git-declared identifiers a planner copies
    into a `ContextDirective`, which the exact resolver then verifies -- a
    proposal becomes a selection only by the caller sending the exact name
    back (ADR 0019 floor 7).
    """

    kind: str
    domain: str
    term: str | None
    signal: dict
    provenance: str = ASSIST_RANKING

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "domain": self.domain,
            "term": self.term,
            "provenance": self.provenance,
            "signal": self.signal,
        }


def _source_text_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(canonical_json({"text": text}).encode()).hexdigest()[:16]


def _signal(*, score: float, matched_on: str, index: IndexVersion) -> dict:
    """The disclosed ranking evidence for one candidate.

    ADR 0022: every candidate discloses the signal that ranked it and the index
    version and embedding model that produced the ranking, so a run is
    reproducible. `score` is the cosine, `matched_on` is which text was
    embedded, and the index metadata is the space plus the snapshot plus the
    source-text hash the vector is reproducible from.
    """
    return {"score": score, "matched_on": matched_on} | index.as_metadata()


def _document(facts: DomainFacts) -> str:
    """The text a domain candidate is embedded from: its title and its concepts.

    Concepts join the title so a domain whose TERMS match the question ranks up
    even when its title does not -- reaching the right governed slice is the
    goal, and a title alone is a thin signal.
    """
    return " ".join((facts.title, *facts.concepts)).strip()


def discover_candidates(
    *,
    question: str,
    corpus: Sequence[DomainFacts],
    provider: EmbeddingProvider,
    limit: int = CANDIDATE_LIMIT,
) -> list[Candidate]:
    """Relevance-ranked domain and concept candidates over the catalog corpus.

    One query embedding, one batched document embedding, cosine for the score.
    The order is by descending score with a deterministic tie-break, so the
    same corpus, question, and provider always produce the same list -- a
    property the deterministic double makes testable and a real model does not
    promise.
    """
    plan: list[tuple[str, str, str | None, str]] = []
    for facts in corpus:
        plan.append((DOMAIN, facts.domain, None, _document(facts)))
        for term in facts.concepts:
            plan.append((CONCEPT, facts.domain, term, term))

    # Nothing to rank is not a call to the provider: an empty catalog returns no
    # candidates without embedding the question, so a deployment with no
    # configured context serves discover without an embedding backend at all.
    if not plan:
        return []

    by_domain = {facts.domain: facts for facts in corpus}
    # Discovery may use a hosted provider, so a credential-bearing URL in the question
    # must not leave the process as part of the embedding input. This is a lexical
    # redaction only; the caller-facing query remains unchanged for traceability.
    query_vec = provider.embed_query(redact_free_text_userinfo(question) or "")
    doc_vecs = provider.embed_documents([text for _, _, _, text in plan])

    candidates: list[Candidate] = []
    for (kind, domain, term, text), doc_vec in zip(plan, doc_vecs, strict=True):
        facts = by_domain[domain]
        index = IndexVersion(
            space=provider.space,
            source_text_hash=_source_text_hash(text),
            commit_sha=facts.commit_sha,
            context_snapshot_id=facts.context_snapshot_id,
        )
        candidates.append(
            Candidate(
                kind=kind,
                domain=domain,
                term=term,
                signal=_signal(score=cosine(query_vec, doc_vec), matched_on=kind, index=index),
            )
        )

    # Descending score, then a total deterministic order over opaque fields so
    # two candidates that tie on score never swap between runs. `term` is None
    # for a domain, so it sorts before any concept of the same domain.
    candidates.sort(key=lambda c: (-c.signal["score"], c.kind, c.domain, c.term or ""))
    return candidates[:limit]
