# The Hyperset Hive-Mind Manifesto

**Hyperset is the flexible-yet-governed analytics Hive-Mind: existing systems
contain the assets; Hyperset supplies the shared understanding.**

## The Problem

Organizations already have years of analytics work in systems such as Superset, Looker, Power BI, dbt, warehouses, catalogs, and internal documentation.

Those systems contain valuable assets:

- datasets and columns,
- metrics and calculated fields,
- dashboards and charts,
- SQL and relationships,
- owners and descriptions,
- certifications and usage patterns.

But an asset is not the same thing as understanding.

An experienced analyst still knows things the system often does not express clearly:

- which definition is actually canonical,
- which dataset is trusted for a particular question,
- which join or filter is mandatory,
- which dashboard is stale or misleading,
- which alternatives are deprecated,
- who is responsible for approving a change,
- what evidence makes an answer trustworthy.

That knowledge is fragmented across BI tools, SQL, documentation, conversations, and people's memories.

AI makes this gap more important. Generating SQL is no longer the hardest part. The hard part is knowing what the business means, what the agent should trust, what has changed, and when a human needs to review the context.

Hyperset exists to make that understanding explicit, governed, searchable, and usable by AI.

## The Product Thesis

Hyperset is a **connector-driven analytics Hive-Mind for trusted answers**: a
walkable knowledge graph that can improve through use without allowing a model,
connector, or UI action to silently create authority.

It connects to the tools where analytics assets already live, preserves what
those systems say, and combines that evidence with business context the company
already owns in Git. Domain experts keep using their normal code-review
workflow; Hyperset makes the merged context searchable, testable, and usable by
agents.

Hyperset does not replace the BI layer. It sits above and alongside it.

```text
Superset / Looker / Power BI / dbt / catalogs / warehouses
                              |
                              v
                     Hyperset connectors
                              |
                              v
                     Observed analytics assets
                              |
                              v
              Gap detection, drift checks, and proposals
                              |
                              v
                  Company-owned context in Git
                              |
                              v
              Search / API / MCP for Claude, Codex,
                    internal agents, and applications
```

Connectors are how Hyperset meets an organization where it already is. Hyperset
does not become another semantic source of truth. The durable product is the
operational layer that binds Git-owned meaning to observed evidence, validates
how agents intend to use it, evaluates outcomes, and keeps the result available
through stable agent interfaces.

## Connectors Observe. Humans Govern Meaning.

A connector may observe that Superset contains a metric with a particular SQL expression, owner, dashboard usage pattern, and modification time.

That observation is useful evidence. It is not automatically business truth.

Hyperset separates two fundamentally different things:

### Observed assets

Observed assets are lossless records of what a connected source system contains.

They preserve:

- source system and version,
- external identifiers,
- raw source payloads,
- normalized relationships,
- ingestion and change timestamps,
- unsupported or source-specific fields.

Observed assets may be popular, certified, or recently edited. None of those signals alone make them canonical.

### Governed context

Governed context expresses what the organization has decided should be trusted.

It may define:

- canonical metric meaning,
- approved datasets and joins,
- required filters and valid dimensions,
- freshness expectations,
- known alternatives and conflicts,
- deprecated or prohibited sources,
- validation rules,
- business ownership,
- review and approval history.

Agents may discover patterns and propose context changes. Humans with domain
knowledge approve, edit, reject, deprecate, and own that meaning through the
company's Git review and merge process.

A connector must never silently promote an observation into governed truth.

## People Ask Questions, Not Domain Identifiers

The exact domain name is an implementation detail, not a prerequisite for a
trusted answer.

A person should be able to ask an ordinary business question. A lightweight
agent may interpret that question and rank where to look. Hyperset then resolves
the selected domain and concepts exactly, pins their Git and source provenance,
and validates the proposed analytical plan deterministically.

Semantic relevance may choose where to look. It may never decide what is
governed. That boundary lets Hyperset remain useful to ordinary users without
turning probabilistic retrieval into business truth.

## Context Is the Product

Dashboards are outputs. Charts are outputs. SQL is an execution artifact. BI tools remain responsible for the analytical experiences they already provide.

Hyperset organizes around the understanding needed to use those systems safely:

