# Apache Superset 6.1 metadata surfaces for the Hyperset connector

> [!NOTE]
> **Status: current, version-pinned connector research. Last verified
> 2026-07-25 against Apache Superset 6.1.0.** This document describes source
> behavior; it is not a Hyperset database schema. Issue #27 is the executable
> compatibility contract.

## Research question

Which Superset assets and fields should Hyperset observe, and which upstream
interfaces can actually provide them?

## Central finding: there is no single Superset payload contract

The connector must keep these surfaces separate:

1. SQLAlchemy ORM models and relationships;
2. REST request/write schemas;
3. REST list and detail response schemas;
4. official import/export ZIP/YAML schemas.

A field visible in the ORM or write schema is not guaranteed to appear in a
list response, a detail response, or an export. The connector therefore needs
transport-specific collectors followed by one normalized observation boundary.

```text
SupersetExportBundleSource ─┐
                            ├─> SupersetNormalizer -> ObservedAssetInput
SupersetRestSource ─────────┘
```

The normalized projection may align equivalent assets across transports, but
the original payloads must remain distinct and lossless.

## Source objects relevant to v0

### Database / connection reference

Superset's database model carries connection identity and engine configuration.
Hyperset needs an observed database reference so datasets can retain their
source relationship.

Persist:

- source UUID and numeric ID when available;
- database name and backend/engine metadata;
- allowed non-secret configuration and capability signals;
- masked connection information only after secret scrubbing;
- relationship to datasets;
- source timestamps and transport locator.

Do not persist plaintext passwords, tokens, decrypted encrypted extras, or
reusable credentials in observed payloads.

### Dataset (`SqlaTable`)

Useful source facts include:

- UUID and numeric ID;
- database relationship;
- schema, catalog when supported, and table name;
- physical versus virtual dataset status;
- virtual dataset SQL;
- description;
- columns and metrics;
- ownership, certification, warning, or tags when the selected transport
  exposes them;
- modification timestamps;
- source-specific configuration and `extra` data after secret review.

### Column (`TableColumn`)

Useful source facts include:

- source ID/UUID if exposed;
- column name, verbose name, type, expression, and description;
- temporal, filterable, dimensional, and visibility flags;
- advanced data type or formatting metadata when present;
- dataset relationship;
- source ordering and timestamps.

Column identity may be transport-specific. When no stable column UUID exists,
identity must be scoped to the stable dataset identity plus an explicit source
locator; a rename should not be silently treated as the same column without
evidence.

### SQL metric (`SqlMetric`)

Useful source facts include:

- source ID/UUID if exposed;
- metric name/label;
- SQL expression;
- description, formatting, currency, warning, or certification data when
  available;
- dataset relationship;
- modification timestamps.

A source metric expression is an observation. It is not automatically an
approved business definition.

### Chart (`Slice`)

Useful source facts include:

- UUID and numeric ID;
- chart name and visualization type;
- dataset relationship;
- description and form/query configuration;
- dashboard relationships;
- ownership, certification, tags, and timestamps when exposed.

In Superset 6.1 export payloads, chart-to-dataset linkage uses `dataset_uuid`.
Code written around older or REST-specific `datasource_name` fields must not be
used as the export contract.

### Dashboard

Useful source facts include:

- UUID and numeric ID;
- title and slug;
- published/certification status where exposed;
- chart membership and layout metadata;
- descriptions, owners, tags, and timestamps when the selected transport
  exposes them.

Superset 6.1 dashboard exports use export-oriented field names including
`position` and `metadata`, rather than assuming ORM/REST names such as
`position_json` and `json_metadata`. The connector must parse the actual
transport payload, not convert through a reduced historical dashboard model.

## Verified export-specific relationships

For the pinned 6.1.0 release, the implementation research found these important
export transformations:

- dataset exports reference their database through `database_uuid`;
- chart exports reference datasets through `dataset_uuid`;
- dashboard exports use `position` and `metadata`;
- export commands may repair or normalize relationships while producing the
  archive;
- standard exports do not necessarily contain the same owner and audit detail
  available through REST detail endpoints.

These facts must be re-proven by issue #27's generated real-source fixture and
must not be generalized to another release without contract tests.

## REST collection strategy

The 6.1 API exposes list, detail, related-object, export, and import endpoints
for core resources. The live connector should:

