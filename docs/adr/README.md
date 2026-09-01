# Architecture Decision Records

This directory is the durable index of Hyperset's architecture and product
boundary decisions. Each record uses the format **Status, Context, Decision,
Consequences** and names any superseding or extending record explicitly. An ADR
is not rewritten when circumstances change; a newer ADR records the change.

## Status legend

- **Accepted** — ratified direction. The implementation status, if any, is
  stated in the ADR itself.
- **Proposed** — design direction awaiting ratification or implementation gates.
- **Superseded** — retained for history; follow the newer ADR named in the record.

## Complete index

| ADR | Status | Decision |
| --- | --- | --- |
| [0001](0001-observed-vs-governed.md) | Accepted | Observations and governed context are separate, versioned records. |
| [0002](0002-postgres-first-storage.md) | Accepted | Postgres is the v0 system of record. |
| [0003](0003-superset-connector-scope.md) | Accepted | Superset is a read-only, transport-specific connector in v0. |
| [0004](0004-real-source-contract-tests.md) | Accepted | Compatibility is proven with real-source contract tests. |
| [0005](0005-human-approval-boundary.md) | Accepted | Human approval is mandatory for governed meaning. |
| [0006](0006-model-agnostic-retrieval.md) | Accepted | HTTP/MCP retrieval stays model agnostic. |
| [0007](0007-evaluation-as-quality-control.md) | Accepted | Evaluation is context-quality control, not a Hyperset-owned agent product. |
| [0008](0008-local-docker-v0.md) | Accepted | Local Docker comes before cloud deployment. |
| [0009](0009-vertical-slice-first.md) | Accepted | Build one real vertical slice before horizontal platform breadth. |
| [0010](0010-two-source-evaluation-loop.md) | Accepted | Prove a two-source governed drafting and evaluation loop. |
| [0011](0011-configurable-curator-and-dual-governance.md) | Accepted | Support a configurable curator and UI or Git governance. |
| [0012](0012-git-owned-context-authority.md) | Accepted | Git-owned context is the v0 authority. |
| [0013](0013-split-benchmark-gate.md) | Accepted | Split benchmark evidence into per-PR traces and scheduled live arms. |
| [0014](0014-no-branch-protection-on-this-plan.md) | Accepted | One merger is the control; checks report without branch protection for this plan. |
| [0015](0015-no-release-process-until-a-publication-event.md) | Accepted | Hold release process until a publication event; beads hold the notes. |
| [0016](0016-declared-coverage-claim-not-question-reading.md) | Accepted | Coverage is declared; Hyperset does not read the question as a coverage claim. |
| [0017](0017-evidence-corroborates-git-context-it-does-not-gate-it.md) | Accepted | Evidence corroborates Git context; it does not gate it. |
| [0018](0018-schema-version-versions-the-answer-not-the-request.md) | Accepted | `SCHEMA_VERSION` versions the answer, not the request. |
| [0019](0019-assist-mode-may-reason-governance-may-not.md) | Accepted | Assist mode may reason; governance may not. |
| [0020](0020-hyperset-hosts-the-agent-loop-it-never-owns-it.md) | Accepted | Hyperset hosts the agent loop; it never owns it. |
| [0021](0021-a-contradiction-is-a-join-not-a-rule.md) | Accepted | A contradiction is a join over evidence, not a rule that invents authority. |
| [0022](0022-natural-language-selection-before-exact-resolution.md) | Accepted | Natural-language selection precedes exact governed resolution. |
| [0023](0023-table-and-pipeline-context-identity.md) | Accepted | Table and pipeline identities are governed explicitly. |
| [0024](0024-ai-sourcing-references-live-lookup.md) | Accepted | AI sourcing proposes references; connectors observe and lookups read. |
| [0025](0025-review-ops-expand-the-mcp-trust-surface.md) | Accepted | Review operations expand MCP's trust surface, but remain proposal-only and PII guarded. |
| [0026](0026-encrypted-at-rest-writeback-token.md) | Accepted | Write-back tokens may be encrypted at rest and keyed from the environment. |
| [0027](0027-github-app-writeback-auth.md) | Accepted | GitHub write-back authenticates with short-lived GitHub App tokens. |
| [0028](0028-the-adapter-boundary.md) | Accepted | Adapters may change the shape carrying meaning, never create meaning. |
| [0029](0029-the-per-source-facet-vocabulary.md) | Accepted | Sources may state governed metadata through a per-source facet vocabulary. |
| [0030](0030-the-authorization-boundary.md) | Accepted | Transport identity plus a fail-closed reader gate protects every operation. |
| [0031](0031-the-domain-hierarchy.md) | Accepted | The existing graph carries a bounded, depth-agnostic governed hierarchy. |
| [0032](0032-the-sql-execution-boundary.md) | Proposed | Hyperset serves governed context and does not execute, generate, or validate SQL. |
| [0033](0033-the-feedback-agent.md) | Accepted | Trace-linked feedback, human citation decisions, and search-to-review proposals are served; autonomous recommendation and notification remain design-only. |
| [0034](0034-the-governed-relationship-vocabulary.md) | Proposed | Cross-domain relationships are declared, governed, and never inferred. |
| [0035](0035-layered-deployment-configuration.md) | Proposed | Customer overlays layer over checked-in defaults; validation fails closed and secrets stay by reference. |
| [0036](0036-bring-your-own-knowledge-graph-authority-adapters.md) | Proposed | Git is one authority backend; provider-neutral adapters support native KG reads and write-backs. |
| [0037](0037-tenant-workspace-isolation.md) | Accepted | An additive, fail-closed workspace dimension isolates every tenant's config, sources, and observed evidence with no cross-tenant leakage. |
| [0038](0038-playground-review-settings-navigation.md) | Accepted | Keep product surfaces visible, diagnostics compact, and settings focused by URL-addressable tab. |
| [0039](0039-v0.1.0-enterprise-readiness-release-focus.md) | Accepted (historical release label) | The v0.1.0-named cycle prioritized enterprise hardening; ADR 0042 supersedes its release name/time box. |
| [0041](0041-the-knowledge-graph-is-flexible-yet-governed-and-improves-through-use.md) | Accepted | The destination is a flexible-yet-governed knowledge graph that improves through use; the governed projection is its canonical core, observed/proposed edges are first-class but never canonical until human write-back. Amends the end-state reading of 0031/0034 and v0-foundation. |
| [0042](0042-first-public-release-is-v0.0.1.md) | Accepted | The first public release target is v0.0.1; v0.1.0 was an unpublished cycle name. |