- metrics,
- entities and dimensions,
- datasets and relationships,
- analytical intent,
- ownership,
- freshness,
- validation,
- conflicts,
- evidence,
- review state.

The core question is not:

> How should Hyperset render this dashboard?

It is:

> What context does a human or agent need to use these existing analytics assets correctly?

## The Offline Processor Is a Core Product Surface

Context becomes stale unless the system actively maintains it.

Hyperset continuously processes connector snapshots and asks:

- What changed in the source system?
- Which governed definitions reference renamed or deleted assets?
- Which metric expressions now conflict?
- Which assets have no meaningful description or owner?
- Which context has exceeded its review interval?
- Which freshness checks are failing?
- Which evaluation cases changed after a connector sync?
- Which agent questions repeatedly fall back to raw assets?

The processor should produce prioritized, explainable review work rather than silently rewriting trusted context.

```text
Connector snapshot
       |
       v
Diff and normalize observations
       |
       v
Detect gaps, conflicts, drift, and stale context
       |
       v
Propose the smallest context or validation change
       |
       v
Run affected evaluations
       |
       v
Create a human review task
       |
       v
Publish only after approval
```

The system should explain why something needs review, what changed, which assets and agents are affected, and what the proposed resolution would do.

## Domain Experts Should Be Able to Govern Context

Maintaining analytical meaning should not require editing internal graphs, YAML formats, or connector payloads.

A business domain expert should be able to:

- write or revise a definition in plain language,
- select the approved source,
- add a required filter or warning,
- resolve a conflict,
- deprecate an obsolete definition,
- assign or accept ownership,
- review the affected evaluations,
- approve a new version.

Hyperset should make the governance workflow easier than allowing meaning to remain hidden in dashboards and people's memories.

## Agents Consume Context; Hyperset Does Not Need to Own the Agent

Hyperset should work with Claude, Codex, internal agents, and future model providers through stable APIs and MCP tools.

The initial agent-facing surface should remain narrow and model-agnostic:

- search connected analytics assets,
- find governed metrics and concepts,
- retrieve dataset and join context,
- inspect conflicts and deprecations,
- retrieve required validations,
- disclose freshness and review state,
- return provenance for every context result.

Hyperset does not need to own the conversational UI, the model loop, or the customer's BI query engine.

A customer may use Hyperset in two ways:

1. **Context-only mode** — the customer's agent already has a warehouse or BI query tool; Hyperset supplies the meaning and trust context.
2. **Evaluation mode** — a local or test executor is used to measure whether the context improves analytical behavior.

Query execution inside the local benchmark is test infrastructure. It is not the defining product boundary.

## Trust Must Be Inspectable

Every trusted context response should make its provenance visible:

- which connector observation supports it,
- which governed version was selected,
- who approved it,
- when it was last reviewed,
- which conflicts or caveats remain,
- what freshness state applies,
- which evaluations protect the behavior.

When an agent produces an answer, Hyperset should make it possible to attach the exact context versions and validations used. Hyperset may store query or result references supplied by an external agent, but it should not claim to have executed or validated work it did not perform.

Trust is not a confidence number. It is an inspectable chain of observations, decisions, checks, and evidence.

## Storage and Deployment Must Remain Replaceable

Hyperset's domain model should not depend on one deployment environment.

The first implementation uses Postgres because the product requires relational workflows:

- versioned assets and context,
- ownership and review queues,
- conflicts and relationships,
- evaluation history,
- provenance and change tracking,
- transactional human approval.

Postgres JSONB can preserve connector-specific payloads while typed relational fields support search, review, and governance.

DynamoDB may become a future storage implementation for AWS-native or serverless deployments. It is not a v0 requirement. Storage is accessed through narrow repository interfaces so future backends can implement the capabilities they support without pretending Postgres and DynamoDB have identical query semantics.

## v0: A Local Docker Proof

The first product milestone is deliberately local and testable.

A developer should be able to run Hyperset with Docker Compose and evaluate the complete idea without production infrastructure.

The v0 stack is:

- a local Hyperset API and MCP service,
- Postgres as the system of record,
- a background processor/worker,
- a Superset connector using fixtures, exports, or a local Superset instance,
- a minimal human review experience,
- a local evaluation harness,
- deterministic sample analytics assets and questions.

The v0 product flow is:

