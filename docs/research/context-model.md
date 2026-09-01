# Context model for connector-driven analytics intelligence

> [!NOTE]
> **Status: current research for the local v0. Last verified 2026-07-25.**
> `MANIFESTO.md` defines the product boundary. This document explains the
> evidence and design reasoning behind the current context model. The current
> GitHub issues remain the implementation contract.

## Research question

What information must Hyperset store so that it can connect to an existing BI
system, preserve what the system actually contains, help domain experts fill in
missing meaning, and give AI clients context they can inspect and trust?

## Conclusion

Hyperset needs two deliberately separate layers:

1. **Observed assets**: lossless, versioned evidence imported from connected
   systems such as Superset.
2. **Governed context**: human-reviewed business meaning, policy, caveats, and
   validation guidance linked to those observations.

Agent attempts, generated SQL, retrieval traces, evaluation results, and other
run-time behavior belong in **evidence/evaluation records**, not in governed
business meaning.

```text
Connection / SyncRun
        ↓
ObservedAsset + immutable ObservedAssetVersion
        ↓
Processor Finding / ReviewTask
        ↓
Human ReviewDecision
        ↓
GovernedContext + immutable GovernedContextVersion
        ↓
HTTP / MCP retrieval
        ↓
External client attempts and EvaluationEvidence
```

## Why the separation is necessary

Connected analytics tools contain valuable signals: names, SQL expressions,
descriptions, owners, tags, certification, chart usage, dashboard membership,
and source timestamps. Those signals are evidence about how an organization
currently works. They are not automatically canonical truth.

A popular or certified metric may still be stale, locally scoped, incorrectly
named, or inconsistent with a finance policy. Conversely, a new metric may be
correct before it becomes widely used. Hyperset must preserve the source signal
without silently promoting it.

The separation also protects provenance. When a source asset changes, Hyperset
can show exactly what was observed, what a processor proposed, what a person
approved, and which context version an agent later retrieved.

## Core persisted objects

### Connection

Identifies one configured external system instance and its connector settings.
It stores connector type, source locator, enabled capabilities, non-secret
configuration, and secret references. It must not embed plaintext credentials in
observed records.

### SyncRun

Represents one connector snapshot attempt. Important fields include:

- connection and connector versions;
- source-system version and capabilities;
- started/completed timestamps;
- complete, partial, failed, or cancelled status;
- checkpoint/cursor;
- counts, warnings, errors, and raw artifact references.

Only a successful **complete** snapshot may infer that a previously seen asset
is now missing. A partial or failed run must never imply deletion.

### ObservedAsset

Stable identity for an external asset within a connection. Initial v0 kinds are
connection/database reference, dataset, column, metric, chart, and dashboard.

Identity should prefer source UUIDs. Numeric IDs are scoped to one source
instance. Names and slugs are mutable locators and must not be the only identity
when a stable source identifier exists.

### ObservedAssetVersion

Immutable evidence from one or more sync runs. It contains:

- original transport-specific payload or content-addressed blob reference;
- canonical raw-payload hash;
- normalized searchable projection;
- source modified timestamp when available;
- first/last seen information;
- resolved and unresolved relationships;
- warnings and capability limitations;
- the producing sync run.

Unknown source fields must survive ingestion. Normalization is an index and
relationship layer; it is not a replacement for the raw source record.

### GovernedContext

Stable human-governed concept such as a metric definition, dataset-use policy,
join rule, caveat, conflict, deprecation, business term, or optional entity.

An `Entity` can be useful context—for example, defining what the business means
by customer or account—but it is **not** a mandatory root that every metric and
dataset must bind to. Mandatory entity roots are common in systems that compile
queries and choose join paths. Hyperset v0 does not own query compilation.

### GovernedContextVersion

Immutable reviewed content. Depending on context type it may include:

- plain-language definition;
- linked observed assets;
- expression or implementation guidance;
- grain and allowed dimensions;
- approved or prohibited sources;
- join and cardinality guidance;
- mandatory filters;
- freshness and review policies;
- owner and reviewer;
- alternatives, conflicts, and deprecations;
- required validations and caveats;
- rationale and prior-version link.

Lifecycle should distinguish candidate, in-review, approved, deprecated, and
superseded states. A machine may draft a candidate. Only a human review decision
may create an approved version.

### ReviewTask and ReviewDecision

A task explains why human review is needed and pins the exact evidence that
created it. A decision records who approved, edited, rejected, deferred, or
acknowledged the proposal, plus the rationale and resulting context version.

