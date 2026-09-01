# Research Fact Check and v0 Implementation Decisions

**Audit date:** 2026-07-25  
**Repository:** `waddle-zoo/hyperset`  
**Authority:** `MANIFESTO.md` defines the product. This audit determines how the historical research applies to that product.

## Purpose

This document audits every Markdown file currently under `docs/research/`, verifies material claims against primary sources where possible, identifies conclusions that no longer follow from the evidence, and converts the surviving research into implementation rules and executable test requirements for the local Docker v0.

The goal is not to preserve earlier architecture. The goal is to prevent a coding agent from implementing a plausible but wrong product because an old research document sounds authoritative.

## Source-of-truth order

When repository documents disagree, use this order:

1. `MANIFESTO.md`
2. This fact-check document
3. Current open v0 GitHub issues and their acceptance criteria
4. Current ADRs and architecture documents explicitly marked current
5. Historical research in `docs/research/`
6. Existing implementation code and tests

Existing code is evidence of prior implementation, not proof of current correctness.

## Verdict labels

- **Verified:** the factual claim is supported by current primary evidence.
- **Corrected:** the original claim contains a factual error or is too broad.
- **Historical:** the claim may have been reasonable for the old thesis but must not guide the current v0.
- **Product decision:** a deliberate Hyperset choice, not an externally established fact.
- **Follow-up required:** implementation must not assume an answer until the specified test or decision exists.

## Audited inventory

Seven research files were found and audited:

1. `context-model.md`
2. `from-scratch-bootstrap.md`
3. `superset-metadata-models.md`
4. `superset-version-compat.md`
5. `superset-annotation-gap.md`
6. `semantic-layer-landscape.md`
7. `agent-eval-framework.md`

## Executive decisions

### 1. Hyperset is not a semantic-layer query engine in v0

The landscape research correctly shows that dbt MetricFlow, Cube, LookML, and AtScale model metrics, dimensions, joins, and query semantics. That does not imply Hyperset should reproduce those systems.

The manifesto defines a different product boundary:

- connectors observe analytics assets where they already live;
- Hyperset preserves those observations and their provenance;
- Hyperset stores human-governed analytical context;
- an offline processor creates explainable review work;
- external agents retrieve context through HTTP/MCP;
- production query execution remains with the customer's existing analytics and data systems.

Therefore v0 must not implement:

- a warehouse query compiler;
- a universal metric engine;
- an autonomous join planner;
- a Superset-compatible backend;
- a Hyperset-owned dashboard runtime;
- a proprietary conversational agent loop.

### 2. Observed assets and governed context are different records

A connector imports evidence, not truth.

