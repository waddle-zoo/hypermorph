# 0029: The per-source facet vocabulary — a source may STATE governed metadata, and v0 only surfaces it

Status: ACCEPTED (2026-08-13). Ratified by Brandon; future per-source facets follow
the invariants below (malformed = error, absent = None + byte-identical, the node id
carries the source ref, tools_hash neutral). Brandon has since resolved the one
upstream question this ADR raised — whether epic #284 folds into #230 — by ruling
FOLD (2026-08-13): facet enforcement is part of #230 under its access model, not a
standalone track (see Resolved questions). The vocabulary below is what the landed
slices
enforce — five per-source facets, each parsed in `hyperset/context/schema.py`
and surfaced in `hyperset/bundle/resolver.py`: grain (hy-gp99, #311, SV 12),
classification (hy-4giv, #319, SV 13), freshness (hy-6c8z, #329, SV 14), lineage
(hy-sr7w, #330, SV 15), and checks (hy-w16y, #331, SV 16). The one upstream
question this ADR raised — whether epic #284 folds into #230 — Brandon resolved as
FOLD (see Resolved questions). ENFORCEMENT of these facets moves under #230 and is
named, not fully designed, below — grain's fork-2 REFINE ruling already landed as
the `grain_fanout` check (hy-bz5f, #317), and the first structural classification
enforcement as `classification_undisclosed` (hy-eif4).

Extends ADR 0012 (authority is a human Git merge, and Hyperset snapshots it —
never a parallel approval lifecycle), ADR 0028 (an adapter may change the shape
that carries meaning, never create it), and ADR 0018 (`SCHEMA_VERSION` versions
the answer, not the request). It adds no served operation. Each facet DID move
the bundle `SCHEMA_VERSION` when it landed, because each is a new key a caller
RECEIVES; this ADR is a doc-only record of vocabulary already served, so it moves
neither `SCHEMA_VERSION` (still 16) nor `tools_hash` (still
`sha256:fe930a003b731211`): the facets are served OUTPUT shape, and no
CATALOG/RESOLVE description or input schema changed.

## Context

v0's Git context format is deliberately narrow (ADR 0028): an approved source is
a governed identity, and an unknown manifest key is a hard error, because a
customer who writes a key Hyperset silently ignores would believe governed
context says something it does not. Epic #284 asked what STRUCTURED metadata an
approved source may carry beyond its ref, and the answer arrived one bounded
slice at a time. Five landed. The purpose of this ADR is to record the vocabulary
as a whole now that it is complete, to fix the invariants every future facet must
share, and to separate what v0 decided (SURFACE the governed contract) from what
it deliberately deferred (ENFORCE it).

An approved source's `facets` is an optional mapping. Absent, it adds nothing and
the source is byte-identical to before facets existed — the back-compat anchor
every slice preserved. Present, only the five sub-keys below are recognized; any
other is an error, exactly as an unknown top-level manifest key is.

## Decision

### 1. Five per-source facets, each SURFACE-ONLY in its projection

Each facet is parsed from `approved_sources[].facets`, rides into the served
`instructions.approved_sources` wholesale, and is projected into `domain_graph`
as ONE node plus ONE edge, mirrored in `projection_summary` so the two
projections cannot drift (a catalog drift test asserts the equality on a
facet-bearing snapshot).

| Facet | Shape | Node kind / edge | SV |
| --- | --- | --- | --- |
| `grain` | a bare string | `grain` / `has_grain` | 12 |
| `classification` | one of a closed set (`restricted`/`pii`/`internal`/`public`) | `classification` / `classified_as` | 13 |
| `freshness` | a mapping of `cadence` and/or `max_staleness` | `freshness` / `has_freshness` | 14 |
| `lineage` | `produced_by` (string) and/or `upstream` (list of refs) | `lineage` / `has_lineage` | 15 |
| `checks` | a list of `{name, description?, severity?}` | `checks` / `has_checks` | 16 |

SURFACE-ONLY means the projection STATES the governed contract and computes
nothing from it. No classification makes an access or PII decision; no freshness
computes or gates on staleness; no lineage resolves `upstream` to nodes, walks
it, or detects a cycle; no check is executed and no pass/fail or derived status
is produced. Enforcement is a later bead for each — with ONE exception: grain's
enforcement already landed, as the `grain_fanout` plan-validation check that
implements Brandon's fork-2 REFINE ruling (section 4). That check lives on the
plan-validation path, not in this projection, so the projection itself stays
surface-only for all five. The domain's own `check`/`validates`
projection (its declared validations) is distinct from a source's `checks`/`has_checks`
facet: disjoint id prefixes (`check:` vs `checks:`), no double-count.

### 2. The consistent invariants every facet shares

These are not five accidents; they are one contract, and a sixth facet must honor
all of them:

- **Malformed is an error, not a silent drop.** A non-mapping facet value, an
  unknown sub-key, a facet that declares none of its required fields, and a blank
  string are each an error. Silence about something the customer wrote is the
  failure being prevented.
- **Absent is None and byte-identical.** A source that declares a facet nowhere
  grows no `facets` key and answers exactly as it did before the facet existed.
  The shipped revenue bundle declares no facets and is unchanged by all five.
- **The node id always carries the source REF, so two sources never collapse.**
  The failure to avoid (the hy-c89s aliasing class) is a node id that lets a
  distinct source or a distinct meaning collapse into one node — the id must
  disambiguate by ref. Two keying forms landed, and both satisfy that: grain and
  classification, whose value is a single scalar, key `{kind}:{ref}:{value}`
  (resolver.py); freshness, lineage, and checks, whose value is a structured
  mapping or list that cannot live in an id, key `{kind}:{ref}` alone and carry
  the structured fields ON the node. What neither form does is key by value alone
  (`{kind}:{value}`), which WOULD collapse two sources that share a value. A sixth
  facet must likewise put the ref in the id and never a value alone.
- **`tools_hash` is neutral.** A facet is served output shape, so it moves the
  bundle `SCHEMA_VERSION` (a new key a caller receives) and never the resolve-path
  `tools_hash` — no MCP operation's description or input schema changes.

### 3. Governed vs observed, and where an adapter may act

A facet is GOVERNED: it is stated in the customer-owned Git manifest, and Git is
authoritative (ADR 0012). An adapter (ADR 0028) may change the SHAPE that carries
a source into the manifest but may never create MEANING: an unmapped key is an
error, and any field an adapter authored rather than the customer is recorded in
`fields_derived`, so a governed field the customer wrote stays authoritative and
is never silently overwritten by a derived one. Observed evidence (what the
connectors see in the estate) corroborates a governed source (ADR 0017); it does
not populate these facets, which are declarations, not observations.

### 4. RESOLVED: a per-source grain REFINES the domain grain (Brandon fork-2)

When a source declares its own grain finer than the plan reads it at, its rows
fan out. Brandon ruled fork-2 as REFINE, not REPLACE: the finer, more-specific
per-source grain WINS on disagreement and is not overridable by reading it
coarser. This landed as the `grain_fanout` plan-validation check (hy-bz5f, #317),
which fires as an ERROR only when `compare_fragments(source_grain, plan_grain)`
is provably `DIFFERENT` and the plan does not aggregate the source. An
`EQUIVALENT` grain is the source used at its own grain (no violation); an
`UNDECIDED` one — differing only in qualifiers or casts — is disclosed by the
sibling `grain_undecidable` WARNING, never judged a hard fan-out, because
Hyperset does not run the query and must not manufacture a disagreement it cannot
prove.

## Resolved questions

**#284↔#230 — RESOLVED by Brandon: FOLD #284 into #230 (2026-08-13).** #230 is the
graph above and between domains; #284 is the per-source facet vocabulary within a
domain. The two touch: a source's `lineage.upstream` names refs that a cross-domain
graph would also want to relate. Brandon ruled they FOLD — #284 is a sub-issue of
#230, not a standalone track — so facet enforcement lands under #230 and its access
model.

- **Fold #284 into #230 — CHOSEN.** One graph model carries per-source facets and
  cross-domain relations together; lineage `upstream` resolution becomes an edge in
  the same graph rather than a second mechanism, and facet ENFORCEMENT lands under
  #230. The first structural enforcement slice has already landed there: a plan that
  reads a `restricted`/`pii` source without a governed handling caveat is a
  `classification_undisclosed` violation (hy-eif4). The identity-gated part — access
  by caller, PII content handling — is held for a future ADR-0030.
- **Keep #284 standalone — REJECTED.** Facet surface and enforcement would evolve on
  their own cadence, gated only on the access model. Rejected because it would leave
  two graph-shaped things (`domain_graph`'s per-source nodes and #230's cross-domain
  graph) needing an explicit reconciliation later; the fold uses one graph model from
  the start and avoids that.

## Consequences

- The per-source facet SURFACE vocabulary is complete and frozen at five; a sixth
  facet is a new slice that must honor every invariant in section 2 and move the
  `SCHEMA_VERSION` by merge order.
- ENFORCEMENT now lives under #230. Its first structural slice has landed —
  `classification_undisclosed` (hy-eif4), a manifest-governance plan check. The
  identity-gated ACCESS part (deny/filter a `restricted`/`pii` payload by caller, PII
  content handling) is held for a future ADR-0030; checks execution and lineage
  reachability likewise wait on the access model and Brandon's eight flags.
- Nothing in this ADR changes a served shape; `SCHEMA_VERSION` stays 16 and
  `tools_hash` stays `sha256:fe930a003b731211`.
- Brandon has ratified this ADR: the surface vocabulary and its invariants are the
  standard every future per-source facet follows. He has also resolved the
  #284↔#230 fork as FOLD, so #284 proceeds as a sub-issue of #230 and its access
  model. This ADR records decisions already shipped and now carries no open question.
