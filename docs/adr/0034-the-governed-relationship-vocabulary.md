# 0034: The governed relationship vocabulary — cross-domain edges on the existing graph, one relation per fact, declared and never inferred

> **END-STATE reading amended by [ADR-0041](0041-the-knowledge-graph-is-flexible-yet-governed-and-improves-through-use.md) (ACCEPTED).** The governed cross-domain vocabulary (`contains`/`depends_on`/`joinable_on`), its one-relation-per-fact and one-evidence-class-per-relation rules, and declared-never-inferred stand UNCHANGED. ADR-0041 only corrects the DESTINATION reading: these governed edges are the CANONICAL layer of a flexible-yet-governed graph that also carries first-class OBSERVED/PROPOSED edges under `evidence: "observation"`/`"proposal"` (never a governed relation string — Decision 2/8 forward), never canonical until human write-back.
>
> **Amended by [ADR-0036](0036-bring-your-own-knowledge-graph-authority-adapters.md) (PROPOSED).** "Declared in the customer's Git manifest" generalizes to "declared in the authority backend, surfaced via the adapter". `evidence:"git"` names a provider-neutral GOVERNED class; a KG adapter maps native governed edges onto it (a rename to `"governed"` is deferred to its own `SCHEMA_VERSION` slice). `depends_on`/`joinable_on`/`contains` stay declared-never-inferred.

Status: PROPOSED — awaiting Overseer ratification. Design-first, builds nothing: this
ADR fixes the GOVERNED cross-domain relationship vocabulary the `domain_graph` will carry
BETWEEN domains, so every later slice that emits or reads a governed cross-domain edge
speaks one agreed set of names. It adds no served operation and changes no served shape on
its own: no edge is emitted, the bundle `SCHEMA_VERSION` does not move, and `tools_hash`
stays `sha256:fe930a003b731211`. It is the #230 req-1 companion to ADR-0031 (the
hierarchy): 0031 fixed the ONE hierarchy relation (`contains`); this fixes the GOVERNED
data-relationship vocabulary (`depends_on`, `joinable_on`) and how each is declared,
directed, evidenced, and validated. The OBSERVED cross-domain relations are deliberately
NOT named here — see Decision 8 for why that naming is unsafe until its own emit slice.

Extends ADR 0012 (the customer Git commit owns domain meaning; a governed edge is a
declared Git fact, never a Hyperset inference), ADR 0021 (a contradiction is a join, not a
rule — a join carries a declared cardinality, and its DRIFT is refused as unobservable
without SQL), ADR 0029 (the `domain_graph` node-id invariant: an id carries the ref and
never a value-alias), ADR 0031 (the `contains` hierarchy this vocabulary sits beside), and
ADR 0001 / ADR 0017 (the governed-versus-observed split the graph already carries). It does
NOT touch the resolve-path directive INPUT, so the MCP trust surface and `tools_hash` are
unaffected regardless.

## Context

`domain_graph` today carries edges WITHIN one governed domain (`owns`, `defined_in`,
`approved_for`, `reads`, `constrains`, `has_grain`, `has_lineage`, `observed_as`, …) and,
since hy-gh-230 (ADR-0031), ONE edge BETWEEN domains: `contains`, the governed hierarchy.
#230 requirement 1 asks for the graph ABOVE and BETWEEN domains to become navigable — a
domain and the other domains it depends on or can be joined to, not only the ones it
contains. Brandon ruled #230's hierarchy a GOVERNED relation (ADR-0031): declared in the
customer's Git context, never inferred from names, kept distinct from data-relationship
edges. This ADR carries that ruling to the remaining GOVERNED cross-domain relations
before any of them is emitted.

The risk this ADR removes is a vocabulary that grows one relation at a time, each slice
minting a name in isolation, until two slices give one fact two relations, or one relation
two evidence classes, or a new name collides with one already served. That last risk is
not hypothetical: a review of this ADR's first draft found that the name it had proposed
for an observed link (`evidenced_by`) is a relation string that WAS served as a governed
`evidence: "git"` edge and still appears in committed eval recordings
(`hyperset/evals/recordings/governed/**/unidentified.json`). Fixing the governed
vocabulary first, and refusing to mint an observed name until the slice that emits it has
reconciled the served history, is the ADR-0031 pattern (design, then sequenced emits)
applied with that lesson built in.

## The measured graph model this reuses

- **Node identity is a ref-carrying id.** A domain is `domain:{slug}` (`resolver.py`), the
  slug carried in the id with no trailing value — two domains are two ids, and the hy-c89s
  value-alias class cannot arise (ADR 0029). The slug is globally unique by hy-gh-282,
  which is the load-bearing precondition (ADR-0031 Decision 3).