Approval should be transactional: the decision, new immutable context version,
current-version pointer, and audit event succeed or fail together.

### EvaluationCase, Attempt, and Result

Evaluation records test whether context helps an external client make better
choices. They can require or prohibit evidence and can allow multiple valid
retrieval paths. Generated SQL and tool trajectories belong here or in linked
evidence, not in governed context.

### Evidence and AuditEvent

Evidence connects a source snapshot, observed versions, processor finding,
review decision, governed version, retrieval response, and evaluation result.
It is append-only and should report completeness rather than invent missing
provenance.

## Relationships

Relationships should be typed and versioned, but their confidence and origin
must remain explicit.

Examples:

- dashboard `contains` chart;
- chart `queries` dataset;
- dataset `belongs_to` database;
- governed metric `approved_implementation` observed metric;
- context `prohibits` observed dataset;
- context `conflicts_with` context;
- review task `caused_by` observed version;
- evidence `used` governed version.

Unresolved source relationships should be retained with warnings. Hyperset
should not fabricate a link because two names happen to match.

## Join and entity evidence

A source relationship, foreign-key test, or model declaration is useful evidence
but does not by itself prove a safe analytical join.

In particular, dbt's built-in `relationships` generic test checks referential
integrity by finding child values without a matching parent. It does not prove
uniqueness, one-to-many cardinality, absence of fanout, or the correct analytical
join direction. Hyperset should represent those as separate governed assertions
or validation evidence.

## Changes that require review

At minimum, the offline processor should create or reopen review work when it
detects material changes to:

- metric SQL or calculation logic;
- mandatory filters;
- source dataset or database;
- grain, dimensions, or join behavior;
- source ownership or certification when used as governance evidence;
- description or business definition;
- asset identity, rename, deletion, or inaccessible state;
- context review deadline or freshness policy;
- conflicts, prohibited sources, or evaluation regressions.

A description-only change may be low severity, but it still remains visible and
must not be discarded as semantically irrelevant by default.

## Retrieval behavior

The API/MCP layer should rank approved governed context ahead of raw observations
without hiding the observations. Responses should expose:

- exact observed and governed version IDs;
- lifecycle and review state;
- source and connector identity;
- freshness and last-seen information;
- conflicts, alternatives, and deprecations;
- unresolved relationships or incomplete evidence;
- provenance links.

The service should never claim that Hyperset executed or validated a production
query when it only supplied guidance.

## Rejected historical model

The earlier research proposed four universal primitives:
`Entity`, `Relationship`, `Concept`, and `AnalyticalIntent`, with Entity as the
root of all metrics and datasets. That model was appropriate for exploring a
Hyperset-owned semantic query layer, but it does not follow from the current
product boundary.

The useful residue is retained:

- context should be typed and versioned;
- relationships and business definitions matter;
- human ownership and review matter;
- exact versions must be pinned for replay;
- agent behavior must be evaluated.

The rejected parts are:

- mandatory Entity roots;
- Hyperset-owned metric compilation and join planning;
- `AnalyticalIntent` as durable governed meaning;
- exact tool trajectory as the default correctness rule;
- YAML as the runtime system of record.

## Implementation implications for v0

- Postgres is the runtime system of record.
- YAML/JSON remain useful for deterministic seed data and portable exports.
- Superset connector output creates observed versions only.
- The processor creates findings, proposals, and review tasks—not approvals.
- The review UI is the human control surface.
- HTTP/MCP exposes context to Claude, Codex, and deterministic clients.
- The evaluator compares raw observations with governed context.

## Primary sources

- dbt Semantic Layer overview: https://docs.getdbt.com/docs/use-dbt-semantic-layer/dbt-sl
- dbt built-in relationships test source: https://github.com/dbt-labs/dbt-core/blob/main/core/dbt/include/global_project/macros/generic_test_sql/relationships.sql
- Cube data modeling: https://cube.dev/docs/product/data-modeling
- LookML overview: https://cloud.google.com/looker/docs/what-is-lookml
- DataHub documentation: https://docs.datahub.com/
- DataHub context platform positioning: https://datahub.com/products/context-platform/

## Follow-up questions resolved by implementation tests

- Which source fields count as a material semantic change versus payload-only
  change?
- Which context types need dedicated typed columns versus JSON fields?
- Which relationship assertions can be derived deterministically, and which
  always require review?
- How should search rank approved context, disputed context, and raw fallback?

These are engineering decisions for issues #27, #30, #31, and #38. They are not
reasons to reintroduce a semantic query engine into v0.
