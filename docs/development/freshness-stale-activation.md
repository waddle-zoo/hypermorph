# Freshness-stale activation -- design (hy-kh9k, follow-on to hy-d9ys)

Status: DESIGN, BUILD-GATED (2026-08-17, hy-kh9k). ACTIVATION FORK, and the STRONGEST
of the three (ownership hy-z3wy, grain hy-yjkv, this): it requires an explicit
overseer/Brandon ruling before any build, and nothing is built until that ruling
lands (section 5). Scope: WIRE the already-shipped `freshness_stale` producer so a
bundle emits it when the customer DECLARES a freshness threshold. The `temporal`
value kind (`hyperset/bundle/reconcile.py` `COMPARATORS[TEMPORAL]`), the
`freshness_stale` producer, and its `RECONCILED_KINDS` entry all shipped inert at
SV20 (hy-d9ys).

Two things make this the strongest gate, and both are named squarely below:
freshness is ALREADY served as an OBSERVATION, so activation changes what freshness
MEANS in a declaring user's bundle; and the verdict needs a deterministic `now`
INSIDE `bundle_id`, which touches the core determinism promise.

## 1. The declared threshold -- an EXISTING surfaced field, not a new one

Honest correction to the framing: the declared threshold ALREADY EXISTS. A per-source
`facets.freshness` (cadence and/or `max_staleness`) shipped and is SURFACED at SV14
(hy-gh-284, 284-6b). It is stored as free STRINGS (`hyperset/context/schema.py`
`_freshness`) and today decides nothing.

The producer needs a `timedelta` threshold. So activation does NOT need a new manifest
field; it needs to PARSE `facets.freshness.max_staleness` (a string) into a
`timedelta` at resolve time. Options:

- **Option A (recommended): reuse `facets.freshness.max_staleness`, parse to a
  `timedelta` at resolve time.** The surfaced string is unchanged; the parsed
  `timedelta` is a resolve-time projection, never stored or surfaced (parse-not-
  surface for the parsed value). No new manifest field.
- **Option B: a new parse-not-surface duration field.** REJECTED as redundant --
  `max_staleness` already states this, and two fields for one contract invite
  disagreement.

But Option A carries its own contract cost, and it is part of the fork:
`max_staleness` is a FREE string today with NO parse grammar, so activating it means
DEFINING what strings are valid durations. A manifest that already set
`max_staleness: "whenever"` would parse-fail once the grammar exists -- a NEW
validation constraint on an EXISTING accepted field. The design must state the
grammar (e.g. ISO-8601 durations, or `<n><unit>`), and the ruling must accept that a
previously-accepted value may now be rejected.

## 2. Resolver wiring, and the deterministic `now`

In `_linked_evidence`, beside the shipped producers, a guarded per-source
`conflicts.extend(freshness_stale(ref, threshold, observed_modified_at, now=..., commit_sha=...))`,
called ONLY when a source declares a parseable `max_staleness`, routed through the
one `reconcile()` dispatch (the `temporal` kind) -- no freshness-specific branch.

- **Threshold:** the parsed `facets.freshness.max_staleness` (section 1).
- **`observed_modified_at`:** already available -- the served `freshness` observation
  carries `source_modified_at` per ref (`resolver.py` `_linked_evidence`).
