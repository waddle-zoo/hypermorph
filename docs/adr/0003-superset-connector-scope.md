# 0003: Read-only Superset connector, transport-specific compatibility

Status: accepted.

## Context

The historical research (`docs/research/superset-version-compat.md`, as
originally written) claimed broad "4.x-6.x compatible" support and treated
ORM fields, REST schemas, and export schemas as interchangeable. Verified
against real `apache/superset` source, this was wrong on specifics:
`ChartPostSchema` and dashboard write schemas are not byte-identical
across versions; export payloads use different field names than REST
detail responses (`dataset_uuid` vs. `datasource_name`, `position` vs.
`position_json`, `database_uuid` vs. nested `database`) — see
`docs/research/FACT_CHECK_2026-07-25.md` §3-4.

## Decision

Superset is the first connector, read-only, pinned to `6.1.0`
(`hyperset.connectors.superset.SupersetConnector`, `hy-gh-27`). ORM
fields, REST request schemas, REST response schemas, and import/export
bundle schemas are treated as four separate contracts. The connector
never writes back to Superset, never migrates its RBAC, and is not used
as Hyperset's own persistence layer. No blanket version-range
compatibility claim is made without a passing real-source contract test
(ADR 0004).

## Consequences

- Version-aware field-shape fallback logic in the connector (see
  `hyperset.connectors.superset.connector._infer_source_version` and the
  `_normalize_*` functions) instead of one fixed schema assumption.
- A second Superset version is supported only after its own contract
  suite passes against a real instance — slower than assuming
  compatibility, but doesn't ship silently-wrong field mappings.
- `hy-gh-17` (post-v0) extracts a generalized connector SDK only once a
  second connector (a different BI tool) proves which abstractions
  generalize.
