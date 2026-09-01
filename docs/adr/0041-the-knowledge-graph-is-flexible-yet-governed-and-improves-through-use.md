# 0041: The knowledge graph is flexible-yet-governed and improves through use

Status: ACCEPTED — ratified by the Overseer/human PRODUCT-ADR RESET (hq-wisp
inbound 2026-08-28, convoy `hq-cv-lo5so`, bead hy-r8job). This directive is the
ratifying authority; Overseer-only ratification is satisfied by the directive
itself, as ADR-0031 was ratified by the Overseer (hq-rqq6). This ADR is
DESIGN-FIRST: it moves no bundle `SCHEMA_VERSION` and changes no `tools_hash`.
It reframes the DESTINATION and specifies the observed/proposed graph layer's
vocabulary; every served-shape change it points at is a separate, sequenced
slice under its own bead.

> **This ADR AMENDS the END-STATE reading of
> [ADR-0031](0031-the-domain-hierarchy.md) and
> [ADR-0034](0034-the-governed-relationship-vocabulary.md), and of
> `docs/v0-foundation.md` ("the domain graph is a deterministic projection
> ... not a graph database").** Those documents are correct about what is
> GOVERNED and about what is served TODAY. They are NOT the end state. The
> destination is a flexible-yet-governed knowledge graph that improves through
> use. The governed/observed split and the "never silently promoted to
> canonical" boundary they draw are PRESERVED here unweakened — this is a
> reframe of the destination, not a relaxation of governance.

## Context

The framing docs read the current domain-only DETERMINISTIC PROJECTION —
a governed domain and its declared edges, rebuilt as a pure function of the
pinned snapshot — as the end state. It is not. It is the governed CORE of a
larger graph. Execution EPICs already point past it: hy-n6atv (hive-mind graph
walking), hy-01442 (flexible-KG discovery), and hy-i3tin (the feedback
flywheel). This ADR makes the ADR/product-goal/gap-matrix FRAMING coherent with
where the work is already going, without moving a served shape itself.

The risk this ADR removes is a destination stated so narrowly that every useful
relationship must be predeclared in Git before an agent may see it. That reading
would make the graph a static projection of a manifest and forbid the very thing
the product is for: an agent inspecting messy sources, reasoning across them, and
proposing what governance has not yet declared. The correction is precise —
declared-never-inferred governs CANONICAL PROMOTION, not the existence of an
observed or proposed edge.

## New product goal (verbatim intent)

A FLEXIBLE YET GOVERNED data knowledge graph that IMPROVES THROUGH USE. Humans
own canonical docs, corpus inclusion/ignore decisions, and approvals. Agents may
inspect messy sources, reason across them, propose nodes/edges/doc changes, and
learn from accepted/rejected/ignored feedback.

## Decisions

### 1. Declared-never-inferred governs CANONICAL promotion, not the graph's existence

`declared-never-inferred` (ADR-0012, ADR-0031, ADR-0034) is a rule about what may
carry GOVERNED authority — `evidence: "git"`, a canonical edge, an authoritative
claim a plan may rely on. It is NOT a rule that a relationship must be predeclared
before it may exist in the graph at all. The graph has two layers, and the rule
binds one:

- **Canonical layer (governed).** Nodes and edges a human declared in the
  authority backend (the customer's Git manifest today, ADR-0036 backends later).
  `contains`/`depends_on`/`joinable_on` and the within-domain governed relations
  live here, all `evidence: "git"`, all declared and never inferred. UNCHANGED by
  this ADR.
- **Observed/proposed layer (first-class, non-canonical).** Nodes and edges an
  agent or connector OBSERVED in messy sources or PROPOSED from reasoning. These
  are first-class graph state: they are stored, walkable, and returned. They are
  NOT canonical, NOT `evidence: "git"`, and NEVER a plan's authority until a human
  accepts them and writes them back.

A useful relationship an agent finds does NOT have to be predeclared to exist as
an observed or proposed edge. It DOES have to be human-accepted to become
canonical. That is the whole of the reframe.

### 2. Observed and proposed edges are first-class graph state, fully provenanced

An observed or proposed node/edge is not a second-class annotation. It carries,
on the edge or node itself:

- **provenance** — the source, connector, agent, and reasoning trace it came from;
- **ACL** — who may see it, under the ADR-0030 authorization boundary;
- **confidence** — a score, never promoted to a governed fact by size (ADR-0019);
- **staleness** — when it was observed/proposed and whether it still holds;
- **source / session / trace linkage** — the exact source, agent session, and
  trace that produced it, so a human accepting it can audit WHY;
- **evidence class** — `observation` or `proposal`, never `git`, so a
  relation-only or legacy client can never read it as canonical (ADR-0034
  Decision 2: one relation string belongs to exactly one evidence class).

These are the ADR-0001/0017 governed-versus-observed properties carried FORWARD
onto the proposed layer, not new authority. An observed edge and a proposed edge
are distinguishable from a governed edge by evidence class alone, and neither is
ever silently promoted.

### 3. The governance boundary, preserved and unweakened

Observed/proposed graph state is NEVER silently promoted to canonical authority.
The invariants below are preserved exactly as ADR-0001/0012/0017/0019 draw them,
and this ADR weakens none:

- provenance, ACL, staleness, source/session/trace linkage, and confidence ride
  on every observed/proposed node and edge;
- a reversible audit records every proposal, acceptance, rejection, and ignore;
- human acceptance/write-back is the ONLY path from proposed to canonical — a
  proposal that has been right a thousand times is still a proposal (ADR-0019
  floor: no authority by accumulation);
- human-owned docs stay authoritative; the graph never overrides them;
- the canonical layer stays a deterministic function of the pinned snapshot, so
  `bundle_id` still hashes the governed answer alone (ADR-0019). Observed and
  proposed content carries its own id and never enters the governed hash.

If any reframe would let observed/proposed state reach canonical without human
acceptance, that is a bug against this ADR, not a feature of it.

### 4. The typed edge-kind vocabulary for the walkable graph

The MVP walkable graph is a bounded walk across TYPED edges connecting six node
kinds. Each carries provenance and an evidence class, and the evidence class —
not the kind — is what tells canonical from observed/proposed:

| node/edge kind | what it is | evidence class it may carry |
| --- | --- | --- |
| `document` | a human-owned canonical doc (manifest, context.md) or a corpus document | `git` when governed; `observation` for an ingested corpus doc |
| `concept` | a governed domain concept/metric, or an observed candidate concept | `git` when declared; `observation`/`proposal` otherwise |
| `entity` | a source-native asset, field, or object | `observation` (what a connector saw); never `git` on its own |
| `source` | a connector source (Superset/DataHub asset origin) | `observation` |
| `evidence` | an observed asset version / snapshot pinned as proof | `observation` (resolved), linked to a governed ref when corroborated |
| `feedback` | an accepted/rejected/ignored decision on a proposal | `proposal` until acted on; the ACT is a human Git write-back |

Rules on the vocabulary:

- A governed edge between these kinds keeps `evidence: "git"` and stays
  declared-never-inferred (`contains`/`depends_on`/`joinable_on` and the
  within-domain relations are unchanged, ADR-0031/0034).
- An observed or proposed edge carries `evidence: "observation"` or
  `evidence: "proposal"` and may NOT reuse a governed relation string
  (ADR-0034 Decision 2, Decision 8 — the `evidenced_by` collision lesson).
- Every node and edge, whatever its kind, carries provenance and its evidence
  class; nothing walkable is unattributed.

Minting the concrete relation strings for each observed/proposed edge is deferred
to the emit slice that serves it, which MUST first reconcile the served history
(ADR-0034 Decision 8) — this ADR fixes the KINDS and their evidence classes, not
the wire strings.

### 5. Flexible without a graph database

The graph stays flexible by a BOUNDED WALK over the existing store, not by
adopting a graph DB. The measured primitives already exist: `domain_graph` nodes
are `{from, to, relation, evidence}` edges (`resolver.py`), and `_bounded` is a
cycle-safe breadth-first walk bounded by `max_hops`, `max_components`, and
`context_budget`, emitting a disclosure warning on truncation and never trimming
`instructions` (ADR-0031). The observed/proposed layer reuses that exact bounded
walk. "Flexible" means the graph does not require every relationship predeclared;
it does NOT mean an unbounded traversal or a new storage engine. Overengineering a
graph DB is out of scope until an evaluator number justifies it (ADR-0009).

### 6. Improves through use, within the boundary

The graph improves because agent use produces proposals and human decisions
produce canonical growth:

1. agents inspect messy sources, reason, and PROPOSE nodes/edges/doc changes;
2. proposals are served as first-class observed/proposed state, provenanced;
3. a human accepts, rejects, or ignores — a reversible, audited decision;
4. an acceptance is a human Git write-back, which grows the canonical layer;
5. the `feedback` edges (accept/reject/ignore) are learning signal for later
   proposals — signal, never authority.

This is the V2 flywheel of the roadmap made the stated DESTINATION rather than a
distant vision, with the nine ADR-0019 floors holding at every step. Coverage
grows one approved commit at a time, never by accumulation.

## MVP scope this reframe describes

- root/high-level domains PLUS bounded walking across the typed edges of
  Decision 4 (`document`/`concept`/`entity`/`source`/`evidence`/`feedback`);
- grep + semantic retrieval over the corpus;
- trace-aware suggestions (a proposal carries the session/trace that produced it);
- the simple Luna/Codex write-back proposal flow with an OPTIONAL PR-open toggle
  (proposal-only by default, ADR-0025/0033);
- the graph kept flexible WITHOUT overengineering a graph DB (Decision 5).

## What this ADR does NOT do

- It moves no bundle `SCHEMA_VERSION` and changes no `tools_hash`. Every
  served-shape follow-on (emitting observed/proposed edges, the corpus retrieval
  surface, the proposal write-back op) is a SEPARATE slice under its own bead and
  takes the SV move by merge order with a full served-surface sweep, exactly as
  ADR-0031/0034 require of their emit slices.
- It weakens no governance invariant. Declared-never-inferred still governs
  canonical promotion; the observed/proposed layer it recognizes was always
  allowed to exist (an observation is not a governed claim) — this ADR names it
  and fixes its vocabulary rather than granting it authority.

## Consequences

- The destination is coherent with the execution EPICs (hy-n6atv, hy-01442,
  hy-i3tin): the framing no longer reads the domain-only projection as the end.
- ADR-0031 and ADR-0034 keep their governed rulings intact; their amendment
  banners now point here for the END-STATE reading, and `contains`/`depends_on`/
  `joinable_on` stay declared-never-inferred canonical edges.
- `docs/v0-foundation.md` and `docs/vision-roadmap.md` state the flexible-yet-
  governed-improves-through-use goal, and the v1 gap matrix carries the MVP
  walkable-graph rows as the measured V1 contract surface.
- The governance boundary is unweakened: no observed or proposed edge is ever a
  plan's authority, and the only path to canonical is a human Git write-back.
