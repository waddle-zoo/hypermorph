# Hyperset research index

The research in this directory has been reconciled with the connector-driven
manifesto and rewritten as implementation-safe background for the local v0.

Research explains evidence, tradeoffs, and unresolved test questions. It does
not override the current GitHub issue acceptance criteria.

## Authority order

When repository sources disagree, use:

1. [`MANIFESTO.md`](../MANIFESTO.md) for the product boundary;
2. current open GitHub issues for implementation and acceptance criteria;
3. current ADRs and primary architecture documents;
4. the reconciled research files in this directory;
5. historical code/tests as evidence of prior implementation only.

[`FACT_CHECK_2026-07-25.md`](./FACT_CHECK_2026-07-25.md) records the audit that
identified factual and product-model errors in the former research. The files
below now incorporate those corrections directly.

## Current research set

| File | Current purpose | Main implementation consequence |
|---|---|---|
| [`context-model.md`](./context-model.md) | Defines observations, governed context, review, evaluation, and evidence boundaries | Source assets never become approved meaning automatically |
| [`from-scratch-bootstrap.md`](./from-scratch-bootstrap.md) | Defines the deterministic local Docker environment | Run a real pinned Superset; never emulate its backend/schema |
| [`superset-metadata-models.md`](./superset-metadata-models.md) | Version-pinned source/transport reference for Superset 6.1 | ORM, REST list/detail, and exports are separate contracts |
| [`superset-version-compat.md`](./superset-version-compat.md) | Defines evidence required for a compatibility claim | v0 supports only the exact real-source 6.1 tuple that passes the contract suite |
| [`superset-annotation-gap.md`](./superset-annotation-gap.md) | Maps Superset signals to missing governed context | Build review/freshness/provenance, not a replacement query engine |
| [`datahub-graphql-v1.md`](./datahub-graphql-v1.md) | Version-pinned source/transport reference for DataHub OSS v1.6.0 | GraphQL is a projection, so losslessness is scoped to a fingerprinted query set |
| [`semantic-layer-landscape.md`](./semantic-layer-landscape.md) | Time-stamped market/category research | Hyperset is a context platform; DataHub overlap must be taken seriously |
| [`agent-eval-framework.md`](./agent-eval-framework.md) | Defines context-effectiveness evaluation | Deterministic outcome graders, accepted alternatives, optional model judges |

## Local v0 model

```text
real pinned Superset 6.1.0
        ↓ export + REST connector
lossless ObservedAsset versions in Postgres
        ↓ offline processor
explainable findings and ReviewTasks
        ↓ human ReviewDecision
GovernedContext versions
        ↓ HTTP / MCP
Claude, Codex, deterministic clients
        ↓
local context-effectiveness evaluations
```

## Product boundary

The local v0 is:

- a read-only connector to existing analytics assets;
- a lossless observation and provenance store;
- a human-governed context system;
- an offline drift/gap processor;
- a focused review workflow;
- a model-agnostic HTTP/MCP context service;
- a deterministic evaluator of context quality.

It is not:

- a Superset-compatible backend or frontend;
- a universal semantic metric compiler;
- a production warehouse query/execution service;
- an autonomous join planner;
- a dashboard runtime;
- an automatic context-approval system;
- a provider-specific conversational agent platform.

## Research quality rules

### Prefer primary sources

For changing software behavior, link to:

- official source code/tag;
- official API/reference documentation;
- official release or project-governance pages.

Vendor product pages can establish what a vendor documents or claims, but not
that one architecture or product is objectively superior.

### Date market claims

MCP support, product packaging, standards, pricing, connector counts, and hosted
evaluation products change quickly. Time-sensitive claims must include a
verification date and should not become hard-coded architecture dependencies.

### Prove connectors with real sources

A connector version/transport is supported only when a pinned real upstream
instance passes its contract suite. Hand-written fixtures are useful unit tests
but cannot establish upstream compatibility.

### Separate facts from product decisions

Examples:

- Fact: Superset exports and REST detail responses expose different fields.
- Decision: Hyperset preserves both payloads and normalizes them to one stable
  observed identity.
- Fact: semantic layers model entities and joins to compile queries.
- Decision: Hyperset v0 stores reviewed join guidance but does not compile
  production queries.

### Turn unresolved questions into tests

Research should end with the smallest implementation spike, fixture, contract
test, ADR, or user study that can resolve the question. Do not leave a coding
agent to invent product semantics while implementing a feature.

## Critical current research finding

DataHub's current context-platform direction overlaps materially with the
Hyperset vision: broad connectors, governance, lineage, MCP delivery, human
review, and AI/context validation. Hyperset must therefore prove a focused
advantage rather than claim the category is empty.

The local v0 tests these hypotheses:

- lighter deployment;
- deeper BI-first connector behavior;
- simpler domain-expert review;
- explicit raw-observation versus approved-context separation;
- measurable context-effectiveness evaluation;
- usefulness alongside an existing BI/catalog stack.

## Coding-agent instruction

Start from the current issue. Read the relevant research file to understand the
evidence and edge cases. When an implementation decision is still listed as an
open question, resolve it with the issue's required test or ADR rather than
copying a historical pattern from existing code.

For Superset work, the minimum proof is a pinned real Superset 6.1.0 instance,
an official generated export, matching live REST evidence, lossless raw payload
preservation, and stable identity/relationship tests.
