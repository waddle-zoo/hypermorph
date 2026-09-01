# 0024: AI-sourcing proposes references; the connector observes and the lookup is a read

Status: ACCEPTED — ratified by the Overseer, 2026-08-08, at reviewed head
2a585d4 (critic verdict BOUNDARY SOUND); supersedes the PROPOSED draft. hy-p0dz.
This is the boundary the estate-scale follow-ons depend on: hy-op9p
(observed-asset / estate-scale retrieval) and hy-1f9h (flywheel step 2: gather
observed sources for a miss) build against it.

Extends ADR 0001, 0017, 0019, and 0022 and amends one storage assumption of ADR
0001 named in decision 1. It changes no part of ADR 0005's approval boundary,
ADR 0012's authority model, or ADR 0020's hosting/owning line, and it does not
touch the "external execution stays external" boundary of `docs/v0-foundation.md`
invariant 6 — decision 2 draws that line sharply rather than moving it.

## Context

Two things arrive together and are easy to conflate, so this ADR fixes both
before either is built.

**The observation model, corrected.** Today an observation is a lossless
ingest. ADR 0001 keeps `ObservedAsset`/`ObservedAssetVersion` "lossless,
immutable, source-scoped" and a connector writes "these and only these"; the
version row stores `raw_payload` **whole** plus a `normalized` projection and a
`content_hash` (`hyperset/db/models.py:184-215`,
`hyperset/repositories/postgres/observed_assets.py:149`). That is correct for
the one revenue domain and its handful of governed assets. It does not survive
an estate: gathering observed sources for a miss (hy-1f9h) and ranking over a
large asset/lineage corpus (hy-op9p) would ingest a volume of payload the store
exists to avoid sending, and `count_by_type` already carries the note that the
catalog "must not read the corpus it exists to avoid sending"
(`observed_assets.py:445-450`). So the estate-scale observation must store a
**reference** to the asset, and Hyperset must **look the asset up live**, from
the external source, only when it actually needs it.

**AI-sourcing.** With many referenced assets and only a few relevant to a
question, something must DECIDE WHICH referenced assets to use and STATE WHY.
That decision is model reasoning over ranking signals — the assist class ADR
0019 opened, and the exact shape ADR 0022's discovery already uses. It proposes
and ranks; it must never create a reference, an observation, or authority.

The hazard is the seam between them: a live lookup is a fetch, and a fetch one
step from a model that "decided which assets to use" reads like the model
sourcing its own evidence. It is not, and the boundary that keeps it from
becoming that is what this ADR states.

## Decision

### 1. An estate-scale observation stores a REFERENCE, not the payload. This amends ADR 0001, and names the amendment.

For assets gathered at estate scale, a connector's observation records the
asset's source-native **identity and a locator** — enough to find it again —
plus whatever small metadata the connector chose to snapshot (kind, name,
freshness, lineage edges), and a `content_hash` over **that reference**, not
over a whole ingested payload. It does not store the asset's full body.

This is a real shift from ADR 0001's lossless ingest, and it is bounded rather
than a reversal:

- It stays in the **same store and the same types**: a reference is still an
  `ObservedAsset` identity row and an immutable `ObservedAssetVersion`, written
  only by the connector through the one path (`run_sync` → `_run`,
  `hyperset/connectors/sync.py:118,175`; `upsert`/`replace_relationships`/
  `mark_missing_deleted`, `observed_assets.py:136,464,315`). No new store, no
  parallel table.
- "Lossless" narrows from "lossless of the asset body" to **lossless of the
  reference and of whatever the connector chose to snapshot**. The version chain,
  the immutability, and the `content_hash`/`hash_basis` change-detection all
  hold, now over the reference. A connector MAY still ingest a full payload
  where that is cheap and useful (the revenue slice keeps doing so); estate-scale
  observation MAY store only the reference. The choice is the connector's,
  recorded on the observation, and never the model's.
- ADR 0001's load-bearing invariant is untouched: two record families stay
  distinct, and nothing lets an `ObservedAsset` become an approved
  `GovernedContext` without `ReviewRepository.approve` in between
  (`docs/adr/0001-observed-vs-governed.md:20-28`).

### 2. A live lookup is a READ of the referenced asset. It is not warehouse SQL, and invariant 6 does not move.

When Hyperset needs a referenced asset it does not hold in full, it fetches that
asset's **definition/metadata** from the external source through the connector's
existing read-only transport — the same Superset REST / DataHub GraphQL surface
that produced the reference. The line, stated so it cannot be blurred:

