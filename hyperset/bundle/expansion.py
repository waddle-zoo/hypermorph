"""Governed progressive expansion (#230 slice 4, hy-fgga).

A bounded, traceable NAVIGATION operation: from one resolved governed domain, follow
the EXACT governed `contains` edges the estate declares into the related domains, and
return which domains are reachable and the governed edges among them. It is a way to
SEE where to look next -- candidate related domains -- and it is NOT a governed answer:
it assembles no `ContextBundle`, carries no `context_authority`, no `instructions`, and
no evidence, composes nothing (that is slice 5), and every domain it names must still be
resolved with `resolve_analytics_context` to obtain governed meaning.

Scope of this slice, stated so a reader does not over-trust it:
- It follows `contains` edges ONLY. The `depends_on`/`joinable_on` relationship edges are
  DEFINED (ADR-0034) but not yet emitted (slice 2b, hy-g5u3, ratification-gated), so they
  cannot be traversed yet; the reachable set widens additively when that emit lands.
- What it DISCLOSES: `expansion_bounded` when a `max_hops`/`max_components` cap dropped
  part of the reachable graph; `expansion_over_context_budget` when the byte budget shrank
  the graph (the far domains are DROPPED to fit, not returned over-budget); and
  `expansion_domain_unavailable` when the estate DECLARES a neighbour of a reached domain
  that is not currently governed (disabled or unsynced). An unavailable neighbour is
  surfaced with `available: false` and its reason, never traversed through and never
  allowed to hide a valid governed sibling.
- What it does NOT disclose: per-domain STALE or CONFLICTING state. That needs each
  domain's evidence resolved, which is the composition slice (slice 5). So the ABSENCE of
  a staleness/conflict warning here means "this op does not check it", NOT "fresh and
  non-conflicting". Do not read the absence as an assurance.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from hyperset.bundle.instructions import concept_terms, git_instructions
from hyperset.bundle.schema import SCHEMA_VERSION
from hyperset.context.hierarchy import contains_edge, expand, unverified_domains
from hyperset.db.base import utcnow
from hyperset.repositories.postgres import PostgresContextRepository
from hyperset.security.redaction import redact_pointer

# The one honest label on every expansion result: this is NAVIGATION over governed edges,
# not a governed answer (the Mayor's slice-4 guardrail 1). A consumer or governance check
# reads `result_kind` and never mistakes an expansion for a resolve bundle.
NAVIGATION = "navigation"

# This op's OWN disclosure vocabulary, deliberately separate from the bundle's
# `WARNING_CODES`: an expansion is not a `ContextBundle`, and mixing its navigation
# disclosures into the served answer vocabulary would blur the two surfaces. `BOUNDED`
# carries `hierarchy.expand`'s truncation messages; `OVER_BUDGET` discloses that the byte
# budget shrank the reachable graph; `UNAVAILABLE` names a domain the estate DECLARES as a
# neighbour of a reached domain but which is not currently governed; the two START codes
# refuse an unknown or uncovered start.
EXPANSION_BOUNDED = "expansion_bounded"
EXPANSION_START_UNKNOWN = "expansion_start_unknown"
EXPANSION_START_UNCOVERED = "expansion_start_uncovered"
EXPANSION_UNAVAILABLE = "expansion_domain_unavailable"
EXPANSION_OVER_BUDGET = "expansion_over_context_budget"
# A domain the estate governs but THIS caller is not authorized to see (hy-l93sc slice 1).
# It is EXCLUDED from the walk and DISCLOSED with this code -- never traversed, never
# pointer- or content-bearing -- so an ACL denial is surfaced (a hole the caller can see
# is there) without leaking the domain's protected content. The denial is fail-closed:
# authorization is decided BEFORE any snapshot content or pointer is read.
EXPANSION_ACL_EXCLUDED = "expansion_acl_excluded"
MAX_EXCLUDED_DISCLOSURES = 50
# Server-side ceilings keep an omitted bound useful without allowing one MCP call to
# materialize an unbounded graph. Callers may choose smaller limits, never larger ones.
DEFAULT_MAX_HOPS = 6
MAX_MAX_HOPS = 32
DEFAULT_MAX_COMPONENTS = 100
MAX_MAX_COMPONENTS = 1000
DEFAULT_CONTEXT_BUDGET = 256_000
MAX_CONTEXT_BUDGET = 2_000_000
# A navigation response has a fixed request/result envelope even when no graph node fits.
# Refuse a smaller budget up front so the fail-closed fallback can never exceed the caller's
# declared byte budget.
MIN_CONTEXT_BUDGET = 512
EXPANSION_WARNING_CODES = (
    EXPANSION_BOUNDED,
    EXPANSION_START_UNKNOWN,
    EXPANSION_START_UNCOVERED,
    EXPANSION_UNAVAILABLE,
    EXPANSION_OVER_BUDGET,
    EXPANSION_ACL_EXCLUDED,
)

# The synthetic HIVE-MIND ROOT (hy-l93sc slice 1, Overseer directive hq-wisp-1d9imq5).
# NAVIGATION ONLY: the root is GENERATED per request from the catalog, never stored, never
# Git authority. Its edges to the top-level domains are marked `evidence: "system"` (catalog-
# derived) -- deliberately NOT `evidence: "git"` -- so a consumer can never mistake a
# navigation link for a governed containment declaration (ADR 0012 boundary: relevance may
# choose WHERE to look, it may not create authority). `result_kind` stays "navigation" and no
# `context_authority` is ever attached, exactly as the exact-node expansion.
ROOT_KIND = "hive_mind_root"
CATALOG_CONTAINS = "catalog_contains"
EVIDENCE_SYSTEM = "system"


def root_node_id(workspace: str | None) -> str:
    """The workspace-scoped synthetic root node id. Scoped by workspace so one tenant's
    root can never link another's domains (the deterministic-bundle_id lesson: scope by
    workspace). A `None` workspace (an internal/system caller) uses the bare `root:` id."""
    return f"root:{workspace or ''}"


def _root_edge(workspace: str | None, child_domain: str) -> dict:
    """A root->domain navigation edge: catalog-derived, NEVER `evidence: "git"`."""
    return {
        "from": root_node_id(workspace),
        "to": f"domain:{child_domain}",
        "relation": CATALOG_CONTAINS,
        "evidence": EVIDENCE_SYSTEM,
    }


def _pointers(*, source_id, repository, snapshot_id, commit_sha, normalized) -> dict:
    """POINTERS to a governed domain's documents/sources -- ids/paths/refs, NEVER inlined
    content (hy-l93sc slice 1). The caller walks STRUCTURE here, then calls search_knowledge
    (grep) or resolve on these pointers to read content. Retains per-source identity
    (`source_id`/`repository`) so a multi-repository estate stays distinguishable.

    The `repository` pointer is a STORED value that may be a credential-bearing Git URL
    (`https://user:token@host/repo`), so it is userinfo-redacted at this serve boundary with
    the ONE canonical detector -- defense-in-depth, the same rule the admin surfaces apply to
    a stored repository/last_error (hy-l93sc round 1, #511 bounce)."""
    instructions = git_instructions(normalized)
    return {
        "source_id": source_id,
        "repository": redact_pointer(repository) if repository else repository,
        "snapshot_id": snapshot_id,
        "commit_sha": commit_sha,
        "context_doc": (normalized.get("documents", {}).get("context_doc") or {}).get("path"),
        "approved_sources": [
            entry["ref"] for entry in instructions.get("approved_sources", []) if entry.get("ref")
        ],
    }


def _warn(code: str, message: str) -> dict:
    """One expansion disclosure, checked against this op's own vocabulary (the `warning`
    discipline, but for the navigation surface, not the bundle's `WARNING_CODES`)."""
    if code not in EXPANSION_WARNING_CODES:
        raise ValueError(f"unknown expansion warning code {code!r}")
    return {"code": code, "message": message}


@dataclass
class ExpansionResult:
    """The served shape of `expand_analytics_context`. Deliberately NOT a `ContextBundle`:
    it carries `result_kind: "navigation"` and no governed sections, so it can never be
    read as a governed answer (slice-4 guardrail 1)."""

    request: dict
    start: str | None
    domains: list[dict]
    edges: list[dict]
    warnings: list[dict]
    resolved_at: datetime
    result_kind: str = NAVIGATION
    schema_version: int = SCHEMA_VERSION
    # The synthetic root node, present ONLY on a walk that started from the root
    # (hy-l93sc slice 1). Absent-unless-rooted keeps the exact-node expansion output
    # byte-identical to before this slice (present-when-true disclosure discipline).
    root: dict | None = None

    def to_dict(self) -> dict:
        payload = {
            "result_kind": self.result_kind,
            "schema_version": self.schema_version,
            "request": self.request,
            "start": self.start,
            "domains": self.domains,
            "edges": self.edges,
            "warnings": self.warnings,
            "resolved_at": self.resolved_at.isoformat(),
        }
        if self.root is not None:
            payload["root"] = self.root
        return payload


def _slug(node_id: str) -> str:
    return node_id[len("domain:") :] if node_id.startswith("domain:") else node_id


def _serialized(result: ExpansionResult) -> bytes:
    return json.dumps(result.to_dict(), sort_keys=False, default=str).encode()


def _effective_bound(value: int | None, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    return min(value, maximum)


def _candidate_parent(candidate, repository) -> str | None:
    """Read hierarchy metadata without falling back to content for real candidates.

    ``parent`` is selected from JSONB metadata by ``list_source_candidates``. The
    compatibility fallback is only for injected repositories written before that field
    existed.
    """
    if hasattr(candidate, "parent"):
        return candidate.parent
    if candidate.current_snapshot_id is None:
        return None
    return repository.get_snapshot(candidate.current_snapshot_id).normalized.get("parent")


def expand_analytics_context(
    *,
    query: str,
    domain: str,
    concepts: list[str],
    session_factory,
    max_hops: int | None = None,
    max_components: int | None = None,
    context_budget: int | None = None,
    workspace: str | None = None,
    authorize_domain=None,
) -> ExpansionResult:
    """Bounded governed navigation from one domain over `contains` edges.

    The start `domain` must be a governed domain (an enabled source with a current
    snapshot) that declares the claimed `concepts` -- the same coverage bar `resolve`
    applies, so an expansion never starts from a domain the caller has not established is
    governed. From there the whole estate's verified `contains` forest is walked
    breadth-first, bounded by `max_hops`/`max_components`/`context_budget`, cycle- and
    duplicate-safe, and every returned edge keeps its `evidence: "git"` provenance.
    """
    authorize_domain = authorize_domain or (lambda _domain: True)
    max_hops = _effective_bound(max_hops, default=DEFAULT_MAX_HOPS, maximum=MAX_MAX_HOPS)
    max_components = _effective_bound(
        max_components, default=DEFAULT_MAX_COMPONENTS, maximum=MAX_MAX_COMPONENTS
    )
    context_budget = _effective_bound(
        context_budget, default=DEFAULT_CONTEXT_BUDGET, maximum=MAX_CONTEXT_BUDGET
    )
    if context_budget < MIN_CONTEXT_BUDGET:
        raise ValueError(
            f"context_budget must be at least {MIN_CONTEXT_BUDGET} bytes for a navigation result"
        )
    request = {
        "query": query,
        "start": domain,
        "concepts": list(concepts),
        "max_hops": max_hops,
        "max_components": max_components,
        "context_budget": context_budget,
    }
    start = domain.strip().casefold()

    # AUTHORIZE-BEFORE-CONTENT (hy-l93sc round 1, #511 bounce): classify over METADATA
    # (`list_source_candidates`, no snapshot `files`), then load a snapshot's content ONLY for
    # a domain the caller is authorized to see. A denied domain's `.files` is never queried --
    # the same hy-r0szz discipline the grep search uses, now on the expansion path too.
    repository = PostgresContextRepository(session_factory)
    candidates = list(repository.list_source_candidates(workspace=workspace))
    governed_meta = {
        candidate.domain: candidate
        for candidate in candidates
        if candidate.enabled and candidate.current_snapshot_id is not None and candidate.domain
    }

    # A start that is not governed OR not visible to this caller is a non-disclosing miss: an
    # unauthorized start is refused as `start_unknown` (never confirming the domain exists),
    # and no content is read for it.
    if start not in governed_meta or not authorize_domain(start):
        return _empty(
            request,
            start,
            EXPANSION_START_UNKNOWN,
            f"{start!r} is not a governed domain in this estate; nothing to expand from",
        )

    # Content is fetched by id ONLY for the governed domains the caller is authorized for.
    visible = {domain: c for domain, c in governed_meta.items() if authorize_domain(domain)}

    def _load_normalized(slugs):
        return {
            slug: repository.get_snapshot(visible[slug].current_snapshot_id).normalized
            for slug in slugs
        }

    declared = set(concept_terms(git_instructions(_load_normalized([start])[start])))
    missing = sorted(set(concepts) - declared)
    if missing:
        return _empty(
            request,
            start,
            EXPANSION_START_UNCOVERED,
            f"the {start!r} domain does not declare {', '.join(repr(m) for m in missing)}; "
            f"expansion starts only from a domain that covers the claimed concepts",
        )

    # The verified `contains` forest among the VISIBLE domains only: an edge is followed only
    # when NEITHER endpoint is on a dangling or cyclic chain AND both are visible, so the walk
    # never traverses to a domain that is not governed OR not authorized (a denied domain is
    # structurally absent -- fail-closed and non-disclosing, its content never read).
    parent_of = {
        domain: _candidate_parent(candidate, repository) for domain, candidate in visible.items()
    }
    unverified = unverified_domains(parent_of)
    edges = [
        contains_edge(parent, child)
        for child, parent in sorted(parent_of.items())
        if parent is not None
        and parent in visible
        and child not in unverified
        and parent not in unverified
    ]

    def _assemble(reached, contains, bound_msgs, budget_dropped):
        warnings = [_warn(EXPANSION_BOUNDED, message) for message in bound_msgs]
        normalized_of = _load_normalized([_slug(node_id) for node_id in reached])
        # Every reached node is in `visible` (the forest is built among visible domains only,
        # so a denied domain is never reached), so each carries pointers from content already
        # authorized and loaded -- no denied content is read here.
        reached_slugs = [_slug(node_id) for node_id in reached]
        reached_set = set(reached_slugs)
        domains = [
            {
                "domain": slug,
                "available": True,
                "pointers": _pointers(
                    source_id=visible[slug].id,
                    repository=visible[slug].repository,
                    snapshot_id=visible[slug].current_snapshot_id,
                    commit_sha=visible[slug].commit_sha,
                    normalized=normalized_of[slug],
                ),
            }
            for slug in reached_slugs
        ]
        contains = [
            edge
            for edge in contains
            if _slug(edge["from"]) in reached_set and _slug(edge["to"]) in reached_set
        ]
        # A domain the estate DECLARES as a neighbour of a reached governed domain but which
        # is NOT itself governed (disabled or unsynced) is DISCLOSED, never traversed and
        # never allowed to hide a valid governed sibling: it appears with `available: false`
        # and its own disclosure saying why (the Mayor's Fork-4 requirement). Restricted to
        # neighbours the caller is AUTHORIZED for, so a denied domain's content is never read.
        for slug, reason in _unavailable_neighbours(
            reached_set, visible, normalized_of, candidates, authorize_domain, repository
        ):
            domains.append({"domain": slug, "available": False})
            warnings.append(_warn(EXPANSION_UNAVAILABLE, f"{slug!r} {reason}"))
        if budget_dropped:
            warnings.append(
                _warn(
                    EXPANSION_OVER_BUDGET,
                    f"the 'context_budget' dropped {budget_dropped} related domain(s) from this "
                    f"expansion, farthest first; raise the budget or lower 'max_hops' to see them",
                )
            )
        return ExpansionResult(
            request=request,
            start=start,
            domains=domains,
            edges=contains,
            warnings=warnings,
            resolved_at=utcnow(),
        )

    reached, contains, bound_msgs = expand(
        start, edges, max_hops=max_hops, max_components=max_components
    )
    result = _assemble(reached, contains, bound_msgs, 0)
    if context_budget is None:
        return result

    # ENFORCE the byte budget BEFORE returning: shrink the reachable BREADTH (drop the
    # farthest governed domains) until the serialized result FITS, disclosing the drop.
    full = len(reached)
    if len(_serialized(result)) <= context_budget:
        return result

    # The full graph did not fit. Find the largest fitting breadth with logarithmic
    # reassembly rather than dropping one component at a time (which would repeatedly
    # reload the estate and turn a tiny budget into quadratic work).
    low, high = 1, full - 1
    fitting: ExpansionResult | None = None
    while low <= high:
        components = (low + high) // 2
        candidate_reached, candidate_contains, candidate_bound_msgs = expand(
            start, edges, max_hops=max_hops, max_components=components
        )
        candidate = _assemble(
            candidate_reached,
            candidate_contains,
            candidate_bound_msgs,
            full - len(candidate_reached),
        )
        if len(_serialized(candidate)) <= context_budget:
            fitting = candidate
            low = components + 1
        else:
            high = components - 1
    if fitting is None:
        # FAIL CLOSED: even the minimum -- the start domain plus its required disclosures --
        # does not fit. No navigation graph is returned; an over-budget graph is never a
        # SUCCESS. The refusal carries empty domains/edges and the over-budget code, the
        # same non-success shape as an unknown or uncovered start, so a caller can never
        # read a populated result that exceeds the budget it set.
        return _empty(
            request,
            start,
            EXPANSION_OVER_BUDGET,
            f"the start domain alone does not fit the 'context_budget' of {context_budget}; "
            f"raise the budget to navigate from here",
        )
    return fitting


def expand_from_root(
    *,
    query: str,
    session_factory,
    max_hops: int | None = None,
    max_components: int | None = None,
    context_budget: int | None = None,
    workspace: str | None = None,
    authorize_domain=None,
    repository=None,
) -> ExpansionResult:
    """Enter at the synthetic HIVE-MIND ROOT and walk DOWN, bounded, WITHOUT already knowing
    an exact domain (hy-l93sc slice 1, Overseer directive). The root is generated per request
    from the catalog and links the ENABLED + CURRENT + ACL-VISIBLE top-level domains; from
    each it follows the governed `contains` forest downward. Every reached domain carries
    document POINTERS (never content); every domain EXCLUDED because it is disabled, unsynced,
    or ACL-denied is DISCLOSED with a reason (never silently dropped, never pointer-bearing).

    Authorize-BEFORE-content: classification and the ACL decision run over METADATA
    (`list_source_candidates`, no snapshot files); a domain's snapshot content is loaded ONLY
    after it passes `authorize_domain`, so a denied domain's bytes never leave the database
    (the hy-r0szz discipline). `authorize_domain(domain) -> bool` is injected by the transport
    from the caller's principal/roles; `None` means the authz gate is off and every domain is
    visible (behaviour-preserving default).
    """
    authorize_domain = authorize_domain or (lambda _domain: True)
    max_hops = _effective_bound(max_hops, default=DEFAULT_MAX_HOPS, maximum=MAX_MAX_HOPS)
    max_components = _effective_bound(
        max_components, default=DEFAULT_MAX_COMPONENTS, maximum=MAX_MAX_COMPONENTS
    )
    context_budget = _effective_bound(
        context_budget, default=DEFAULT_CONTEXT_BUDGET, maximum=MAX_CONTEXT_BUDGET
    )
    if context_budget < MIN_CONTEXT_BUDGET:
        raise ValueError(
            f"context_budget must be at least {MIN_CONTEXT_BUDGET} bytes for a navigation result"
        )
    request = {
        "query": query,
        "start": None,
        "from_root": True,
        "max_hops": max_hops,
        "max_components": max_components,
        "context_budget": context_budget,
    }
    repository = repository or PostgresContextRepository(session_factory)
    candidates = list(repository.list_source_candidates(workspace=workspace))
    root_id = root_node_id(workspace)
    root_node = {"id": root_id, "kind": ROOT_KIND, "workspace": workspace}

    # Classify over METADATA only (no content read yet). A domain is VISIBLE only when its
    # source is enabled, has a current snapshot, AND the caller is authorized for it; every
    # other case is disclosed-excluded with its reason.
    visible: dict[str, object] = {}  # domain slug -> candidate (authorized + governed)
    excluded: list[tuple[str, str, str]] = []  # (name, exclusion, reason)
    for candidate in sorted(candidates, key=lambda c: c.domain or c.id):
        domain = candidate.domain
        if not candidate.enabled:
            excluded.append(
                (domain or candidate.id, "disabled", "is disabled; resolve will not serve it")
            )
            continue
        if candidate.current_snapshot_id is None or domain is None:
            excluded.append(
                (
                    domain or candidate.id,
                    "unsynced",
                    "has no current snapshot (never synced or the sync failed); resolve will "
                    "not serve it",
                )
            )
            continue
        if not authorize_domain(domain):
            # ACL-excluded: decided from metadata, BEFORE any snapshot content is read, so a
            # denied domain contributes no pointer and no byte of content.
            excluded.append((domain, "acl", "is not visible to this caller"))
            continue
        visible[domain] = candidate

    # Parent metadata is selected without loading snapshot files. Content is loaded only for
    # domains that the bounded walk actually returns, below.
    parent_of = {
        domain: _candidate_parent(candidate, repository) for domain, candidate in visible.items()
    }
    # A domain is a ROOT CHILD when it has no governed-visible parent (a true root, or a
    # domain whose declared parent is not visible to this caller/estate): it must still be
    # reachable from the root, never orphaned behind an invisible ancestor.
    root_children = sorted(
        domain for domain, parent in parent_of.items() if parent is None or parent not in visible
    )
    # The governed `contains` forest among VISIBLE domains only (evidence: git), plus the
    # system/catalog-derived root->child edges (evidence: system).
    contains_edges = [
        contains_edge(parent, child)
        for child, parent in sorted(parent_of.items())
        if parent is not None and parent in visible
    ]

    def _assemble(
        reached: list[str], bound_msg: str | None, budget_dropped: int
    ) -> ExpansionResult:
        reached_set = set(reached)
        normalized_of = {
            domain: repository.get_snapshot(visible[domain].current_snapshot_id).normalized
            for domain in reached
        }
        domains = [
            {
                "domain": domain,
                "available": True,
                "pointers": _pointers(
                    source_id=visible[domain].id,
                    repository=visible[domain].repository,
                    snapshot_id=visible[domain].current_snapshot_id,
                    commit_sha=visible[domain].commit_sha,
                    normalized=normalized_of[domain],
                ),
            }
            for domain in reached
        ]
        warnings: list[dict] = []
        if bound_msg:
            warnings.append(_warn(EXPANSION_BOUNDED, bound_msg))
        # Exclusions are useful governance disclosure, but a large estate must not turn a
        # root walk into an unbounded dump of every disabled/unsynced/denied source. Keep the
        # disclosure bounded alongside reachable graph components. `max_components` is the
        # caller's explicit response budget; without it, use a small deterministic cap.
        excluded_limit = min(
            MAX_EXCLUDED_DISCLOSURES,
            max(0, max_components - len(reached))
            if max_components is not None
            else MAX_EXCLUDED_DISCLOSURES,
        )
        for name, exclusion, reason in excluded[:excluded_limit]:
            code = EXPANSION_ACL_EXCLUDED if exclusion == "acl" else EXPANSION_UNAVAILABLE
            domains.append({"domain": name, "available": False, "exclusion": exclusion})
            warnings.append(_warn(code, f"{name!r} {reason}"))
        omitted_excluded = len(excluded) - min(len(excluded), excluded_limit)
        if omitted_excluded:
            warnings.append(
                _warn(
                    EXPANSION_BOUNDED,
                    f"the root walk omitted {omitted_excluded} unavailable domain disclosure(s); "
                    "raise 'max_components' to inspect more",
                )
            )
        if budget_dropped:
            warnings.append(
                _warn(
                    EXPANSION_OVER_BUDGET,
                    f"the 'context_budget' dropped {budget_dropped} domain(s) from this walk, "
                    f"farthest first; raise the budget or lower 'max_hops' to see them",
                )
            )
        edges = [_root_edge(workspace, domain) for domain in root_children if domain in reached_set]
        edges += [
            edge
            for edge in contains_edges
            if _slug(edge["from"]) in reached_set and _slug(edge["to"]) in reached_set
        ]
        return ExpansionResult(
            request=request,
            start=root_id,
            domains=domains,
            edges=edges,
            warnings=warnings,
            resolved_at=utcnow(),
            root=root_node,
        )

    reached, bound_msg = _walk_from_root(
        root_children, parent_of, visible, max_hops=max_hops, max_components=max_components
    )
    result = _assemble(reached, bound_msg, 0)
    if context_budget is None:
        return result
    if len(_serialized(result)) <= context_budget:
        return result

    # The full graph did not fit. Find the largest fitting breadth with logarithmic
    # reassembly rather than dropping one component at a time (which would repeatedly
    # reload the whole estate and turn a tiny budget into quadratic work).
    full = len(reached)
    low, high = 1, full - 1
    fitting: ExpansionResult | None = None
    while low <= high:
        components = (low + high) // 2
        candidate_reached, candidate_bound_msg = _walk_from_root(
            root_children, parent_of, visible, max_hops=max_hops, max_components=components
        )
        candidate = _assemble(
            candidate_reached,
            candidate_bound_msg,
            full - len(candidate_reached),
        )
        if len(_serialized(candidate)) <= context_budget:
            fitting = candidate
            low = components + 1
        else:
            high = components - 1
    if fitting is None:
        # FAIL CLOSED: even the minimum walk does not fit; return no navigation graph, the
        # same non-success shape as an over-budget exact-node expansion.
        return _empty(
            request,
            root_id,
            EXPANSION_OVER_BUDGET,
            f"the root walk does not fit the 'context_budget' of {context_budget}; raise it",
        )
    return fitting


def _walk_from_root(
    root_children: list[str],
    parent_of: dict[str, str | None],
    visible: dict,
    *,
    max_hops: int | None,
    max_components: int | None,
) -> tuple[list[str], str | None]:
    """A bounded, cycle-safe breadth-first walk DOWN from the synthetic root over the
    VISIBLE governed `contains` forest. Depth 0 is the root; its children (the top-level
    visible domains) are depth 1; their governed descendants deeper. `max_hops` caps depth
    from the root and `max_components` caps how many domains the walk returns. Returns the
    reached domain slugs (breadth order) and a disclosure message when a bound dropped part
    of the forest, or `None` when nothing was dropped."""
    children_of: dict[str | None, list[str]] = {}
    for domain, parent in parent_of.items():
        if parent is not None and parent in visible:
            children_of.setdefault(parent, []).append(domain)

    def _walk(*, hops: int | None, components: int | None) -> list[str]:
        reached: list[str] = []
        seen: set[str] = set()
        # depth 1 == the root's direct children; the synthetic root itself is depth 0.
        frontier = deque((child, 1) for child in root_children)
        while frontier:
            domain, depth = frontier.popleft()
            if domain in seen:
                continue
            if hops is not None and depth > hops:
                continue
            if components is not None and len(reached) >= components:
                break
            seen.add(domain)
            reached.append(domain)
            for grandchild in sorted(children_of.get(domain, ())):
                if grandchild not in seen:
                    frontier.append((grandchild, depth + 1))
        return reached

    reached = _walk(hops=max_hops, components=max_components)
    # Do not materialize an unbounded graph merely to calculate a disclosure
    # count. Served callers install ceilings; one extra slot detects that a
    # ceiling was crossed while keeping the comparison bounded.
    full = _walk(
        hops=max_hops + 1 if max_hops is not None else None,
        components=max_components + 1 if max_components is not None else None,
    )
    dropped = len(full) - len(reached)
    if not dropped:
        return reached, None
    bounds = []
    if max_hops is not None:
        bounds.append(f"{max_hops} hop(s)")
    if max_components is not None:
        bounds.append(f"{max_components} component(s)")
    message = (
        f"the walk from the root was bounded to {' and '.join(bounds) or 'a limit'}: "
        f"{dropped} domain(s) are not shown -- raise the bound to see the rest of the hierarchy"
    )
    return reached, message


def _unavailable_neighbours(
    reached_slugs, visible, normalized_of, candidates, authorize_domain, repository
):
    """The domains the estate DECLARES adjacent (by `contains`) to a reached governed domain
    but which are NOT currently governed (disabled or unsynced) -- surfaced as unavailable,
    never traversed. Two directions: a reached domain's declared PARENT that is not a governed
    domain, and any source that declares a reached domain as ITS parent but is itself not
    governed. AUTHORIZE-BEFORE-CONTENT (hy-l93sc round 1): computed from metadata plus content
    the caller is ALREADY authorized for; a domain the caller cannot see is neither disclosed
    nor has its `.files` read, so a denial never leaks and never touches denied content."""
    # The disabled sources the caller IS authorized for (a disabled source keeps its last
    # snapshot, so it has a domain + fetchable content; an unsynced source has no snapshot and
    # so cannot declare a parent). Only these may have their content read for direction (b).
    authorized_disabled = {
        candidate.domain: candidate
        for candidate in candidates
        if candidate.domain
        and candidate.domain not in visible
        and candidate.current_snapshot_id is not None
        and not candidate.enabled
        and authorize_domain(candidate.domain)
    }
    out: dict[str, str] = {}
    # (a) a reached domain's declared parent that is an authorized-but-disabled source.
    for slug in reached_slugs:
        parent = normalized_of[slug].get("parent")
        if parent and parent in authorized_disabled:
            out[parent] = (
                f"is declared as the parent of {slug!r} but is not a governed domain "
                f"(disabled or unsynced); resolve will not serve it"
            )
    # (b) an authorized-but-disabled source whose declared parent is a reached domain (its
    # content is read only because the caller is authorized for it).
    for domain, candidate in authorized_disabled.items():
        parent = repository.get_snapshot(candidate.current_snapshot_id).normalized.get("parent")
        if parent in reached_slugs:
            out[domain] = (
                f"declares {parent!r} as its parent but is not a governed domain "
                f"(disabled or unsynced); resolve will not serve it"
            )
    return sorted(out.items())


def _empty(request: dict, start: str, code: str, message: str) -> ExpansionResult:
    return ExpansionResult(
        request=request,
        start=start,
        domains=[],
        edges=[],
        warnings=[_warn(code, message)],
        resolved_at=utcnow(),
    )
