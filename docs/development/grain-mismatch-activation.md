# Grain-mismatch activation -- design (hy-yjkv, follow-on to hy-868w)

Status: DESIGN, BUILD-GATED (2026-08-17, hy-yjkv). This is an ACTIVATION FORK: it
requires an explicit overseer/Brandon ruling before any build, and nothing is built
until that ruling lands (see section 4). Scope: WIRE the already-shipped
`grain_mismatch` producer so a bundle emits it when the customer DECLARES a
structured grain (and an optional rollup relation) AND a connector projects an
observed grain to compare against. The `grain_rollup` value kind
(`hyperset/bundle/reconcile.py` `COMPARATORS[GRAIN_ROLLUP]`), the `grain_mismatch`
producer, and its `RECONCILED_KINDS` entry all shipped inert at SV20 (hy-868w). The
new things here are TWO new governed inputs -- and that, plus the observed side
below, is why this is a fork rather than a mechanical wiring.

This activation is STRICTLY LARGER than the ownership one (hy-z3wy): ownership reused
an observed value the connector PROJECTS into the normalized shape (`owner_urns`),
while grain has NO projected observed grain today -- the raw primary keys sit in
`raw_payload`, but nothing derives a comparable grain the resolver can read.

## 1. The governed manifest field (declared structured grain)

Today's `grain` is a FREE-TEXT string, both the domain-level `grain` and the
per-source `facets.grain` (hy-gh-284 slice 1) -- a bare string surfaced verbatim
(SV12) and NOT comparable. The producer needs a STRUCTURED grain (a set of
dimension names) so two sides can be joined; a free-text string cannot be, and grain
is NEVER inferred from `column_names` (a guess is ADR-0021 decision 2's failure
mode). So this needs a NEW, structured field distinct from the surfaced free-text one.