- A live lookup reads the **asset object** (a Superset dataset definition, a
  DataHub entity, its lineage). It never runs the asset's query against the
  warehouse, and it is not a license to. "External execution stays external"
  holds: `execution.performed_by_hyperset` and `result_validated_by_hyperset`
  stay `false` on every response (`docs/v0-foundation.md:50,211-213`;
  `hyperset/bundle/schema.py:293-300`; ADR 0019:502-503), and plan validation
  still executes no SQL.
- The lookup is an **observation refresh**, so its result is subject to decision
  1: fetching an asset's fuller body at lookup time produces a normal connector
  observation (a new `ObservedAssetVersion` if it changed), written through the
  same `run_sync`/`upsert` path, never by the caller that requested the lookup
  and never by the model that ranked it.

### 3. AI-sourcing is assist-class: it decides WHICH references and states WHY, and creates nothing.

AI mode ranks the referenced assets against a question and discloses its
rationale. It is modeled exactly on `hyperset/bundle/discovery.py::
candidate_sources`, which is already assist output in ADR 0019's sense:

- Every entry is labelled `governance = "observed"` (`OBSERVED`,
  `discovery.py:134-136`) — never governed, approved, canonical, or trusted
  (ADR 0019 floor 1).
- Every ranking names what produced it: `produced_by = {producer, model,
  signals}` (`discovery.py:319`), `PRODUCER = "deterministic_ranking/1"` today
  and a named model when a model ranks (ADR 0019 floor 9). "No model was
  involved" is itself a useful attribution.
- The ranking discloses its **signals** and rests on stated signal or does not
  rank (`SIGNALS`, `discovery.py:150`).
- The served entry carries **one** `ref`, and it is the observed asset's own
  source-native identity; there is **no field that can hold a declared ref**
  (`_served`, `discovery.py:867-884`), which is what keeps a candidate from
  being substitutable for a resolved link (ADR 0019, ADR 0017).
- Its own id (`assist_id`) is never folded into `bundle_id` (`discovery.py:
  344-348`, ADR 0019 floor 8).

AI-sourcing produces exactly this shape over the estate's references. It
proposes and ranks; the connector, through `run_sync`, remains the **sole**
creator of a reference or an observation.

### 4. AI-sourcing has no writer it may call, and this is enforced by construction and checked on the payload.

The failure mode is not AI ranking; it is an AI-derived value entering a
governed or observed-creation path. The writers AI mode must not call, each with
its single legitimate caller:

- `PostgresObservedAssetRepository.upsert` / `.mark_missing_deleted` /
  `.replace_relationships` (`observed_assets.py:136,315,464`) — created only by
  `run_sync`'s `_run` (`sync.py:212-268`). AI never creates, deletes, or
  re-relates an observation.
- `ReviewRepository.approve` (`hyperset/repositories/postgres/review.py:104`) —
  the sole advance of a `GovernedContext` to approved (ADR 0005). AI never
  approves, and cannot: it holds no `decided_by`.
- The GovernedContext writers, `PostgresGovernedContextRepository.
  propose_version` included (`governed_context.py:104`) — which cannot itself
  approve an approved pointer either.
- `_linked_evidence` and `git_instructions` (`hyperset/bundle/resolver.py:683,
  658`), which build the governed sections from the pinned Git snapshot and the
  observed versions the answer pins. AI output never feeds them.
- `ConnectorSnapshot.established_denominators` (`hyperset/connectors/types.py:
  87`). AI must not **fabricate** one: a denominator is "the warrant to
  soft-delete one asset type" and a count is insufficient without the instrument
  that established it (`types.py:27-56`); no connector produces one today
  (`types.py:98`), so an AI-asserted denominator would license deleting live
  assets on a model's under-read.
- The customer's Git. AI never writes governed meaning; that path is a human Git
  change, ADR 0012.

Because AI-sourcing output IS the `candidate_sources` shape (decision 3), it has
no field that can hold a declared ref and no argument it can pass to any writer
above — the leak is not merely forbidden, it is not expressible, the same
mechanism ADR 0019 uses for `FindingCandidate` and assist output generally.

### 5. Every ADR 0019 floor holds, and the resolution boundary is the one that catches a fabrication.