```text
Local Superset assets
       |
       v
Read-only Superset connector
       |
       v
ObservedAsset versions in Postgres
       |
       v
Offline gap, conflict, drift, and freshness processing
       |
       v
Human review and governed context approval
       |
       v
MCP/API search from Claude, Codex, or a deterministic test client
       |
       v
Local evaluations showing whether the context improves behavior
```

The v0 is complete when this flow works end to end with one command, persists across restarts, exposes reviewable context, and demonstrates measurable value over using raw connected assets alone.

The v0 does not need cloud hosting, multi-region infrastructure, enterprise authentication, DynamoDB, or production-scale connector coverage.

## Superset Is the First Connector, Not the Product Center

Superset is the first BI connector because it is open, inspectable, and provides realistic datasets, metrics, charts, dashboards, ownership, and relationships.

The Superset connector should support a narrow read-only contract first:

- connect to a local instance or ingest an official export bundle,
- capture supported assets losslessly,
- preserve source IDs and raw payloads,
- detect changes between syncs,
- never write back by default,
- never convert source metadata directly into trusted context.

The v0 source boundary should support the two complementary systems needed for
the first product proof: Superset for BI behavior and DataHub for catalog,
domain, ownership, glossary, and lineage evidence. It should make later Looker
and Power BI connectors possible without requiring a generic framework that
delays the first working product.

The first two adapters prove the product need. Their repeated behavior earns
the smallest common connector contract; it does not justify a broad SDK.

## Flexible Governance

Hyperset should preserve exploration while making trust explicit.

Useful context can move through stages such as:

```text
Observed -> Candidate -> In review -> Approved -> Deprecated
```

Observed assets remain immutable source evidence. Candidate context can be machine-generated. Approved context requires a human decision. Deprecated context remains inspectable for history and conflict explanation.

The goal is not fully autonomous governance. The goal is to use agents to reduce the cost of discovering and maintaining context while keeping human authority clear.

## Evaluation Is Part of the Product

Hyperset should be able to demonstrate that its context is useful and remains useful.

Evaluations should measure questions such as:

- Did the agent choose the governed metric?
- Did it avoid a deprecated source?
- Did it apply the required filter or join rule?
- Did it notice stale or disputed context?
- Did the connector change invalidate prior behavior?
- Did a proposed context edit improve the affected cases without causing regressions?

The local evaluator may use deterministic planners, Claude, Codex, or other adapters. No single model provider is the product.

Evaluation results should feed the human review process. They should not automatically approve context.

## Core Principles

1. **Connect to the existing stack; do not demand migration first.**
2. **Treat connector data as observation, not truth.**
3. **Make governed context the durable product.**
4. **Keep human domain experts in control of meaning.**
5. **Continuously detect gaps, drift, staleness, and regressions.**
6. **Expose context through model-agnostic APIs and MCP.**
7. **Keep BI, visualization, and warehouse execution outside the core boundary.**
8. **Make provenance and review history inspectable.**
9. **Build Postgres-first and local-first, without hard-coding the future deployment model.**
10. **Prove value through evaluations before broadening the connector surface.**

## What Hyperset Is Not

Hyperset is not:

- a replacement for Superset, Looker, Power BI, or another BI frontend;
- a dashboard or visualization product;
- a warehouse transformation framework;
- a mandatory replacement semantic layer;
- a generic enterprise data catalog;
- a model provider or general-purpose agent framework;
- a system that automatically declares discovered metadata to be canonical;
- a system that claims causal or analytical certainty without evidence;
- a production cloud platform in v0.

## The Future

The long-term opportunity is a lightweight intelligence layer that enterprises can place above any analytics stack.

As connectors expand, Hyperset can help an organization preserve meaning across BI tools, catalogs, transformations, and agents without forcing those systems into one product.

In that future:

- connected assets remain usable where they already live,
- business experts maintain context without becoming data engineers,
- agents receive the exact meaning and constraints they need,
- changes create review tasks before they create silent analytical drift,
- evaluations protect institutional knowledge,
- every answer can point back to its context and evidence.

The next generation of analytics will not be defined by who owns the dashboard. It will be defined by whether humans and agents can understand the business correctly across every system where analytics lives.

Hyperset exists to become that smart context system.
