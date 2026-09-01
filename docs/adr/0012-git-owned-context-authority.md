# 0012: Treat Git-owned context as the v0 authority

> **Partially superseded by [ADR-0036](0036-bring-your-own-knowledge-graph-authority-adapters.md) (PROPOSED).** Git is generalized from THE authority to ONE authority *backend*: authority becomes the backend's native human-reviewed revision (a Git merge for Git). This ADR's PRINCIPLE is preserved and generalized — the customer's own workflow owns governance; Hyperset snapshots a revision and never authors, approves, or merges.

Status: accepted.

Supersedes ADR 0011's Hyperset-owned `DomainPack`, dual UI/Git authoring, and
required server-side curator. For Git-owned v0 context it also supersedes ADR
0005's requirement that Hyperset create its own `ReviewDecision` before context
can be treated as authoritative. Extends ADR 0010's two-source evaluation loop.

## Context

Hyperset exists to help people and agents get trusted analytical answers from an
enterprise's existing data stack. The durable business meaning needed to do that
should remain close to the code and workflows that already own it, rather than
being copied into another proprietary authoring system.

In many organizations the best place for domain context is Git: next to dbt,
pipeline, semantic-model, or analytics code, or in a dedicated reviewed context
repository. Engineers and coding agents can update that context in the same work
that changes the underlying implementation, and domain experts can review and
merge it through the organization's existing process.

Hyperset still needs durable snapshots for retrieval, comparison, evaluation,
replay, and provenance. Those operational records must not become a second
source of semantic truth.

## Decision

V0 uses one configured Git repository/ref/path as the authoritative source of
revenue-domain context.

The configured Git context:

- contains the declarative business guidance required by the benchmark;
- is identified by repository, ref, path, and exact commit SHA;
- may be supplied at runtime as a CI-produced Git bundle rather than a
  checkout. The bundle retains the exact commit/tree while the runtime image
  need not contain `.git`; a directory of loose files without that provenance
  is not authoritative context;
- may live beside transformation/semantic code or in a dedicated context repo;
- is treated as organization-owned context once it appears on the configured
  authoritative ref;
- may expose owner metadata through its manifest and/or repository ownership
  conventions such as CODEOWNERS.

The customer's Git workflow owns human governance. Hyperset does not recreate
that workflow or claim to know more about approval than the configured authority
provides. For v0, content on the configured authoritative ref is accepted as the
customer's current context and the exact commit is recorded as provenance.

Hyperset:

- reads the configured Git context and persists immutable snapshots keyed to the
  exact Git identity;
- preserves Superset and DataHub observations separately as source evidence;
- links context claims to observed evidence only when identity/lineage supports
  the relationship;
- detects contradictions, missing evidence, stale dependencies, and evaluation
  regressions without rewriting the authoritative context;
- compiles a `ContextBundle` from the pinned Git context plus linked evidence,
  qualifiers, validation state, and provenance;
- may cache/normalize context in Postgres for runtime retrieval, but every
  semantic field must remain traceable to the authoritative Git snapshot;
- never treats a Postgres-only edit or Hyperset-local approval action as new
  authoritative domain meaning.

The v0 UI is a context-operations surface only: connection/sync health, the
current Git context snapshot, provenance/evidence, findings, and evaluation
state. V0 has no independent context editor or approval workflow.

The server-side LLM curator is removed from the mandatory v0 path. A later
AI-assisted maintenance feature may investigate findings and propose a Git patch
or pull request, but it cannot approve or merge context.

## Consequences

- Git remains the customer-owned semantic authority; Hyperset owns the plumbing
  around synchronization, evidence, validation, evaluation, and agent delivery.
- Postgres remains authoritative for Hyperset operational state—sync runs,
  snapshots, findings, evaluation attempts, notification state, and replay—but
  not for independently authored business meaning.
- ADR 0005 still constrains any future Hyperset-local approval workflow, but that
  workflow is not part of the Git-authoritative v0 path.
- The canonical governance event happens in the customer's Git workflow, outside
  Hyperset. V0 records exact commit provenance rather than recreating the
  customer's code-review system.
- `ContextBundle` remains the stable agent-facing contract.
- Superset and DataHub remain complementary evidence sources, not semantic
  authorities merely because they are connected.
- Context drift/contradiction detection is a maintenance capability, not the
  product identity; the product test remains trusted analytics answers.
- A future curator should propose changes back to Git rather than creating an
  independently approvable Hyperset context object.

## Rejected alternatives

- Make Hyperset's database the canonical home for domain meaning.
- Maintain separate UI-authored and Git-authored approval lifecycles.
- Require customers to migrate existing context into a Hyperset CMS.
- Let an LLM curator create or approve authoritative context directly.
- Treat Superset or DataHub metadata as approved business meaning without an
  explicit customer-owned context source.