## Current decision map

These records are the shortest route through the decisions most relevant to
the local Docker product:

- **Trust and authority:** [0001](0001-observed-vs-governed.md),
  [0005](0005-human-approval-boundary.md),
  [0012](0012-git-owned-context-authority.md), and
  [0036](0036-bring-your-own-knowledge-graph-authority-adapters.md).
- **Adapters, domains, and permissions:**
  [0028](0028-the-adapter-boundary.md),
  [0030](0030-the-authorization-boundary.md), and
  [0031](0031-the-domain-hierarchy.md).
- **Product destination (the knowledge graph):**
  [0041](0041-the-knowledge-graph-is-flexible-yet-governed-and-improves-through-use.md)
  — flexible-yet-governed, improves through use; amends the end-state reading of
  [0031](0031-the-domain-hierarchy.md) and
  [0034](0034-the-governed-relationship-vocabulary.md).
- **Deployment and enterprise configuration:**
  [0035](0035-layered-deployment-configuration.md) and
  [0037](0037-tenant-workspace-isolation.md).
- **Release identity and historical focus:**
  [0042](0042-first-public-release-is-v0.0.1.md) — the first public target is
  v0.0.1 — and [0039](0039-v0.1.0-enterprise-readiness-release-focus.md) — the
  preserved enterprise-hardening cycle whose release label 0042 supersedes.
- **Review, write-back, and operations:**
  [0025](0025-review-ops-expand-the-mcp-trust-surface.md),
  [0026](0026-encrypted-at-rest-writeback-token.md), and
  [0027](0027-github-app-writeback-auth.md).
- **Current frontend shell:**
  [0038](0038-playground-review-settings-navigation.md), with the
  [v1 UX page map](../ux/v1-page-map-and-persona-flows.md) as the product-flow
  companion.

## How to add an ADR

Use the next four-digit number, give the file a decision-oriented slug, and
state the status at the top. Link the new record here in the complete index.
If it changes an existing decision, name the older ADR and say whether the new
record extends, amends, or supersedes it. Keep implementation gates and
acceptance tests in the ADR when they are necessary to prevent a design-only
decision from being mistaken for shipped behavior.
