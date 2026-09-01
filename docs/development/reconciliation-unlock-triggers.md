# Refused-dimension unlock triggers -- design (hy-imr0, #125 slice iv)

Status: DESIGN (2026-08-16, hy-imr0). DESIGN-FIRST UMBRELLA: this specifies, per
refused reconciliation dimension, the DECLARATION that unlocks it, how it enters
the one join mechanism, what it emits, and how it stays GOVERNED-not-assist. It
builds NOTHING and decomposes into one child bead per unlockable dimension. It
REUSES the join mechanism and `COMPARATORS` registry from slice (ii) (design
[Section 3](reconciliation-engine.md)) and does not re-derive them.

## 1. The rule (ADR-0021 decision 4)

A dimension is REFUSED not "not yet" but because it has no observable second side,
or bridging its two sides would be INFERENCE. Each refusal names exactly what is
missing, and the missing thing is always a customer DECLARATION in Git -- never a
guess Hyperset makes:

- **Default-deny until declared.** With no declaration the dimension stays refused
  and emits nothing; a stale source stays a `freshness` observation, an owner
  mismatch is silent. Inferring the bridge/threshold/grain is exactly decision 2's
  failure mode (a guessed contradiction), so it is never done.
- **Label-by-provenance.** An unlocked dimension emits a `bundle_reconciliation`
  conflict (`produced_by`), computed deterministically from Git-plus-observation,
  so it stays inside `bundle_id` (ADR-0019 floor 8) and an assist reasoner may
  cite it but not author it.
- **Reuse the mechanism where a shipped kind fits.** An unlocked dimension is a new
  joinable FIELD handed to the slice-(ii) dispatch, judged by the comparator its
  VALUE KIND selects. `COMPARATORS` today ships EXACTLY three kinds -- `expression`
  (`compare_fragments`), `presence` (existence), and `projected` (a trusted
  persisted finding). A field of one of THOSE needs no mechanism change; ANY kind
  not among those three -- equality/identity, a rollup relation, a temporal
  threshold -- is a NEW value kind, a reviewed change, and a FORK (section 6). The
  design's Section 3 named an `identity` comparator as a possibility, but slice (ii)
  did not ship it, so adding it is a fork like any other new kind.

## 2. Ownership mismatch -- child bead

- **Refused because:** both sides carry owners in DIFFERENT identifier spaces --
  Git's `owner_refs` (`team:finance-data`) and the catalog's `owners`/`owner_urns`
  (`urn:li:corpuser:...`). Bridging them by heuristic is inference, and here a
  wrong bridge costs a FALSE error finding (a stronger refusal than discovery's,
  which only risked a worse ranking).
- **Declaration that unlocks it:** the customer declares an IDENTITY BRIDGE in Git
  -- an explicit mapping from a governed `owner_ref` to the catalog identity/ies it
  corresponds to (e.g. `owner_bridges: [{ ref: team:finance-data, urns: [...] }]`).
  Never inferred; the bridge is governed content like any other.
- **How it enters the mechanism:** with the bridge, the join key is the owner and
  the pair is `(declared owner via bridge, observed owner)`, judged by EQUALITY.
  None of the three SHIPPED kinds (`expression`/`presence`/`projected`) is equality
  of two identity strings, so this needs a NEW `identity` value kind in
  `COMPARATORS` -- a reviewed change, hence a FORK (section 6).
- **Emits:** a `bundle_reconciliation` conflict, kind `ownership_mismatch`, with a
  fixed declared severity in `RECONCILED_KINDS` (recommend `warning` -- a mismatch
  to reconcile, not a data-correctness error). `context_says` the governed owner,
  `source_says` the observed owner. Silent when the bridge maps them equal, or when
  no bridge is declared.
- **Contract note:** needs a NEW `identity` value kind -> ASK-ON-FORK before
  building. The new `RECONCILED_KINDS` VALUE is itself additive under ADR-0018
  decision 5 (the kind vocabulary is default-deny) and does not move
  `SCHEMA_VERSION`, but the new value kind is the reviewed change that gates it.

## 3. Grain mismatch -- child bead

- **Refused because:** `grain` is a free-text manifest string and NO connector
  projects a grain to compare against. Inferring grain from `column_names` is a
  guess.
- **Declaration that unlocks it:** a DECLARED grain/rollup relation -- a structured
  (not free-text) grain the manifest states AND a connector projection that reports
  the observed grain, so both sides carry a comparable value on a shared key.
- **How it enters the mechanism:** the pair is `(declared grain, observed grain)`.
  EXACT match of a normalised grain descriptor is EQUALITY -- the same `identity`
  kind ownership needs and which slice (ii) did NOT ship, so it is a NEW value kind.
  A ROLLUP RELATION (observed grain must be a valid rollup of the declared one, not
  merely equal) is set/hierarchy semantics no shipped comparator carries -- also a
  NEW value kind. EITHER WAY grain needs a new value kind, hence a FORK (section 6);
  the child bead measures whether the declaration needs equality or rollup before
  proposing which.
