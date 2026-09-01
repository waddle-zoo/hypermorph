# 0001: Observations and governed context are separate, versioned records

Status: accepted.

## Context

The pre-pivot architecture converted source records directly into
Hyperset's native semantic objects at ingest time
(`hyperset.bridge.superset_extract`), discarding the raw payload and
implicitly treating "what the source says" as "what Hyperset trusts"
(`docs/research/FACT_CHECK_2026-07-25.md` Blocker A). A source's certification
flag, popularity, or last-edited timestamp is a signal, not proof of
correctness.

## Decision

Keep two record families permanently distinct, at the type level and the
storage level:

- `ObservedAsset`/`ObservedAssetVersion` — lossless, immutable, source-
  scoped. A connector writes these and only these.
- `GovernedContext`/`GovernedContextVersion` — human-owned, immutable per
  version, one current pointer. Only a review decision
  (`ReviewRepository.approve`) creates or advances one.

Nothing in the connector or persistence layer allows an `ObservedAsset` to
become an approved `GovernedContext` without an explicit human decision in
between (`docs/artifact-contracts-v0.md` §4, `docs/postgres-persistence-v0.md`
§1).

## Consequences

- Every governed fact is traceable back to the observation(s) and the
  human decision that approved it.
- A connector bug can corrupt observed data, never governed meaning,
  without a human in the loop.
- Costs one extra hop (review) before any newly observed asset is usable
  as trusted context — accepted deliberately (`MANIFESTO.md` "Connectors
  Observe. Humans Govern Meaning").