- **An edge is `{from, to, relation, evidence}`.** Governed edges carry `evidence: "git"`;
  observed edges carry `evidence: "observation"`. Today every `domain_graph` edge has
  exactly those four keys. The relation names the kind, and the emit code adds one
  node/edge block per relation (`domain_graph` in `resolver.py`).
- **The whole-estate emit posture already exists.** hy-gh-230 established that before a
  cross-domain edge is served, the emit re-validates the whole estate and drops an edge
  whose endpoint is not safe, fail-closed (`hierarchy.unverified_domains` over
  `validate_forest`). That is a FOREST-chain check that drops a whole domain's emit; the
  governed relations here reuse its POSTURE (compute the known-domain set whole-estate,
  fail closed) but need a different, per-edge localizer — see Decision 5.

## Decisions

### 1. The governed cross-domain vocabulary, fixed

Three GOVERNED cross-domain relationship concepts, each with exactly one served `relation`
string, one direction, and `evidence: "git"`. All are declared in the customer's Git
manifest and none is inferred.

| concept | served `relation` | direction | evidence | declared from |
| --- | --- | --- | --- | --- |
| hierarchy | `contains` | parent → child | git | manifest `parent` (ADR-0031, already served) |
| dependency | `depends_on` | dependent → dependency | git | manifest `depends_on: [slug, …]` |
| joinability | `joinable_on` | declaring → target | git | manifest `joinable_on: [{domain, key, grain, cardinality}]` |

`parent_of` is NOT a separate relation. The parent relationship is the `contains` edge
already served; "parent_of" is the human name for reading that edge from the child, and
minting a second relation for the same fact would give one edge two names and reopen the
ADR-0021 distinctness the graph keeps. One fact, one relation, read in either direction —
and the emit already materializes both a resolved domain's parent edge and its child edges
(`_contains_edges`), so both directions are navigable without a second relation.

### 2. One relation belongs to exactly one evidence class

A governed relation string is ALWAYS `evidence: "git"`: `contains`, `depends_on`, and
`joinable_on` never appear as observed edges. This is what lets a reader know an edge is a
customer declaration from the relation alone, without inspecting provenance. It is also
why an OBSERVED relation may NOT reuse a governed relation's string (Decision 8): a name
carried under two evidence classes is the ADR-0001/0017 leak — an observed derivation read
as a governed claim — this rule exists to prevent.

### 3. Governed relations are declared once, by the source; both directions are served

`depends_on` and `joinable_on` name their target domain by its governed slug in the
DECLARING domain's Git manifest, exactly as `contains` names its `parent`. A shared name
prefix, a common owner, an overlapping field name, or an observed co-occurrence creates NO
governed edge (ADR 0012). The DECLARATION is one-sided — a domain records its own outgoing
relations, so a graph grows without editing the target to note that another now points at
it (the ADR-0031 self-contained-manifest rule).

Navigability is NOT one-sided, though: exactly as the `contains` emit scans the whole
estate to materialize a resolved domain's CHILD edges (not only its parent edge), the
`depends_on`/`joinable_on` emit scans the estate so a resolved domain shows both the
targets it declares AND the domains that declared it as a target. The declaration stays
with one source; the served graph shows the edge from either endpoint.

### 4. `depends_on` and `joinable_on` are distinct, with a decision procedure

`depends_on` is a SEMANTIC/OPERATIONAL dependency: this domain's meaning presumes
another's (a metric defined in terms of another domain's concept, a domain that cannot be
interpreted without another). `joinable_on` is a DATA-JOIN CAPABILITY: two domains' rows
can be related on a declared key. Neither is containment (`contains`), and neither is
reducible to the other.

The decision procedure a manifest author follows, so two authors emit the same relations
for the same intent: **declare `joinable_on` exactly when there is a shared KEY on which
the two domains' rows relate; declare `depends_on` for a semantic reliance that has no such
key.** The two are not exclusive — a domain may both semantically depend on another AND be
joinable to it on a key, and then it declares BOTH (the worked example below is exactly
this case: `revenue` depends on `pricing` for meaning and is joinable to it on `sku`). A
`joinable_on` without a key, or a `depends_on` that is really "we can join these", is a
manifest error the ADR names so review can catch it.

### 5. The hierarchy is a forest; the dependency and join graphs are general digraphs