The required separation is:

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
```

Source certification, popularity, ownership, descriptions, and tags are useful signals. None of them automatically makes an asset canonical.

### 3. Superset compatibility is transport-specific, not a blanket claim

The connector must distinguish at least:

- official ZIP/YAML export bundle behavior;
- live REST list behavior;
- live REST detail behavior;
- source version and enabled feature flags.

These surfaces are not interchangeable. ORM fields, Marshmallow request schemas, REST response schemas, and export payloads are separate contracts.

For v0, the supported claim should be narrow:

> Tested against a local Apache Superset 6.1.0 instance using the exact live API and export paths exercised by the integration suite.

Superset 4.x support remains provisional until a real 4.x instance produces fixtures that pass the same contract suite. Hand-written YAML is not sufficient evidence.

### 4. Raw connector payloads must be retained losslessly

Every observed version must retain the original source payload or a content-addressed reference to it, plus a normalized projection used for search and relationships.

Unknown fields must survive. Normalization must not overwrite or replace source evidence.

The current `hyperset.bridge.superset_extract` behavior is incompatible with this rule because it immediately converts payloads into reduced native semantic objects and explicitly avoids storing raw dictionaries.

### 5. Human approval is mandatory for governed meaning

Automated inference may create findings and candidate proposals. It may not create an approved governed version.

Metric expression drift, changed filters, renamed/deleted source assets, changed ownership, expired review intervals, and evaluation regressions are review triggers.

### 6. The evaluator protects context quality, not a Hyperset-owned analytics agent

The core v0 evaluator grades:

- correct concept/context selection;
- correct source and observed-asset selection;
- prohibited-source avoidance;
- conflict, deprecation, and freshness disclosure;
- provenance completeness;
- required filters, joins, caveats, and validation guidance;
- unsupported claims;
- optional result equivalence for fixture-backed external SQL cases.

Dashboard generation quality and a Hyperset-owned SQL trajectory are not v0 product gates.

### 7. Competitive capability claims are time-stamped evidence

MCP support, product APIs, hosted evaluation products, and standards work change quickly. They should inform product strategy, not become hard-coded architectural dependencies.

For example:

- DataHub now has an official MCP server, contradicting the landscape file's statement that it has no MCP support.
- Open Semantic Interchange moved into the Apache Incubator and was renamed **Apache Ossie (Incubating)** in July 2026.
- Ragas now documents agent/tool-call and SQL metrics, contradicting the evaluation file's description of it as RAG-only.

## Critical implementation blockers found

### Blocker A — current Superset extractor uses the wrong product model

`hyperset/bridge/superset_extract.py` currently:

- converts source records directly into `hyperset.semantic` objects;
- discards the original raw payload as a persisted observation;
- treats charts/dashboards as extraction-only semantic context;
- assumes old architecture documents are authoritative.

Required correction:

- replace this path with connector snapshot, normalization, versioning, and raw-payload persistence;
- keep source identity and source version independent from governed context IDs;
- let the processor/review workflow propose governed context later.

### Blocker B — current extractor assumes non-real export fields

Verified against Apache Superset 6.1.0 source:

- chart export removes `datasource_name` and adds `dataset_uuid`;
- dashboard export renames `position_json` to `position`;
- dashboard export renames `json_metadata` to `metadata`;
- dashboard export repairs missing/orphan chart references in `position`;
- dataset export uses `database_uuid` rather than a nested `database` object;
- standard export payloads do not provide the same owner/audit fields as detail REST responses.

The current implementation reads `position_json`, expects nested/string `database`, and derives owners from standard export payloads. A passing hand-written fixture therefore does not demonstrate compatibility.

### Blocker C — golden Superset fixtures are not golden upstream evidence

The fixture README explicitly says the fixtures are hand-written. The integration tests prove only that two hand-written payloads reduce to the same expected objects.

They do not prove:

- that either payload was emitted by a real Superset instance;
- that live and bundle transports normalize equivalently;
- that version-specific export transformations are handled;
- that unknown fields are preserved;
- that relationships, deletions, partial syncs, or secrets behave correctly.

Required correction:

- generate fixtures from Dockerized upstream Superset versions;
- retain the unmodified ZIP and source version metadata;
- compare bundle and live detail normalization;
- test raw-payload hashes and relationship resolution.

### Blocker D — canonical docs still describe the rejected architecture

At audit time, these documents still describe a native semantic-layer store and/or Superset-compatible backend as the product:

- `docs/architecture.md`
- `docs/context-object-model-v0.md`
- `docs/context-layer-object-model.md`
- `docs/agent-runtime-v0.md`
- related examples and `hyperset.semantic` code

Until rewritten, they must be treated as historical. They must not override the manifesto or current issues.

## File-by-file audit

# 1. `docs/research/context-model.md`

## Verdict

**Historical architecture with material factual and product errors. Do not implement as the v0 domain model.**

## What remains useful

- Context should be typed and versioned rather than stored as unstructured prompt text.
- Relationships, business terms, provenance, ownership, and validation guidance are valuable context.
- Evaluations should connect failures to the context versions used.
- Stable IDs and explicit lifecycle states are useful.

## Corrections

### Mandatory `Entity` is not established by the evidence

The file makes every metric/dataset depend on a Hyperset-native entity and models joins through those entities. dbt MetricFlow and Cube use entity/join concepts because they compile analytical queries. Hyperset v0 does not own query compilation.

For Hyperset, an entity or join rule may be a typed governed-context object when useful, but it is not a mandatory root for every observed asset or metric.

### `AnalyticalIntent` must not be a core governed-context primitive

The file combines:

- a user question;
- selected semantic artifacts;
- generated SQL;
- validation results;
- expected tool trajectory.

That is an evaluation attempt or externally supplied agent-run evidence, not durable business meaning. Store it under evaluation/evidence records when needed.

### Exact trajectory expectations are too rigid

Multiple correct tool paths may exist. A case may require or prohibit particular evidence/tools without prescribing one exact sequence. Exact sequence matching is appropriate only for workflows where ordering is semantically required.

### The dbt `relationships` test does not validate cardinality

The official dbt generic test left-joins child foreign-key values to parent key values and returns unmatched children. It checks referential integrity. It does not establish one-to-many vs. many-to-many cardinality, detect fanout, or prove a safe analytical join direction.

### Metric edits must trigger review/evaluation impact analysis

The file says a metric expression edit does not require context review unless an entity or concept binding changes. Under the manifesto, metric SQL/expression, mandatory filters, source dataset, grain, and join behavior are trust-critical. Material changes must create processor findings and run affected evals.

### YAML is not the v0 runtime system of record

YAML remains suitable for fixtures, import/export, examples, and reviewable seed data. Postgres is the v0 runtime system of record.

## Replacement implementation model

Use issue #26's model:

- Connection
- SyncRun
- ObservedAsset / ObservedAssetVersion
- GovernedContext / GovernedContextVersion
- ReviewTask / ReviewDecision
- EvaluationCase / EvaluationRun / EvaluationAttempt
- Evidence / AuditEvent

# 2. `docs/research/from-scratch-bootstrap.md`

## Verdict

**Historical rejected option. Almost none of its recommended architecture applies to the current v0.**

## Verified facts

A real Superset instance needs its own metadata database, migrations, user/role setup, and initialization. Its security model is based on Flask-AppBuilder roles and permissions.

## Corrections

### Hyperset does not need to bootstrap a Superset-compatible metadata schema

The local v0 runs a real Superset container as a source system. Hyperset does not impersonate Superset's backend or frontend.

### Alembic history is not an application runtime contract

Superset's migration tooling uses the Alembic revision chain to construct and upgrade the metadata schema. That does not mean normal application behavior should be modeled as branching on migration-history rows. Do not reproduce that claim in Hyperset architecture.

### Schema parity would not imply behavioral compatibility

Even a copied metadata schema would not reproduce Superset APIs, security behavior, feature flags, export transformations, application invariants, or frontend expectations.

## Correct local bootstrap

```text
Docker Compose
  ├─ Superset metadata DB
  ├─ real Superset 6.1.0
  ├─ analytics fixture DB
  ├─ Hyperset Postgres
  ├─ Hyperset API/MCP
  ├─ Hyperset worker
  └─ review UI
