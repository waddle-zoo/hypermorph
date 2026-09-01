# Analytics semantic and context platform landscape

> [!NOTE]
> **Status: current, time-stamped market research. Last verified 2026-07-25.**
> Product capabilities change quickly. This document informs positioning and
> future interoperability; it is not an implementation specification. The
> manifesto and current v0 issues define what Hyperset builds.

## Research question

Where does Hyperset fit among semantic layers, BI modeling systems, metadata
platforms, context platforms, and emerging interoperability standards?

## Executive conclusion

The market is converging on three related but distinct layers:

1. **Semantic computation and query systems** define metrics, dimensions,
   joins, policies, and query generation.
2. **Metadata and context platforms** connect many systems, preserve lineage and
   governance, and increasingly expose context to AI through APIs and MCP.
3. **Interchange standards** attempt to make semantic definitions portable
   across products.

Hyperset v0 belongs primarily in the second category. It connects to existing
analytics systems, preserves their assets as observations, adds human-governed
business context, detects drift, and measures whether that context improves AI
behavior. It does not compile production queries or replace the customer's BI
and semantic systems.

The closest broad product overlap identified in this review is DataHub's current
context-platform direction. DataHub now documents connectors, governance,
lineage, AI-oriented context delivery, MCP, human review, and context validation.
Hyperset therefore cannot differentiate itself merely by saying that catalogs
lack MCP or governance. Its v0 differentiation hypotheses must be tested:

- materially lighter local/self-hosted deployment;
- BI-first depth beginning with a rigorously tested Superset connector;
- a focused domain-expert context-review workflow;
- explicit separation of raw observations from approved meaning;
- deterministic raw-versus-governed context evaluation;
- model-agnostic use from Claude, Codex, and internal clients.

These are product hypotheses, not established market facts.

## Category 1: semantic computation and query systems

### dbt Semantic Layer and MetricFlow

The dbt Semantic Layer uses MetricFlow concepts to define metrics, semantic
models, entities, dimensions, and measures, then serves consistent metric
queries across consumers.

Relevant lessons:

- typed metric definitions and version control are valuable;
- entity and join concepts matter when a system compiles queries;
- central definitions can reduce duplicated BI logic;
- APIs and agent interfaces can expose governed calculations.

Boundary for Hyperset:

- Hyperset may connect to and observe dbt semantic assets later;
- it may store approved guidance about which definition to use;
- it does not need to reproduce MetricFlow's query planner or require every
  observed metric to bind to a Hyperset-native entity.

### Cube

Cube models cubes, dimensions, measures, joins, access policies, and
pre-aggregations, then provides APIs for querying the modeled layer.

Relevant lessons:

- a stable API over modeled analytics is useful to agents and applications;
- join and measure semantics need explicit contracts;
- caching and pre-aggregation are specialized query-serving concerns.

Boundary for Hyperset:

- Cube is a possible future connector or context source;
- Hyperset should not copy its query execution, cache, or pre-aggregation
  infrastructure into v0.

### Looker and LookML

LookML defines explores, views, dimensions, measures, joins, access controls,
and reusable modeling logic used by Looker's query and visualization runtime.

Relevant lessons:

- business logic often lives in BI-specific source code rather than a separate
  catalog;
- ownership, descriptions, hidden filters, joins, and usage relationships are
  important connector evidence;
- a future Looker connector must preserve source-specific structures rather
  than forcing them into Superset-shaped payloads.

Boundary for Hyperset:

- Looker is a plausible second connector after the Superset boundary is proven;
- Hyperset should not implement LookML execution.

### AtScale and similar universal semantic layers

AtScale and related products advertise governed metrics, multidimensional
models, acceleration, and broad BI compatibility.

Relevant lesson:

- enterprises value one consistent analytical meaning across multiple tools.

Caution:

- vendor capability and performance claims are evidence of documented product
  intent, not independent proof that one architecture is superior;
- Hyperset should not use marketing comparisons as its design authority.

## Category 2: metadata and context platforms

### DataHub

DataHub began as a metadata/catalog platform and now explicitly positions a
broader context platform for humans and AI. Current official material describes:

- ingestion from many enterprise systems;
- metadata graph, lineage, ownership, domains, glossary, and governance;
- APIs and an official MCP server;
- generated and curated context;
- routing context to people for review;
- validation and monitoring for AI/data use cases;
- managed and self-hosted/open-source components, with product-specific
  capability differences.

This creates meaningful overlap with Hyperset's vision.

What Hyperset can learn:

- connector breadth and a typed metadata graph become valuable over time;
- context delivery through MCP is becoming expected rather than novel;
- ownership, review, lineage, and policy need to be first-class;
- context quality must be measured, not merely indexed.

What Hyperset must not assume:

- DataHub is only a glossary;
- DataHub lacks MCP;
- catalogs do not support review workflows or AI context;
- connector count alone proves useful context quality.

Differentiation questions Hyperset must answer empirically:

1. Can a new team run the full product locally with substantially less
   operational weight?
2. Does a deep BI connector preserve and explain more of a real analytics
   workflow than a generic catalog ingestion path?
3. Can a domain expert resolve a context problem faster in Hyperset's focused
   review UI?
4. Does the raw-versus-governed evaluator show measurable improvements that a
   general metadata search experience does not expose?
5. Is Hyperset useful without adopting it as the enterprise-wide catalog?

