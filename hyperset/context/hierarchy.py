"""The domain-hierarchy node/edge shape, its whole-estate validators, and a bounded,
cycle-safe expansion (ADR-0031, hy-53l9).

Two of the three are now on the served path (hy-gh-230 slice 1). `contains_edge`,
`domain_node_id`, and `unverified_domains` (which runs `validate_forest`) are imported by
`hyperset/bundle/resolver.py` and emit the governed `contains` edge into the served
`domain_graph` -- an immediate parent/child level each way -- which took `SCHEMA_VERSION`
to 17. `tools_hash` is unaffected: the graph is served OUTPUT, and the resolve-path input
schema is untouched. `expand` -- the bounded multi-level traversal -- is NOT yet wired to
any transport or resolver; the slice that serves it is a later step of #230.

The relation is GOVERNED containment, declared in Git and never inferred: a domain
names its `parent` by governed slug, and Hyperset derives one parent->child
`contains` edge (`evidence: "git"`). It is depth-agnostic -- the SAME relation carries
domain->subdomain->deeper with no per-level type -- and distinct from any
data-relationship edge (reads / join / lineage).
"""

from __future__ import annotations

from collections import deque

# The one governed hierarchy relation. Distinct from every data-relationship edge
# (`reads`, `constrains`, `has_lineage`, a cross-domain join): containment is not a
# data path and must never be conflated with one (ADR 0021).
CONTAINS = "contains"

# Every relation `contains` must stay distinct from, for the acceptance check.
_DATA_RELATIONS = frozenset(
    {
        "reads",
        "constrains",
        "owns",
        "defined_in",
        "approved_for",
        "has_lineage",
        "join",
        "validates",
    }
)


def domain_node_id(domain: str) -> str:
    """The stable node id for a domain, keyed by the domain SLUG. Two conditions make
    it collision-safe across a hierarchy that spans multiple domains: the slug is the
    globally-unique governed domain identity -- casefolded to one canonical form and
    collision-checked so two contexts cannot both claim it (hy-gh-282) -- and the id
    appends NO value after the slug, so it cannot alias a value-keyed node (the
    hy-c89s form ADR 0029 avoids). The first is what keeps two DIFFERENT domains from
    sharing an id; it rests on the slug being unique, not on the slug being a ref."""
    return f"domain:{domain}"


def contains_edge(parent: str, child: str) -> dict:
    """The governed parent->child containment edge, in the served `domain_graph` edge
    shape. `evidence: "git"` because it is a governed Git declaration -- observed
    evidence never creates a hierarchy edge."""
    return {
        "from": domain_node_id(parent),
        "to": domain_node_id(child),
        "relation": CONTAINS,
        "evidence": "git",
    }


def validate_forest(parent_of: dict[str, str | None]) -> list[str]:
    """The reasons a declared `parent` map is not a governed forest, or `[]` when it
    is. `parent_of` maps each domain to its declared parent slug (or `None` for a
    root); the map shape already enforces at-most-one-parent. Two remaining ways it
    is malformed, both detected deterministically -- never by a name heuristic:

    - a `parent` naming a domain that is not itself declared; and
    - a cycle in the parent chain (a domain reachable from itself by following
      `parent`), which would make containment not a forest.

    The WHOLE-ESTATE check the served EMIT runs (hy-gh-230 slice 1): `unverified_domains`
    calls this before any `contains` edge reaches `domain_graph`, so no dangling or
    transitive-unknown-ancestor edge ever serves. That is the case `validate_domain`
    deliberately does not cover: `validate_domain` is the per-source SYNC check (hy-2zoy),
    scoped to the incoming domain's own DIRECT parent so a pre-existing fault elsewhere
    cannot wedge an unrelated sync -- correct for a write, but insufficient for the emit,
    where a whole-estate forest must hold. The two are complementary, not redundant:
    `validate_domain` gates each WRITE, `validate_forest` (via `unverified_domains`) gates
    the EMIT.
    """
    reasons: list[str] = []
    known = set(parent_of)
    for domain, parent in parent_of.items():
        if parent is not None and parent not in known:
            reasons.append(f"domain {domain!r} names an unknown parent {parent!r}")
    for domain in parent_of:
        seen: set[str] = set()
        cursor: str | None = domain
        while cursor is not None:
            if cursor in seen:
                reasons.append(f"domain {domain!r} is in a parent cycle")
                break
            seen.add(cursor)
            cursor = parent_of.get(cursor)
    return reasons


def validate_domain(domain: str, parent_of: dict[str, str | None]) -> list[str]:
    """The forest reasons attributable to ONE domain within the estate's `parent_of`
    map: its declared parent is unknown, or its own parent chain cycles. This is the
    check a per-source sync runs on the INCOMING manifest -- it must reject a domain
    that names a phantom parent or closes a loop, WITHOUT punishing that sync for a
    pre-existing dangling parent elsewhere in the estate (which `validate_forest`, a
    whole-estate check, would attribute to the innocent syncing source and wedge
    unrelated writes). `domain` is assumed present in `parent_of`."""
    reasons: list[str] = []
    parent = parent_of.get(domain)
    if parent is not None and parent not in parent_of:
        reasons.append(f"domain {domain!r} names an unknown parent {parent!r}")
    seen: set[str] = set()
    cursor: str | None = domain
    while cursor is not None:
        if cursor in seen:
            reasons.append(f"domain {domain!r} is in a parent cycle")
            break
        seen.add(cursor)
        cursor = parent_of.get(cursor)
    return reasons


