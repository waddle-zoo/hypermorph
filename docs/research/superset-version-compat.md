# Superset connector version and transport compatibility policy

> [!NOTE]
> **Status: current research and release policy. Last verified 2026-07-25.**
> The only required v0 target is a pinned real Apache Superset 6.1.0 instance.
> Issue #27 owns the executable contract; this document defines what a support
> claim means.

## Research question

How broadly can Hyperset claim Superset compatibility, given that Superset
changes its models, schemas, endpoints, exports, and feature behavior over time?

## Conclusion

Compatibility is not a single version number. It is a tested tuple:

```text
source version
× transport
× asset kind
× enabled capabilities / feature flags
× connector version
```

For example, successful parsing of a hand-written 4.0-style dataset YAML file
does not prove support for 4.0 REST detail responses, dashboard exports, or a
live instance with different feature flags.

## v0 support statement

The maximum defensible v0 claim is:

> Tested against a pinned local Apache Superset 6.1.0 instance using the exact
> official export and live REST transports exercised by the Hyperset connector
> contract suite.

Any other Superset release remains unsupported or provisional until the same
real-source suite passes.

## Why earlier broad claims were invalid

The earlier research compared selected Marshmallow write schemas and concluded
that core payloads were almost identical across 4.x–6.x. That evidence was too
narrow because:

- write schemas do not define list responses;
- list responses do not define detail responses;
- REST responses do not define export YAML;
- export commands transform fields and relationships;
- feature flags can change available fields or behavior;
- source permissions can produce partial snapshots;
- additive fields can still affect identity, relationships, or security;
- hand-written fixtures can accidentally encode the connector's assumptions.

There were also direct factual errors: the inspected 6.1 chart write schema adds
`uuid` relative to 4.0, and the inspected dashboard schemas add fields including
`uuid`, `theme_id`, and tags. Those examples alone do not establish full
incompatibility, but they disprove claims of byte-identical schemas.

## Compatibility matrix

Maintain a machine-readable matrix with one row per tested tuple. Suggested
fields:

```yaml
source: apache-superset
source_version: 6.1.0
connector_version: 0.x
transport: export_bundle  # or rest
asset_kinds:
  - database
  - dataset
  - column
  - metric
  - chart
  - dashboard
capabilities:
  catalog_support: observed
  tags: observed
  dashboard_tabs: not_collected
fixture_revision: revenue-v1
contract_status: passing
verified_at: 2026-07-25
limitations:
  - owners require REST detail for selected asset kinds
```

The API should expose this capability information so clients can distinguish
unsupported data from genuinely absent data.

## Transport-specific support

### Official export bundle

Support means Hyperset can:

- generate the archive from the pinned upstream instance;
- retain the original ZIP and metadata;
- parse supported asset files;
- preserve unknown fields;
- resolve documented UUID relationships;
- produce typed warnings for unsupported files or fields;
- repeat the process deterministically.

### Live REST

Support means Hyperset can:

- authenticate safely;
- paginate list endpoints;
- fetch required detail/relationship endpoints;
- distinguish complete and partial access;
- preserve transport-specific payloads;
- align equivalent assets with export observations;
- handle permission, rate-limit, and retryable failures without deletion
  inference.

Passing export tests does not imply REST support, and vice versa.

## Real-source evidence requirements

For every newly supported version:

1. Start the exact pinned upstream image.
2. Verify the reported application version.
3. Apply source-controlled relevant configuration and feature flags.
4. Bootstrap deterministic datasets, metrics, charts, and dashboards.
5. Generate official export artifacts.
6. Collect live list/detail payloads.
7. Preserve unmodified source evidence and hashes.
8. Normalize and compare stable identities and relationships.
9. Repeat sync and prove idempotency.
10. Modify expression, description, ownership, and relationships independently.
11. Test rename, deletion, unresolved links, and reappearance.
12. Simulate partial access/failure and prove no deletion inference.
13. Scan payloads, logs, traces, and DB records for secrets.
14. Run processor and evaluation regressions affected by the source change.

A version is supported only when all required cases pass.

## Compatibility categories

Classify changes rather than relying on major-version labels:

- **Additive:** new optional field; preserve even if normalization ignores it.
- **Renamed/transformed:** equivalent meaning under a transport-specific name.
- **Removed:** field no longer available; capability/limitation must say so.
- **Type change:** string-to-object or identifier representation changes.
- **Relationship change:** target ID, UUID, or membership representation changes.
- **Behavioral change:** endpoint, export, permission, or feature-flag semantics
  change without a simple schema difference.
- **Security-sensitive:** credentials, RLS, ownership, certification, or access
  behavior changes.

Behavioral and security-sensitive changes require explicit review even when the
parser still succeeds.

## Current version findings

### Superset 6.1.0

This is the required v0 target. The official API documentation exposes list,
detail, export, and import endpoints for databases, datasets, charts, and
dashboards. Exact payload behavior must be captured from the local pinned
instance.

### Superset 4.0.0

Existing hand-written fixtures may remain as parser regression inputs. They do
not establish support. 4.0 becomes supported only after a pinned real instance
passes the same suite.

### Superset 5.x and 6.0

No support claim is made merely because selected schemas appear additive or
similar. Add them only when a user need justifies running the complete suite.

## Upgrade process

When adding a new Superset version:

1. Create a dedicated matrix entry and pinned Docker profile.
2. Generate—not hand-author—the source fixtures.
3. Diff raw payloads and normalized results against existing supported versions.
4. Add version/transport adapters only where evidence requires them.
5. Record unsupported capabilities and lossy behavior explicitly.
6. Run all connector, processor, API, provenance, and evaluator tests.
7. Update documentation only after the contract is green.

Avoid a global `if major_version >= ...` strategy when capability detection or
field presence is more precise.

## Allowed and disallowed claims

Allowed:

- "Tested with Superset 6.1.0 export bundles."
- "Tested with selected Superset 6.1.0 REST list/detail endpoints."
- "The connector preserves unknown export fields."
- "Superset 4.0 parser fixtures are present but live support is provisional."

Disallowed without additional evidence:

- "Supports Superset 4.x–6.x."
- "Drop-in compatible with Superset."
- "Any `/api/v1` payload is supported."
- "Schema compatibility guarantees behavioral compatibility."
- "A hand-written golden fixture proves upstream support."

## CI policy

Fast CI may use synthetic/unit fixtures for parser edge cases. Required slow CI
or release validation must run the pinned real-source suite. Connector-relevant
PRs may not replace the real-source gate with snapshots derived from the same
normalization code being tested.

## Primary sources

- Superset 6.1 API reference: https://superset.apache.org/developer-docs/6.1.0/api/
- Superset 6.1 repository: https://github.com/apache/superset/tree/6.1.0
- Superset updating/breaking-change notes: https://github.com/apache/superset/blob/6.1.0/UPDATING.md
- Dataset API: https://superset.apache.org/developer-docs/6.1.0/api/datasets/
- Chart API: https://superset.apache.org/developer-docs/6.1.0/api/charts/
- Dashboard API: https://superset.apache.org/developer-docs/6.1.0/api/dashboards/

## Implementation ownership

- #27 implements the connector and contract suite.
- #28 owns deterministic source assets.
- #37 owns pinned Docker startup and fixture generation.
- #36 makes the real-source contract a required gate.
- #34 prevents release until the exact supported tuple works end to end.