- **Emits:** a `bundle_reconciliation` conflict, kind `grain_mismatch`, fixed
  severity in `RECONCILED_KINDS` (child-bead decision; recommend `warning`). Silent
  with no declared grain or no observed projection.
- **Contract note:** BOTH the exact and the rollup form need a new value kind ->
  ASK-ON-FORK before building.

## 4. Freshness beyond a declared threshold -- child bead

- **Refused because:** nothing declares how fresh a source MUST be, so a stale
  source contradicts nothing. It is already served as a `freshness` OBSERVATION,
  and calling it a contradiction puts a clock in front of a human as an error.
- **Declaration that unlocks it:** the manifest declares a required freshness
  threshold (a cadence and/or a max-staleness) per source. Only then does an
  observed `source_modified_at` older than the threshold become a disagreement.
- **How it enters the mechanism:** the pair is `(declared threshold, observed
  source_modified_at)`. Deciding it is a TEMPORAL threshold test (is `now -
  observed > threshold`?), which is neither expression, presence, nor identity --
  a NEW value kind, hence a FORK (section 6). The comparison must be deterministic
  for a pinned resolve, so the "now" it compares against is the resolve's own
  clock input, not wall-time read twice (or it cannot sit inside `bundle_id`).
- **Emits:** a `bundle_reconciliation` conflict, kind `freshness_stale`, fixed
  severity in `RECONCILED_KINDS` (child-bead decision; recommend `warning`). The
  existing `freshness` observation STAYS -- an observation about the source is true
  whether or not it breaches a threshold -- and the conflict is the two-sided form
  the declaration makes possible. Silent with no declared threshold.
- **Contract note:** needs a NEW value kind (temporal) -> FORK, AND turning a
  served observation into an error is a served-behaviour change the child bead must
  surface before building.

## 5. Join cardinality -- PERMANENTLY refused, no unlock

`joins[].type` is declared, but checking whether a declared join still holds 1:1
requires EXECUTING SQL against the warehouse to count rows. Hyperset does not
execute warehouse SQL -- a HARD v0 boundary (`docs/v0-foundation.md` invariant 6),
not a missing declaration. No customer declaration can substitute for running the
query, so there is NO unlock and NO child bead: the refusal is permanent for v0
and is recorded here so a future reader does not mistake it for an oversight.

## 6. Fork-gate summary

`COMPARATORS` ships exactly `expression` / `presence` / `projected`. NONE of the
four unlocks reuses one of those, so EVERY unlock needs a new value kind and
therefore forks. (The design's Section 3 mentioned an `identity` comparator, but
slice (ii) did not build it -- so equality is a new kind, not a reuse.)

| Unlock | New value KIND? | Other contract move | Fork before building? |
|---|---|---|---|
| ownership_mismatch | YES -- a new `identity`/equality comparator | new `RECONCILED_KINDS` value (default-deny, additive) | YES, ask-on-fork |
| grain_mismatch (exact) | YES -- a new `identity`/equality comparator | new `RECONCILED_KINDS` value | YES, ask-on-fork |
| grain_mismatch (rollup) | YES -- a rollup/set comparator | new `RECONCILED_KINDS` value | YES, ask-on-fork |
| freshness_stale | YES -- a temporal-threshold comparator | turns a served observation into an error | YES, ask-on-fork |

Every unlock needs a new value KIND in `COMPARATORS`, which is a reviewed change;
freshness additionally turns a served observation into an error, which may need
Brandon. So ALL FOUR child-bead paths ASK-ON-FORK before building -- none is
fork-free, because none reuses a shipped comparator.

## 7. Child beads and what is held separate

One child bead per unlockable dimension (design-first, build none):

- ownership_mismatch unlock -- bead **hy-ocbd** (asks-on-fork: new `identity` value kind).
- grain_mismatch unlock -- bead **hy-868w** (asks-on-fork: new value kind, equality or rollup).
- freshness_stale unlock -- bead **hy-d9ys** (asks-on-fork: new temporal value kind + observation-to-error).

Join cardinality is intentionally NOT a bead (section 5). **`hy-rmm1` (publish the
conflict kind/producer register) is HELD as its own contract-moving slice and is
NOT folded in here:** these unlocks ADD `RECONCILED_KINDS` values (gated at
construction, additive), which is independent of serving the register as a
self-declaring enumeration.

## 8. See also

- [Reconciliation-engine design](reconciliation-engine.md) -- the taxonomy (Section 2) and the one join mechanism + `COMPARATORS` (Section 3).
- [ADR-0021](../adr/0021-a-contradiction-is-a-join-not-a-rule.md) -- decision 4 (the four refusals) and decision 2 (what is not a contradiction).