`contains` must be a forest — at most one parent, no cycle (ADR-0031 Decision 5). A
`depends_on` or `joinable_on` graph has NO such constraint: a domain may depend on or be
joinable to many others, two domains may depend on each other (a real mutual dependency is
not a malformed hierarchy), and joinability is very nearly symmetric. So the ONLY
validation these two carry is:

- **Target existence.** A declared target must be a known governed domain; an edge to an
  unknown target is dropped fail-closed, checked whole-estate before emit, and a clean
  domain is never blacked out by a dangling target elsewhere.
- **No self-reference.** A domain does not depend on or join to ITSELF; a self target is
  dropped at parse (it is meaningless, not malformed-fatal).
- **Deduplication.** A repeated target yields one edge.

There is NO acyclicity requirement. Reusing ADR-0031's forest cycle-check for `depends_on`
would wrongly reject a legitimate mutual dependency; the emit slice MUST NOT copy the
forest rule. Concretely, the emit reuses hy-gh-230's whole-estate POSTURE (compute the
known-domain set once, fail closed) but with a NEW per-edge target-membership test — it
does NOT call `unverified_domains`/`validate_forest`, which are forest-chain-specific and
drop a whole domain rather than a single edge.

### 6. Declaring joinability is not executing a join, and its drift is not checked

`joinable_on` carries a `key`, a `grain`, and a `cardinality` — a governed CLAIM about how
two domains' data relate, which Hyperset serves and never queries. Invariant 6 (Hyperset
executes no warehouse SQL in v0) is untouched: `joinable_on` is a governed edge in the
OUTPUT graph, not a directive INPUT and not an execution.