def unverified_domains(parent_of: dict[str, str | None]) -> set[str]:
    """The domains that MUST NOT have a `contains` edge served for them, given the whole
    estate's `parent_of` map -- the emit-path localizer (hy-gh-230). Empty when the estate
    is a governed forest; otherwise the set of domains whose OWN ancestor chain reaches a
    fault: an unknown parent ANYWHERE up the chain (transitive, not only the direct
    parent) or a cycle.

    Two properties the emit relies on, neither of which `validate_domain` provides:

    - It runs the WHOLE-ESTATE `validate_forest` FIRST (the mayor's #352 requirement), so
      the fast, common path -- every shipped context is a root, a valid forest -- returns
      the empty set without a per-domain walk.
    - It catches a TRANSITIVE-unknown ancestor. `validate_domain` checks only the DIRECT
      parent for existence (it walks the chain solely for cycles), so a domain whose
      parent exists but whose GRANDparent was disabled after a valid sync passes it. That
      domain's `contains` edge would put a dangling ancestor into the served graph; here
      it is flagged and dropped. Blame stays local: a domain on a clean chain is NOT in
      the set just because a fault exists elsewhere (the hy-2zoy no-blackout lesson).
    """
    if not validate_forest(parent_of):
        return set()
    bad: set[str] = set()
    for domain in parent_of:
        seen: set[str] = set()
        cursor: str | None = domain
        while cursor is not None:
            if cursor in seen:  # a cycle anywhere in this domain's chain
                bad.add(domain)
                break
            seen.add(cursor)
            parent = parent_of.get(cursor)
            if parent is not None and parent not in parent_of:  # a transitive-unknown ancestor
                bad.add(domain)
                break
            cursor = parent
    return bad


def expand(
    root: str,
    edges: list[dict],
    *,
    max_hops: int | None = None,
    max_components: int | None = None,
) -> tuple[list[str], list[dict], list[str]]:
    """A bounded, cycle-safe breadth-first governed expansion over `contains` edges
    from `root`, reusing the `_bounded` shape (undirected hops, a `reached` visited
    set). Returns the reachable domain ids (root first, then the order discovered),
    the `contains` edges among them, and disclosure warnings for what a bound dropped.

    Bounded three ways: `max_hops` caps depth from the root, `max_components` caps how
    many domains one expansion may pull in (breadth), and the caller's byte budget
    bounds serialization elsewhere. Cycle-safe by the visited set regardless of
    whether `validate_forest` was run. Only `contains` edges are followed, so no data
    edge and no name similarity can widen the walk.
    """
    root_id = domain_node_id(root)
    neighbours: dict[str, list[str]] = {}
    for edge in edges:
        if edge.get("relation") != CONTAINS:
            continue
        neighbours.setdefault(edge["from"], []).append(edge["to"])
        neighbours.setdefault(edge["to"], []).append(edge["from"])

    def _walk(*, hops: int | None, components: int | None) -> list[str]:
        reached = [root_id]
        seen = {root_id}
        frontier: deque[tuple[str, int]] = deque([(root_id, 0)])
        while frontier:
            node, depth = frontier.popleft()
            if hops is not None and depth >= hops:
                continue
            for other in sorted(set(neighbours.get(node, ()))):
                if other in seen:
                    continue
                if components is not None and len(reached) >= components:
                    return reached
                seen.add(other)
                reached.append(other)
                frontier.append((other, depth + 1))
        return reached

    reached = _walk(hops=max_hops, components=max_components)
    # The comparison walk is itself bounded. Served callers install ceilings;
    # one extra slot detects that a ceiling was crossed without materializing
    # an entire large component just to count it.
    full = _walk(
        hops=max_hops + 1 if max_hops is not None else None,
        components=max_components + 1 if max_components is not None else None,
    )
    kept = set(reached)
    contains = [
        edge
        for edge in edges
        if edge.get("relation") == CONTAINS and edge["from"] in kept and edge["to"] in kept
    ]
    warnings: list[str] = []
    dropped = len(full) - len(reached)
    if dropped:
        bounds = []
        if max_hops is not None:
            bounds.append(f"{max_hops} hop(s)")
        if max_components is not None:
            bounds.append(f"{max_components} component(s)")
        warnings.append(
            f"the expansion from {root_id!r} was bounded to {' and '.join(bounds) or 'a limit'}: "
            f"{dropped} domain(s) are not shown -- raise the bound to see the rest of the hierarchy"
        )
    return reached, contains, warnings
