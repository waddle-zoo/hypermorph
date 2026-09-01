# What Superset metadata explains—and what Hyperset must govern

> [!NOTE]
> **Status: current gap analysis for the local v0. Last verified 2026-07-25
> against the Superset 6.1 source/API surface.** This document identifies
> missing context and maintenance workflows; it does not justify building a
> replacement semantic query engine.

## Research question

What useful analytical meaning already exists in Superset, what remains absent
or too weakly structured for reliable AI use, and how should Hyperset fill that
gap without replacing Superset?

## Conclusion

Superset contains substantial technical and descriptive context. Hyperset
should preserve those signals as observations, then add the governed lifecycle,
cross-asset reconciliation, review workflow, freshness policy, and provenance
needed for AI clients to know what should be trusted.

The gap is a **context-governance and maintenance gap**, not proof that Hyperset
must compile metrics, plan joins, execute production SQL, or render dashboards.

## What Superset already provides

Availability varies by transport, permissions, source version, and feature
configuration. The connector must record that context rather than assume every
field exists everywhere.

### Database and dataset signals

Superset can provide:

- database/source identity and engine capabilities;
- physical versus virtual datasets;
- schema, catalog, table name, and virtual dataset SQL;
- dataset descriptions;
- columns, calculated columns, metrics, and types;
- owners, tags, certification, warnings, or audit timestamps on selected
  surfaces;
- flags and `extra` configuration that affect exploration/query behavior;
- chart and dashboard usage relationships.

### Column signals

Depending on the endpoint/export, columns can expose:

- name, verbose name, type, description, and expression;
- temporal, filterable, dimensional, and visibility behavior;
- formatting or advanced data type hints;
- dataset membership.

### Metric signals

Superset SQL metrics can expose:

- label/name;
- SQL expression;
- description;
- formatting, currency, warning, or certification metadata when available;
- dataset membership;
- source modification information.

### Chart and dashboard signals

Superset can expose:

- chart names, visualization types, form/query configuration, and datasets;
- dashboard membership and layout;
- dashboard titles, slugs, publication status, filters, and metadata;
- structural graph edges among dashboards, charts, and datasets.

Therefore the statement "Superset has no lineage" is too broad. It has useful
structural relationships. What it does not provide is a complete,
human-governed semantic derivation graph across tools and business decisions.

## Gaps Hyperset must fill

### 1. Canonical business definition

A source expression shows what a Superset metric computes. It does not establish
whether that expression is the company-approved meaning of recognized revenue,
active subscriber, or another business concept.

Hyperset adds:

- plain-language definitions;
- approved implementations;
- owners and reviewers;
- alternatives and conflicts;
- decision rationale and version history.

### 2. Governed lifecycle

Source metadata may be edited and timestamped, but it does not provide
Hyperset's lifecycle:

```text
observed source signal
→ processor finding
→ candidate context
→ human review decision
→ approved/deprecated/superseded context
```

A connector must never map source certification, ownership, popularity, or age
directly to approved Hyperset context.

### 3. Cross-source reconciliation

Superset describes one source system. Enterprises may later connect Looker,
Power BI, dbt, catalogs, documentation, or warehouse metadata. Hyperset must be
able to preserve each observation independently and surface potential matches or
conflicts without choosing the newest or most popular source automatically.

### 4. Explicit caveats and policy

AI clients often need information that is not reliably represented as a single
source field:

- mandatory exclusion of test/internal customers;
- approved source datasets;
- prohibited/deprecated sources;
- valid dimensions;
- grain and join warnings;
- freshness requirements;
- review intervals;
- accounting or business-policy caveats;
- conditions under which raw fallback is acceptable.

### 5. Freshness and change impact

A source timestamp says when an asset changed; it does not say whether the
business definition should be reviewed or which agent evaluations may regress.

Hyperset must track:

- last source observation;
- last human review;
- required review deadline;
- source-versus-context drift;
- affected context and evaluation cases;
- processor findings and review task state.

### 6. Provenance and evidence completeness