This is exactly consistent with ADR-0021, and the ADR must be cited precisely: ADR-0021
DECLARES join cardinality (as `joins[].type`) but REFUSES cardinality DRIFT as a finding,
because checking whether a join still holds its cardinality would require executing SQL and
is not observable (ADR-0021 Decision 4's table: "join cardinality drift … refused"). So
`joinable_on.cardinality` here is likewise a DECLARED governed fact that Hyperset serves
and never verifies by running a query — the no-drift-check is not a gap, it is the same
no-SQL boundary ADR-0021 already drew.

The three attributes are defined so the 2b emit does not guess:

- **`key`** — the declared identifier the two domains' rows relate on (a field name common
  to both, in the customer's terms). Required.
- **`grain`** — the level at which `key` identifies a row on the TARGET domain (e.g.
  `key: sku`, `grain: sku` means one target row per sku; `key: sku`, `grain: sku_daily`
  means the target is keyed by sku-and-day). It is what makes `cardinality` meaningful, and
  it MAY equal `key`. Required.
- **`cardinality`** — a CLOSED vocabulary: `one_to_one`, `one_to_many`, `many_to_one`,
  `many_to_many`, validated against that set at parse (free text is rejected), because
  ADR-0021 already treats cardinality as a named governed value rather than prose. Required.

### 7. The join attributes ride on the edge

The `joinable_on` edge carries `key`, `grain`, and `cardinality` as fields on the edge
dict, beside `from`/`to`/`relation`/`evidence`. The join claim's identity IS its key,
grain, and cardinality — an edge with none would assert two domains are joinable without
saying how, which is not a governed claim a plan could use. This is the first graph edge to
carry attributes beyond the base four; the alternative (a `join`-like NODE plus a bare
edge, mirroring how `has_grain`/`has_lineage` attach a node) was rejected because these
attributes describe the RELATIONSHIP between two domains, not a third entity, and a node
would invent one and force a node id whose only content is the pair — an id-shape the
graph does not otherwise carry. It does not touch node ids, so ADR-0029 is not engaged. A
reader that ignores the extra fields still sees a well-formed `{from, to, relation,
evidence}` edge, and the extra keys are additive — which is why the emit still moves the
bundle `SCHEMA_VERSION` (a caller now RECEIVES more).

### 8. The OBSERVED relations are deliberately NOT named here

#230 will also want observed cross-domain relations — a governed domain LINKED to an
observed asset, and OBSERVED lineage between assets a connector saw. This ADR does NOT mint
names for them, on purpose, for two measured reasons:

- The observation LINK is already served: `domain_graph` emits `observed_as`
  (field → `observed_version`, `evidence: "observation"`) today. A cross-domain observed
  link must be reconciled with that existing relation, not invented beside it.
- A candidate name for such a link (`evidenced_by`) is NOT free: it was served as a
  governed `evidence: "git"` edge historically and still appears in committed recordings.
  Minting it as an OBSERVED relation would violate Decision 2 and collide with the served
  history.

So naming the observed cross-domain relations is deferred to the slice that EMITS them,
which MUST first grep the served recordings and history and reconcile or retire the stale
`evidenced_by` usage. Naming them now, before that reconciliation, is the exact mistake
this ADR exists to prevent. (This narrows the original slice-2 ask, which listed observed
names; the narrowing is reported to the Mayor with the collision as its evidence.)

**Named by the emit slice (#230 slice 7, hy-c6vx).** That slice projects two OBSERVED
relationship edges into `domain_graph`, both `evidence: "observation"`, and neither reuses
a governed relation string (Decision 2):

- `lineage_to` — DataHub upstream lineage between observed assets (the connector's
  `derived_from`). DISTINCT from the governed `has_lineage` (Section 9): a different fact
  (what a connector SAW, not what Git DECLARED) under a different string.
- `has_glossary_term` — the observed evidence-of-meaning link from an asset to a glossary
  term (the connector's own word). It deliberately does NOT take `evidenced_by`: that
  string was served as a governed `evidence: "git"` edge historically and STILL appears in
  committed recordings, so an observed edge under that name could be read by a relation-only
  or legacy client as the governed one. `evidenced_by` therefore stays retired and is never
  emitted; its stale-recording removal rides the deferred governed re-record (hy-l13a), and
  a later governed observation link, if minted, reconciles with `observed_as` per this
  Section's first reason rather than reusing `evidenced_by`.

### 9. `has_lineage` (governed) and any future observed lineage are distinct

For the avoidance of the collision Decision 8 warns about: the graph already serves a
GOVERNED `has_lineage` relation (from a source's git-declared `facets.lineage`
produced_by/upstream). Any future OBSERVED lineage relation is a DIFFERENT fact (what a
connector SAW, not what Git DECLARED) and MUST take a different relation string under
`evidence: "observation"`; it may not reuse `has_lineage`. This is Decision 2 applied
forward so the observed slice does not collide.

## The manifest shapes and emitted edges, concretely (for the emit slice, 2b)

Declared in a domain's `manifest.yaml` (both optional; absent means the domain names no
such relation):

```yaml
depends_on:
  - pricing
joinable_on:
  - domain: pricing
    key: sku
    grain: sku
    cardinality: many_to_one
```

Emitted into `domain_graph` (both `evidence: "git"`, both dropped fail-closed if the target
is not a known governed domain, both materialized from either endpoint per Decision 3):

```json
{"from": "domain:revenue", "to": "domain:pricing", "relation": "depends_on", "evidence": "git"}
{"from": "domain:revenue", "to": "domain:pricing", "relation": "joinable_on", "evidence": "git",
 "key": "sku", "grain": "sku", "cardinality": "many_to_one"}
```

Both reference domains by their slug-keyed ids (`domain:{slug}`), and the emit adds a
`domain:{target}` node for the other endpoint, as the `contains` emit does. The emit MUST
take the bundle `SCHEMA_VERSION` by merge order and sweep every served surface, because it
adds served node/edge shapes; `tools_hash` stays `sha256:fe930a003b731211` (the graph is
served OUTPUT, the directive INPUT is untouched).

Note on the two cardinality encodings, stated so it is a choice and not an accident: the
existing WITHIN-domain `joins[].type` is free-text prose (`hyperset/context/schema.py`),
while this CROSS-domain `joinable_on.cardinality` is a closed vocabulary. They are not
unified here — they are different scopes (one relates two sources inside a domain, the
other two domains) and unifying them would be a separate change to the within-domain join
shape. The cross-domain relation takes the stricter, named form from the start.

## What this ADR lands, and what is deferred

- **Lands here (slice 2a, doc-only):** the fixed GOVERNED vocabulary
  (`contains`/`depends_on`/`joinable_on`), the direction and evidence class of each, the
  distinctness rulings and the `depends_on`-vs-`joinable_on` decision procedure, the
  forest-versus-digraph validation rule, the `joinable_on` attribute definitions and closed
  cardinality, and the manifest/edge shapes. No code, no emit, no bundle `SCHEMA_VERSION`
  move, `tools_hash` unchanged.
- **Deferred to slice 2b (the governed emit, its own bead — hy-g5u3):** parsing
  `depends_on`/`joinable_on` into `SUPPORTED_MANIFEST_FIELDS`, validating the cardinality
  vocabulary and the target-exists / no-self / dedup rules, and emitting the two governed
  edges into `domain_graph` with the whole-estate posture (a NEW per-edge target-membership
  localizer, NOT the forest check), the bundle `SCHEMA_VERSION` move, and the full
  served-surface sweep. Lands only after this ADR is ratified.
- **Deferred to a later slice, with its naming EXPRESSLY unresolved:** the OBSERVED
  cross-domain relations (an observed link and observed lineage), which must first
  reconcile the served `observed_as` relation and the stale `evidenced_by` recordings
  before minting any name (Decision 8).

## Acceptance tests (for the slices that implement this ADR)

1. **One relation per fact.** No `parent_of` edge is ever emitted; the parent relationship
   is the served `contains` edge, and the graph carries no second relation for it.
2. **Governed evidence class.** Every `contains`/`depends_on`/`joinable_on` edge is
   `evidence: "git"`; no governed relation ever appears as an observed edge, and no observed
   relation reuses a governed relation string.
3. **Declared, never inferred.** A `depends_on`/`joinable_on` edge exists only for a target
   NAMED in the declaring domain's manifest; a shared name prefix, owner, or observed
   co-occurrence creates none.
4. **Distinct relations + decision procedure.** `contains`, `depends_on`, and `joinable_on`
   are three different relation strings; a `joinable_on` requires a declared `key` and a
   `depends_on` does not.
5. **Digraph, not forest.** A `depends_on` cycle (A→B, B→A) is VALID and both edges are
   served; only an unknown target is dropped fail-closed, a self target is dropped at
   parse, and no acyclicity error is raised for `depends_on`/`joinable_on`.
6. **Target-exists guard, per edge.** A `depends_on`/`joinable_on` naming a domain no
   enabled source provides serves no edge (whole-estate check before emit), a clean domain
   is never blacked out by a dangling target elsewhere, and one bad target does not drop a
   sibling good target on the same domain (per-EDGE, not per-domain).
7. **Closed cardinality + defined grain.** A `joinable_on.cardinality` outside
   `{one_to_one, one_to_many, many_to_one, many_to_many}` is rejected at parse; a valid
   edge carries `key`, `grain`, and `cardinality`.
8. **Both directions navigable.** Resolving the declaring domain and resolving the target
   domain both surface the `depends_on`/`joinable_on` edge between them.
9. **No SQL, no INPUT change.** `joinable_on` runs no query and adds no directive field;
   `tools_hash` stays `sha256:fe930a003b731211`.
10. **Vocabulary invariant (this slice).** ADR 2a emits nothing: the bundle
    `SCHEMA_VERSION` and `tools_hash` are unchanged by this document.

## Consequences

- **Blast radius, stated plainly.** This ADR (2a) is doc-only: no served shape, no bundle
  `SCHEMA_VERSION` move, `tools_hash` unchanged. The SV-moving blast radius — two new served
  edge shapes, an `SCHEMA_VERSION` move by merge order, and a full served-surface sweep —
  belongs to the governed emit (slice 2b), which this ADR flags and 2a does not take.
- **A weakness, self-reported.** The forest-versus-digraph split (Decision 5) means
  `depends_on`/`joinable_on` carry a WEAKER validation than `contains`: only the target
  must exist. A malformed or adversarial manifest could declare a dense dependency mesh
  that a bounded expansion must then cap by breadth — the `max_components` bound ADR-0031
  specifies is what contains that, and any expansion over these relations MUST reuse it. If
  a future rule needs `depends_on` acyclicity for a specific purpose (e.g. a build order),
  that is a NEW governed constraint in its own ADR, not a silent tightening here.
- **A second weakness, self-reported.** Decision 7 puts attributes on an edge for the first
  time. A consumer that validated edges as exactly four keys will see extra keys on a
  `joinable_on` edge; this is additive (the base four are unchanged) and is the reason the
  emit moves `SCHEMA_VERSION`, but a stricter external validator is a cost named here.
- **A narrowed ask, reported not reinterpreted.** The original slice-2 ask listed observed
  relation names (`evidenced_by`/`lineage_to`); this ADR fixes only the GOVERNED vocabulary
  and defers the observed naming, because a measured collision (Decision 8) makes minting
  those names now unsafe. That narrowing is reported to the Mayor for a ruling rather than
  taken silently, and the stale `evidenced_by` recordings are flagged as a pre-existing
  artifact to reconcile.
- These relations are GOVERNED, never inferred: a human Git merge owns them (ADR 0012);
  Hyperset snapshots and serves them and invents none.
- `tools_hash` and the MCP trust surface are unaffected — the vocabulary lives in the
  governed graph OUTPUT, not the directive INPUT.
- Ratification is Brandon's via the Overseer, as ADR-0031 was; no `depends_on`/`joinable_on`
  emit (slice 2b) lands until this ADR is ACCEPTED.
