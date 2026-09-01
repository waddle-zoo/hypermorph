# Multi-domain context graph and governed bundle expansion -- design map (hy-gh-230, #230)

Status: DESIGN MAP (2026-08-17, hy-gh-230). #230 is a HEADLINE V1 directive that was
ALREADY designed and decomposed before this pass: the design lives in the issue plus
ADR-0031 (ACCEPTED) and ADR-0034 (PROPOSED), and the work was cut into slices 1-8
with follow-on beads, MOST of which have LANDED (SV17/18/19). This document does NOT
redo that design. It CONSOLIDATES the shipped architecture into one navigable map,
answers the directive's five core questions against what is actually built (with
file and bead evidence), and states precisely what remains and the one open fork.
Measured on current main, not predicted.

## Slice status (the decomposition already exists)

| # | Slice | Bead | State |
|---|---|---|---|
| 1 | Governed `contains` hierarchy edge (ADR-0031) | hy-gh-230.1 / hy-2zoy | LANDED (SV 16->17) |
| 2 | Relationship vocabulary ADR (`depends_on`/`joinable_on`) | hy-kc1g (ADR-0034) | ADR written, PROPOSED |
| 2b | EMIT `depends_on`/`joinable_on` edges | **hy-g5u3** | OPEN -- gated on ADR-0034 ratification |
| 3 | Multi-domain resolve (`domains[]`, lift `multiple_domains`) | hy-cnto | LANDED (SV 17->18) |
| 4 | Governed progressive expansion operation | hy-fgga | LANDED |
| 5 | Composed multi-domain ContextBundle (`composition`) | hy-uaks | LANDED (SV 18->19) |
| 6 | Cross-domain plan validation | hy-i2us | LANDED (partial -- see Q2) |
| 6b | Verify a cross-domain join against `joinable_on` | **hy-oc0h** | OPEN -- depends on 2b |
| 7 | Observed relationships (`lineage_to`/`observed_as`) | hy-c6vx | LANDED |
| 8 | Linked multi-domain playground scenario (fixture/plumbing only) | hy-2pqi | LANDED -- playground half only (#368) |
| 8+ | Multi-domain adversarial benchmark cases -> #141 | **hy-m8ao** | OPEN -- benchmark capability NOT yet wired |
| -- | Assist cross-domain bundle composition | **hy-gh-129** | OPEN |

## Q1. A bundle spanning multiple domains without losing per-domain authority (LANDED)

A directive naming more than one governed domain no longer refuses with
`multiple_domains`; it resolves into a top-level `domains[]` envelope (slice 3,
hy-cnto, SV18). Each entry is built by the SAME single-domain governed path
(`resolver.py` `_multi_domain` -> `_governed`), so each carries its OWN
`context_authority`, `instructions`, `linked_evidence`, `domain_graph`,
`provenance_refs`, and content-derived `bundle_id` -- the SAME governed content and
the SAME `bundle_id` as that domain's solo resolve, modulo the intentionally-omitted
per-entry `resolved_at` (`_multi_domain` strips it; a solo `ContextBundle.to_dict()`
still carries one, so a `domains[]` entry is not literally byte-identical, only
governed-content- and identity-identical). Authorities never merge: the FLAT envelope on a multi-domain answer is
`context_authority=null` with empty flat `instructions`/`linked_evidence`/
`domain_graph`/`provenance_refs`, meaning "authority is per-domain, read `domains[]`",
and `ContextBundle.__post_init__` REFUSES to construct if a flat governed field is
non-empty while `domains` is set (`schema.py`). Single-domain answers stay
byte-compatible: the envelope appears only on multi-domain.

## Q2. The cross-domain join / expansion model (LANDED, with the join edge gated)

Three shipped pieces, plus the one gated piece:

- **Navigation (LANDED).** `expand_analytics_context` (slice 4, hy-fgga,
  `bundle/expansion.py`) does a whole-estate-verified BFS over the governed `contains`
  forest from a start domain -- so it traverses INTO other domains -- and returns
  navigation only (no authority/instructions/evidence). It follows `contains` edges
  ONLY today; a disabled/unsynced neighbour is surfaced `available: false` with a
  reason, never traversed, never hidden.
- **Composition (LANDED, `contains`-only).** A multi-domain answer also carries a
  top-level `composition.graph` (slice 5, hy-uaks, SV19): a DOMAIN-LEVEL cross-domain
  graph of `domain:{slug}` nodes and governed `contains` edges, each edge keeping its
  own `evidence` provenance. It exposes how domains relate WITHOUT flattening their
  separate authorities. The edge allowlist is `contains` only this slice
  (`schema.py` guard); `depends_on`/`joinable_on` widen it when slice 2b emits them.
- **Cross-domain plan validation (LANDED, partial).** `validate_analytics_plan`
  routes a composed bundle to `_validate_composed` (`plan.py`): every source and field
  is checked against its OWNING component, with `ambiguous_source_component` /
  `ambiguous_field_component` disclosures. A cross-domain join is DISCLOSED as
  `cross_domain_join_unverifiable` -- a WARNING, never verified, and cross-domain
  grain/fan-out is explicitly NOT checked. Verifying the join (key, direction, grain
  compatibility, cardinality, required filters) needs the governed `joinable_on` edge
  and is slice 6b (hy-oc0h), which depends on 2b.
- **Ties to #125 reconciliation.** Cross-domain contradictions surface through the
  same `linked_evidence.conflicts` mechanism per domain; a cross-domain join that
  cannot be governed stays `unverifiable`/assist-class and is NEVER upgraded because a
  model proposed it (#230 requirement 4).

The relation vocabulary `domain_graph` emits today (measured): governed `owns`,
`defined_in`, `approved_for`, `has_grain`, `classified_as`, `has_freshness`,
`has_lineage`, `has_checks`, `reads`, `constrains`, `validates`, `contains`; observed
`observed_as`, `lineage_to`, `has_glossary_term`. `depends_on`, `joinable_on`, and the
retired `evidenced_by` exist in the reserved `GOVERNED_RELATIONS` set but are emitted
NOWHERE -- they are ADR-0034 (PROPOSED) and land in slice 2b. `parent` is never a
served relation; the parent fact is served solely as `contains` (ADR-0034 test 1).

## Q3. Bounded expansion (LANDED)

The multi-domain graph stays finite and deterministic:

- selection refuses unless EVERY named domain matched, so the domain set is bounded by
  the directive;
- each `domains[]` entry is bounded by the same `context_budget`/`max_hops` as a solo
  resolve; a budget overage is DISCLOSED (`OVER_CONTEXT_BUDGET`), never silently
  dropped;
- `contains` emit is one immediate level each way; deeper traversal is the explicit
  `expand` operation, which is BFS-bounded and cycle-/duplicate-safe over the VERIFIED
  forest (`hierarchy.validate_forest` gates the emit fail-closed);
- determinism holds because `domains` and `composition` are governed content inside
  `bundle_id` and carry no per-entry wall clock (one top-level `resolved_at`), so the
  same commit + directive is the same multi-domain answer.

## Q4. Served-contract impact (ALREADY MOVED, precisely)

- **`SCHEMA_VERSION`:** the multi-domain slices MOVED it, and it is recorded in the
  `schema.py` ledger and v0-foundation section 7: **17** (governed `contains` edge,
  ADR-0031), **18** (`domains[]` envelope), **19** (`composition` graph). Slice 2b will
  move it again when `depends_on`/`joinable_on` become served edge VALUES (an
  additive relation-value move, ADR-0034).
- **`tools_hash`:** HELD across 17/18/19 -- ADR-0031 states it, and these are bundle
  OUTPUT shape, not served tool name/description/input schema. Slice 2b likewise adds
  no tool.
- **Single-domain byte-compatibility:** preserved -- `domains`/`composition` are
  present ONLY on a multi-domain answer, so a single-domain bundle is byte-identical to
  before these slices.

## Q5. What remains, and the one open FORK

Decomposition is done; the open work is four beads and one ratification:

- **hy-g5u3 (slice 2b, OPEN):** emit governed `depends_on`/`joinable_on` edges --
  parse them into `SUPPORTED_MANIFEST_FIELDS` (they are unparsed today; only `parent`
  is), validate a closed cardinality set, emit with a target-exists guard, widen the
  composition allowlist, move `SCHEMA_VERSION`. **Gated on the fork below.**
- **hy-oc0h (slice 6b, OPEN):** upgrade `cross_domain_join_unverifiable` to real
  verification against the emitted `joinable_on` edge (join key/direction/grain
  compatibility/cardinality/required filters). Depends on 2b.
- **hy-m8ao (OPEN):** multi-domain adversarial benchmark cases into the #141 harness.
- **hy-gh-129 (OPEN):** assist-class cross-domain bundle composition.

**THE OPEN FORK -- already filed, NOT re-filed here:** ADR-0034 (the governed
relationship vocabulary, `depends_on`/`joinable_on`) is PROPOSED and awaits Overseer
ratification; that is Brandon's call, exactly as ADR-0031 was. It is tracked as
**hy-qx6e** ([BRANDON next session] Ratify ADR-0034 PROPOSED->ACCEPTED). Slice 2b
(hy-g5u3) and therefore 6b (hy-oc0h) cannot land until it is ratified. This is the
directive's remaining big-shape decision (a new governed manifest input +
served-edge-value move); it is the overseer/Brandon fork, and it is already logged.

## See also

- Issue #230; `docs/adr/0031-the-domain-hierarchy.md` (ACCEPTED);
  `docs/adr/0034-the-governed-relationship-vocabulary.md` (PROPOSED).
- `docs/v0-foundation.md` section 7 (the SV 17/18/19 narrative) and the `schema.py`
  ledger.
- Slice beads: hy-gh-230.1, hy-cnto, hy-fgga, hy-uaks, hy-i2us, hy-c6vx, hy-2pqi
  (landed); hy-g5u3, hy-oc0h, hy-m8ao, hy-gh-129, hy-qx6e (open).
