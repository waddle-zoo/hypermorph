"""Compile one `ContextBundle` from the pinned Git commit plus real evidence
(hy-gh-31), walking-skeleton step 9, now driven by a `ContextDirective`
(hy-x7f, GitHub #70).

The resolver reads; it never writes. It cannot author, edit, or approve
meaning: every semantic field it returns is copied from one immutable
`ContextSnapshot`, and every evidence claim names an exact observed version.
Where Git says nothing, the bundle says so rather than inventing guidance --
missing context is a valid answer.

It also no longer decides what the question is about. Domain selection used
to split the question into words and look for a configured domain name among
them; that literal routing is deleted (#70). A directive names exact domains
and refs, this module resolves exactly those, and a directive that names
nothing gets told to plan first. Retrieval is bounded here -- by hops through
the projection and by a byte budget -- and both bounds are disclosed, because
a quietly shortened answer is worse than a long one.

One shared application service, so the HTTP and MCP adapters stay thin and
cannot drift apart: they call this and serialize what it returns.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from hyperset.bundle.directive import PLAN_FIRST, ContextDirective
from hyperset.bundle.gather import gather
from hyperset.bundle.instructions import _source_refs, concept_terms, git_instructions
from hyperset.bundle.reconcile import (
    PROCESSOR_FINDING,
    PROJECTED,
    JoinPair,
    prohibited_but_referenced,
    reconcile,
    source_deleted_while_governed,
)
from hyperset.bundle.schema import (
    DOMAIN_AMBIGUOUS,
    DOMAIN_DOES_NOT_DECLARE,
    EVIDENCE_REF_UNRESOLVED,
    GIT_LINKED,
    MAX_HOPS_NOT_APPLICABLE,
    NO_CONTEXT_SOURCE,
    OBSERVED_ONLY,
    OBSERVED_PAYLOADS_OMITTED,
    OVER_CONTEXT_BUDGET,
    PLAN_FIRST_REQUIRED,
    PROJECTION_BOUNDED,
    REF_CORROBORATED_LATE,
    REF_OUTSIDE_CONTEXT,
    UNKNOWN_DOMAIN,
    ContextBundle,
    warning,
)
from hyperset.context.adapter.apply import adapter_projection
from hyperset.context.evidence import ObservedEvidenceResolver
from hyperset.context.hierarchy import (
    contains_edge,
    domain_node_id,
    unverified_domains,
)
from hyperset.context.schema import is_unevaluated, parse_evidence_ref
from hyperset.db.base import utcnow
from hyperset.repositories.errors import NotFoundError
from hyperset.repositories.postgres import (
    PostgresConnectionRepository,
    PostgresContextRepository,
    PostgresObservedAssetRepository,
    PostgresProcessorRepository,
)
from hyperset.repositories.scope import resolve_workspace_scope

# The two connector relation words this slice projects as OBSERVED-provenance
# graph edges (#230 slice 7, hy-c6vx): DataHub upstream lineage (`derived_from`)
# and the connector's evidence-of-meaning link (`has_glossary_term`). Every other
# connector relation (queries/contains/owned_by/in_domain/belongs_to) is
# deliberately absent -- the walking skeleton names exactly these two. The graph
# names are OBSERVED and carry `evidence: "observation"`.
#
# Neither observed name reuses a GOVERNED relation string, per ADR-0034 Decision 2
# and its Section 8: `lineage_to` is distinct from the governed `has_lineage`
# (Section 9), and the glossary link keeps the connector's own `has_glossary_term`
# rather than the governed string `evidenced_by`. `evidenced_by` was served as a
# governed `evidence: "git"` edge historically and STILL appears in committed
# recordings, so minting it as an OBSERVED relation would let a relation-only or
# legacy client read the observed edge as the governed one -- exactly the
# collision ADR-0034 forbids. It stays retired; its stale-recording removal rides
# the deferred re-record (hy-l13a).
CONNECTOR_RELATION_TO_GRAPH = {
    "derived_from": "lineage_to",
    "has_glossary_term": "has_glossary_term",
}

# The complete GOVERNED relation vocabulary -- the authoritative set an OBSERVED
# relation may never reuse (ADR-0034 Decision 2). It is: every within-domain edge
# `domain_graph` emits (`owns`..`validates`, see the emit sites below), the
# governed hierarchy `contains` (ADR-0031), the cross-domain edges ADR-0034
# Section 2b names (`depends_on`, `joinable_on`), and the RETIRED governed
# `evidenced_by` -- retired from live emit but still present in committed
# recordings, so still reserved. A relation-only or legacy client keys on
# `relation`, so reusing any of these for an observation edge could be read as the
# governed one. Kept complete against the emit by
# `test_every_within_domain_governed_relation_is_in_the_authoritative_set`.
GOVERNED_RELATIONS = frozenset(
    {
        "owns",
        "defined_in",
        "approved_for",
        "has_grain",
        "classified_as",
        "has_freshness",
        "has_lineage",
        "has_checks",
        "reads",
        "constrains",
        "validates",
        "contains",
        "depends_on",
        "joinable_on",
        "evidenced_by",
    }
)


def _empty_evidence() -> dict:
    return {
        "observed_assets": [],
        "findings": [],
        "freshness": [],
        "conflicts": [],
        "deprecations": [],
        "uncorroborated": [],
    }


def resolve_analytics_context(
    *,
    query: str,
    directive: ContextDirective,
    session_factory,
    workspace: str | None = None,
) -> ContextBundle:
    """Retrieve exactly what one directive names, for one question.

    `query` travels verbatim: it is the caller's stated intent and part of
    the record, and nothing here reads it. The directive decides what is
    retrieved.

    `workspace` bounds the estate this resolve reads (hq-t6nx): a concrete
    tenant confines every source read (selection, hierarchy, assist gather) to
    that workspace, and `None` reads the whole estate unchanged. The public
    RESOLVE op passes `None` (its data scoping is deferred per ADR-0037); the
    VALIDATE op passes the caller's workspace so a validation bundle can never
    be built from a sibling tenant's source.
    """
    request = {"query": query, "directive": directive.to_dict()}
    if directive.is_empty:
        return _plan_first(request)

    sources = [
        source
        for source in PostgresContextRepository(session_factory).list_sources(workspace=workspace)
        if source.enabled and source.current_snapshot is not None
    ]
    selection = _select(sources, directive)
    matched, warnings = selection.matched, selection.warnings
    if selection.refused:
        # Governance has said no, and two of the four ways it says no are about
        # a question that was actually asked: the coverage refusal, where the
        # domain exists and does not declare the terms, and the unknown-domain
        # refusal, where nothing declares the domain either (hy-xq55). Those are
        # the places where Git is silent and the estate is not, so they are the
        # entry points discovery opens. The other two are requests to fix rather
        # than questions to answer -- no configured source, or two domains at
        # once -- and stay untouched.
        return _no_match(
            request,
            warnings,
            # `directive.assist` is the refusal half of ADR 0019 decision 1
            # (hy-c9mb). Checked here rather than inside `_discovered_candidates`
            # so a declining caller costs no discovery query at all: refusing
            # assist means governance alone was computed, not that assist ran and
            # its output was dropped.
            assist=_discovered_candidates(session_factory, selection, workspace=workspace)
            if selection.discoverable and directive.assist
            else None,
        )

    if selection.matched_domains is not None:
        # A directive naming several governed domains: resolve each independently
        # and carry them in a `domains[]` envelope (#230 slice 3, hy-cnto).
        return _multi_domain(
            request, selection.matched_domains, directive, session_factory, workspace=workspace
        )

    evidence, relationships, evidence_warnings = _linked_evidence(
        session_factory,
        snapshot=matched.current_snapshot if matched else None,
        directive=directive,
        workspace=workspace,
    )
    warnings.extend(evidence_warnings)
    if matched is None:
        if not evidence["observed_assets"]:
            return _no_match(request, warnings)
        return _observed_only(request, evidence, warnings, directive, relationships)
    return _governed(
        request,
        matched,
        evidence,
        warnings,
        directive,
        session_factory,
        relationships,
        workspace=workspace,
    )


def _composed_graph(
    resolved: list[str], snapshot, session_factory, *, workspace: str | None = None
) -> dict:
    """The composed cross-domain graph over the RESOLVED domains (#230 slice 5): a
    `domain:{slug}` node for each, plus the governed `contains` edges AMONG them -- an
    edge is included only when BOTH endpoints are resolved domains and neither is on a
    dangling or cyclic chain (the hy-gh-230 verified-edge guard). DOMAIN-LEVEL only: no
    within-domain node, instruction, or authority is composed here; those stay per-domain
    in `domains[]`. Every edge keeps its `evidence: "git"` provenance."""
    parent_of = _estate_parent_map(snapshot, session_factory, workspace=workspace)
    unverified = unverified_domains(parent_of)
    resolved_set = set(resolved)
    nodes = [{"id": domain_node_id(slug), "kind": "domain", "label": slug} for slug in resolved]
    edges = [
        contains_edge(parent, child)
        for child, parent in sorted(parent_of.items())
        if parent is not None
        and child in resolved_set
        and parent in resolved_set
        and child not in unverified
        and parent not in unverified
    ]
    return {"nodes": nodes, "edges": edges}


def _multi_domain(
    request: dict,
    sources: list,
    directive: ContextDirective,
    session_factory,
    *,
    workspace: str | None = None,
) -> ContextBundle:
    """Resolve several named governed domains INDEPENDENTLY, carry them in a `domains[]`
    envelope (#230 slice 3, hy-cnto), and COMPOSE them with a cross-domain graph (#230
    slice 5, hy-uaks). Each `domains[]` entry is built by the SAME single-domain governed
    path, so each is byte-identical to that domain's solo resolve and no domain's authority
    or evidence bleeds into another's.

    Composition is DOMAIN-LEVEL and additive: a top-level `composition.graph` relates the
    resolved domains with `domain:{slug}` nodes and governed `contains` edges among them,
    WITHOUT flattening their authorities -- all per-domain content stays in `domains[]`.

    The FLAT bundle stays the documented envelope the Mayor required (hy-cnto Fork-1):
    `context_authority` is null and the flat governed fields (including `domain_graph`) are
    empty, which means "authority is per-domain -- read `domains[]`; the composed graph is
    in `composition`". `ContextBundle.__post_init__` enforces that fail-closed."""
    sections: list[dict] = []
    statuses: list[str] = []
    for source in sorted(sources, key=lambda s: s.current_snapshot.domain):
        domain = source.current_snapshot.domain
        declared = set(concept_terms(git_instructions(source.current_snapshot.normalized)))
        covered = [term for term in directive.concepts if term in declared]
        sub = ContextDirective(
            domains=[domain],
            concepts=covered,
            asset_refs=list(directive.asset_refs),
            max_hops=directive.max_hops,
            context_budget=directive.context_budget,
            assist=directive.assist,
        )
        sub_request = {"query": request["query"], "directive": sub.to_dict()}
        evidence, relationships, evidence_warnings = _linked_evidence(
            session_factory, snapshot=source.current_snapshot, directive=sub, workspace=workspace
        )
        section = _governed(
            sub_request,
            source,
            evidence,
            list(evidence_warnings),
            sub,
            session_factory,
            relationships,
            workspace=workspace,
        )
        # Each entry carries its own (content-derived, deterministic) `bundle_id` and
        # governed content, but NOT its wall-clock `resolved_at`: the envelope has one
        # top-level `resolved_at` for the whole answer, and since `domains` is inside
        # `_content()`, a per-entry timestamp would leak the wall clock into the outer
        # `bundle_id` and break the determinism promise `domains` is in `_content()` for.
        entry = section.to_dict()
        entry.pop("resolved_at", None)
        sections.append(entry)
        statuses.append(section.status)

    status = "mixed" if "mixed" in statuses else "governed"
    resolved = sorted(source.current_snapshot.domain for source in sources)
    named = ", ".join(resolved)
    # The COMPOSED cross-domain graph (#230 slice 5, hy-uaks): DOMAIN-LEVEL only -- a
    # `domain:{slug}` node per resolved domain and the governed `contains` edges AMONG
    # them, each edge keeping its `evidence` provenance. It exposes how the domains relate
    # without flattening their authorities; all per-domain content stays in `domains[]`.
    composition = {
        "graph": _composed_graph(
            resolved, sources[0].current_snapshot, session_factory, workspace=workspace
        )
    }
    summary = (
        f"{len(sections)} governed domains resolved independently ({named}) and composed. "
        f"Each carries its own authority in 'domains[]'; the top-level 'context_authority' is "
        f"null because no single commit owns a multi-domain answer, and the cross-domain "
        f"'composition.graph' relates the domains without flattening their authorities."
    )
    resolution = {"status": status, "summary": summary, "warnings": []}

    def _envelope() -> ContextBundle:
        return ContextBundle(
            request=request,
            resolution=resolution,
            context_authority=None,
            instructions={},
            linked_evidence=_empty_evidence(),
            domain_graph={"nodes": [], "edges": []},
            provenance_refs=[],
            resolved_at=utcnow(),
            domains=sections,
            composition=composition,
        )

    bundle = _envelope()
    budget = directive.context_budget
    if budget is not None and len(bundle.to_json().encode()) > budget:
        # The per-domain answers are governed content and are never dropped to fit a
        # budget (the `_bundle` posture); the overage is DISCLOSED so a caller that set a
        # budget can tell the multi-domain answer exceeded it rather than reading a
        # silently truncated one.
        resolution["warnings"] = [
            warning(
                OVER_CONTEXT_BUDGET,
                f"this {len(sections)}-domain answer is over the 'context_budget': each "
                f"domain's governed answer is served in full in 'domains[]' and none is "
                f"dropped to fit. Raise the budget, or resolve fewer domains per directive",
            )
        ]
        bundle = _envelope()
    return bundle


def _governed(
    request: dict,
    source,
    evidence: dict,
    warnings: list[str],
    directive: ContextDirective,
    session_factory,
    relationships: list[dict] | None = None,
    *,
    workspace: str | None = None,
) -> ContextBundle:
    """A domain resolved. `mixed` when the directive also named refs the Git
    commit says nothing about: those travel as evidence, disclosed, and are
    never folded into the governed part."""
    snapshot = source.current_snapshot
    instructions = git_instructions(snapshot.normalized)
    ungoverned = [
        asset for asset in evidence["observed_assets"] if asset["governance"] == OBSERVED_ONLY
    ]
    # `resolution.projection` discloses that this domain came through a context
    # adapter and what it did (283-5, hy-13b8); computed here (before status)
    # because 283-6 (fork 3) reads it. Present ONLY for an adapter-sourced snapshot.
    projection = adapter_projection(snapshot.files)
    # 283-6 (hy-v5iy), Brandon's fork-3 ruling = REUSE governed->mixed: an adapter
    # DERIVED a field and no human reviewed it, so that field is not itself
    # governed and the domain degrades governed->mixed -- reusing the existing
    # status value, no new one. GOVERNED stays authoritative: a governed Git-owned
    # field is NEVER a `fields_derived` entry (that list is what the ADAPTER
    # authored, never a Git field), so an adapter overlay never downgrades a
    # governed field; only an adapter-derived-AND-unreviewed field triggers this.
    # `fields_derived` is empty by construction until a producer populates it, so
    # every domain served today keeps the status it had -- byte-identical.
    unreviewed_derived = _has_unreviewed_derived(projection)
    status = "mixed" if (ungoverned or unreviewed_derived) else "governed"
    summary = (
        f"Guidance for {snapshot.domain!r} from the customer-authoritative Git context at "
        f"commit {snapshot.commit_sha[:12]}, with linked {_evidence_summary(evidence)}. "
        f"Hyperset did not author or approve this meaning."
    )
    if ungoverned:
        summary += (
            f" {len(ungoverned)} requested ref(s) are observed-only: no governed context "
            f"covers them, and nothing about them is approved or canonical."
        )
    if evidence["uncorroborated"]:
        # The other direction, and the one that used to be silence: Git
        # approves the ref and no connected system carries its identity. The
        # guidance is still governed -- corroboration is not what makes it
        # authoritative (ADR 0017) -- so this is stated, not subtracted.
        summary += (
            f" {len(evidence['uncorroborated'])} declared ref(s) uncorroborated: the commit "
            f"approves them and no observation backs them, so nothing here says what the "
            f"source actually contains."
        )
    if unreviewed_derived:
        # State why mixed, in the same voice as the ungoverned case: the governed
        # part is not weakened, an unreviewed derived overlay simply is not itself
        # governed (283-6).
        summary += (
            " An adapter derived one or more fields that no human has reviewed "
            "(no reviewed_by), so this answer is mixed: the governed Git context stays "
            "authoritative and is not downgraded, but an unreviewed derived field is not "
            "itself governed or approved."
        )
    return _bundle(
        request=request,
        resolution={
            "status": status,
            "summary": summary,
            "warnings": warnings,
            **({"projection": projection} if projection is not None else {}),
        },
        context_authority={
            "type": "git",
            "repository": source.repository,
            "ref": source.ref,
            "path": source.path,
            "commit_sha": snapshot.commit_sha,
            "committed_at": _iso(snapshot.committed_at),
            "context_snapshot_id": snapshot.id,
            "content_sha256": snapshot.content_hash,
            "owner_refs": [owner["ref"] for owner in snapshot.owner_refs],
            # Present ONLY when the domain declared no eval bank (hy-gh-287), so a
            # resolved answer states its own lack of coverage as a fact. Absent on
            # an evaluated domain -- byte-for-byte the authority a domain with a
            # bank served before this field existed -- because a default of
            # `false` would change every governed answer, and this is a
            # disclosure, not a governed field. A caller reading it never mistakes
            # an unmeasured domain for a passing one.
            **({"unevaluated": True} if is_unevaluated(snapshot.normalized) else {}),
        },
        instructions=instructions,
        evidence=evidence,
        graph=_governed_graph(
            snapshot, instructions, evidence, session_factory, relationships, workspace=workspace
        ),
        provenance_refs=[
            f"git_context:{snapshot.id}@{snapshot.commit_sha}",
            *_evidence_provenance(evidence),
        ],
        directive=directive,
        root=f"domain:{snapshot.domain}",
    )


def _observed_only(
    request: dict,
    evidence: dict,
    warnings: list[str],
    directive: ContextDirective,
    relationships: list[dict] | None = None,
) -> ContextBundle:
    """Refs resolved, no governed context covers them. Observations are
    returned as observations: instructions stay empty, there is no authority,
    and the summary refuses the word approved (`docs/v0-foundation.md`
    invariant 7)."""
    return _bundle(
        request=request,
        resolution={
            "status": "observed_only",
            "summary": (
                f"No configured Git context covers the requested refs. What follows is "
                f"{_evidence_summary(evidence)} exactly as the source reported it: raw "
                f"observation, not approved, canonical, or validated business meaning, and "
                f"no instruction here has been reviewed by anyone."
            ),
            "warnings": warnings,
        },
        context_authority=None,
        instructions=git_instructions({}),
        evidence=evidence,
        graph=_observed_graph(evidence, relationships),
        provenance_refs=_evidence_provenance(evidence),
        directive=directive,
        root=None,
    )


def _plan_first(request: dict) -> ContextBundle:
    """A directive that names nothing. The deleted behaviour would have
    guessed a domain from the wording; this says what to do instead."""
    return _no_match(
        request,
        [
            warning(
                PLAN_FIRST_REQUIRED,
                f"the directive names no domain and no asset refs, so there is nothing to "
                f"retrieve: {PLAN_FIRST}",
            )
        ],
    )


def _no_match(request: dict, warnings: list[str], *, assist: dict | None = None) -> ContextBundle:
    """No authoritative context answers this. Say so, and say nothing else:
    an empty instruction set cannot be mistaken for approved meaning.

    `assist` rides beside that answer without changing any part of it. The
    status stays `no_match`, the warnings keep their codes, the instructions
    stay empty, and `provenance_refs` stays empty -- a candidate pins nothing,
    because pinning it would put it where the evaluator derives a recording's
    cited sources from. What the caller gets extra is one clearly ungoverned
    section it can ignore entirely (ADR 0019 decision 2(b)).

    `no_match` rather than the `assisted` status ADR 0019 decision 3 proposes:
    that name is explicitly unratified, and serving it would also have to move
    the RESOLVE tool description, which enumerates the four statuses and whose
    text is hashed into every evaluation recording's `tools_hash`. Both are
    rulings to take deliberately rather than in passing, and `no_match` is the
    conservative summary in the meantime -- decision 1 requires the status
    never to read BETTER than the answer's worst claim, and there is no worse
    claim than "nothing governed here".
    """
    return ContextBundle(
        request=request,
        resolution={
            "status": "no_match",
            "summary": (
                "No configured Git context covers this request. Nothing here is "
                "approved, canonical, or trusted business meaning."
            ),
            "warnings": warnings,
        },
        context_authority=None,
        instructions=git_instructions({}),
        linked_evidence=_empty_evidence(),
        domain_graph={"nodes": [], "edges": []},
        provenance_refs=[],
        resolved_at=utcnow(),
        assist=assist,
    )


@dataclass(frozen=True)
class _Selection:
    """What `_select` decided, with the kinds of refusal kept apart.

    Two of the refusals are requests to fix and have no question behind them
    worth ranking for: nothing configured at all, and two domains at once. The
    other two are governance being silent about a question that was asked, and
    those are the ones discovery runs on. `uncovered` resolved a real domain
    and found it does not declare what the caller claimed; `ungoverned` could
    not resolve the domain at all (hy-xq55). All four serve no governed context
    and all four set `matched` to None, so the single `(None, warnings, True)`
    this replaces could not tell them apart -- and the difference is the fact
    discovery runs on.

    The two discoverable refusals are separate fields rather than one, because
    only `uncovered` has a source object behind it. `ungoverned` carries a
    domain NAME the corpus could not resolve, and that asymmetry is exactly
    what `domain_is_configured` reports downstream; collapsing them would mean
    a None in the source slot standing in for it silently.
    """

    matched: object | None
    warnings: list[str]
    refused: bool = False
    uncovered: tuple[object, list[str]] | None = None
    ungoverned: tuple[str, list[str]] | None = None
    # The N>1 governed sources when a directive named several domains (#230 slice
    # 3, hy-cnto). `None` on the single-domain and refusal paths; a list only when
    # the multi-domain resolve is to run. Kept separate from `matched` (one source
    # or None) so the single-domain path is byte-for-byte unchanged.
    matched_domains: list | None = None

    @property
    def discoverable(self) -> bool:
        """Is this a refusal discovery has something to say about? Read at the
        call site so the two fields cannot drift out of sync with the test
        that gates the query."""
        return self.uncovered is not None or self.ungoverned is not None


def _select(sources, directive: ContextDirective) -> _Selection:
    """Exact selection by configured domain name, then the coverage claim.
    Nothing is matched by similarity, by word overlap, or by anything read
    out of the question: a guess here would attach the wrong meaning to a
    real question.

    Returns the selected source, warnings, whether the request was refused
    outright rather than answered from evidence alone, and -- for the coverage
    refusal alone -- the domain that refused it and the terms it does not
    declare.
    """
    domains = directive.domains
    available = sorted(source.current_snapshot.domain for source in sources)
    if not domains:
        return _Selection(None, [])
    if not sources:
        return _Selection(
            None,
            [
                warning(
                    NO_CONTEXT_SOURCE,
                    "no Git context source is configured; add one with `hyperset context add`",
                )
            ],
            refused=True,
        )

    wanted = list(dict.fromkeys(domains))
    matches = [source for source in sources if source.current_snapshot.domain in wanted]
    unknown = sorted(set(wanted) - {source.current_snapshot.domain for source in matches})
    if unknown:
        # Discovery runs here only when the whole request has ONE subject and
        # nothing governs it (hy-xq55). A directive naming a configured domain
        # AND an unknown one is refused by this same branch, and ranking for it
        # would have to pick which of the two names the list answers for -- the
        # missing-single-subject problem `multiple_domains` is already refused
        # over, so it gets that refusal's silence rather than an answer to a
        # question nobody asked.
        single_subject = not matches and len(wanted) == 1
        return _Selection(
            None,
            [
                warning(
                    UNKNOWN_DOMAIN,
                    f"the directive names {', '.join(repr(name) for name in unknown)}, which "
                    f"no configured context declares; configured domains: "
                    f"{', '.join(available) or 'none'}",
                )
            ],
            refused=True,
            ungoverned=(wanted[0], list(directive.concepts)) if single_subject else None,
        )
    # An ESTATE ambiguity comes first, and is a different fault than the caller
    # naming several domains (hy-gh-282): if a SINGLE requested domain is claimed
    # by more than one configured source, no commit can be the authority, and the
    # thing to fix is the estate, not the directive. `multiple_domains` told such
    # a caller it named two domains when it named one, with a recovery a directive
    # cannot carry out. Grouped by domain so the check is independent of how many
    # domains were requested: a request for two domains, one of them ambiguous,
    # is an ambiguity first.
    by_domain: dict[str, list] = {}
    for source in matches:
        by_domain.setdefault(source.current_snapshot.domain, []).append(source)
    ambiguous = {domain: claimants for domain, claimants in by_domain.items() if len(claimants) > 1}
    if ambiguous:
        return _Selection(None, [_ambiguous_warning(ambiguous)], refused=True)
    if len(matches) > 1:
        # A directive naming several governed domains RESOLVES (#230 slice 3,
        # hy-cnto), each domain answered independently in its own `domains[]`
        # entry -- no bundle merges two commits' guidance. Coverage is checked by
        # UNION: every claimed concept must be declared by at least one named
        # domain, and each named domain must declare at least one claimed concept,
        # so every per-domain answer resolves against a non-empty subset of the
        # claim (a validity check, not a content merge). `multiple_domains` is
        # retired as a refusal; the estate-ambiguity check above still runs first.
        declared = {
            source.current_snapshot.domain: set(
                concept_terms(git_instructions(source.current_snapshot.normalized))
            )
            for source in matches
        }
        claimed = list(dict.fromkeys(directive.concepts))
        uncovered = [term for term in claimed if not any(term in d for d in declared.values())]
        if uncovered:
            covering = ", ".join(sorted(declared))
            return _Selection(
                None,
                [
                    warning(
                        DOMAIN_DOES_NOT_DECLARE,
                        f"the directive needs {', '.join(repr(term) for term in uncovered)}, "
                        f"which none of the named domains ({covering}) declares; name a domain "
                        f"that declares them, or drop them",
                    )
                ],
                refused=True,
            )
        vacuous = sorted(
            domain
            for domain, terms in declared.items()
            if not any(term in terms for term in claimed)
        )
        if vacuous:
            return _Selection(
                None,
                [
                    warning(
                        DOMAIN_DOES_NOT_DECLARE,
                        f"the {', '.join(repr(name) for name in vacuous)} domain(s) declare none "
                        f"of the concepts this directive claims, so they add nothing to the "
                        f"answer; drop them, or name concepts they declare",
                    )
                ],
                refused=True,
            )
        return _Selection(None, [], matched_domains=matches)
    return _covered(matches[0], directive.concepts)


def _ambiguous_warning(ambiguous: dict[str, list]):
    """One estate-ambiguity disclosure naming the conflicting sources and commits
    (hy-gh-282). It points at `hyperset context disable`, the supported way to
    reconcile, because a directive has no field to pick which claimant to use --
    that was the unfollowable half of the old `multiple_domains` recovery."""
    parts = []
    for domain in sorted(ambiguous):
        claimants = ", ".join(
            f"{source.id} ({source.repository}@{source.ref}:{source.path} "
            f"commit {source.current_snapshot.commit_sha})"
            for source in sorted(ambiguous[domain], key=lambda source: source.id)
        )
        parts.append(f"{domain!r} is claimed by {len(ambiguous[domain])} sources: {claimants}")
    return warning(
        DOMAIN_AMBIGUOUS,
        "the configured estate is ambiguous, so no single commit can be the authority: "
        + "; ".join(parts)
        + ". Disable or remove all but one claimant with `hyperset context disable <source-id>`; "
        "the directive is correct and does not need changing.",
    )


def _covered(source, claimed: list[str]) -> _Selection:
    """Does this domain declare what the caller said it needs? (hy-9lct)

    Hyperset cannot answer 'does this domain cover this question' -- reading
    the question is the routing #70 deleted, and the resolver never looks at
    `query`. So the caller declares the concepts its answer needs and this
    checks that declaration against the domain's own Git definitions, by the
    same exact set membership the domain name itself gets. No similarity, no
    stemming, no synonyms.

    What that buys is bounded and worth stating: a caller that names a
    concept the domain does declare, for a question about something else,
    still gets a bundle. What it can no longer do is get one by saying
    nothing -- which is the failure that was measured, where a supplier
    lead-time question was answered from the revenue domain because the
    substrate had nowhere to put the objection.

    `claimed` is never empty here: a domain without a claim is malformed and
    `ContextDirective` refuses it at construction, so this runs only on
    claims there is something to check. That refusal is a request-shape
    verdict and belongs there; this one is about the corpus and belongs here.
    """
    domain = source.current_snapshot.domain
    declared = concept_terms(git_instructions(source.current_snapshot.normalized))
    missing = sorted(set(claimed) - set(declared))
    if missing:
        return _Selection(
            None,
            [
                warning(
                    DOMAIN_DOES_NOT_DECLARE,
                    f"the directive needs {', '.join(repr(term) for term in missing)}, which "
                    f"the {domain!r} domain does not declare; {domain!r} declares: "
                    f"{', '.join(declared) or 'nothing'}. The nearest domain is not the right "
                    f"domain: if no configured domain declares what you need, nothing governs "
                    f"this question",
                )
            ],
            refused=True,
            uncovered=(source, missing),
        )
    return _Selection(source, [])


def _discovered_candidates(
    session_factory, selection: _Selection, *, workspace: str | None = None
) -> dict | None:
    """The refusal path's assist half, delegated to the gather producer
    (hy-1f9h). Two of `_select`'s four refusals reach here and two never do,
    and the split is not about what is computable (hy-xq55). `multiple_domains`
    is entirely computable and stays silent because ranking would answer a
    question that IS governed, which is worse than saying nothing;
    `no_context_source` stays silent because no rank could ever be Git-relative
    and the refusal's own remedy -- `hyperset context add` -- is the answer.

    `uncovered` resolved a real domain and found it does not declare the terms;
    `ungoverned` could not resolve the domain at all. `gather` re-derives which
    from the domain name, so this hands it the miss's semantic inputs and
    nothing more.
    """
    if selection.uncovered is not None:
        named, missing = selection.uncovered
        domain = named.current_snapshot.domain
    else:
        domain, missing = selection.ungoverned
    return gather(
        domain=domain, undeclared=missing, session_factory=session_factory, workspace=workspace
    )


def _observed_relationships(
    session_factory, observed_assets: list[dict], *, workspace: str | None = None
) -> list[dict]:
    """Pre-resolved OBSERVED relationship fragments for the assets in play (#230
    slice 7, hy-c6vx): each is one persisted `asset_relationships` row whose
    connector word maps to an observed graph relation (`derived_from` -> lineage,
    `has_glossary_term` -> evidence), with both endpoints resolved to graph node
    ids. Only the two mapped relations are kept; the rest are the connector's own
    references and are not projected here.

    The `from` node id mirrors the existing `observed_as` edge exactly
    (`source:{governed_source_ref or ref}`), so a governed source and its lineage
    hang off the same node. The `to` node is the referenced asset, resolved to its
    source-native ref the same way `_references_into` names a referring asset; it
    is generally observed-only and gets its own `observed_source` node."""
    if not observed_assets:
        return []
    repository = PostgresObservedAssetRepository(session_factory)
    connectors = {
        connection.id: connection.connector_type
        for connection in PostgresConnectionRepository(session_factory).list(
            workspace=resolve_workspace_scope(workspace)
        )
    }
    fragments: list[dict] = []
    for observed in observed_assets:
        from_node = f"source:{observed.get('governed_source_ref') or observed['ref']}"
        for row in repository.list_relationships(
            from_asset_id=observed["asset_id"], include_deleted=False
        ):
            graph_relation = CONNECTOR_RELATION_TO_GRAPH.get(row.relation)
            if graph_relation is None:
                continue
            try:
                target = repository.get(row.to_asset_id)
            except NotFoundError:
                continue
            connector = connectors.get(target.connection_id)
            if connector is None:
                # Same rule `_references_into` applies: a target whose connection
                # is unknown cannot be named as a ref, so the edge is not evidence.
                continue
            to_ref = f"{connector}:{target.asset_type}:{target.external_id}"
            fragments.append(
                {
                    "from": from_node,
                    "to": f"source:{to_ref}",
                    "to_label": target.external_id,
                    "to_connector": connector,
                    "relation": graph_relation,
                }
            )
    return fragments


def _references_into(
    session_factory, declared: list[dict], *, workspace: str | None = None
) -> dict[str, list[str]]:
    """Live references into each ref the pinned commit declares, keyed by the
    ref as Git wrote it (hy-llk4).

    Git names a source by ref and the reference count is an observed edge into
    an asset, so the ref has to be resolved before the two can be joined. The
    resolution is the exact source-native identity match every other path here
    uses, so an ambiguous or unobserved ref resolves to nothing -- and its
    refusals are DISCARDED rather than served as warnings: a prohibited source
    the estate never observed is an absence, and ADR 0021 decision 2 keeps an
    absence out of the disagreement list. It is already disclosed as a
    `prohibited_by_context` deprecation, which is a fact about Git and needs no
    observation to be true.

    Two statements for the whole set, and none at all for a commit that
    declares nothing: `IN ()` is a query whose answer is known, and so is a
    connection list nothing will be looked up in.
    """
    if not declared:
        return {}
    evidence_to_governed = {
        entry["bi_override"]["ref"]: entry["ref"]
        for entry in declared
        if entry.get("bi_override") is not None
    }
    refs = list(evidence_to_governed)
    if not refs:
        return {}
    resolved, _refusals = _resolve_by_identity(session_factory, refs, workspace=workspace)
    by_asset_id = {entry["asset_id"]: evidence_to_governed[entry["ref"]] for entry in resolved}
    if not by_asset_id:
        return {}
    connectors = {
        connection.id: connection.connector_type
        for connection in PostgresConnectionRepository(session_factory).list(
            workspace=resolve_workspace_scope(workspace)
        )
    }
    references: dict[str, list[str]] = {}
    rows = PostgresObservedAssetRepository(session_factory).list_live_references(
        to_asset_ids=list(by_asset_id)
    )
    for row in rows:
        connector = connectors.get(row.from_connection_id)
        if connector is None:
            # The same rule the ranking applies: a referring asset whose
            # connection is unknown cannot be named as a ref, and a reference
            # the reader cannot look up is not evidence.
            continue
        references.setdefault(by_asset_id[row.to_asset_id], []).append(
            f"{connector}:{row.from_asset_type}:{row.from_external_id}"
        )
    return references


def _linked_evidence(
    session_factory, *, snapshot, directive: ContextDirective, workspace: str | None = None
):
    """Evidence for exactly the refs in play: the Git commit's own refs, plus
    any ref the directive named that the commit does not cover -- resolved by
    exact source-native identity and marked observed-only.

    The commit's own refs are the ones it DECLARED, which is not the same set
    as the ones a sync managed to link. ADR 0017 defines `evidence_refs` as
    declared AND observed and puts the rest on the snapshot as findings, so
    reading only the first list gave the resolver a smaller idea of the commit
    than the commit has: a declared ref with no observation behind it was
    invisible when the directive named nothing, and was reported as
    `ref_outside_context` -- Git never approved it -- when the directive named
    it (hy-zhv9).
    """
    assets_repository = PostgresObservedAssetRepository(session_factory)
    findings_repository = PostgresProcessorRepository(session_factory)

    warnings: list[str] = []
    requested = list(dict.fromkeys(directive.asset_refs))
    declared = list(snapshot.evidence_refs) if snapshot is not None else []
    gaps = list(snapshot.evidence_findings) if snapshot is not None else []
    known_refs = {ref["ref"] for ref in declared} | {gap["ref"] for gap in gaps}
    outside = [ref for ref in requested if ref not in known_refs]

    def in_play(ref: str) -> bool:
        return not requested or ref in requested

    entries = [
        (ref, GIT_LINKED)
        for ref in sorted(declared, key=lambda entry: entry["ref"])
        if in_play(ref["ref"])
    ]
    uncorroborated: list[dict] = []
    pending = sorted({gap["ref"] for gap in gaps if in_play(gap["ref"])})
    if pending:
        # Re-resolved rather than replayed. The snapshot's finding is a fact
        # about the moment the commit was read, and a bundle answers now: a
        # connector synced since then makes the ref corroborated, and serving
        # the stored "matches no observed asset" sentence would be a
        # present-tense claim this code can check and did not (hy-5lgg, on the
        # served surface). Nothing is guessed either way -- this is the same
        # exact source-native identity match, so an ambiguous ref still
        # resolves to nothing.
        late, uncorroborated = _resolve_by_identity(session_factory, pending, workspace=workspace)
        governed_by_ref = {gap["ref"]: gap.get("governed_source_ref") for gap in gaps}
        late = [
            {
                **entry,
                **(
                    {"governed_source_ref": governed_by_ref[entry["ref"]]}
                    if governed_by_ref.get(entry["ref"])
                    else {}
                ),
            }
            for entry in late
        ]
        corroborated = {entry["ref"] for entry in late}
        warnings.extend(
            warning(
                REF_CORROBORATED_LATE,
                f"evidence ref {gap['ref']!r} was uncorroborated at commit "
                f"{snapshot.commit_sha[:12]} and a connector has since observed it; the "
                "snapshot still discloses the gap and is not re-authored",
            )
            for gap in sorted(gaps, key=lambda entry: entry["ref"])
            if gap["ref"] in corroborated
        )
        entries.extend(
            # No commit pinned a version for these. The observation is real
            # and current, and `linked_version_id` stays null because the
            # snapshot links none -- the ref is governed, its version is not.
            ({**entry, "observed_version_id": None}, GIT_LINKED)
            for entry in late
        )
        warnings.extend(warning(gap["code"], gap["message"]) for gap in uncorroborated)
    if outside:
        resolved, refusals = _resolve_by_identity(session_factory, outside, workspace=workspace)
        warnings.extend(warning(gap["code"], gap["message"]) for gap in refusals)
        if snapshot is not None:
            warnings.extend(
                warning(
                    REF_OUTSIDE_CONTEXT,
                    f"asset ref {ref['ref']!r} is not part of the {snapshot.domain!r} context "
                    f"at commit {snapshot.commit_sha[:12]}; it is returned as observed-only "
                    f"evidence and nothing about it is approved",
                )
                for ref in resolved
            )
        entries.extend((ref, OBSERVED_ONLY) for ref in resolved)

    observed_assets, freshness, deprecations, findings, conflicts = [], [], [], [], []
    deleted_and_governed: list[dict] = []
    for ref, governance in entries:
        try:
            asset = assets_repository.get(ref["asset_id"])
        except NotFoundError:
            warnings.append(
                warning(
                    EVIDENCE_REF_UNRESOLVED,
                    f"evidence ref {ref['ref']!r} no longer resolves to an observed asset",
                )
            )
            continue

        version = asset.current_version
        observed_assets.append(
            {
                "ref": ref["ref"],
                "governed_source_ref": ref.get("governed_source_ref"),
                "connector": ref["connector"],
                "governance": governance,
                "asset_type": asset.asset_type,
                "external_id": asset.external_id,
                "asset_id": asset.id,
                "connection_id": asset.connection_id,
                "observed_version_id": version.id if version else None,
                "observed_version": version.version if version else None,
                "content_sha256": version.content_hash if version else None,
                # Which version the Git commit was linked against. A newer
                # current version is the source having moved since, which the
                # findings below explain. An observed-only ref has none:
                # no commit ever linked it.
                "linked_version_id": ref.get("observed_version_id")
                if governance == GIT_LINKED
                else None,
                "normalized": version.normalized if version else {},
            }
        )
        freshness.append(
            {
                "ref": ref["ref"],
                "last_observed_at": _iso(asset.last_seen_at),
                "observed_version_at": _iso(version.created_at) if version else None,
                "source_modified_at": _iso(asset.source_modified_at),
                "deleted_at": _iso(asset.deleted_at),
            }
        )
        if asset.deleted_at is not None:
            deprecations.append(
                {
                    "ref": ref["ref"],
                    "kind": "source_deleted",
                    "reason": (
                        f"the source stopped reporting this {asset.asset_type} at "
                        f"{_iso(asset.deleted_at)}"
                        + ("; Git still approves it" if governance == GIT_LINKED else "")
                    ),
                }
            )
            if governance == GIT_LINKED:
                # The deprecation above already carries both facts, in a
                # sentence. This is the same pair where a reader looks for a
                # pair (hy-llk4); the deprecation stays, because an observation
                # about the source is true whether or not Git approves it.
                deleted_and_governed.append(
                    {
                        "ref": ref["ref"],
                        "asset_type": asset.asset_type,
                        "deleted_at": _iso(asset.deleted_at),
                    }
                )

        for finding in sorted(
            findings_repository.list_findings(state="current", affected_asset_id=asset.id),
            key=lambda row: row.id,
        ):
            findings.append(
                {
                    "finding_id": finding.id,
                    "finding_type": finding.finding_type,
                    "rule_version": finding.rule_version,
                    "severity": finding.severity,
                    "ref": ref["ref"],
                    "explanation": finding.explanation,
                    "context_snapshot_id": finding.affected_context_snapshot_id,
                    "proposed_action": finding.proposed_action,
                }
            )
            conflict = _conflict(ref["ref"], finding)
            if conflict is not None:
                conflicts.append(conflict)

    prohibited_sources = (
        snapshot.normalized.get("prohibited_sources", []) if snapshot is not None else []
    )
    conflicts.extend(
        source_deleted_while_governed(
            deleted_and_governed,
            prohibited_refs=[ref for entry in prohibited_sources for ref in _source_refs(entry)],
            commit_sha=snapshot.commit_sha if snapshot is not None else None,
        )
    )

    if snapshot is not None:
        for prohibited in prohibited_sources:
            deprecations.append(
                {
                    "ref": prohibited["ref"],
                    "kind": "prohibited_by_context",
                    "reason": prohibited["reason"],
                }
            )
        conflicts.extend(
            prohibited_but_referenced(
                prohibited_sources,
                referenced_by=_references_into(
                    session_factory, prohibited_sources, workspace=workspace
                ),
                commit_sha=snapshot.commit_sha,
            )
        )

    # Threaded as a SEPARATE return value, never a key on the evidence dict:
    # `_bundle` serves `linked_evidence=evidence` verbatim, so a new evidence key
    # would be a new served bundle key and force a SCHEMA_VERSION bump. The
    # observed relationship edges ride into `domain_graph` (an existing key) as new
    # relation VALUES, which is additive under ADR 0018 (#230 slice 7, hy-c6vx).
    relationships = _observed_relationships(session_factory, observed_assets, workspace=workspace)
    return (
        {
            "observed_assets": observed_assets,
            "findings": findings,
            "freshness": freshness,
            "conflicts": conflicts,
            "deprecations": deprecations,
            "uncorroborated": uncorroborated,
        },
        relationships,
        warnings,
    )


def _resolve_by_identity(session_factory, refs: list[str], *, workspace: str | None = None):
    """Resolve raw refs by source-native identity, across the connections of
    that connector IN one workspace, with an ambiguous or unobserved ref refused
    rather than guessed.

    Serves both refs no Git commit declared and refs a commit declared that
    its sync could not corroborate. One function because the resolution is the
    same question -- does an observed asset carry this exact identity? -- and
    what differs is what the caller does with the answer.

    Returns the resolutions and the refusals, each refusal `{code, ref,
    message}`, already coded by the layer that knows why: a malformed ref from
    the parser, an ambiguous or unobserved one from the evidence resolver.
    Nothing here re-derives a code from a sentence.
    """
    prose: list[str] = []
    coded: list[dict] = []
    parsed = []
    for raw in refs:
        ref = parse_evidence_ref(raw, prose, coded=coded)
        if ref is not None:
            parsed.append(ref)
    resolution = ObservedEvidenceResolver(
        session_factory, workspace=resolve_workspace_scope(workspace)
    ).resolve(parsed)
    coded.extend(resolution.findings)
    return sorted(resolution.resolved, key=lambda entry: entry["ref"]), coded


def _conflict(ref: str, finding) -> dict | None:
    """A finding that puts a Git instruction and an observation in direct
    disagreement is surfaced as a conflict too, so a model reading the
    instructions sees which one is disputed instead of having to interpret a
    finding.

    One of two producers now, and it says so on every entry: the other is
    `hyperset.bundle.reconcile`, which joins Git to current observation at
    bundle time for the two dimensions no finding stands behind (hy-llk4).
    """
    git = finding.evidence.get("git", {})
    observed = finding.evidence.get("observed", {})
    if "expression" not in git or "expression" not in observed:
        return None
    # A persisted processor finding is the PROJECTED binding of the one join
    # mechanism (hy-gl39): the processor already compared the expressions to
    # create the finding, so the bundle projects that decision verbatim and does
    # NOT re-decide it. Routing it through the DIFFERENT-only expression
    # comparator dropped a persisted UNDECIDED finding from `conflicts` while it
    # stayed in `.findings`; `PROJECTED` trusts the finding, exactly as this
    # projection did before the mechanism landed. It still INHERITS the finding's
    # severity unchanged (hy-xfhh).
    emitted = reconcile(
        [
            JoinPair(
                value_kind=PROJECTED,
                declared=git["expression"],
                observed=observed["expression"],
                entry=dict(
                    kind=finding.finding_type,
                    produced_by=PROCESSOR_FINDING,
                    severity=finding.severity,
                    finding_id=finding.id,
                    ref=ref,
                    field=finding.evidence.get("field"),
                    context_says=git["expression"],
                    source_says=observed["expression"],
                    unresolved_since_commit=git.get("commit_sha"),
                ),
            )
        ]
    )
    return emitted[0] if emitted else None


def projection_summary(snapshot, instructions: dict) -> dict:
    """Which node kinds and relation names `domain_graph` would produce for
    this snapshot, without building it (hy-aq3).

    The catalog needs the shape of the projection, not the projection: it
    used to materialize every node and edge per domain per call and keep two
    sets of strings. Each line below mirrors one block of `domain_graph`, and
    `tests/postgres/test_context_catalog.py` compares this summary against
    the real projection so the two cannot drift apart unnoticed.

    Observation-only kinds are absent by construction: a catalog lists what
    Git declares, and no evidence has been resolved at this point.
    """
    kinds = ["domain"]
    relations: list[str] = []
    if snapshot.owner_refs:
        kinds.append("owner")
        relations.append("owns")
    if instructions["definitions"]:
        kinds.append("concept")
        relations.append("defined_in")
    if instructions["approved_sources"]:
        kinds.append("source")
        relations.append("approved_for")
    # Mirrors the per-source grain block in `domain_graph` (hy-gp99): a source
    # that declares `facets.grain` adds a grain node and a `has_grain` edge.
    if any(
        (source.get("facets") or {}).get("grain") for source in instructions["approved_sources"]
    ):
        kinds.append("grain")
        relations.append("has_grain")
    # Mirrors the per-source classification block in `domain_graph` (hy-4giv): a
    # source that declares `facets.classification` adds a classification node and a
    # `classified_as` edge.
    if any(
        (source.get("facets") or {}).get("classification")
        for source in instructions["approved_sources"]
    ):
        kinds.append("classification")
        relations.append("classified_as")
    # Mirrors the per-source freshness block in `domain_graph` (hy-6c8z): a source
    # that declares `facets.freshness` adds a freshness node and a `has_freshness`
    # edge.
    if any(
        (source.get("facets") or {}).get("freshness") for source in instructions["approved_sources"]
    ):
        kinds.append("freshness")
        relations.append("has_freshness")
    # Mirrors the per-source lineage block in `domain_graph` (hy-sr7w): a source
    # that declares `facets.lineage` adds a lineage node and a `has_lineage` edge.
    if any(
        (source.get("facets") or {}).get("lineage") for source in instructions["approved_sources"]
    ):
        kinds.append("lineage")
        relations.append("has_lineage")
    # Mirrors the per-source checks block in `domain_graph` (hy-w16y): a source that
    # declares `facets.checks` adds a `checks` node and a `has_checks` edge. Distinct
    # from the `check`/`validates` projection above, which is the domain's own
    # validations, not the data-quality checks a source asserts it owns.
    if any(
        (source.get("facets") or {}).get("checks") for source in instructions["approved_sources"]
    ):
        kinds.append("checks")
        relations.append("has_checks")
    if instructions["fields"]:
        kinds.append("field")
        relations.append("reads")
    if instructions["joins"]:
        kinds.append("join")
    if instructions["grain"]:
        kinds.append("grain")
    if instructions["joins"] or instructions["grain"]:
        relations.append("constrains")
    if instructions["validations"]:
        kinds.append("check")
        relations.append("validates")
    return {"node_kinds": sorted(set(kinds)), "relationships": sorted(set(relations))}


def _contains_edges(domain: str, parent_of: dict[str, str | None]) -> tuple[list[dict], list[dict]]:
    """The governed `contains` edges (ADR-0031, hy-gh-230 slice 1) that connect one
    domain to its declared PARENT and its declared CHILDREN in the served graph -- the
    graph ABOVE and BETWEEN domains, not within one. Depth-agnostic: the SAME `contains`
    relation, one immediate level each way (deeper traversal is the bounded expansion op,
    a later slice of #230). Every edge is a governed Git declaration (`evidence: "git"`),
    never a name inference (ADR 0012, ADR 0031).

    FAIL-CLOSED before serving (the mayor's #352 requirement): `parent_of` is the WHOLE
    estate's parent map, and `unverified_domains` runs `validate_forest` over it first and
    then localises the fault to the domains actually on a broken chain. If THIS domain is
    one of them nothing is served. That single drop is sufficient: every edge this function
    emits sits on `domain`'s own chain (its parent is an ancestor, its children are
    descendants), so a `domain` on a clean ancestor chain has clean endpoints, and a
    transitive-unknown or cyclic ancestor -- which the per-sync `validate_domain` cannot
    catch once an intermediate domain is disabled AFTER a valid sync -- makes `domain`
    itself unverified and drops the whole emit. A dropped relation is simply not asserted;
    it is not a hidden governed fact.
    """
    if domain in unverified_domains(parent_of):
        return [], []

    nodes: list[dict] = []
    edges: list[dict] = []

    def _add(parent: str, child: str) -> None:
        other = parent if child == domain else child
        nodes.append({"id": domain_node_id(other), "kind": "domain", "label": other})
        edges.append(contains_edge(parent, child))

    parent = parent_of.get(domain)
    if parent is not None:
        _add(parent, domain)  # this domain is a child of `parent`
    for child, child_parent in sorted(parent_of.items()):
        if child_parent == domain:
            _add(domain, child)  # `child` is a child of this domain
    return nodes, edges


def _estate_parent_map(
    snapshot, session_factory, *, workspace: str | None = None
) -> dict[str, str | None]:
    """The whole estate's `domain -> declared parent` map from every enabled source that
    has a current snapshot, plus this snapshot itself (an old snapshot with no `parent`
    key reads as a root). Built exactly as the sync-time hierarchy check builds it, so the
    emit validates against the same estate the sync validated."""
    parent_of = {
        source.current_snapshot.domain: source.current_snapshot.normalized.get("parent")
        for source in PostgresContextRepository(session_factory).list_sources(workspace=workspace)
        if source.enabled and source.current_snapshot is not None
    }
    parent_of.setdefault(snapshot.domain, snapshot.normalized.get("parent"))
    return parent_of


def _governed_graph(
    snapshot,
    instructions: dict,
    evidence: dict,
    session_factory,
    relationships=None,
    *,
    workspace: str | None = None,
) -> dict:
    """The served `domain_graph` for a governed domain: the within-domain projection plus
    the governed `contains` edges to its parent and children (ADR-0031). The hierarchy is
    re-validated whole-estate before any edge is emitted (see `_contains_edges`)."""
    graph = domain_graph(snapshot, instructions, evidence, relationships)
    parent_of = _estate_parent_map(snapshot, session_factory, workspace=workspace)
    hierarchy_nodes, hierarchy_edges = _contains_edges(snapshot.domain, parent_of)
    graph["nodes"].extend(hierarchy_nodes)
    graph["edges"].extend(hierarchy_edges)
    return graph


def domain_graph(snapshot, instructions: dict, evidence: dict, relationships=None) -> dict:
    """A deterministic projection for agent use -- not a graph store and not
    an authority. Every edge comes from an explicit Git declaration or a
    resolved observation; nothing is inferred from name similarity.

    `relationships` are the pre-resolved OBSERVED relationship fragments (#230
    slice 7): they append observed `lineage_to`/`has_glossary_term` edges and
    their target nodes, `evidence: "observation"`, distinct from every governed
    edge. Neither observed name reuses a governed string; the retired governed
    `evidenced_by` is never emitted (ADR-0034 Decision 2)."""
    domain_node = f"domain:{snapshot.domain}"
    nodes = [{"id": domain_node, "kind": "domain", "label": snapshot.domain}]
    edges = []

    for owner in snapshot.owner_refs:
        node = f"owner:{owner['ref']}"
        nodes.append(
            {"id": node, "kind": "owner", "label": owner["ref"], "source": owner["source"]}
        )
        edges.append({"from": node, "to": domain_node, "relation": "owns", "evidence": "git"})

    for definition in instructions["definitions"]:
        node = f"concept:{definition['term']}"
        nodes.append({"id": node, "kind": "concept", "label": definition["term"]})
        edges.append({"from": node, "to": domain_node, "relation": "defined_in", "evidence": "git"})

    for source in instructions["approved_sources"]:
        node = f"source:{source['ref']}"
        nodes.append({"id": node, "kind": "source", "label": source["ref"], "role": source["role"]})
        edges.append(
            {"from": node, "to": domain_node, "relation": "approved_for", "evidence": "git"}
        )
        # A per-source grain (hy-gp99, 284-3) is the grain THIS source is
        # aggregated at, distinct from the domain grain the `constrains` edge
        # above carries. Keyed by source so two sources at the same grain do not
        # collapse to one node, and edged source->grain (`has_grain`) so a caller
        # can read a source's grain without deciding whether it refines or
        # replaces the domain's -- that decision is fork 2 (the 284-4 check).
        grain = (source.get("facets") or {}).get("grain")
        if grain:
            grain_node = f"grain:{source['ref']}:{grain}"
            nodes.append(
                {"id": grain_node, "kind": "grain", "label": grain, "source_ref": source["ref"]}
            )
            edges.append(
                {"from": node, "to": grain_node, "relation": "has_grain", "evidence": "git"}
            )
        # A per-source classification (hy-4giv, 284-6a) is the governed sensitivity
        # label of THIS source, keyed by source so two sources of the same class do
        # not collapse to one node, and edged source->classification
        # (`classified_as`). SURFACE-ONLY: it states the label the Git context
        # declares; it enforces no access or PII rule -- that is 284-9, a
        # resolve-path hook on the enterprise access model.
        classification = (source.get("facets") or {}).get("classification")
        if classification:
            class_node = f"classification:{source['ref']}:{classification}"
            nodes.append(
                {
                    "id": class_node,
                    "kind": "classification",
                    "label": classification,
                    "source_ref": source["ref"],
                }
            )
            edges.append(
                {"from": node, "to": class_node, "relation": "classified_as", "evidence": "git"}
            )
        # A per-source freshness contract (hy-6c8z, 284-6b): cadence and/or
        # max_staleness, carried as fields on one freshness node keyed by source
        # (so two sources with the same contract stay distinct), edged
        # source->freshness (`has_freshness`). SURFACE-ONLY: it states the governed
        # contract; it computes and enforces no staleness -- that is a later check
        # bead. `label` is a deterministic summary of the present fields.
        freshness = (source.get("facets") or {}).get("freshness")
        if freshness:
            fresh_node = f"freshness:{source['ref']}"
            label = "; ".join(f"{key}={freshness[key]}" for key in sorted(freshness))
            nodes.append(
                {
                    "id": fresh_node,
                    "kind": "freshness",
                    "label": label,
                    "source_ref": source["ref"],
                    **freshness,
                }
            )
            edges.append(
                {"from": node, "to": fresh_node, "relation": "has_freshness", "evidence": "git"}
            )
        # A per-source lineage contract (hy-sr7w, 284-7): produced_by and/or
        # upstream, carried as fields on ONE lineage node keyed by source ref only
        # (no trailing value, so it cannot alias a value-keyed node -- hy-c89s),
        # edged source->lineage (`has_lineage`). SURFACE-ONLY: it states the
        # governed provenance contract; it does NOT resolve `upstream` refs to
        # nodes, walk them, detect cycles, or compute reachability -- that is a
        # later check bead. `upstream` rides as a list field, not as edges, so no
        # graph relationship is asserted the Git context did not state directly.
        lineage = (source.get("facets") or {}).get("lineage")
        if lineage:
            lineage_node = f"lineage:{source['ref']}"
            label = "; ".join(f"{key}={lineage[key]}" for key in sorted(lineage))
            nodes.append(
                {
                    "id": lineage_node,
                    "kind": "lineage",
                    "label": label,
                    "source_ref": source["ref"],
                    **lineage,
                }
            )
            edges.append(
                {"from": node, "to": lineage_node, "relation": "has_lineage", "evidence": "git"}
            )
        # A per-source checks contract (hy-w16y, 284-8): the data-quality checks the
        # source asserts it OWNS, carried as a list field on ONE checks node keyed by
        # source ref only (no trailing value -- hy-c89s), edged source->checks
        # (`has_checks`). SURFACE-ONLY: it states the governed contract; it does NOT
        # RUN a check, compute a pass/fail, or derive a status -- that is a later
        # check bead. The checks ride as a list field, so no result is asserted the
        # Git context did not state directly.
        checks = (source.get("facets") or {}).get("checks")
        if checks:
            checks_node = f"checks:{source['ref']}"
            label = "; ".join(check["name"] for check in checks)
            nodes.append(
                {
                    "id": checks_node,
                    "kind": "checks",
                    "label": label,
                    "source_ref": source["ref"],
                    "checks": checks,
                }
            )
            edges.append(
                {"from": node, "to": checks_node, "relation": "has_checks", "evidence": "git"}
            )

    for item in instructions["fields"]:
        node = f"field:{item['name']}"
        nodes.append({"id": node, "kind": "field", "label": item["name"]})
        edges.append(
            {
                "from": node,
                "to": f"source:{item['source_ref']}",
                "relation": "reads",
                "evidence": "git",
            }
        )

    for join in instructions["joins"]:
        node = f"join:{join['from']}->{join['to']}"
        nodes.append({"id": node, "kind": "join", "label": node[5:], "join_type": join["type"]})
        edges.append({"from": node, "to": domain_node, "relation": "constrains", "evidence": "git"})

    if instructions["grain"]:
        node = f"grain:{instructions['grain']}"
        nodes.append({"id": node, "kind": "grain", "label": instructions["grain"]})
        edges.append({"from": node, "to": domain_node, "relation": "constrains", "evidence": "git"})

    for check in instructions["validations"]:
        node = f"check:{check}"
        nodes.append({"id": node, "kind": "check", "label": check})
        edges.append({"from": node, "to": domain_node, "relation": "validates", "evidence": "git"})

    nodes.extend(_observed_nodes(evidence))
    edges.extend(_observed_edges(evidence))
    _append_observed_relationships(nodes, edges, relationships)
    return {"nodes": nodes, "edges": edges}


def _observed_graph(evidence: dict, relationships) -> dict:
    """The observed-only `domain_graph`: provenance nodes/edges plus any observed
    relationship edges. No governed nodes exist here, so a relationship target is
    deduped only against the observed nodes (#230 slice 7, hy-c6vx)."""
    nodes = _observed_nodes(evidence)
    edges = _observed_edges(evidence)
    _append_observed_relationships(nodes, edges, relationships)
    return {"nodes": nodes, "edges": edges}


def _append_observed_relationships(nodes: list[dict], edges: list[dict], relationships) -> None:
    """Append the observed relationship target nodes (deduped against every node
    already in the graph, so a target that coincides with a governed or observed
    source is not emitted twice) and their edges."""
    existing = {node["id"] for node in nodes}
    for node in _observed_relationship_nodes(relationships):
        if node["id"] not in existing:
            nodes.append(node)
            existing.add(node["id"])
    edges.extend(_observed_relationship_edges(relationships))


def _observed_relationship_nodes(relationships) -> list[dict]:
    """One `observed_source` node per distinct relationship target, in first-seen
    order. Reuses the existing observed node kind, so no new node kind is added."""
    nodes: dict[str, dict] = {}
    for relationship in relationships or []:
        node_id = relationship["to"]
        if node_id not in nodes:
            nodes[node_id] = {
                "id": node_id,
                "kind": "observed_source",
                "label": relationship["to_label"],
                "connector": relationship["to_connector"],
            }
    return list(nodes.values())


def _observed_relationship_edges(relationships) -> list[dict]:
    """The observed relationship edges, `evidence: "observation"` -- never `git`."""
    return [
        {
            "from": relationship["from"],
            "to": relationship["to"],
            "relation": relationship["relation"],
            "evidence": "observation",
        }
        for relationship in relationships or []
    ]


def _observed_nodes(evidence: dict) -> list[dict]:
    """Provenance nodes, plus a node for any source only an observation knows
    about. The observed-only source gets its own kind and no edge to the
    domain: Git declared no relationship, so the projection states none."""
    nodes = []
    for observed in evidence["observed_assets"]:
        if observed["governance"] == OBSERVED_ONLY:
            nodes.append(
                {
                    "id": f"source:{observed['ref']}",
                    "kind": "observed_source",
                    "label": observed["ref"],
                    "connector": observed["connector"],
                }
            )
        if observed["observed_version_id"] is None:
            continue
        nodes.append(
            {
                "id": f"observed_version:{observed['observed_version_id']}",
                "kind": "provenance",
                "label": observed["external_id"],
                "connector": observed["connector"],
            }
        )
    return nodes


def _observed_edges(evidence: dict) -> list[dict]:
    return [
        {
            "from": f"source:{observed.get('governed_source_ref') or observed['ref']}",
            "to": f"observed_version:{observed['observed_version_id']}",
            "relation": "observed_as",
            "evidence": "observation",
        }
        for observed in evidence["observed_assets"]
        if observed["observed_version_id"] is not None
    ]


def _bounded(graph: dict, *, root: str | None, max_hops: int | None) -> tuple[dict, list[str]]:
    """Keep the nodes within `max_hops` of the domain node, and the edges
    whose both ends survived. Breadth-first over the projection as an
    undirected graph: hops are distance from the domain, not edge direction,
    because `field -> source -> domain` is two hops out however it is drawn.

    Bounds the projection only. `instructions` are never trimmed -- a budget
    must not be able to make a caveat or a prohibition disappear.
    """
    if max_hops is None:
        return graph, []
    if root is None:
        # An observed-only answer has no domain node to measure from, so
        # there is nothing for hops to bound. Said out loud (hy-hu7): the
        # bundle echoes `max_hops` back in `request.directive`, and a
        # parameter that is accepted, echoed, and quietly inert is a
        # contract lie even when nothing is hidden by it.
        return graph, [
            warning(
                MAX_HOPS_NOT_APPLICABLE,
                f"'max_hops' ({max_hops}) does not apply to this answer: hops are distance "
                f"from a domain node, and no domain was named, so nothing was bounded",
            )
        ]
    neighbours: dict[str, set[str]] = {}
    for edge in graph["edges"]:
        neighbours.setdefault(edge["from"], set()).add(edge["to"])
        neighbours.setdefault(edge["to"], set()).add(edge["from"])

    reachable = {root}
    frontier = deque([(root, 0)])
    while frontier:
        node, depth = frontier.popleft()
        if depth >= max_hops:
            continue
        for other in sorted(neighbours.get(node, ())):
            if other not in reachable:
                reachable.add(other)
                frontier.append((other, depth + 1))

    nodes = [node for node in graph["nodes"] if node["id"] in reachable]
    edges = [
        edge for edge in graph["edges"] if edge["from"] in reachable and edge["to"] in reachable
    ]
    dropped = len(graph["nodes"]) - len(nodes)
    warnings = (
        [
            warning(
                PROJECTION_BOUNDED,
                f"the directive bounded the projection to {max_hops} hop(s) from "
                f"{root!r}: {dropped} node(s) and "
                f"{len(graph['edges']) - len(edges)} edge(s) are not shown. The instructions "
                f"are complete; raise 'max_hops' to see the rest of the graph",
            )
        ]
        if dropped
        else []
    )
    return {"nodes": nodes, "edges": edges}, warnings


def _bundle(
    *,
    request: dict,
    resolution: dict,
    context_authority: dict | None,
    instructions: dict,
    evidence: dict,
    graph: dict,
    provenance_refs: list[str],
    directive: ContextDirective,
    root: str | None,
) -> ContextBundle:
    """Assemble, bound, and -- only if the caller asked for a budget and the
    answer exceeds it -- reduce."""
    graph, hop_warnings = _bounded(graph, root=root, max_hops=directive.max_hops)
    resolution = dict(resolution)
    resolution["warnings"] = [*resolution["warnings"], *hop_warnings]

    def build(evidence: dict, resolution: dict) -> ContextBundle:
        return ContextBundle(
            request=request,
            resolution=resolution,
            context_authority=context_authority,
            instructions=instructions,
            linked_evidence=evidence,
            domain_graph=graph,
            provenance_refs=provenance_refs,
            resolved_at=utcnow(),
        )

    bundle = build(evidence, resolution)
    budget = directive.context_budget
    if budget is None or len(bundle.to_json().encode()) <= budget:
        return bundle

    trimmed, reduced = _within_budget(evidence, resolution, budget=budget, served=bundle)
    bundle = build(trimmed, reduced)
    if len(bundle.to_json().encode()) <= budget:
        return bundle
    # The governed part alone is over the ceiling. It is served anyway, and
    # the caller is told: a bundle that came in under budget by dropping an
    # instruction would be the failure this whole system exists to prevent,
    # and a caller that cannot tell it went over cannot plan around it.
    reduced = dict(reduced)
    reduced["warnings"] = [
        *reduced["warnings"],
        warning(
            OVER_CONTEXT_BUDGET,
            "this answer is still over the 'context_budget' with the payloads already "
            "omitted: governed instructions, provenance, findings, conflicts, freshness "
            "and uncorroborated refs are never dropped to fit a budget. Raise the budget, "
            "or name fewer refs in the directive",
        ),
    ]
    return build(trimmed, reduced)


def _within_budget(evidence: dict, resolution: dict, *, budget: int, served: ContextBundle):
    """One reduction, and it is always the same one: the raw normalized
    payload of each observed asset, by far the largest part of a bundle and
    the only part that is a copy of something the caller can fetch from the
    source by ref.

    Governed instructions, provenance, findings, conflicts, freshness and
    uncorroborated refs stay whole at any budget -- they are what makes the
    answer safe to act on. If they alone exceed the budget the bundle is
    served over it and says so, because a bundle silently missing its warnings
    is the failure this whole system exists to prevent.
    """
    trimmed = dict(evidence)
    trimmed["observed_assets"] = [
        {**asset, "normalized": {}} for asset in evidence["observed_assets"]
    ]
    reduced = dict(resolution)
    reduced["warnings"] = [
        *resolution["warnings"],
        warning(
            OBSERVED_PAYLOADS_OMITTED,
            f"the answer was {len(served.to_json().encode())} bytes against a "
            f"'context_budget' of {budget}: the observed payloads are omitted, and every "
            f"ref, version, finding, and instruction is intact. Fetch a payload by ref, or "
            f"raise the budget",
        ),
    ]
    return trimmed, reduced


def _evidence_provenance(evidence: dict) -> list[str]:
    refs = [
        f"observed_version:{observed['observed_version_id']}"
        for observed in evidence["observed_assets"]
        if observed["observed_version_id"]
    ]
    refs.extend(f"finding:{finding['finding_id']}" for finding in evidence["findings"])
    return refs


def _has_unreviewed_derived(projection: dict | None) -> bool:
    """Whether an adapter DERIVED a field that no human reviewed (283-6, hy-v5iy).

    Reads `resolution.projection.fields_derived` (283-5): each entry is a field the
    ADAPTER authored -- never a governed Git field -- and it is reviewed when it
    carries a non-empty `reviewed_by`. An entry with no reviewed_by, a blank one,
    or a malformed (non-mapping) entry is UNREVIEWED, so this degrades the domain
    governed->mixed (`_governed`). Fail toward degrading: an entry this cannot read
    as reviewed is treated as unreviewed, because an unattributed derived field is
    exactly what must not read as governed. A non-adapter snapshot (no projection)
    and the empty `fields_derived` every adapter serves today both return False, so
    no domain served now is downgraded."""
    if not projection:
        return False
    for entry in projection.get("fields_derived", []):
        reviewed = entry.get("reviewed_by") if isinstance(entry, dict) else None
        if not (isinstance(reviewed, str) and reviewed.strip()):
            return True
    return False


def _evidence_summary(evidence: dict) -> str:
    assets = len(evidence["observed_assets"])
    findings = len(evidence["findings"])
    text = f"{assets} observed source version(s)"
    if findings:
        text += f" and {findings} open finding(s)"
    return text


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None
