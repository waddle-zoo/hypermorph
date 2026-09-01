# 0031: The domain hierarchy — a governed `contains` relation on the existing graph, depth-agnostic and bounded

> **END-STATE reading amended by [ADR-0041](0041-the-knowledge-graph-is-flexible-yet-governed-and-improves-through-use.md) (ACCEPTED).** The governed `contains` hierarchy and its declared-never-inferred rule stand UNCHANGED; ADR-0041 only corrects the DESTINATION reading: the domain-only deterministic projection is the governed CORE of a flexible-yet-governed graph that also carries first-class OBSERVED/PROPOSED edges (never canonical until human write-back), not the end state. `declared-never-inferred` governs CANONICAL promotion, not the existence of an observed edge.
>
> **Amended by [ADR-0036](0036-bring-your-own-knowledge-graph-authority-adapters.md) (PROPOSED).** "Declared in the customer's Git manifest" generalizes to "declared in the authority backend, surfaced via the adapter"; the globally-unique collision-checked domain-slug rule generalizes across backends. `evidence:"git"` names a provider-neutral GOVERNED class; `contains` stays declared-never-inferred.

Status: ACCEPTED — approved by Brandon via the Overseer (hq-rqq6, 2026-08-15):
"ADR-0031 hierarchy: domain slug nodes, Git-governed contains edges, parent-by-slug,
unlimited nesting, bounded cycle-safe expansion, disclosed truncation, and Git
provenance." The contract stands as designed. Delivery is SEQUENCED: hy-2zoy wires the
VALIDATION half — a manifest declares an optional governed `parent` slug, and the
parent forest is validated fail-closed at sync (an unknown parent or a cycle is
rejected, the last valid snapshot keeps serving), with `parent` parsed and stored but
NOT surfaced, so `SCHEMA_VERSION` stays 16 and `tools_hash` stays
`sha256:fe930a003b731211`. The follow-on hy-gh-230 EMITS the `contains` edge into the
served `domain_graph` and takes `SCHEMA_VERSION` by merge order, as the paragraph below
says. Originally PROPOSED 2026-08-14 (Brandon ruled #230). The access-model layer of
#230 is ADR-0030 (separate), and cross-domain COMPOSITION is #129 (a later follow-on).

Extends ADR 0012 (the customer Git commit owns domain meaning; a hierarchy edge is a
governed Git declaration, never a Hyperset inference), ADR 0021 (a contradiction is a
join, not a rule — containment and data-relationship edges stay distinct kinds), and
ADR 0029 (the domain_graph node-id invariant: an id carries the ref and never a
value-alias — the hy-c89s class). It adds no served operation and changes no served
shape: the validation-first slice (hy-2zoy) validates a declared `parent` at sync but
emits nothing, and the `contains` edge is specified here but NOT yet emitted into the
served `domain_graph`, so `SCHEMA_VERSION` stays 16 and `tools_hash` stays
`sha256:fe930a003b731211` (the graph is served OUTPUT and the directive INPUT is
untouched regardless). The follow-on slice that EMITS `contains` edges into the
served graph adds a served node/edge shape and MUST take `SCHEMA_VERSION` by merge
order and sweep every served surface — this ADR says so and does not.

## Context

`domain_graph` today is built per snapshot: one governed domain per bundle, with
nodes `domain:{domain}`, `source:{ref}`, `field:{name}`, the facet nodes, and edges
`{from, to, relation, evidence}` (`evidence: "git"` for governed, observed
otherwise). #230 is the graph ABOVE and BETWEEN domains — a domain and its
subdomains, and deeper later. Brandon ruled it a GOVERNED hierarchy: declared in the
customer's Git context, never inferred from names, and kept distinct from the
data-relationship edges (join, lineage) a later composition will add.

## The measured graph model this reuses

- **Node identity is a ref-carrying id.** A domain is `domain:{domain}`
  (`resolver.py`), the domain slug carried in the id with no trailing value — two
  domains are two ids, and the hy-c89s value-alias class cannot arise (ADR 0029).
- **An edge is `{from, to, relation, evidence}`.** Governed edges carry
  `evidence: "git"`; the relation names the kind (`owns`, `reads`, `constrains`,
  `has_lineage`, …). Relations are already many and distinct.
- **Bounded, cycle-safe traversal already exists.** `_bounded` (`resolver.py`) is a
  breadth-first walk over the graph as UNDIRECTED, keeping nodes within `max_hops` of
  a root and the edges whose both ends survived. It is cycle-safe by a `reachable`
  visited set (`if other not in reachable`), it never trims `instructions` (a budget
  must not make a caveat or prohibition disappear), and it emits a `projection_bounded`
  warning when it drops nodes. The hierarchy expansion reuses this exact shape.

## Decisions

### 1. A governed `contains` edge, declared and never inferred

A domain MAY declare a `parent` — the governed slug of the domain that contains it.
Hyperset derives one edge:

    {"from": "domain:{parent}", "to": "domain:{child}", "relation": "contains", "evidence": "git"}

There is NO subdomain node kind and NO subdomain schema type: a subdomain is simply a
`domain:{...}` node that is the `to` of a `contains` edge. The parent is stated by its
governed slug in Git — never guessed from a name prefix, a title, or a ref (Decision
4). A domain that declares no `parent` is a root.

The child declaring its one `parent` (rather than a parent enumerating its children)
is the minimal governed statement: it keeps each domain's manifest self-contained and
lets a subtree grow without editing an ancestor. The `contains` edge is always
derived parent→child so the served direction is uniform.

### 2. Depth-agnostic: no maximum depth, no per-level type

The SAME `contains` relation carries domain→subdomain today and subdomain→deeper
tomorrow. Depth is emergent from chained `contains` edges, not encoded anywhere: a
domain at any level is the same `domain:{...}` node with the same shape, and a deeper
level needs no new relation, node kind, or schema field. The initial UI/API may
expose only a domain-and-its-subdomains affordance, but validation, storage, and
traversal are depth-agnostic by construction — nothing in them counts levels or caps
depth by type.

### 3. Stable, collision-safe node identity

The `contains` edge references the two domains by their slug-keyed ids
(`domain:{parent}`, `domain:{child}`). Collision-safety across a hierarchy that spans
multiple domains rests on TWO conditions, and the load-bearing one is a precondition
this ADR states rather than an appeal to hy-c89s. First, the domain slug is the
globally-unique governed domain identity: it is casefolded to one canonical form and
collision-checked so two contexts cannot both claim it (hy-gh-282). That — not any
"ref-carrying" property — is what keeps two DIFFERENT domains from sharing a
`domain:{slug}` node; a `domain:{slug}` id is value-keyed by the slug, and it is safe
only BECAUSE the slug is unique. Second, the id appends no value after the slug, so it
cannot alias a value-keyed node (the hy-c89s form ADR 0029 avoids). Two same-named
domains cannot arise in a valid store precisely because hy-gh-282 forbids it; an
implementation that ever lets a slug repeat would reintroduce the collision, so the
uniqueness invariant is a hard precondition, not a convenience.

### 4. Distinct from join/lineage; governed vs observed preserved

`contains` is CONTAINMENT and is a distinct relation from every data-relationship
edge — `reads`, `constrains`, a cross-domain join, `has_lineage`. A composition that
relates two domains' DATA (#129) uses its own relation and must never be read as
containment, nor containment as a data path. A hierarchy edge is ALWAYS
`evidence: "git"`: it is a governed declaration, and observed evidence never creates
one. The governed/observed split the graph already carries is preserved unchanged.

### 5. Cycle-safe in two independent layers

- **Validation (the governed relation is a forest).** Each domain has at most one
  `parent`, and the `parent` chain must not cycle. A declared cycle (A parent B, B
  parent A; or any longer loop) and a `parent` naming an unknown domain are hard
  errors at parse — detected deterministically by walking the parent chain to a
  repeat, never by a heuristic.
- **Traversal (defence in depth).** The bounded expansion carries a visited set (the
  `_bounded` `reachable` shape), so even a legacy or malformed cycle that reached
  storage cannot loop the walk. Cycle-safety does not depend on validation alone.

### 6. Bounded, provenance-preserving expansion

A governed hierarchy expansion traverses `contains` edges from a root domain
breadth-first, bounded by every one of:

- `max_hops` — depth from the root (the existing directive bound);
- `context_budget` — the serialized byte budget (the existing bundle bound); and
- a new `max_components` cap — the maximum number of domains one expansion may pull
  in, so a wide forest cannot blow the packet by breadth rather than depth.

A bound that drops a domain emits a disclosure warning (the `projection_bounded`
shape), never a silent truncation. Every visited domain node and every `contains`
edge keeps its provenance — `evidence: "git"` and the per-domain snapshot commit —
and the expansion never trims `instructions`, exactly as `_bounded` does not.

## The node/edge shape and expansion, concretely

The typed shapes live in `hyperset/context/hierarchy.py`. `validate_domain` is WIRED at
sync as of hy-2zoy (see "What has landed" below); `contains_edge`/`expand`/
`validate_forest` remain unwired by any SERVED path until the emit follow-on (hy-gh-230),
so the graph shape and the acceptance list are reviewable and executable without changing
the served graph today:

- `contains_edge(parent, child)` → the edge dict above (emitted by hy-gh-230).
- `domain_node_id(domain)` → `domain:{domain}` (the ref-carrying id).
- `validate_domain(domain, parent_of)` → the reasons the ONE named domain's declared
  parent is unknown or its own chain cycles. WIRED at sync (hy-2zoy): it rejects the
  incoming manifest without punishing it for a pre-existing dangling parent elsewhere.
- `validate_forest(parent_of)` → the reasons a WHOLE declared `parent` map is not a
  forest (unknown parent, a cycle; at most one parent is enforced by the map shape). Its
  concrete future caller is the emit slice (hy-gh-230), which MUST run it over the whole
  estate before serving any `contains` edge, so no dangling or transitive-unknown-ancestor
  edge ever reaches the graph — the case `validate_domain` deliberately does not cover.
- `expand(root, edges, *, max_hops, max_components)` → the reachable domain ids, the
  `contains` edges among them, and the bound warnings; cycle-safe and bounded (hy-gh-230).

## What has landed, and what is deferred

The design (this ADR) returned the typed `contains` node/edge shape, the bounded
cycle-safe `expand`/`validate_forest` primitives, and the acceptance-test list below.
Brandon ratified it (Overseer hq-rqq6, 2026-08-15).

Landed in hy-2zoy (the validation-first slice): the manifest `parent` field IS built —
it is in `SUPPORTED_MANIFEST_FIELDS`, on `ContextDocument.parent`, parsed and casefolded
by `parse_context` with a self-parent rejected at parse — and the parent forest is
validated fail-closed at SYNC via `validate_domain`. An unknown-parent or cyclic manifest
is now REJECTED at sync (the last valid snapshot keeps serving), so the sync BEHAVIOUR
changed. The served bundle SHAPE did not: `parent` is manifest input, validated and
stored but never surfaced, so `SCHEMA_VERSION` stays 16 and `tools_hash` stays
`sha256:fe930a003b731211`.

Deferred to later slices: EMITTING `contains` edges into the served `domain_graph`
(hy-gh-230 — an SV move, taken by merge order then, which MUST run `validate_forest`
whole-estate before serving any edge), the multi-snapshot cross-domain resolve that
assembles a subtree, the #129 composition of two domains' data, and the ADR-0030 access
model that governs who may traverse.

## Acceptance tests

1. **Governed edge shape.** `contains_edge(p, c)` is exactly
   `{"from": "domain:p", "to": "domain:c", "relation": "contains", "evidence": "git"}`
   — a governed edge, distinct relation, ref-carrying ids.
2. **Depth-agnostic.** A three-level chain (a→b→c) expands with the SAME edge and the
   SAME expansion — no per-level type, no depth cap by construction.
3. **No name inference.** `validate_forest`/`expand` follow only DECLARED edges; a
   name prefix or shared token creates no edge.
4. **Distinct from data edges.** The `contains` relation is not any of
   `reads`/`constrains`/`has_lineage`/join; a data edge is never treated as
   containment.
5. **Cycle-safe, both layers.** `validate_forest` rejects a declared cycle and an
   unknown parent; `expand` terminates on a cyclic edge set via its visited set.
6. **Bounded.** `max_hops` caps depth, `max_components` caps breadth, and each drop
   emits a disclosure warning; the byte budget is the bundle's existing bound.
7. **Provenance-preserving.** Every edge `expand` returns carries `evidence: "git"`;
   no observed edge becomes a hierarchy edge.
8. **Collision-safe ids.** Two distinct domains yield two distinct `domain:{...}` ids
   (the slug is globally unique by hy-gh-282), and the id appends no value (no
   hy-c89s alias); the edge references those ids.
9. **Invariant.** The hierarchy is off the SERVED path: `validate_domain` runs on the
   sync WRITE path (hy-2zoy) and emits nothing into the bundle, and the `contains` edge
   is not served until hy-gh-230 — so `tools_hash` stays `sha256:fe930a003b731211` and
   `SCHEMA_VERSION` stays 16.

## Consequences

- **Blast radius, stated plainly.** hy-2zoy changed the sync BEHAVIOUR (a malformed
  parent is rejected) but not the served SHAPE: the served graph emits no `contains`
  edge, so `SCHEMA_VERSION` and `tools_hash` did not move. The SV-moving blast radius —
  a new served node/edge shape in `domain_graph`, an `SCHEMA_VERSION` move by merge
  order with a full served-surface sweep — belongs to the emit slice (hy-gh-230), which
  this ADR flags and hy-2zoy does not take.
- Hierarchy is GOVERNED: a human Git merge in the customer repository owns it (ADR
  0012); Hyperset snapshots and traverses it, and infers none of it.
- `tools_hash` and the MCP trust surface are unaffected — the hierarchy lives in the
  governed graph OUTPUT, not the directive INPUT, and nothing hy-2zoy wired touches the
  resolve path.
- Brandon ratified the shape and the expansion semantics (Overseer hq-rqq6,
  2026-08-15); the validation-first slice landed against it in hy-2zoy, and the served
  emit is the deferred follow-on (hy-gh-230).