```

Superset runs its own supported initialization commands. Hyperset creates a read-only connection and imports observations.

# 3. `docs/research/superset-metadata-models.md`

## Verdict

**Useful field inventory, partially verified. Must be rewritten as a connector-source reference, not a Hyperset schema specification.**

## Verified

The main 6.1.0 ORM objects and many listed fields are present:

- `Database`
- `SqlaTable`
- `TableColumn`
- `SqlMetric`
- `Slice`
- `Dashboard`
- dashboard/chart/dataset relationships
- audit and UUID mixins

The chart's datasource identity remains polymorphic in the model. Dashboard-to-chart is a many-to-many relationship.

## Corrections

### Separate four contracts

Every connector implementation must distinguish:

1. ORM model fields
2. REST request schemas
3. REST response/list/detail schemas
4. import/export bundle schemas

A field present in one is not guaranteed in another.

### Secret handling statement was too broad

Standard exports use masked connection URIs. Database passwords and encrypted extras are handled separately during import/configuration. A connector must never assume export ZIPs contain recoverable credentials, and must never persist plaintext secrets in observed payloads, logs, or API output.

### Virtual-dataset RLS is best-effort in the inspected path

The 6.1.0 virtual-dataset path catches row-level-security rewriting errors and logs them rather than failing the request closed. The research must not describe this behavior as an unconditional security guarantee.

Hyperset should preserve the source's RLS metadata/status as observed evidence and avoid claiming it independently enforced or validated external query behavior.

### `superset-core` is inspectable and abstract

The 6.1.0 monorepo includes `superset-core` abstract model interfaces. They define type/interface contracts and are replaced by host implementations. They do not establish a separate hidden persisted schema.

### Metadata-table replication is out of scope

The physical Superset metadata schema is not a Hyperset persistence target. It is relevant only for understanding source behavior and fallback diagnostics.

# 4. `docs/research/superset-version-compat.md`

## Verdict

**Partially correct research with overbroad compatibility claims and direct internal contradictions.**

## Verified

- Superset retains the `/api/v1` namespace through 6.1.0.
- Many core dataset request-schema changes from 4.0 to 6.1 are additive.
- Metric `currency` changed from a string representation to a structured object, while newer deserialization tolerates legacy string input.
- Dashboard list responses may omit fields that detail responses contain.
- Export/import behavior is distinct from standard CRUD schemas.

## Direct factual corrections

### `ChartPostSchema` was not identical

Superset 6.1.0 adds a `uuid` field that is absent from the inspected 4.0.0 schema.

### dashboard schemas were not identical

The 6.1.0 dashboard write schemas add fields including `theme_id`, `uuid`, and tags compared with 4.0.0.

The file later lists some of these additions, contradicting its earlier claim of byte-for-byte identity.

### "Any 4.x–6.x metadata payload" is not a defensible transport claim

Compatibility cannot be inferred from write-schema diffs alone. List, detail, export, and feature-flag-dependent payloads differ. A version claim requires tests against the exact transport and version.

## v0 compatibility policy

- Required: real local Superset 6.1.0, live API and official export bundle.
- Required: capability/version metadata and explicit unsupported-field warnings.
- Provisional: 4.0 bundle parsing until generated by a real upstream instance.
- No blanket "drop-in compatible" or "all 4.x–6.x payloads" claim.
- Add a new version only when the full connector contract suite passes.

# 5. `docs/research/superset-annotation-gap.md`

## Verdict

**Good source-gap inventory; old semantic-layer conclusion must be replaced.**

## Useful findings

Superset supplies useful but often freeform or source-local signals:

- descriptions and labels;
- raw SQL metric expressions;
- dataset ownership;
- certification strings/details;
- warnings;
- formatting and usage metadata;
- audit timestamps;
- dashboard/chart/dataset relationships.

It does not provide Hyperset's required governed lifecycle, review decisions, context version history, conflict resolution, or cross-source canonicalization.

## Corrections

### "No lineage" was too absolute

Superset has structural relationships among dashboards, charts, datasets, columns, and queries. It does not provide a complete governed semantic derivation graph for metric composition and cross-system lineage.

### Do not infer a query engine from missing annotations

The gap supports building:

- observed signals;
- governed definitions and caveats;
- ownership and review policies;
- conflict/deprecation state;
- required validation guidance;
- provenance and freshness.

It does not require Hyperset to compile metrics, execute queries, or materialize a replacement Superset schema.

# 6. `docs/research/semantic-layer-landscape.md`

## Verdict

**Time-stamped market research. Useful for product differentiation; not a v0 architecture specification. Several 2026 capability claims are already stale.**

## Verified durable patterns

- Semantic-layer products commonly model metrics, dimensions, joins, and access policies.
- Modeling vs. governed consumption is a recurring split.
- Git/version-control workflows are common.
- MCP has become a common agent-facing interface.
- Append-only/queryable change history is useful for agents and humans.

## Corrections and updates

### DataHub does have MCP support

DataHub has an official MCP server and product documentation. The matrix's "No MCP" claim is false as of this audit.

### OSI has changed governance and name

The initial Open Semantic Interchange specification became publicly available in January 2026. In July 2026 the project entered the Apache Incubator and was renamed **Apache Ossie (Incubating)**.

Do not hard-code a product schema around the January snapshot. Track it as a possible future import/export adapter after v0.

### Participant-count and rollout claims need dated sources

Claims such as "60+ organizations" and specific Q2–Q4 platform-support timelines are not durable implementation inputs. Keep exact dates and participant counts only when linked to a dated primary announcement.

### MetricFlow is not merely an internal folded component

MetricFlow and dbt's semantic tooling have continued to evolve, and dbt maintains an official `dbt-mcp` project with semantic-layer tools. Describe current distribution/deployment facts from the version being evaluated rather than using a permanent category statement.

### Vendor marketing is not comparative proof

AtScale and Cube product capability statements are evidence that those products advertise and document a feature. They do not prove one governance model "beats" another or establish Hyperset's architecture.

## Manifesto application

Borrow:

- connector-neutral asset identity;
- governed context separate from raw observations;
- queryable version/provenance history;
- model-agnostic MCP retrieval;
- typed relationships and validation guidance where useful.

Do not borrow for v0:

- query compilation;
- join-path planning;
- runtime row/column enforcement;
- metric serving;
- cache/pre-aggregation infrastructure;
- OSI/Ossie-native storage schema.

# 7. `docs/research/agent-eval-framework.md`

## Verdict

**Useful evaluation principles, but the matrix is materially outdated and scoped to the old Hyperset-owned agent/dashboard architecture.**

## Verified principles

- Use deterministic graders wherever possible.
- Keep cases and results under Hyperset control.
- Compare changes against the same cases.
- Inspect traces/attempt details to diagnose failures.
- Use explicit, versioned rubrics for judgment-based checks.
- Do not make one hosted vendor the system of record.

## Corrections

### Ragas is not RAG-only

Current Ragas documentation includes agent/tool-use metrics, tool-call F1/accuracy, agent goal accuracy, and SQL execution/equivalence metrics.

### DeepEval has explicit MCP and tool-use evaluation

Current DeepEval documentation includes MCP evaluation and tool correctness. Any adoption remains optional; Hyperset should own its case/result schema.

### OpenAI product sunset needs precise wording

OpenAI announced that the Agent Builder and Evals products introduced with AgentKit will no longer be available after 2026-11-30. At the same time, current OpenAI API documentation still exposes Evals and Graders APIs.

Therefore:

- do not depend on the hosted product as Hyperset's system of record;
- do not state that all OpenAI evaluation APIs or the open-source `openai/evals` project necessarily disappear;
- recheck API availability before building an optional adapter.

### Exact counts and popularity claims should be removed

Counts such as "10 graders," "50+ metrics," or GitHub star totals change quickly and do not affect the v0 decision.

### LLM-as-judge is not "unavoidable"

Use a model grader only when deterministic predicates and human review cannot represent the requirement adequately. Trust behavior can combine deterministic claim/source checks, explicit unsupported-claim detection, model grading, and human calibration.

### Exact artifact/trajectory matches can reject correct behavior

Cases should support:

- required context/assets;
- prohibited context/assets;
- optional valid alternatives;
- required outcome predicates;
- order constraints only where semantically required.

### Dashboard quality is not a P0 dimension

Hyperset v0 does not own dashboard generation. Remove dashboard aesthetics/layout as a release gate.

## v0 evaluator dimensions

1. context/concept selection;
2. source/asset selection;
3. lifecycle and freshness behavior;
4. conflict/deprecation behavior;
5. required caveats and validations;
6. provenance completeness;
7. unsupported claims;
8. optional fixture-backed result equivalence;
9. latency/token/tool diagnostics as non-blocking initially.

## Superset connector implementation contract

### Transport adapters

Implement separate source adapters that feed one normalized contract:

```python
class SupersetSource(Protocol):
    def test_connection(self) -> ConnectionTest: ...
    def snapshot(self, checkpoint: ConnectorCheckpoint | None) -> ConnectorSnapshot: ...