1. authenticate using a local connector credential boundary;
2. use list endpoints for discovery and pagination;
3. fetch detail endpoints for fields omitted by lists;
4. use explicit relationship endpoints when they are the authoritative source;
5. record endpoint, query, status, and completeness metadata;
6. preserve raw list and detail records separately if both contribute evidence;
7. surface permission or partial-access limitations instead of silently treating
   missing assets as deleted.

The minimal endpoint set must be chosen empirically against the pinned local
instance and documented in connector capability metadata.

## Identity model

Each observed asset should carry:

- Hyperset connection ID;
- connector type/version;
- source system/version;
- asset kind;
- source UUID when available;
- source numeric ID when relevant;
- transport-specific locator;
- raw payload hash/reference;
- first seen, last seen, and source modified timestamps;
- complete/partial snapshot status.

Rules:

- UUID is preferred for cross-transport alignment.
- Numeric IDs are valid only within the same Superset instance.
- Names and slugs are searchable locators, not sole stable IDs.
- Rename preserves identity only when stable source evidence supports it.
- An unresolved relationship is preserved with a warning.

## Normalized relationship set

Initial typed relationships:

- database `contains` dataset;
- dataset `contains` column;
- dataset `contains` metric;
- chart `queries` dataset;
- dashboard `contains` chart;
- dashboard `uses` dataset when provided by an authoritative endpoint;
- owner/tag/certification links as source-local signals where useful.

Do not infer chart or dashboard membership only from a field that the selected
transport does not emit.

## Security and RLS boundary

Superset may carry row-level-security configuration and may apply it during its
own query execution. Hyperset's connector observes this metadata; it does not
prove that every external query path enforced the policy.

The inspected virtual-dataset path in 6.1.0 treats some RLS rewriting failures
as best-effort/logged behavior rather than an unconditional fail-closed contract.
Hyperset should therefore report source policy metadata and uncertainty rather
than claiming independent enforcement.

## What not to derive from the ORM

The physical Superset metadata schema is useful for understanding source
relationships and diagnosing transport gaps. It is not:

- the Hyperset Postgres schema;
- proof of what a REST endpoint returns;
- proof of export shape;
- permission to query the Superset metadata DB directly in normal connector
  operation;
- evidence that copying the schema would reproduce Superset behavior.

The `superset-core` model interfaces in the upstream repository are abstract
contracts, not a hidden second persisted schema.

## Required real-source contract tests

The connector research is considered implemented only when a pinned real 6.1.0
instance proves:

1. database, dataset, column, metric, chart, and dashboard collection;
2. official export parsing with unmodified raw archive retention;
3. live list/detail collection;
4. equivalent stable identity across transports;
5. transport-specific raw payload preservation;
6. database, dataset, chart, and dashboard relationship resolution;
7. repeated-sync idempotency;
8. independent expression, description, and owner changes;
9. rename, deletion, inaccessible, and unresolved-link behavior;
10. partial sync never implying deletion;
11. no plaintext secret leakage.

## Primary sources

- Superset 6.1 API reference: https://superset.apache.org/developer-docs/6.1.0/api/
- Dataset endpoints: https://superset.apache.org/developer-docs/6.1.0/api/datasets/
- Chart endpoints: https://superset.apache.org/developer-docs/6.1.0/api/charts/
- Dashboard endpoints: https://superset.apache.org/developer-docs/6.1.0/api/dashboards/
- Database endpoints: https://superset.apache.org/developer-docs/6.1.0/api/database/
- Upstream 6.1.0 source: https://github.com/apache/superset/tree/6.1.0
- Dataset models: https://github.com/apache/superset/blob/6.1.0/superset/connectors/sqla/models.py
- Chart model: https://github.com/apache/superset/blob/6.1.0/superset/models/slice.py
- Dashboard model: https://github.com/apache/superset/blob/6.1.0/superset/models/dashboard.py

## Remaining implementation questions

- Which REST detail and related-object endpoints provide the smallest complete
  v0 snapshot?
- How should a bundle UUID and live numeric ID be persisted when only one
  transport exposes both?
- Which fields should participate in the semantic-change hash versus a raw-only
  payload hash?
- Which source configuration fields require connector-specific redaction?

Issue #27 must answer these with generated fixtures and tests rather than new
architecture prose.