An AI client should be able to trace an answer's context back to:

- connection and sync run;
- exact source payload/version;
- processor rule/finding;
- human decision;
- governed context version;
- retrieval response and evaluation evidence.

Missing links should be reported as incomplete evidence, not guessed.

### 7. Safe analytical relationships

Superset's dashboard/chart/dataset graph is useful, but it does not necessarily
prove:

- join cardinality;
- uniqueness of join keys;
- absence of fanout;
- business-approved join direction;
- metric compatibility across datasets.

Hyperset can store reviewed join guidance and required validations without
becoming the query planner.

## Mapping source signals to Hyperset behavior

| Superset observation | Useful signal | Hyperset action |
|---|---|---|
| SQL metric expression | Current implementation | Link as observed evidence; compare with approved guidance |
| Description | Candidate business explanation | Detect missing/change; propose review |
| Owner | Potential accountable person | Preserve source owner; require explicit governance decision |
| Certification | Source-local endorsement | Rank as evidence, never auto-approve |
| Dashboard usage | Importance/adoption signal | Use for impact prioritization, not canonical truth |
| Changed timestamp | Drift signal | Trigger comparison and potentially review |
| Dataset SQL/filter | Hidden implementation behavior | Preserve losslessly; surface material changes |
| Chart/dataset relationship | Structural lineage | Store typed edge; do not infer safe analytical join |
| Deleted/missing asset | Potential deprecation or access loss | Only infer from complete snapshot; create review task |

## Processor findings required for v0

The offline processor should detect at least:

- missing business definition;
- missing accountable owner/reviewer;
- conflicting metrics or descriptions;
- approved context pointing at deleted/inaccessible assets;
- source expression, filter, grain, or relationship drift;
- stale source observation;
- expired context review;
- use of a prohibited source such as `raw_payments`;
- evaluation regression after a proposed change;
- unresolved source relationships;
- materially changed certification/ownership evidence.

Each finding must include exact source/context versions, explanation, severity
factors, suggested owner, and deduplication key.

## Human-review boundary

Models and rules may:

- summarize changes;
- suggest definitions or links;
- identify likely conflicts;
- propose a candidate context edit;
- run targeted evaluations.

They may not:

- create approved context;
- silently change current context;
- resolve a conflict based on popularity alone;
- claim causal meaning from structural usage;
- mark a source as safe for production queries without a reviewed rule.

## What Hyperset should not build from this gap

The following do not follow from the evidence and remain outside v0:

- a universal metric compiler;
- autonomous join-path selection;
- a replacement Superset metadata/API backend;
- production warehouse execution;
- BI chart/dashboard rendering;
- automatic row/column policy enforcement for external tools.

Hyperset can tell an agent which metric, source, filter, join, freshness check, or
caveat is approved. The agent and existing data systems perform the query.

## Evaluation implications

The raw-versus-governed evaluator should test whether added context improves:

- canonical concept and source selection;
- prohibited-source avoidance;
- mandatory filter/join/caveat disclosure;
- conflict and deprecation handling;
- freshness behavior;
- provenance completeness;
- unsupported-claim avoidance.

Dashboard aesthetics and one exact tool trajectory are not relevant v0 gates.

## Primary sources

- Superset 6.1 API reference: https://superset.apache.org/developer-docs/6.1.0/api/
- Dataset API: https://superset.apache.org/developer-docs/6.1.0/api/datasets/
- Chart API: https://superset.apache.org/developer-docs/6.1.0/api/charts/
- Dashboard API: https://superset.apache.org/developer-docs/6.1.0/api/dashboards/
- Superset 6.1 source: https://github.com/apache/superset/tree/6.1.0
- Dataset/column/metric models: https://github.com/apache/superset/blob/6.1.0/superset/connectors/sqla/models.py

## Implementation ownership

- #27 preserves the source signals correctly.
- #38 converts gaps and drift into review work.
- #39 provides the minimal human decision interface.
- #30 preserves provenance.
- #25 measures whether governed context helps.