class SupersetNormalizer(Protocol):
    def normalize(self, snapshot: ConnectorSnapshot) -> Iterable[ObservedAssetInput]: ...
```

Source adapters:

- `SupersetExportBundleSource`
- `SupersetRestSource`

### Required normalized asset types

- database/connection reference, without plaintext credentials;
- dataset;
- column;
- metric;
- chart;
- dashboard;
- typed relationships among them;
- source capability and limitation records.

### Required identity

Each observed asset version includes:

- Hyperset connection ID;
- connector type/version;
- source system version;
- source asset type;
- stable external UUID when available;
- external numeric ID where relevant;
- transport-specific locator;
- source modified timestamp when available;
- first/last seen timestamps;
- complete/partial snapshot status;
- raw payload hash/reference;
- normalized searchable fields.

Do not use slugified names as the only identity. Names can collide and change.

### Relationship resolution

- Prefer UUID relationships from official exports.
- Use numeric IDs only within the same source instance and transport context.
- Preserve unresolved relationships with warnings instead of silently dropping them.
- Never infer dashboard membership only from a field that the inspected export does not emit.

### Change detection

- Hash a canonical representation of the raw source record and normalized relationship set.
- Repeated unchanged snapshots create no new asset version.
- A complete snapshot can mark missing assets deleted/inaccessible.
- A partial/failed snapshot must never imply deletion.
- Renames should preserve identity when stable UUID/external ID is unchanged.

### Secret handling

- Never persist plaintext passwords, access tokens, secret keys, or decrypted encrypted extras in observed payloads.
- Scrub logs and errors.
- Test masked connection URIs and explicit secret fields.
- Store local connector secrets through a separate configuration/secret boundary.

## Required real-source test matrix

### Superset 6.1.0 release gate

1. Start a pinned upstream Superset 6.1.0 image in Docker.
2. Initialize its metadata DB through supported commands.
3. Seed the deterministic revenue assets.
4. Export database/dataset/chart/dashboard assets through official endpoints.
5. Save the unmodified ZIP as a generated test artifact or reproducible build output.
6. Sync the same instance through live REST detail endpoints.
7. Normalize both modes and compare stable identity/relationships while preserving transport-specific raw payloads.
8. Repeat sync and assert zero new versions.
9. Change metric SQL/description/owner independently and assert the correct semantic changes.
10. Rename a dataset/dashboard and preserve stable UUID identity.
11. Remove an asset in a complete snapshot and retain history with deleted/inaccessible state.
12. Simulate partial failure and assert no deletion inference.
13. Confirm secrets never appear in Postgres raw payloads, logs, traces, or API responses.
14. Confirm the processor receives deterministic change records.

### Historical version tests

A Superset version is not supported until the same suite passes against a real pinned instance. Hand-authored fixtures may supplement, but never replace, the real-source contract.

## Implementation no-go list for Codex

Do not:

- implement from `docs/architecture.md` until it is rewritten;
- treat `docs/context-object-model-v0.md` as current;
- extend the current semantic extractor as the persistence model;
- discard raw connector payloads;
- make source popularity/certification equal canonical truth;
- infer owner fields from a transport that does not provide them;
- use names/slugs as sole stable identity;
- implement Superset REST write compatibility;
- build a metric compiler or warehouse executor;
- let processor output approve governed context;
- gate v0 on an external model provider or hosted eval service;
- claim support for versions/transports not covered by the real-source suite.

## Resolved questions

- **What is Hyperset?** A connector-driven analytics context system.
- **What is the v0 source?** Real local Superset, read-only.
- **What is the v0 store?** Postgres.
- **Does v0 need DynamoDB?** No.
- **Does v0 replace Superset?** No.
- **Does v0 execute production warehouse queries?** No.
- **Can automated discovery approve context?** No.
- **How do agents consume context?** Model-agnostic HTTP/MCP.
- **What proves value?** Deterministic raw-observation vs. governed-context evaluations.
- **What is the first supported Superset target?** Pinned local 6.1.0, exact tested transports.

## Remaining questions requiring implementation spikes

These are narrow engineering decisions, not product ambiguity:

1. Which exact Superset REST fields/endpoints produce the minimal complete v0 snapshot with acceptable request count?
2. How are local Superset credentials stored and rotated without leaking into observed payloads?
3. What is the canonical stable identity when bundle UUID and live REST numeric ID are both available?
4. Which source fields count as semantic change vs. raw-payload-only change?
5. Which owner/audit fields require live detail calls because the export omits them?
6. How is a source connector version/capability matrix represented in Postgres and exposed through the API?
7. Should generated upstream fixtures be checked into Git LFS, regenerated in CI, or stored as small deterministic ZIPs?
8. Which first 20 revenue cases protect the context model without hard-coding one valid agent trajectory?

Each answer must be resolved through a fixture, contract test, or ADR—not an undocumented assumption.

## Follow-up mapping

- **Issue #27:** replace current extractor assumptions; implement transport-specific lossless connector and real-source tests.
- **Issue #26:** keep observed, governed, review, evaluation, and evidence models distinct; do not port old `Entity`/`AnalyticalIntent` model wholesale.
- **Issue #25:** implement current context-quality dimensions; remove dashboard generation as a P0 gate.
- **Issue #28:** generate real upstream Superset assets/bundles and deterministic drift scenarios.
- **Issue #30:** preserve exact source snapshot, raw payload reference, review decision, and retrieval versions.
- **Issue #35:** rewrite or mark superseded all old architecture/context/agent-runtime documents.
- **Issue #36:** make the real Superset connector contract and credential-free eval subset CI gates.
- **Issue #34:** do not pass the release gate until the real-source connector and review loop run after container restart.

## Primary sources consulted

### Apache Superset

- Source tag 6.1.0: <https://github.com/apache/superset/tree/6.1.0>
- Dataset schemas: <https://github.com/apache/superset/blob/6.1.0/superset/datasets/schemas.py>
- SQLA models: <https://github.com/apache/superset/blob/6.1.0/superset/connectors/sqla/models.py>
- Chart export: <https://github.com/apache/superset/blob/6.1.0/superset/commands/chart/export.py>
- Dashboard export: <https://github.com/apache/superset/blob/6.1.0/superset/commands/dashboard/export.py>
- Dataset export: <https://github.com/apache/superset/blob/6.1.0/superset/commands/dataset/export.py>
- Database model/URI masking: <https://github.com/apache/superset/blob/6.1.0/superset/models/core.py>
- `superset-core` model interfaces: <https://github.com/apache/superset/blob/6.1.0/superset-core/src/superset_core/common/models.py>
- API documentation: <https://superset.apache.org/docs/api/>

### Context and semantic systems

- dbt relationships test: <https://github.com/dbt-labs/dbt-core/blob/main/crates/dbt-loader/src/dbt_macro_assets/dbt-adapters/macros/generic_test_sql/relationships.sql>
- dbt MCP server: <https://github.com/dbt-labs/dbt-mcp>
- Cube documentation: <https://docs.cube.dev/>
- DataHub documentation: <https://docs.datahub.com/>
- DataHub MCP server: <https://github.com/acryldata/mcp-server-datahub>
- AtScale MCP documentation: <https://documentation.atscale.com/container/connecting-with-ai/manage-mcp-server>
- Apache Ossie / former OSI: <https://github.com/open-semantic-interchange/OSI>

### Evaluation systems

- OpenAI Evals API: <https://platform.openai.com/docs/api-reference/evals>
- OpenAI Graders API: <https://platform.openai.com/docs/api-reference/graders>
- OpenAI AgentKit wind-down notice: <https://openai.com/index/introducing-agentkit/>
- Ragas metrics: <https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/>
- DeepEval MCP evaluation: <https://deepeval.com/docs/evaluation-mcp>
- Phoenix evaluation/tracing: <https://arize.com/docs/phoenix/>
- LangSmith trajectory evaluation: <https://docs.langchain.com/langsmith/trajectory-evals>

## Audit conclusion

The research contains substantial useful evidence, but the old synthesis was wrong for the current product. The most dangerous failure mode is not a missing feature; it is implementing a coherent semantic-layer/Superset-replacement architecture that the manifesto has explicitly rejected.

The v0 implementation should now be straightforward:

1. run real Superset locally;
2. ingest exact source evidence through tested read-only transports;
3. preserve immutable observations and provenance in Postgres;
4. detect gaps/drift through deterministic processing;
5. require human review for governed meaning;
6. serve exact context versions through HTTP/MCP;
7. prove improvement and prevent regressions through local deterministic evaluations.

Anything outside that loop is either post-v0 or a separate product decision.