Proposed shape (per approved source, matching the producer's per-`ref` signature):

```yaml
approved_sources:
  - ref: table:postgres:analytics.public.finance_orders_daily
    role: primary
    reason: ...
    facets:
      grain: "order_date by region"        # existing free-text, SURFACED (unchanged)
      grain_dimensions: [order_date, region]  # NEW structured grain (this bead)
      grain_relation: exact                    # NEW: exact | rollup (default exact)
```

Options, if there is a real choice:

- **Option A (recommended): new per-source `facets.grain_dimensions` + optional
  `facets.grain_relation`.** Per-source matches `grain_mismatch(ref, ...)`. Parsed
  into `snapshot.normalized`, added to `_APPROVED_SOURCE_FACETS`, and
  PARSE-NOT-SURFACE -- `git_instructions` copies `facets` as stored today, so the
  design must STRIP the two new sub-keys there (or store them off `facets`) to keep
  the served instructions byte-identical, unlike the free-text `grain` which stays
  surfaced. This is a stricter parse-not-surface than ownership's, because
  `facets` is already a surfaced dict.
- **Option B: promote the free-text `grain` to accept a structured form.** REJECTED
  in this design: it would change the shape of an ALREADY-SURFACED field
  (`facets.grain` / domain `grain`), a served-contract move (SV) on every existing
  declaring user -- exactly what parse-not-surface is meant to avoid.
- **Option C: a domain-level structured grain.** Deferred: the producer is per-ref,
  and per-source is the tighter join; a domain-level rollup is a refinement.

`grain_relation` is the DECLARED control the producer already fail-closes on: `exact`
(equality of the dimension set via the `identity` kind) or `rollup` (observed set is
a subset/coarsening of the declared one via the `grain_rollup` kind). An unrecognised
value must REFUSE at parse, never default to a comparator.

## 2. Resolver wiring, and the observed-grain source (named honestly)

In `_linked_evidence`, beside the shipped producers, a guarded per-source
`conflicts.extend(grain_mismatch(ref, declared_grain, observed_grain=..., relation=...,
commit_sha=...))`, called ONLY when a source declares `grain_dimensions` and an
observed grain exists, routed through the one `reconcile()` dispatch -- no
grain-specific branch.

**The observed side is the honest hard part: there is NO PROJECTED observed grain today.**
`docs/development/reconciliation-unlock-triggers.md` section 3 says it plainly --
"NO connector projects a grain to compare against."

- The closest observed signal is DataHub `schemaMetadata.primaryKeys`. It IS
  persisted -- the connector passes the whole dataset payload as
  `ObservedAssetInput.raw_payload` (`connectors/datahub/connector.py`) and
  `PostgresObservedAssetRepository.upsert` stores `raw_payload` WHOLE, so
  `primaryKeys` sits in `raw_payload`. What is absent is a NORMALIZED/PROJECTED
  observed-grain FIELD: `_normalize_dataset` derives `column_names` only, no grain,
  so the resolver has no projected observed grain to join on. Activation therefore
  still needs new connector projection work -- deriving an observed grain
  (candidate: a primary-key proxy) into the normalized shape the resolver reads,
  not merely reading a value already in `raw_payload`.
- Superset has no primary-key concept, so it can project no grain. `grain_mismatch`
  would therefore be a DataHub-estate capability, silent everywhere else.
- Projecting a grain from primary keys is a PROXY, not a true grain (the natural key
  is not always the aggregation grain); the design states that limit rather than
  hiding it. Any richer observed grain is connector research beyond this bead.

So activation needs NEW CONNECTOR WORK (project + make available an observed grain),
not only a resolver call -- a second new input on top of the declared field.

Where the observed grain is stored is itself a served-contract question (section 3):
`observed_assets[].normalized` is served WHOLE (`resolver.py`), so a new
`primaryKeys`/`observed_grain` key on the observed normalized would appear in the
served bundle for EVERY DataHub estate, declared grain or not -- changing their bytes.
An option that avoids that is to store the projection OFF the served normalized (a
resolver-only field), at more repository cost. This choice is part of what the fork
must rule on.

## 3. IMPACT EVIDENCE (supporting only -- NOT a self-approval)

Presented as evidence that bounds blast radius. It does NOT downgrade the fork below;
`reconcile.py`'s producer docstring states the wiring reads "new governed inputs, the
deferred-activation fork hy-yjkv" -- that shipped contract is authoritative.

- **Non-declaring user, declared side:** with no `grain_dimensions`, the producer is
  never called and the field is parse-not-surface, so the GIT-declared contribution
  is byte-additive -- no change, including `bundle_id`.
- **SCHEMA_VERSION:** new `conflicts[]` entries carry the already-registered
  `grain_mismatch` kind and keys shipped at SV20, so the conflict output needs no new
  served KEY -- no SV move from the conflict itself.
- **`tools_hash`:** `conflicts` is bundle output, not a served tool
  name/description/input schema -- unchanged.
- **BUT the observed side is NOT purely byte-additive.** Making an observed grain
  available means new connector work, and if it is stored on the served
  `observed_assets[].normalized`, EXISTING DataHub estates' bundles change bytes
  (and `bundle_id`) with no declaration -- a real served-side impact, unlike
  ownership. Whether that is acceptable, or the projection must be kept off the
  served shape, is a decision for the ruling, not for this design to make.

Net: the declared half is additive; the observed half carries a served-side change
that must be ruled on. That asymmetry is itself a reason this is a fork.

## 4. Classification -- ACTIVATION FORK (build-gated)

This activation is a FORK and requires an explicit overseer/Brandon ruling before any
build. Authoritative, and NOT overridden by the byte-additivity of the declared half:

- **`reconcile.py`'s shipped contract governs.** The producer's docstring binds the
  wiring to "new governed inputs, the deferred-activation fork hy-yjkv." We do not
  ratify around a shipped contract.
- **A declaring user gains NEW GOVERNED INPUTS** -- a structured grain field and a
  declared rollup relation the customer's Git commit can now state and that Hyperset
  parses, stores, and acts on.
- **A declaring user gains newly-ACTIVATED served conflicts** -- a declared grain that
  disagrees with the observed projection produces `grain_mismatch` entries no prior
  version emitted, which an assist reasoner may then cite.
- **The observed side changes served bytes for existing DataHub estates** (section 3)
  unless deliberately kept off the served shape -- a contract question on its own.

The byte-additivity of the declared half is SUPPORTING EVIDENCE that bounds blast
radius, not a reason it is not a fork. The fork goes to overseer/Brandon (same track
as ownership hy-z3wy). **BUILD NOTHING until it is ruled.**

## 5. The build slice (ONLY after the ruling)

1. Parse `facets.grain_dimensions` (+ `facets.grain_relation`, fail-closed on an
   unknown value) into `normalized`; add to `_APPROVED_SOURCE_FACETS`; strip both from
   `git_instructions` (and `to_manifest_document` round-trip) so they never surface.
2. Project an observed grain (candidate: DataHub `primaryKeys`), and DECIDE per the
   ruling whether it is stored on the served observed normalized or kept off it.
3. Add the guarded per-source `conflicts.extend(grain_mismatch(...))` in
   `_linked_evidence`, routed through `reconcile()`.
4. Regression (mutation-verified): a DECLARED grain that disagrees emits exactly one
   `grain_mismatch` (exact AND rollup arms); agreement is silent; no declared grain
   or no observed projection emits nothing; an unknown `grain_relation` fails closed;
   a non-declaring bundle is BYTE-IDENTICAL (assert `bundle_id` unchanged). Confirm
   `SCHEMA_VERSION` stays 20 and `tools_hash` stays `sha256:fe930a003b731211`, and
   state explicitly whatever observed-side byte change the ruling accepted.

## See also

- `docs/development/reconciliation-unlock-triggers.md` section 3 (why refused, the
  declaration that unlocks it).
- `docs/development/ownership-mismatch-activation.md` (the sibling activation fork;
  same parse-not-surface + fork-first pattern, but with an observed side that already
  existed).
- `hyperset/bundle/reconcile.py` (`grain_mismatch`, `GRAIN_ROLLUP`, `GRAIN_EXACT`,
  `RECONCILED_KINDS` -- and the "new governed inputs, forks first" contract this
  honors).
- hy-kh9k (freshness activation) -- the third sibling; hy-imr0 (refinements).