If these answers are not favorable, the product positioning must be revisited.

### Other catalogs and metadata graphs

OpenMetadata, Collibra, Alation, and similar products also cover combinations of
connectors, lineage, glossary, ownership, governance, search, and AI assistance.
They should be evaluated before hosted-market positioning, but they are not v0
implementation dependencies.

The durable lesson is that Hyperset should not claim the category is empty. It
must prove a focused, deployable, high-quality context workflow.

## Category 3: interchange standards

### Apache Ossie (Incubating)

The Open Semantic Interchange work entered the Apache Incubator as Apache Ossie
(Incubating) in 2026. The goal is portable semantic model interchange across
analytics products.

Potential future value:

- import/export of metrics, dimensions, joins, and semantic definitions;
- reduced connector-specific mapping for systems that adopt the standard;
- portable evidence or context links around shared semantic identities.

v0 boundary:

- do not design Hyperset's native Postgres schema around a changing incubating
  specification;
- track the project and consider a future adapter after the local connector and
  governance model are proven;
- preserve source payloads so future remapping remains possible.

## Comparative capability map

This table is intentionally qualitative and should be re-verified before public
use.

| Capability | Semantic query systems | Metadata/context platforms | Hyperset local v0 |
|---|---|---|---|
| Define and execute governed metrics | Core capability | Usually integrates or describes | Not a production executor |
| Model joins/entities | Core for query planning | Captures lineage/relationships | Stores observations and reviewed guidance |
| Connect to BI/catalog/data tools | Varies | Core capability | Superset first; more later |
| Preserve raw source evidence | Varies | Often connector-dependent | Required design principle |
| Human governance/review | Varies | Common/core | Required minimal workflow |
| Detect context drift | Varies | Increasingly common | Core offline processor |
| MCP/agent delivery | Increasingly common | Increasingly common | Core v0 interface |
| Measure context effect on agents | Product-dependent | Emerging | Core v0 proof |
| Local lightweight demo | Product-dependent | Product-dependent | Explicit v0 requirement |
| Render BI dashboards | Some consumers do | No | No |

## What Hyperset should borrow

- connector-neutral stable identity;
- lossless source observations;
- typed relationships and lifecycle state;
- human ownership and review;
- versioned provenance;
- model-agnostic API/MCP delivery;
- portable import/export boundaries;
- evaluation and monitoring of context quality.

## What Hyperset should not build in v0

- metric compilation and production query execution;
- autonomous join-path planning;
- row/column policy enforcement for external systems;
- pre-aggregation and query caching;
- dashboard rendering;
- a universal enterprise catalog before one focused workflow works;
- native storage coupled to Apache Ossie or another evolving standard;
- dozens of shallow connectors before one connector passes a real-source
  contract suite.

## Connector roadmap implication

Superset should remain the first connector because it lets the team prove:

- export and REST transport handling;
- raw asset preservation;
- dashboards, charts, datasets, columns, and metrics;
- source drift and review tasks;
- context retrieval and evaluation.

The generic connector SDK should be extracted only after the Superset connector
has exposed real abstraction needs. A second connector such as Looker or Power
BI then tests whether the abstraction is genuinely source-neutral.

DataHub itself may also become a connector or integration target later: Hyperset
could consume DataHub metadata or publish governed context/evidence into a
larger enterprise catalog. That decision should follow a concrete customer
workflow rather than a desire to duplicate catalog breadth.

## Evaluation and positioning requirements

Before claiming a differentiated product, the local v0 should demonstrate:

- one-command deployment;
- real-source connector correctness;
- a domain expert completing review without YAML/database edits;
- preserved provenance from source to approved context;
- measurable improvement on context-sensitive agent cases;
- explicit behavior when context is stale, disputed, incomplete, or absent;
- usefulness alongside—not instead of—the existing BI tool.

Future competitive research should compare actual workflows and deployment
costs, not feature-checklist marketing pages alone.

## Primary sources

- dbt Semantic Layer: https://docs.getdbt.com/docs/use-dbt-semantic-layer/dbt-sl
- dbt MCP project: https://github.com/dbt-labs/dbt-mcp
- Cube data modeling: https://cube.dev/docs/product/data-modeling
- LookML overview: https://cloud.google.com/looker/docs/what-is-lookml
- DataHub documentation: https://docs.datahub.com/
- DataHub MCP introduction: https://datahub.com/blog/datahub-cloud-v0-3-12/
- DataHub MCP overview: https://datahub.com/blog/mcp-server-101/
- DataHub context management: https://datahub.com/blog/mcp-context-management/
- DataHub Cloud 2.0: https://datahub.com/blog/datahub-cloud-2-0/
- DataHub context platform: https://datahub.com/products/context-platform/
- Apache Ossie incubator status: https://incubator.apache.org/clutch/ossie.html
- Apache incubator project list: https://incubator.apache.org/projects/index.html

## Research follow-ups

- Run a hands-on local comparison with DataHub's current open-source stack and
  document deployment footprint, connector output, review workflow, MCP tools,
  and evaluation capabilities.
- Interview potential users to determine whether BI-first context governance is
  sufficiently distinct from adopting a broader metadata platform.
- Select the second connector based on a real workflow, not theoretical market
  coverage.
- Revisit Apache Ossie after its contracts and adoption stabilize.