- **`now` -- the hard part, and the determinism crux.** The producer's cutoff
  (`now - threshold`) lands in `context_says`, which is inside `conflicts`, which is
  inside `bundle_id` (`ContextBundle._content`). So `now` MATERIALLY affects
  `bundle_id`, and a wall-clock `now` would make `bundle_id` NON-DETERMINISTIC --
  breaking "same commit + directive = same bundle" (ADR-0019 floor 8). The existing
  `resolved_at = utcnow()` is exactly the wall clock this cannot use: it is
  deliberately OUTSIDE `_content()` for that reason. There is NO deterministic clock
  input threaded into resolve today, so one must be introduced. Options, each a
  contract decision for the ruling:
  - **(a) a caller-supplied `as_of` clock** (a request input): deterministic and
    replayable, but adds a request parameter and makes `bundle_id` depend on a
    request-time value the caller pins;
  - **(b) the estate's latest observation/sync time**: deterministic for a pinned
    estate state, and arguably the most meaningful ("stale as of the last time we
    looked"), but ties `bundle_id` to sync state;
  - **(c) the pinned commit's timestamp**: deterministic per commit, but measures
    staleness against when the CONTEXT was written, not against now -- usually the
    wrong question for freshness.

  A recommendation is NOT made here to avoid self-approval; the choice is the ruling's.

## 3. The OBSERVATION-vs-ERROR question (the served-behaviour change)

Freshness is ALREADY served as an OBSERVATION (`linked_evidence.freshness`:
`last_observed_at`, `source_modified_at`, `deleted_at`). Activation ADDS a
`freshness_stale` CONFLICT ALONGSIDE that observation -- it does NOT replace or
reclassify it. The producer docstring and `reconciliation-unlock-triggers.md`
section 4 are explicit: "the existing `freshness` observation STAYS -- an observation
about the source is true whether or not it breaches a threshold."

But add-alongside is still a SERVED-BEHAVIOUR CHANGE for a DECLARING user: the same
source that today produces only a neutral freshness observation would then ALSO
produce a `freshness_stale` conflict (a `warning`-severity ERROR-class entry an
assist reasoner may cite). It changes what freshness MEANS in that user's bundle --
from "here is when it was last modified" to "this is a governed staleness breach."
That is the fork basis, independent of the byte-additivity below, and it is why this
is the strongest gate: it puts a clock in front of a human as an error, which
ADR-0021 decision 4 flagged as the thing to rule on before building.

## 4. IMPACT EVIDENCE (supporting only -- NOT a self-approval)

- **Non-declaring user:** no `max_staleness`, so the producer is never called; a
  non-declaring bundle is byte-additive, unchanged including `bundle_id`.
- **SCHEMA_VERSION:** new `conflicts[]` entries carry the already-registered
  `freshness_stale` kind and SV20 keys, so the conflict output needs no new served
  KEY -- no SV move from the conflict itself.
- **`tools_hash`:** `conflicts` is bundle output, not a served tool schema --
  unchanged.
- **default-deny:** nothing emits without a declared, parseable threshold.
- **BUT the declaring side is NOT a clean opt-in.** For a declaring user it (i)
  changes freshness semantics (section 3), (ii) introduces a deterministic `now`
  into `bundle_id`, changing the determinism contract's inputs (section 2), and
  (iii) may reject a previously-accepted `max_staleness` string once a parse grammar
  exists (section 1). None of these move SV or `tools_hash`, but all three are
  served-behaviour/contract changes the byte-additivity does not cover.

The byte-additivity for non-declaring users is SUPPORTING EVIDENCE that bounds blast
radius. It is NOT a reason this is not a fork.

## 5. Classification -- ACTIVATION FORK (build-gated), strongest of the three

Requires an explicit overseer/Brandon ruling before any build. Authoritative, and not
overridden by the byte-additivity:

- **`reconcile.py`'s shipped contract governs.** The producer docstring binds the
  wiring to "new governed inputs -- the deferred-activation fork hy-kh9k," and names
  the observation-to-error change as a served-behaviour change. We do not ratify
  around a shipped contract.
- **A declaring user's served bundle changes MEANING** (observation gains an
  error-class conflict) -- section 3.
- **`bundle_id`'s determinism inputs change** -- a deterministic `now` enters the
  identity hash; the choice of clock is a contract decision -- section 2.
- **An existing accepted field gains a parse/validation constraint** -- section 1.

Any one of these is a fork; together they make freshness the strongest Brandon gate.
**BUILD NOTHING until it is ruled.**

## 6. The build slice (ONLY after the ruling)

1. Define and validate a `max_staleness` duration grammar; parse it to a `timedelta`
   at resolve time (parse-not-surface for the parsed value); confirm the ruling
   accepts that a previously-accepted string may now be rejected.
2. Thread the ruled deterministic `now` clock input into `resolve_analytics_context`
   / `_linked_evidence` (NEVER `resolved_at`/wall time).
3. Add the guarded per-source `conflicts.extend(freshness_stale(...))`, routed through
   `reconcile()`.
4. Regression (mutation-verified): a DECLARED threshold an observed `source_modified_at`
   breaches emits exactly one `freshness_stale`; within the threshold is silent; no
   declared threshold or no observed time emits nothing; the existing `freshness`
   observation is UNCHANGED (assert both present); a non-declaring bundle is
   BYTE-IDENTICAL; two resolves at the SAME clock input produce the SAME `bundle_id`
   (determinism). Confirm `SCHEMA_VERSION` stays 20 and `tools_hash` stays
   `sha256:fe930a003b731211`, and state the clock-input the ruling chose.

## See also

- `docs/development/reconciliation-unlock-triggers.md` section 4 (why refused, the
  declaration that unlocks it, the observation-to-error caveat).
- `docs/development/ownership-mismatch-activation.md`,
  `docs/development/grain-mismatch-activation.md` (the sibling activation forks; same
  parse-not-surface + fork-first pattern).
- `hyperset/bundle/reconcile.py` (`freshness_stale`, `TEMPORAL`, `RECONCILED_KINDS`,
  and the "new governed inputs, forks first" + "served-behaviour change" contract).
- hy-imr0 (refinements).