AI-sourcing output inherits every floor of ADR 0019: no governed label, no
identity, no execution, no authority by accumulation, no overriding a governed
verdict, no suppressing a disclosure, no borrowing the determinism guarantee (its
`assist_id` stays out of `bundle_id`), no unattributed reasoning. And the
conformance check that matters is stated over **derivation and resolution**, not
presence — the same shape ADR 0019 already proves for `git_linked` evidence
(0019:176-212): a value that claims to be governed or observed must RESOLVE to a
stored `observed_asset_version`, because a fabricated identity "has no stored
version behind it," and a ranking is a function of none of governance's inputs.

### 6. No implementation until ratified.

This ADR is PROPOSED. hy-op9p and hy-1f9h do not build against it until the
Overseer ratifies it, and the ratifying edit flips this Status line to
`Accepted` and records the date.

## Conformance

The test that AI-sourcing output cannot enter a governed section, sketched here
for the implementation to encode (mirrors `tests/postgres/test_context_bundle.py`
`::test_every_observed_version_served_as_evidence_is_pinned_in_provenance` and
ADR 0019's derivation rule):

1. **Shape.** An AI-sourcing entry deserializes to exactly the
   `candidate_sources` `_served` keys: one `ref` (source-native), `governance ==
   "observed"`, `produced_by` naming producer and model, disclosed `signals`, and
   NO field that holds a declared ref. Adding a field that could hold a declared
   ref reds this test.
2. **Resolution, not membership.** Every governed value and every evidence entry
   in a served answer resolves to a row in `observed_assets` /
   `observed_asset_versions` (or is a pure function of the pinned snapshot and
   configured source). An AI-fabricated `observed_version_id` has no stored
   version behind it and fails on its attributes, not its identifier — so a
   ranking cannot smuggle a citation.
3. **No writer reached.** With a spy/stub over `PostgresObservedAssetRepository.
   upsert`/`mark_missing_deleted`/`replace_relationships`, `ReviewRepository.
   approve`, the GovernedContext writers, and `_linked_evidence`/`git_
   instructions`, running an AI-sourcing proposal calls **none** of them; only
   `run_sync` does. Removing the guard reds the test.
4. **`bundle_id` unmoved.** An answer's `bundle_id` is byte-identical whether or
   not AI-sourcing ran (its `assist_id` is its own), and `execution.*` stay
   `false`.
5. **Live lookup is a read.** A live lookup issues the connector's read-only
   fetch and never a warehouse query; the negative control drives a lookup and
   asserts no warehouse execution path is reached and `execution.performed_by_
   hyperset` stays `false`.

## Consequences

- The estate-scale observation gets cheap: a reference plus small metadata, not a
  payload per asset, so hy-op9p and hy-1f9h can gather at estate scale without
  the store ingesting what the catalog exists not to send.
- ADR 0001's "lossless" is now explicitly "lossless of the reference and what the
  connector snapshotted," and a reader must consult this ADR beside 0001. The
  observed/governed split and the sole-writer path are unchanged.
- Live lookup adds a runtime dependency on the external source being reachable
  when an uncached asset is needed; an unreachable source is disclosed by the
  existing evidence machinery (freshness/`ref_not_observed`), never guessed
  around.
- AI-sourcing is a second caller of the assist shape ADR 0022 introduced, not a
  new authority surface; if the technique generalises, `candidate_sources` is the
  instrument it reuses.
- The execution boundary is untouched and is stated once more so a future reader
  does not read "live lookup" as "runs SQL."

## Rejected alternatives

- **Keep ingesting full payloads at estate scale.** Correct for one domain,
  unaffordable for an estate, and it makes the observation store the corpus the
  catalog exists to avoid sending.
- **Let AI create the reference it decides to use.** This is the auto-source
  hazard: a model sourcing its own evidence. The connector owns creating a
  reference (`run_sync`), and AI only ranks references that already exist.
- **Treat a live lookup as permission to execute the asset's query.** It reads
  the asset definition, never runs it; conflating the two would delete invariant
  6 by implication rather than by decision.
- **State "AI must not write governed context" and stop.** ADR 0019 already
  rejected convention-and-review as the enforcement: the boundary is that AI
  output has no field to hold a declared ref and no writer it may call, checked
  on the served payload by resolution.
- **Give AI-sourcing its own store or its own served status.** It is observed
  evidence, ranked; it reuses `ObservedAsset*` for the reference and the assist
  shape for the ranking, and adds neither a parallel store nor a new HTTP/MCP
  operation.
