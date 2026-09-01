# Hyperset v0 Foundation Contract

Status: **current and binding for v0 implementation**.

This document turns the product vision into an executable build contract. It is
the first implementation reference after `MANIFESTO.md`. When a current issue,
older design document, test fixture, package, or existing class conflicts with
this contract, stop and resolve the conflict before adding code.

## 1. Authority and engineering discipline

Use this order when deciding what to build:

1. `MANIFESTO.md` — product beliefs and boundaries.
2. This document — the v0 product loop, public contract, sequencing, and proof requirements.
3. Accepted ADRs, with newer ADRs superseding older ones explicitly.
4. Current research under `docs/research/`.
5. Current GitHub issue acceptance criteria.
6. Existing code and tests.

Existing code is evidence of repository history, not proof that its abstraction
should survive the connector-driven pivot.

Anthropic's published operating lessons remain constraints:

- prefer the simplest composable pattern;
- keep business context human-owned and close to the systems that define it;
- make provenance and freshness visible;
- test observable agent outcomes with deterministic graders where possible;
- add agentic complexity only after measured failures justify it.

For Hyperset this means: **do not create another semantic source of truth. Let
customers keep domain context in Git, then make that context operational across
their analytics stack and agents.**

## 2. The v0 product in one sentence

> Hyperset combines customer-owned Git context with real Superset and DataHub evidence so a small local model can choose, validate, and explain the right revenue data more reliably than an agent given raw metadata alone.

Hyperset does not own the external agent's planning loop, production warehouse
execution, BI presentation, or canonical authoring of business meaning.

## 3. Non-negotiable invariants

1. **Git owns v0 domain meaning.** One configured repository/ref/path is the authoritative revenue-context source. Hyperset records the exact commit; it does not recreate the customer's review workflow.
2. **Real source before abstraction.** Superset and DataHub behavior is authoritative only when observed from the exact pinned environments and supported read-only transports.
3. **Observation is not meaning.** Superset/DataHub connector output creates observations only. Connected metadata never becomes trusted business guidance merely because Hyperset saw it.
4. **Postgres owns Hyperset operational state, not business truth.** Sync runs, immutable source/context snapshots, links, findings, eval attempts, notifications, and replay state live in Postgres. A Postgres-only semantic edit can never become authoritative context.
5. **One public retrieval shape.** HTTP, MCP, local-model clients, and the evaluator consume the same `ContextBundle` contract.
6. **External execution stays external.** Hyperset never runs, generates, or validates the customer's SQL or its results — a permanent platform boundary (ADR 0032), not a v0 default. Every response discloses it, and `execution.performed_by_hyperset` and `result_validated_by_hyperset` are always `false`; a consumer that runs the query does so with its own tools, and Hyperset does not claim it.
7. **Fallback is disclosed.** Raw observations may be returned when authoritative context is absent, but they are labeled `observed_only` and never presented as company-approved meaning.
8. **Provenance is exact.** Returned context pins Git repository/ref/path/commit plus every selected observed version and evaluation reference.
9. **No hidden autonomy.** V0 has no LLM path that can author, approve, or mutate canonical context. AI-assisted repair may be added later as a Git patch/PR proposal only.
10. **Breadth follows proof.** Additional connectors, authoring surfaces, model-curator workflows, rules, tools, or context kinds require a demonstrated need after the walking skeleton is green.
11. **Natural language selects; exact code governs.** A supported lightweight
    planner must turn an ordinary analytics question into bounded candidate
    domains/concepts and then an exact `ContextDirective`. Candidate relevance
    is derived and non-authoritative. Only exact Git membership, source-native
    identity, and deterministic resolution may populate governed fields.

## 4. The mandatory walking skeleton

The first green product path is one vertical slice, not a set of independently
polished subsystems.

### Canonical scenario

Use the revenue demo and this canonical answer question:

> Which source and rules should an analyst use for recognized revenue by region?

Every component must exercise the same scenario and stable identifiers.

The V0 selection proof also includes at least two plausible decoy domains,
hidden paraphrases that do not contain the configured domain name, an ambiguous
question, and a true no-match question. This is not context breadth: it proves
that a normal user can reach the one governed revenue slice without already
knowing its internal identifier.

### Required sequence

1. Start exact pinned Apache Superset 6.1.0 and DataHub OSS environments.
2. Seed one shared revenue domain: Superset holds BI/query evidence; DataHub holds catalog/domain/owner/glossary/lineage evidence.
3. Configure one Git repository/ref/path containing the authoritative revenue context and record its exact commit SHA.
4. Read Superset and DataHub through one real supported read-only transport each and persist unmodified payloads as immutable observed versions.
5. Read the Git context and persist an immutable context snapshot whose semantic fields remain traceable to repository/ref/path/commit.
6. Link context claims to Superset/DataHub evidence only when native identifiers or explicit lineage support the relationship. Never merge by display-name similarity alone.
7. Apply one deterministic source change that causes observed reality to disagree with or weaken the Git-owned context, then persist the connector change.
8. Run one deterministic rule that creates one explainable `Finding` about the affected revenue context/evidence relationship.
9. Give a real lightweight planner the natural-language question and a bounded
   discovery surface; record the derived candidates and the exact
   `ContextDirective` it selects. The planner must abstain when candidates are
   ambiguous or absent.
10. Resolve one `ContextBundle` from the exact directive, pinned Git context,
    linked observations, findings, qualifiers, and exact provenance; expose the
    same bundle through HTTP and MCP.
11. Deterministically validate an agent's proposed source/fields/joins/filters/grain/checks against the exact bundle version before external query execution.
12. Run the locked Inspect AI benchmark with a separate small pinned Ollama model. The governed arm uses Hyperset; raw baselines receive only source/lake metadata. All arms use the same controlled read-only demo query tool.
13. Change a depended-on observation or Git context commit, invalidate only affected cases, rerun them, and persist one review/maintenance finding plus one generic webhook notification when a critical case fails.
14. Restart the Docker stack and replay retrieval, validation, evaluation, and failure notification from persisted state.

No P0 component is integrated until it participates in this path.

## 5. Canonical lifecycle and ownership

```text
Git repository/ref/path
        |
        v
   Git commit
        |
        v
 ContextSnapshot ---------------------------+
                                             |
SourceSnapshot -> ObservedAssetVersion       |
                        |                    |
                        v                    |
                 ConnectorChange             |
                        |                    |
                        +--------+-----------+
                                 v
                              Finding
                                 |
                                 v
                           ContextBundle
                                 |
                                 v
                         PlanValidation
                                 |
                                 v
                        EvaluationAttempt
```

### Boundary rules

- Git owns canonical v0 domain context and the customer's human review/merge process.
- The Git sync owns exact repository/ref/path/commit capture and immutable context snapshots.
- Connectors own source access, lossless raw capture, stable source identity, and source change detection.
- Linking/processing owns deterministic relationships, contradictions, and findings. It does not rewrite Git context.
- The retrieval service compiles Git context plus evidence into `ContextBundle`; it does not alter canonical meaning.
- The plan validator checks a proposed analytics fetch against the exact bundle; it does not execute SQL.
- Inspect AI runs evaluation tasks and scorers; Hyperset owns dependencies, persisted attempts, stale state, and notification evidence.
- Warehouse execution remains external and is a permanent platform boundary (ADR 0032), not a v0 default. The controlled read-only demo query tool belongs to the benchmark/consumer arm — an agent's own tool, not a Hyperset core capability — and records calls/results as its own evidence; the served bundle still discloses `execution.performed_by_hyperset: false`.

Existing `ReviewDecision`/`GovernedContext` persistence may remain for repository
compatibility while the pivot lands, but it is not the v0 authority path. No new
v0 behavior may depend on Hyperset-local semantic approval.

## 6. Canonical public response: `ContextBundle`

The first public product contract is a task-oriented bundle, not a bag of
unrelated object endpoints.

```yaml
schema_version: 26
bundle_id: cb_...
resolved_at: 2026-07-26T00:00:00Z
request:
  query: Which source and rules should an analyst use for recognized revenue by region?
  directive:                    # what the caller's planner asked to retrieve
    domains: [revenue]
    asset_refs: []
    max_hops: null
    context_budget: null

resolution:
  status: governed              # governed | mixed | observed_only | no_match
  summary: Revenue guidance from the configured Git context with linked source evidence.
  warnings: []               # each entry: {code, message}; branch on code, never wording

context_authority:
  type: git
  repository: example/analytics
  ref: main
  path: playground/examples/revenue/
  commit_sha: abc123...
  owner_refs: [team:finance-analytics]

instructions:
  definitions: []
  approved_sources: []
  fields: []
  filters: []
  joins: []
  grain: null
  caveats: []
  validations: []
  prohibited_sources: []

linked_evidence:
  observed_assets: []
  findings: []
  freshness: []
  conflicts: []              # each entry: {kind, produced_by, severity, finding_id, ref,
                             # field, context_says, source_says, unresolved_since_commit};
                             # `severity` is error/warning (default-deny); `finding_id`
                             # is null unless `produced_by` is `processor_finding`
  deprecations: []
  uncorroborated: []          # each entry: {ref, code, message}; declared, not observed

domain_graph:
  nodes: []
  edges: []

provenance_refs:
  - git_context:...
  - source_snapshot:...
  - connector_sync:...
  - evaluation_case:...

execution:
  performed_by_hyperset: false
  result_validated_by_hyperset: false

assist:                         # ABSENT unless assist mode produced something
  assist_id: as-...             # its own identity; never part of bundle_id
  kind: candidate_sources
  produced_by: {producer: deterministic_ranking/1, model: null, signals: []}
  answers: {domain: revenue, undeclared_concepts: [churn]}
  candidates: []                # each: {rank, ref, governance: observed,
                                # signals[], disagrees_with_git[]}
  proposal:                     # ALWAYS present; `ref` is null on a decline
    outcome: proposed           # or no_git_relative_signal, not_separated_...,
                                # every_candidate_disagrees_with_git
    ref: superset:dataset:...   # null unless outcome is `proposed`
    governance: observed
    basis: [git_engagement]     # the signals that separated it; [] on a decline
    statement: ...
  considered: 0
  returned: 0
  bound: 5
  disclosure: Nothing here is approved, canonical, or validated meaning.
```

### Contract rules

- The bundle is deterministic for a pinned Git commit, repository state, and directive.
- `status: governed` means the guidance comes from the configured customer-authoritative Git context; it does not mean Hyperset independently approved the business meaning.
- Every semantic field is traceable to the exact Git context snapshot.
- Every source/evidence claim is traceable to an exact observed version.
- `status: mixed` identifies which parts come from Git context and which are observational fallback: every entry in `linked_evidence.observed_assets` carries `governance: git_linked | observed_only`.
- `status: observed_only` cannot imply approved, trusted, canonical, or validated business meaning.
- Missing context is a valid result, not an invitation to invent guidance.
- Conflict, deprecation, freshness, finding, and evaluation state travel with the bundle when safety-relevant.
- A disagreement between Git and an observation is compared as a **computation**, by the one comparator described below, and it has **three** outcomes rather than two: equivalent is silence, a real difference is an `error` finding, and a difference confined to table qualifiers or casts is a `warning` under its own finding type stating both forms. The rule that detects expression drift compared characters until hy-803q, so a reformatted expression was served as an error, as a `linked_evidence.conflicts` entry, and as a sunk candidate in the `assist` ranking whose own `expression_agreement` signal simultaneously reported agreement. Detection now uses the comparator plan validation and ranking already use, so the three cannot disagree about the same pair (ADR 0021).
- A finding never says which side is **wrong**. It states both sides, where each was read from, and which side moved; the choices live in `proposed_action`, in the customer's own repository. `conflicts[].kind` is the finding type passed through, and the types are published as `hyperset.processor.FINDING_TYPES` and gated where a candidate is constructed -- the same shape that gates `VIOLATION_CODES` and `WARNING_CODES`, for the same reason: a value that reaches a client without being published fails at construction rather than on the wire. The set is not yet enumerated in any served payload.
- **`linked_evidence.conflicts` has two producers, and every entry names which one built it.** `produced_by` is one of `processor_finding` or `bundle_reconciliation`, published as `hyperset.bundle.reconcile.CONFLICT_PRODUCERS` and gated where an entry is constructed. A `processor_finding` entry is the projection of a persisted finding and carries its `finding_id`; a `bundle_reconciliation` entry is computed when the bundle is built, by joining what the pinned commit declares to what the estate currently shows, and its `finding_id` is null. The reconciled kinds are published as `hyperset.bundle.reconcile.RECONCILED_KINDS`: `prohibited_but_referenced` (the context prohibits a source and live assets still reference it, counted from observed edges because no source discloses an execution count), `source_deleted_while_governed` (the commit approves a source the connector stopped reporting), `ownership_mismatch` (the estate reports an owner different from a Git-DECLARED identity bridge -- reconciled ONLY where the customer declared the bridge mapping a governed `owner_ref` to a catalog identity, NEVER inferred across identifier spaces, so an undeclared owner is silent), `grain_mismatch` (the estate reports a grain different from a Git-DECLARED grain -- exact equality, or falling outside a DECLARED rollup relation -- reconciled ONLY from declared inputs, NEVER inferred from column names, so an undeclared grain is silent), and `freshness_stale` (the estate last modified a source before a Git-DECLARED freshness threshold -- reconciled ONLY from a declared threshold and a DETERMINISTIC resolve-clock `now`, never the wall clock and never a fabricated threshold, so an undeclared source stays a `freshness` observation and is silent). These stay deterministic for a pinned commit, repository state and directive, which is what lets them sit inside `bundle_id` at all (ADR 0019 floor 8, ADR 0021 decisions 4 and 7). Neither carries a `field`, so neither can reach plan validation's `disputed_field`, and neither carries a moved side: movement is measured per field against the version a commit pinned, and a prohibition and a deletion are about the source. **Every entry carries a `severity`, by provenance:** a `processor_finding` entry INHERITS its finding's severity (`error`/`warning`), and a `bundle_reconciliation` entry carries a FIXED severity declared per kind in `hyperset.bundle.reconcile.RECONCILED_KINDS` (`prohibited_but_referenced` is `error`, `source_deleted_while_governed`, `ownership_mismatch`, `grain_mismatch` and `freshness_stale` are `warning`) -- a governance constant, never a computed score, so it too stays inside `bundle_id`. The values are published as `SEVERITIES` and are default-deny: an unrecognised severity is treated as the most severe and always surfaced, so a later value is additive under ADR 0018 decision 5 (hy-xfhh).
- **A disagreement is refused where a side is absent, and where the two sides agree.** A prohibited ref no observed asset carries, a prohibited source nothing references, a deleted asset no commit approves, and a deleted source the commit prohibits are not conflicts: the first is an absence, the second is the prohibition working, the third is an observation, and the fourth is agreement twice over -- the customer said not to use it and the estate stopped carrying it. Each is already disclosed, as a `prohibited_by_context` or a `source_deleted` deprecation, and the deprecation stays when a conflict is added, because an observation about a source is true whether or not Git approves it (ADR 0021 decision 2).
- **Which side moved** is measured against the version the commit's evidence ref pinned, and is one of four values published as `hyperset.processor.MOVED_SIDES` and gated the same way: `observed` when Git agrees with that version and the source has changed since, `git` when the source still computes what that version computed and the approved expression differs from it, `both` when neither side matches it, and `undecidable` when the commit pinned no version or the pinned one is no longer readable. Both comparisons are the one comparator over the version's expressions, not its id: a version is written for an asset and a finding is about one field. The finding never claims a commit was edited -- Hyperset reads one commit, not the history of one. `neither` is not a value: `equivalent` is canonical equality and therefore transitive, so if neither side left the pinned version the two sides agree and no finding exists. The fact is persisted on the finding and reaches a client as a sentence inside `explanation`; no served payload carries it as a field (ADR 0021 decision 3).
- Missing corroboration is disclosed, never a refusal and never silence (ADR 0017). A ref the pinned commit declares that no observation carries appears in `linked_evidence.uncorroborated` as `{ref, code, message}` and as a `resolution.warnings` entry with the same code; the guidance stays `governed`, because what makes it authoritative is the commit and not the connector. Corroboration is re-checked when the bundle is resolved rather than replayed from the snapshot: a connector synced since the commit was read makes the ref corroborated, and it is then served as `git_linked` evidence with a null `linked_version_id`, because no commit ever pinned a version for it.
- The domain graph is a deterministic projection for agent use, and is never an independent source of authority. In v0 that projection is the whole served graph; it is NOT the destination. The destination (ADR 0041) is a flexible-yet-governed knowledge graph that improves through use: this governed projection is its CANONICAL CORE, and a later layer carries first-class OBSERVED/PROPOSED nodes and edges (each provenanced, ACL'd, confidence- and staleness-marked, `evidence: "observation"`/`"proposal"`, never `git`). Those observed/proposed edges do not have to be predeclared to exist, and are never canonical authority until a human accepts them and writes them back to Git — `declared-never-inferred` governs CANONICAL promotion, not the graph's existence. v0 stays flexible without a graph database: a bounded cycle-safe walk over the existing store.
- Retrieval resolves exactly what the directive names. It does not rank, score, expand a term into synonyms, or infer a domain from the wording of the question; a directive that names nothing is refused with instructions to read the catalog first (ADR 0009, GitHub #70). That sentence is about the GOVERNED answer and ADR 0019 scopes it there rather than deleting it: the `assist` section may rank, and everything in it is labelled `observed`, carries the signals that ordered it, and is served in its own section so a caller reading the governed sections gets exactly the governed answer it would have got with assist switched off.
- `assist` is absent from every answer that has none, and it is never merged into a governed section. Nothing in it is `governed`, `approved`, `canonical`, or `trusted`; it contributes nothing to `provenance_refs`; and it carries no field that can hold a declared ref beside an observed asset, so a candidate can never be substituted for a resolved link (ADR 0019). `bundle_id` continues to hash the governed answer alone, so the determinism promise above is unaffected; assist content carries its own `assist_id`.
- `max_hops` bounds the `domain_graph` projection to that many hops from the domain node. `context_budget` is NOT a hard byte ceiling: when the answer exceeds it, the only thing dropped is the OBSERVED PAYLOADS, and `observed_payloads_omitted` is stated in `resolution.warnings` (and `over_context_budget` too, but only if the governed answer alone still exceeds the budget after the payloads are omitted). `instructions`, refs, versions, and findings are never dropped, so a budget smaller than the governed answer cannot shrink it below that floor -- governed meaning is served whole or not at all. It shaves the observed bulk, not the governed answer, and the description is honest about that so a caller with a real ceiling is not told the parameter can meet it (hy-gh-281 item 4).

The public `ContextBundle` is the compatibility boundary for v0 clients.

## 7. Minimal v0 HTTP and MCP surface

### Agent-facing P0 operations

1. `list_context_catalog(limit?, offset?) -> ContextCatalog`
2. `discover_analytics_context(query, limit?) -> DiscoveryResult`
3. `resolve_analytics_context(query, directive) -> ContextBundle`
4. `validate_analytics_plan(query, directive, bundle_id, source_refs?, fields?, joins?, filters?, grain?, checks?) -> PlanValidation`
5. `expand_analytics_context(query, domain, concepts, from_root?, max_hops?, max_components?, context_budget?) -> ExpansionResult`
6. `search_knowledge(query, sources?, filters?, mode?, limit?, intent?) -> SearchKnowledgeResult`
7. `record_answer_feedback(outcome, bundle_id?, source_ref?, review_task_id?, notes?) -> AnswerFeedback`
8. `lookup_answer_feedback(session_id?, correlation_id?, source_ref?, review_task_id?, limit?) -> AnswerFeedbackList`
9. `list_review_tasks(status?) -> ReviewTasks`
10. `get_review_task(task_id) -> ReviewTask`
11. `edit_review_draft(task_id, definition) -> ReviewTask`
12. `refine_review_draft(task_id, feedback?) -> ReviewTask`
13. `propose_review_to_git(task_id) -> Proposal`
14. `set_review_assignee(task_id, assigned, assignee?) -> ReviewTask`

A parameter marked `?` is optional; every other parameter is required. An
unknown parameter is refused rather than ignored.

`list_context_catalog`, `resolve_analytics_context`, and
`validate_analytics_plan` are the deterministic trust surface. They remain the
exact compatibility boundary even when a model chooses their inputs.

`discover_analytics_context` is served alongside them, per the bounded V0 slice
of GitHub #206 (split from #126) and ADR 0022, and is assist-class rather than
part of that deterministic surface: it returns candidate domain/concept
identifiers with disclosed ranking signals, never instructions, source
identity, provenance, or governed meaning. It ranks the full declared lists so
a relevant concept past the catalog's positional cap is reachable, and it feeds
the same exact resolver above rather than becoming a second retrieval path.
Because it is assist-class it is deliberately absent from the governed planner
tool surface (`hyperset.planner.loop.tool_specs`, an explicit resolve-path
allowlist), so serving it does not move the benchmark's `tools_hash`. Wiring it
into the planning flow, with the live re-record that entails, is separate work.

`expand_analytics_context` is served alongside them (#230 slice 4) and is
NAVIGATION over the governed graph, not part of the deterministic answer surface.
From one governed domain it follows the customer's declared `contains` hierarchy
edges into the related domains and returns which are reachable and the governed
edges among them, bounded by `max_hops`/`max_components`/`context_budget`, cycle-
and duplicate-safe, with every edge keeping its `evidence: "git"` provenance. Its
result carries `result_kind: "navigation"` and NO `context_authority`,
`instructions`, or evidence: it names WHERE to look, and each domain it lists must
still be resolved with `resolve_analytics_context` for governed meaning. It
composes nothing (a single combined bundle is a later slice). Like discovery it is
absent from the resolve-path allowlist, so serving it does not move `tools_hash`,
and it carries the current `schema_version`. Two scope limits are part of the
contract, not accidents. It follows `contains` edges ONLY: the governed
`depends_on`/`joinable_on` relationship edges (ADR-0034) are defined but not yet
emitted, so they are not traversable, and the reachable set widens additively when
that emit lands. And it discloses three conditions: a hop/component bound dropped
part of the graph (`expansion_bounded`); the byte budget shrank the graph
(`expansion_over_context_budget`, the far domains DROPPED to fit rather than a
graph returned over-budget); and the estate declares a neighbour of a reached
domain that is not currently governed (`expansion_domain_unavailable`, surfaced
with `available: false` and its reason, never traversed through and never hiding a
valid governed sibling). The walk itself never FOLLOWS an edge into an ungoverned
domain; the unavailable neighbour is disclosed, not entered. It also runs from a synthetic
workspace ROOT (`from_root: true`, no start domain), linking the enabled, current, and
ACL-visible top-level domains by `evidence: "system"` (catalog-derived NAVIGATION, never
`evidence: "git"`); each reached domain carries document POINTERS (ids/paths/refs, never
content), and a domain the caller may not see is disclosed EXCLUDED-with-reason
(`expansion_acl_excluded`), fail-closed and pointer-free, never dropped. The root is
navigation only: no `context_authority`, no governed meaning, generated per request. What it does NOT check
is per-domain STALE or CONFLICTING state (that needs each domain's evidence
resolved, the composition slice), so the absence of a staleness or conflict warning
means this operation did not check it, never that a domain is fresh or
non-conflicting. Its authorization is
coarse in this slice: because the start domain rides as a top-level `domain`
parameter rather than in a `directive`, an enabled gate requires an all-domain
reader grant to expand at all (fail-closed); a per-domain-scoped expand is a
follow-on.

`search_knowledge` is a READ-ONLY, NON-AUTHORITATIVE lexical or semantic search over
the sources a deployment has CONFIGURED -- the messy Git/context content an agent
searches BEFORE resolving the governed answer. `mode` defaults to byte-compatible
`grep`; `semantic` ranks authorized lines through the same configured embedding
provider and space as `discover_analytics_context`, and each semantic hit discloses
its cosine score and exact space metadata. It searches ONLY
configured/governed sources through an adapter, never an arbitrary file on the
server, and each hit names its source (id + repository), path, line, commit and
content version, ACL decision, staleness, and match type. It is FAIL-CLOSED per
source: a caller without access to a source gets ZERO hits from it, decided
before that source's content is read or embedded, reusing the same `security.authz`
gate. Credential-bearing URL userinfo is redacted before a line reaches a hosted
provider. It
writes, proposes, approves, and resolves nothing -- a hit is a place to look, and
its meaning must still be got from `resolve_analytics_context`. Like discovery
and expansion it is absent from the resolve-path allowlist, so serving it does
not move `tools_hash`, and it returns its own hit envelope rather than a
`ContextBundle`. Semantic hits add a `signal` key, so this served response-shape
change moves `schema_version` to 25 under ADR 0018; grep hits retain their prior
shape. Hybrid remains a follow-on.

`record_answer_feedback` appends one operational decision (`accept`, `reject`,
`include`, `ignore`, `correct`, or `needs_review`) to a hit or bundle already present in
the current MCP session/correlation trace. Session and correlation come from transport
metadata rather than tool arguments; the referenced source/document or bundle must match
an existing trace in the caller's workspace, or the write fails closed. Free-text refs and
notes are redacted before persistence. `lookup_answer_feedback` is the bounded read path,
requiring at least one exact session, correlation, source/document, or review-task filter
and always applying the caller's workspace. Both are assist/audit class, absent from
`RESOLVE_PATH_OPERATIONS`, and confer no authority: they do not approve, merge, resolve,
advance a review task, write governed context, or run SQL. The trace itself records elapsed
milliseconds, narrow served-source staleness, explicit miss targets, answer bundle ids, and
linked citation-decision/feedback ids, while retaining only redacted query/intent and opaque
hit ids (ADR 0033, hy-8f2r4).

`list_review_tasks`, `get_review_task`, `edit_review_draft`,
`refine_review_draft`, and `propose_review_to_git` expose the reviewer workflow
so a customer's agent can review a miss and propose a context change into Git
itself. They are served on both transports and are PROPOSAL-ONLY and PII-guarded
(ADR 0025 records this expanded MCP trust surface): `edit`/`refine` mutate only
the UNAPPROVED assist draft on a task and leave it `governance=unapproved`;
`propose_review_to_git` opens a pull request and stops, redacting the proposal
content through the PII guard before it commits and failing closed when the
guard is engaged but unhostable. None of them approves, merges, writes a
governed version, or runs SQL — the only path to authority is a human Git merge
(ADR 0012). Like `discover`, they are served but absent from the resolve-path
planner allowlist, so adding them does not move `tools_hash`.

`set_review_assignee(task_id, assigned, assignee?)` assigns a review task
(`assigned: true`) or unassigns it (`assigned: false`). It is REVIEW-authorized like
the authoring ops and served on both transports (ADR 0025), but assignment is task
METADATA — who is working the gap so two reviewers do not duplicate it — never an
approval or an access grant: it writes no governed row, resolves nothing, and runs
no SQL. Omit `assignee` to SELF-claim: the owner is the CALLER'S OWN verified
identity, computed by the server as an opaque `subject@issuer` and NEVER accepted as
caller free text — PII-safe by construction, since syntax alone cannot separate an
opaque subject from a PII- or credential-shaped value. Give `assignee` to assign
ANOTHER user: it is accepted ONLY as a KNOWN approved identity from the reviewer
allowlist (hy-a607k), never as typed free text, and requires that allowlist to be
configured — so the value is always a resolved, operator-curated identity. It
surfaces as the review task's `assignee` field (the key whose addition moved
`schema_version` to 21). It too is off the resolve-path allowlist, so it does not
move `tools_hash`.

Before the V0 exit gate, GitHub #70 must prove the supported lightweight
planner path from an ordinary question to the exact directive.

Any further tool -- `get_provenance` included -- requires evaluator evidence
and an ADR amendment; necessity alone is not the gate (hy-9fq).

The catalog is bounded on both axes and neither bound is a relevance
judgement. `limit` and `offset` page over domains positionally in a stable
order; the lists inside a domain are capped by a fixed inner bound the caller
cannot raise, so the listing stays a preview rather than becoming a delivery
mechanism. Refusal, not clamping: a `limit` past the cap, below one, or a
negative `offset` is an error, so a caller that asked for everything is never
quietly served a page.

What was bounded is disclosed structurally. Each `page.truncated` entry is
`{list, reason}` with `reason` one of `cut` or `withheld`; each domain's
`counts` gives the full size of every list whether or not this response
carries all of it; `page.next_offset` is where the next call starts.
`evidence_refs` is `withheld` rather than `cut` when it exceeds the bound and
its key is then ABSENT from the domain -- an absent key means withheld, not
"none declared" -- because a partial list of seeds is unfit for the one thing
seeds are for. A prohibition is never cut by any bound.

`counts.evidence_refs` is the number of refs the commit DECLARED, and the
served list is narrower than it for a second reason that is not a bound: a
declared ref no connector has observed is not offered as a seed, because
resolving with it returns nothing. The count is what lets a caller tell three
declared refs from four with one uncorroborated -- when the two disagree,
resolve the domain and read `linked_evidence.uncorroborated`, which names
which.

It is a listing -- identifiers, titles, counts -- and carries no governed
meaning: it exists so a planner can name exact domains and refs instead of the
resolver inferring them, and so the whole corpus is never sent to the strong
model. At small V0 scale the planner may read this listing directly; at larger
scale the assist-class discovery index narrows it before exact resolution.
`ContextDirective` is the planner's structured output
(`domains`, `asset_refs`, `concepts`, `max_hops`, `context_budget`); the
semantic work of producing it belongs to the caller's agent.

`concepts` is the coverage claim, and it is required whenever `domains` is
named. Hyperset does not read the question, so this is the only place a caller
can say what the domain has to cover. A claim naming a term the domain's Git
context does not declare is refused with `domain_does_not_declare`, and no
governed context is served. The check is the same exact set membership the
domain name itself gets: no similarity, no synonyms, and nothing derived from
the wording of `query`. What it guarantees is bounded and stated rather than
overclaimed: an unstated coverage claim can no longer produce a governed
bundle, which is the failure that was measured (hy-9lct, a supplier lead-time
question answered from the revenue domain). A caller that claims a term the
domain does declare, for a question about something else, is still served --
verifying that would require Hyperset to interpret the question, which GitHub
#70 removed.

**BREAKING at this version, and `SCHEMA_VERSION` does not move because the
bundle's shape did not.** `{"directive": {"domains": ["revenue"]}}` was a
served example and returned a governed bundle; it is now refused. Both halves
of the pair -- `domains` without `concepts`, and `concepts` without `domains`
-- are malformed directives refused as `invalid_params` before any retrieval
runs, and neither is a `resolution.warnings` code. A missing required
parameter is knowable from the request alone, so it gets a verdict about the
request; it must not be answered with a bundle whose summary makes a claim
about the corpus instead (hy-bdff: `no_match`'s "no configured Git context
covers this request" was served for a request a configured Git context did
cover). A caller sending the old shape gets `invalid_params` naming
`list_context_catalog`; the fix is to add the terms it needs.

**`SCHEMA_VERSION` is 26**, and every move but two was for the other direction:
at 2 `linked_evidence` gained `uncorroborated`, at 3 every `violations` entry
gained `recovery`, at 4 a bundle may carry an `assist` section, at 5
`ref_not_observed` narrowed, at 6 that assist section always carries
`proposal`, at 7 every `conflicts` entry names its producer and may carry a
null `finding_id`, at 8 `assist.proposal.outcome` serves a fifth value, at 9 a
validate result may carry `sections_not_checkable` and a `valid_with_gaps`
status, at 10 a domain may declare no eval bank and the answer states it --
the bundle's `context_authority` carries `unevaluated` and the catalog carries
`counts.eval_cases` and `page.unevaluated_domain_count` -- and at 11 a domain
projected through a context adapter discloses what the adapter did: the bundle's
`resolution` carries `projection`, and at 12 an approved source may declare a
per-source `grain` and the served `instructions.approved_sources` and the domain
graph carry it (a grain node and a `has_grain` edge), and at 13 an approved source
may declare a per-source `classification` -- a governed sensitivity label the
domain graph carries as a `classification` node and a `classified_as` edge, and
at 14 an approved source may declare a per-source `freshness` contract (cadence
and/or max-staleness) the domain graph carries as a `freshness` node and a
`has_freshness` edge, and
at 15 an approved source may declare a per-source `lineage` contract (produced_by
and/or upstream) the domain graph carries as a `lineage` node and a `has_lineage`
edge, and
at 16 an approved source may declare a per-source `checks` contract (the owned
data-quality checks, each a name plus optional description/severity) the domain
graph carries as a `checks` node and a `has_checks` edge, and
at 17 a domain that declares a governed `parent` (ADR-0031), or is the declared
parent of another domain, carries that hierarchy in the served domain graph as
`domain` nodes and `contains` edges to its immediate parent and children -- a
depth-agnostic governed edge ABOVE and BETWEEN domains (`evidence: "git"`, never a
name inference), validated whole-estate (`hierarchy.validate_forest`) before any
edge is emitted so a dangling or transitive-unknown ancestor never reaches the
served graph, with an unverified endpoint's edge omitted fail-closed, and
at 18 a directive that names MORE THAN ONE governed domain resolves instead of
being refused with `multiple_domains`, and the bundle carries a top-level
`domains` list -- one entry per named domain, EACH byte-identical to that domain's
solo resolve, no domain's authority or evidence bleeding into another's (#230
slice 3, hy-cnto). On such an answer the FLAT governed fields are an explicit,
documented envelope: `context_authority` is `null` and the flat
`instructions`/`linked_evidence`/`domain_graph`/`provenance_refs` are empty, which
MEANS "authority is per-domain -- read `domains[]`". That envelope can never be
read as a single-authority governed answer or an assist/downgraded one: the
`domains` key is present exactly when the flat authority is null, and the bundle
refuses to construct if a flat governed field is non-empty while `domains` is set.
Each `domains[]` entry carries its own content-derived `bundle_id` but NOT its own
`resolved_at`: the answer has one top-level `resolved_at`, and because `domains` is
governed content inside the identity hash, a per-entry wall clock would make the
envelope's `bundle_id` non-deterministic -- so the determinism promise (same commit +
directive is the same answer) holds for the multi-domain answer too, and
at 19 that multi-domain answer also carries a top-level `composition` section: the
COMPOSED cross-domain graph relating the resolved domains (#230 slice 5, hy-uaks).
`composition.graph` is DOMAIN-LEVEL ONLY -- `domain:{slug}` nodes plus the governed
domain-to-domain `contains` edges among the composed domains, each edge keeping its
own `evidence` provenance -- so it exposes how the domains relate without flattening
their separate authorities: all per-domain authority, instructions, and evidence stay
in `domains[]`, and the flat envelope stays the null/empty shape 18 defined. The bundle
refuses to construct if the composed graph is not fail-closed domain-level and governed:
every node must be kind `domain` with a `domain:{slug}` id, every edge must connect two
composed domain nodes, carry `evidence: "git"`, and (this slice) be a `contains` edge --
`depends_on`/`joinable_on` widen that allowlist when their emit lands (slice 2b) -- and a
flat governed field may not be non-empty while `domains` is set, and
at 20 every `linked_evidence.conflicts` entry gains a `severity` key -- error or
warning, published as `hyperset.bundle.reconcile.SEVERITIES` and default-deny (an
unrecognised value is treated as the most severe and always surfaced), a
processor-finding conflict inheriting its finding's severity and a reconciled
conflict carrying its kind's fixed severity (`prohibited_but_referenced` error,
`source_deleted_while_governed` warning), a governance CONSTANT never a computed
score so it stays inside `bundle_id` (hy-xfhh, ADR 0021 decision 1), and
at 21 a review task carries a first-class `assignee` field -- an opaque
`subject@issuer` owner, or null when unassigned -- set or cleared by the new
`set_review_assignee` op, a key a review-task consumer must know to read (hy-s8a6), and
at 22 a `list_review_tasks` entry MAY carry a `suggested_assignee` field -- an
assist-class owner HINT (the most-recent prior in-domain reviewer, filtered to a KNOWN
allowlisted reviewer so an unapproved/anonymous id is never suggested) -- and a companion
`suggested_assignee_rationale` object (`signal`/`summary`/`assist`) naming the
deterministic reason, both present only when there is a suggestion and absent otherwise --
a SUGGESTION a human overrides via `set_review_assignee`, never an auto-assignment or
approval (ADR 0019), and keys a review-task consumer must know to read (hy-38mk8), and
at 23 the review-task DETAIL view (`get_review_task` and `list_review_tasks`) gains three
keys a reviewer judges a proposal by at detail -- `current_meaning` (the domain's governed
current definition beside the proposed draft, or null when nothing is governed yet),
`uncertainty` (the miss's undeclared concepts, assist-labelled), and `proposed_diff` (the
exact current-vs-proposed delta, the diff that today only materialises inside the PR) -- keys
a review-task consumer must know to read (hy-z6zv, V1 gap Reviewer/2), and
at 24 `expand_analytics_context` gains a walk that starts from a synthetic workspace ROOT
(`from_root: true`, no start `domain`/`concepts`) and a richer navigation shape: a `root`
node (present only on a root walk), per-reached-domain document `pointers`
(`source_id`/`repository`/`snapshot_id`/`commit_sha`/`context_doc` path/`approved_sources` --
POINTERS to fetch next, never inlined content), an `exclusion` marker and a new
`expansion_acl_excluded` disclosure on a domain the caller may not see, and root->domain edges
carrying a new edge `evidence: "system"` value (catalog-derived NAVIGATION, never the governed
`evidence: "git"`). The root is NAVIGATION only -- `result_kind` stays `navigation`, it carries
no `context_authority` and no governed meaning, it is generated per request and never stored --
so it creates no authority (ADR 0012); keys/values a walk consumer must know to read (hy-l93sc), and
at 25 `search_knowledge` accepts an opt-in `mode="semantic"` whose ranked hits add a
`signal` object with cosine score and exact embedding-space identity; grep stays the default
and its hit shape is unchanged (hy-0unvk), and
at 26 the served assist/audit surface adds `record_answer_feedback` and
`lookup_answer_feedback`: the first returns a durable, trace-verified feedback record and
the second returns a bounded list of those records. These are response shapes a client must
know to read, while the `ContextBundle` itself gains no key (hy-8f2r4).
Every one of those but
the narrowing and the fifth
value is a key a caller RECEIVES that it did not before -- additive, so nothing a client read
changed meaning, and the number moved anyway, because the question ADR 0018
asks is which direction changed, not how bad it is.

**5 is the one move that is not additive, and it is a narrowing.**
`ref_not_observed` meant "no observation carries this ref", for any reason. It
now means the connector read the estate and the asset was not in it; the unread
case is `ref_awaiting_sync`. The new code on its own is an added value in
`resolution.warnings[].code` and additive under ADR 0018 decision 5. The
narrowing is what moves the number, and it is the move a client cannot detect
by parsing: no key appeared, no key vanished, and a code it already handles now
denotes a smaller world. A client that read `ref_not_observed` as "come back
after a sync" must now read `ref_awaiting_sync` for that, and read
`ref_not_observed` as the estate having answered.

ADR 0018's text does not yet carry this case: decision 1 moves the number for a
change in the SHAPE of what a caller receives, and decision 5 rules an ADDED
value additive. A narrowed value is neither. The bump rests on the third-case
ruling -- a served code whose meaning narrows moves the number even though the
shape is byte-identical -- and the ADR amendment stating that case, together
with the demonstration that no shipped gate can detect a narrowing, rides in
its own change rather than this one.

The 6 is the case ADR 0018 decision 5 draws a line through, and the line is
whether the version a reader already holds told them to expect the addition. A
new member of `produced_by.signals` did: it is a self-declaring list, meant to
be read at whatever length it arrives, so it ships inside 4. A new
always-present key did not, and moves the number. That assist publishes no
response schema does not exempt it: `recovery` moved 2 to 3 for the same shape
with no published schema for `violations` either, and a served section exempted
from the version signal is a section the number no longer covers -- after which
nothing tells a reader which half of the payload the version is about.

The 7 is the second half of the same line, and it is the harder half. Adding
`produced_by` to every `conflicts` entry is the 6's shape again -- a new
always-present key -- but `finding_id` is the part a client cannot parse its way
around: it was an id on every entry, and an entry no processor finding stands
behind now carries null. A client that read it as always present breaks on a
value, not on a key, which is exactly the direction ADR 0018 asks about.

**The 8 is the first move for an ADDED VALUE, and it is decision 5 applied
rather than reversed.** `assist.proposal.outcome` gained `no_governing_domain`,
and decision 5 makes an added value additive ONLY where default-deny is
published for that field. It is not published for this one: the field has been
served since 6 and appears neither as a row in decision 5's own table nor in
either shipped client surface, so a client meeting an outcome it does not
recognise has no rule to apply and "the parser still parses" is precisely the
argument that decision names as insufficient. Publishing it instead would move
`prompt_hash` and `tools_hash` and re-roll the evaluation arm, which this
repository takes once in hy-hj9g's ratification pass; that publication is
hy-1bh1, and it does not unmove this number when it lands. Of the two available
mistakes, an unversioned value is the one a client cannot detect.

The 9 is two moves in one shape, both in the direction ADR 0018 versions, and
it fixes a false green (hy-gh-285). A validate result whose governed context
declares nothing in a section -- no filters, no joins, no fields -- came back
`valid` with zero violations, because there was nothing for the plan to
contradict; "checked and clean" and "there was nothing to check" were served
identically, and an agent acting on `valid` would report that a check happened.
So a validate result now gains a `sections_not_checkable` list naming each
empty section and why, and a `valid_with_gaps` status when it is non-empty.
Both are what a caller RECEIVES: a new always-absent-when-empty key is the 6's
case, and `valid_with_gaps` is a new value in `status`, which publishes no
default-deny -- the 8's case, an added value that moves the number rather than
riding a default-deny it does not have. It is additive by construction: the key
is absent and the status stays `valid` for a fully-specified result, and the
disclosure is computed only for an otherwise-`valid` result, so no `invalid`,
`warnings`, or `unverifiable` verdict changes by a byte. It is a disclosure,
not a violation -- an unconstrained domain is a legitimate state and is not
failed for it, the same posture as `resolution.warnings`.

The 10 lets a domain declare no eval bank honestly and says so on the answer
(hy-gh-287). A context directory could not onboard without an eval bank, so a
domain with no evals yet had to fabricate one, and a fabricated bank -- one
trivially-passing case -- is indistinguishable from real coverage. Now `evals:
none`, an omitted key, or a null value validates and syncs; pointing `evals` at
a file that holds zero cases stays an error, because that one is a mistake, not
a declaration. The answer states it: the bundle's `context_authority` gains an
`unevaluated` key on such a domain, and the catalog gains `counts.eval_cases` on
every domain and a corpus `page.unevaluated_domain_count`. It is the 8's and the
9's case, an added shape that publishes no default-deny and so moves the number
rather than riding one it does not have: an absent `unevaluated` reads as
"evaluated" to a version-9 client, which is default-ALLOW and launders exactly
the uncertainty the field exists to state, so the field is versioned. Additive
by construction -- the key is present only when the domain declared none, so a
domain with a bank answers byte-for-byte the authority it did before, with no
redundant `unevaluated: false` -- and an unevaluated domain counts as passing in
no aggregate: `unevaluated_domain_count` is kept apart from `domain_count`, the
same refusal to launder that keeps `ref_awaiting_sync` apart from
`ref_not_observed`.

The 11 lets a domain govern the corpus a customer already has, in its own shape,
through a context adapter (epic #283), and DISCLOSES that on the answer. An
adapter reads the customer corpus at its own commit and projects it onto v0
context, running a closed whitelist of transforms; it may change the shape that
carries meaning and may never create it (ADR 0028). A caller reading a governed
bundle could not tell an adapter-projected domain from a hand-written one -- so a
bundle whose domain came through an adapter now carries `resolution.projection`,
stating the adapter and its version and, as the fork-agnostic substrate for what
follows, the fields it left unmapped, lossy, or derived (each derived field with
the human who owns the rule). In this first slice those three lists are empty by
construction: an unmapped source key is an error, not a tolerated drop, and the
adapter authors nothing and loses nothing, so the block reports the provenance and
nothing to disclose yet. It is the 8's, 9's, and 10's case, an added shape that
publishes no default-deny and so moves the number rather than riding one it does
not have: an absent `projection` reads as "hand-written v0" to a version-10
client, and a projected domain that did not say so would launder exactly the
adapter provenance the field exists to state. Additive by construction -- the key
is present only for an adapter-sourced domain, so a hand-written domain answers
byte-for-byte the resolution it did before, with no redundant empty block. What
this deliberately does NOT do is DECIDE what an unreviewed derived field means for
the governed status (does it degrade `governed` to `mixed`, or need a new status
value): that is Brandon's fork 3, built at 283-6, and nothing here presupposes it.

The 12 surfaces a per-source grain (epic #284). A manifest may already declare a
domain grain, and the `constrains` edge and `grain` node carry it; what an
approved source could not say until now is the grain IT is aggregated at, which is
the concrete #284 bug -- an `fx_rates_daily` read as if it were at order grain.
Slice 1 (hy-n9sq) parsed `approved_sources[].facets.grain` and stored it on the
snapshot but stripped it out of `git_instructions`, so nothing served changed. At
12 that strip is gone: a source that declared a grain carries it into
`instructions.approved_sources`, and the domain graph gains a grain node keyed by
source (so two sources at the same grain do not collapse to one) with a
`has_grain` edge from the source, mirrored in the catalog's `projection_summary`
so the two projections cannot drift. It is the 8's through 11's case again, an
added shape that publishes no default-deny and so moves the number: a source
without a grain grows no `facets` key and answers byte-for-byte as before, and the
shipped revenue bundle is unchanged because it declares none. What 12 deliberately
does NOT do is DECIDE whether a source grain refines or replaces the domain grain,
or fan the check out across sources -- that is Brandon's fork 2, the 284-4 check,
and this only EXPOSES the stored grain for it.

The 13 surfaces a per-source classification (epic #284). An approved source may
carry `facets.classification` -- a governed sensitivity label from a CLOSED
vocabulary (`restricted`, `pii`, `internal`, `public`); a value outside it is an
error, never a label that rides through as governed while nothing verified it. It
is parsed like the grain and surfaced like the grain: into
`instructions.approved_sources`, and as a `classification` node keyed by source
with a `classified_as` edge in the domain graph, mirrored in `projection_summary`
so the two projections cannot drift. It is the same additive, no-default-deny shape
as the 12: a source without a classification grows no `facets` key and answers
byte-for-byte as before, and the shipped revenue bundle is unchanged because it
declares none. The 13 itself only STATES the governed label; enforcement arrived
later and in two parts. The structural part has landed (hy-eif4, folded into #230):
a plan that reads a `restricted` or `pii` source while no governed caveat declares
its handling is a `classification_undisclosed` violation -- a deterministic
manifest-governance check that judges only whether a governed handling caveat
EXISTS, never whether the handling is adequate, and makes no access decision from
the label. The identity-gated part -- whether a `restricted`/`pii` source's payload
may enter a bundle FOR A GIVEN CALLER, and PII content handling -- still needs the
enterprise access model and is held for a future ADR-0030; it is not built here.

The 14 surfaces a per-source freshness contract (epic #284). An approved source may
carry `facets.freshness` -- a small structured shape, `cadence` (how often the
source refreshes) and/or `max_staleness` (the SLA, the oldest the data may be); an
unknown sub-key or a block that declares neither is an error, and the values are
governed labels stated as the customer wrote them, not parsed into durations. It is
surfaced like the grain and the classification: into `instructions.approved_sources`,
and as one `freshness` node keyed by source carrying the contract fields with a
`has_freshness` edge in the domain graph, mirrored in `projection_summary` so the two
projections cannot drift. Same additive, no-default-deny shape: a source without a
freshness contract grows no `facets` key and answers byte-for-byte as before, and the
shipped revenue bundle is unchanged because it declares none. What 14 deliberately
does NOT do is COMPUTE or ENFORCE staleness -- no source is called stale, no plan is
gated on the SLA; that is a later check bead, tied to the flywheel's own freshness.
14 only states the governed contract.

The 15 surfaces a per-source lineage contract (epic #284). An approved source may
carry `facets.lineage` -- a small structured provenance shape, `produced_by` (the
upstream system or job that produces this source) and/or `upstream` (a list of refs
this source derives from); an unknown sub-key or a block that declares neither is an
error, and the values are governed labels stated as the customer wrote them. It is
surfaced like the freshness: into `instructions.approved_sources`, and as one
`lineage` node keyed by source carrying the contract fields with a `has_lineage` edge
in the domain graph, mirrored in `projection_summary` so the two projections cannot
drift. Same additive, no-default-deny shape: a source without a lineage contract grows
no `facets` key and answers byte-for-byte as before, and the shipped revenue bundle is
unchanged because it declares none. What 15 deliberately does NOT do is WALK the
lineage -- `upstream` is stated, not resolved to nodes, and no cycle, reachability, or
derived status is computed from it; that is a later check bead. 15 only states the
governed contract.

The 16 surfaces a per-source checks contract (epic #284). An approved source may
carry `facets.checks` -- a list of the data-quality checks it asserts it OWNS, each
a small structured shape with a required `name` and an optional `description` and
`severity`; a non-mapping entry, an unknown sub-key, an entry missing `name`, or a
`checks` that lists nothing is an error, and the values are governed labels stated
as the customer wrote them. It is surfaced like the lineage: into
`instructions.approved_sources`, and as one `checks` node keyed by source carrying
the checks as a list field with a `has_checks` edge in the domain graph, mirrored in
`projection_summary` so the two projections cannot drift. Same additive,
no-default-deny shape: a source without a checks contract grows no `facets` key and
answers byte-for-byte as before, and the shipped revenue bundle is unchanged because
it declares none. What 16 deliberately does NOT do is RUN the checks -- no check is
executed, no pass or fail is computed, and no status is derived from them; that is a
later check bead. 16 only states the governed contract. With it the per-source facet
SURFACE vocabulary -- grain, classification, freshness, lineage, checks -- is
complete; the remaining #284 work is ENFORCEMENT, gated on the enterprise access
model.

What 17 deliberately does NOT do is WALK the hierarchy. It serves ONE immediate level
each way -- a domain's declared parent and its declared children -- as `contains` edges
in the domain graph (hy-gh-230 slice 1, ADR-0031). It does not transitively expand
ancestors or descendants, order a forest, or bound a subtree; depth-crossing traversal
and the bounded multi-domain expansion are the later slices of #230. The emit is
guarded whole-estate: before any `contains` edge is served, `hierarchy.validate_forest`
re-checks the entire parent map, and an edge whose parent or child is on an unknown or
cyclic chain is omitted fail-closed -- `validate_domain` (the per-sync, direct-parent
check that landed the ADR-0031 hierarchy at 16's predecessor) cannot see a transitive-
unknown ancestor once an intermediate domain is disabled AFTER a valid sync, so the
whole-estate check is what stands between a dangling ancestor and the served graph. A
domain that declares no parent and parents nothing (every shipped playground context)
grows no edge and answers byte-for-byte as before.

What 18 deliberately does NOT do is COMPOSE the domains. It serves each named domain's
answer INDEPENDENTLY -- a `domains[]` entry is byte-identical to that domain's solo
resolve, and nothing merges two domains' instructions, joins their graphs, relates their
evidence, or resolves a cross-domain edge between them (hy-cnto, #230 slice 3). It only
LIFTS the `multiple_domains` refusal: a directive naming N governed domains now gets N
independent governed answers instead of a "resolve one directive per domain" refusal. The
composed cross-domain bundle -- one graph, related evidence, a single navigable answer --
is slice 5 (hy-uaks), and progressive expansion is slice 4 (hy-fgga); neither is taken
here. Coverage is checked by UNION: every claimed concept must be declared by at least one
named domain (a concept nothing declares fails the request), and each named domain is
resolved against the subset of the claim it declares -- a validity check, not a merge. The
single-domain answer is untouched: for N=1 there is no `domains` key and the bundle is
byte-for-byte what it was before this field existed.

What 19 deliberately does NOT do is FLATTEN the domains. It adds `composition.graph`, the
domain-level graph relating the resolved domains, but it does NOT merge their instructions,
join their within-domain graphs, relate their evidence, or resolve a within-domain node
into the composed graph: every `composition` node is a `domain:{slug}` node, every edge is
a governed domain-to-domain edge with its own provenance, and all per-domain authority and
content stays in `domains[]` (#230 slice 5, hy-uaks). The composed graph carries `contains`
edges only for now; the governed `depends_on`/`joinable_on` edges join it when their emit
lands (slice 2b, hy-g5u3), and OBSERVED cross-domain relations (slice 7) stay OUT of this
governed graph -- governed and observed are not mixed. The flat envelope keeps the exact
null/empty shape 18 defined, and the single-domain answer is still untouched: no `domains`
key, no `composition` key, byte-for-byte as before.

The 4 was 4 and not 3 because two changes claimed the 3 at once and the other
one merged first (hy-fhtr). The number is allocated by merge order rather than
by which branch wrote it down first, so two different shapes never ship under
one number. The same rule put the narrowing at 5, the proposal at 6, the
producer at 7, the added value at 8, the disclosure at 9, the eval
declaration at 10, the adapter projection at 11, the per-source grain at 12, the
per-source classification at 13, the per-source freshness at 14, the per-source
lineage at 15, the per-source checks at 16, the governed hierarchy `contains`
edge at 17, the multi-domain `domains[]` answer at 18, and the composed
`composition.graph` at 19 -- 16 was what its branch took only because it merged before
#286, which wanted the same next number; had #286 landed first, that would have rebased
to 17, 17 is what the hierarchy-emit branch took for the same merge-order reason, 18 is
what the multi-domain-resolve branch took, and 19 is what this composition branch takes
for it in turn.

That is an instance of a rule, and the rule is ADR 0018: **`SCHEMA_VERSION`
versions the answer, not the request.** A break in what a caller may SEND is
announced here, in these words, with the shape that stopped working and what to
send instead, and is carried as a `release-note` bead under ADR 0015's register.
It does not move `SCHEMA_VERSION`, which is a field on the bundle and therefore
a statement about a response. A break in what a caller RECEIVES does move it.

**One assist-derived section is served, and this is its announcement.** ADR
0019 scopes the no-semantics invariant to governance so a second mode can
answer what the exact path goes quiet on, and the first thing it answers is the
coverage refusal: a caller names a domain that exists, states the concept terms
its answer needs, and the domain declares none of them. That is still
`domain_does_not_declare`, still `no_match`, and still serves nothing governed
-- and a bundle refused that way now also carries `assist`, holding the
observed datasets the estate actually contains, ranked, each stating the
signals that placed it and every disagreement with Git that was found
(hy-gh-124 slice 1).

Four signals, and they are named in `produced_by.signals` on every response
rather than left to be inferred: `git_engagement` (the configured Git corpus
already approves this source, or cites it as evidence, for some other claim),
`expression_agreement` (this source computes something the named domain's
commit declares, compared as a computation by the same comparator plan
validation uses, never by name), `declared_references` (how many live observed
assets declare a reference to this source, and which ones), and
`source_freshness` (what the source itself disclosed, and `null` means unknown
rather than old). Display-name similarity is not a signal.

`declared_references` is named for what it counts and not for what hy-gh-124
asked for. A Superset chart declares the dataset it queries, so the estate's own
reference structure is readable (hy-d7xh); **an execution count is not**. No
projection carries how many times anyone ran a query or opened a dashboard, and
nothing infers one, so the signal's statements say "declares a reference to" and
never "queries it often". It counts direct references only -- a dashboard
containing a referring chart is a second hop, which is lineage proximity under
another name -- and only between live endpoints, since the projection keeps rows
whose endpoints were soft-deleted and counting those would rank a deleted
chart's dataset above a live one. It ranks below both Git signals: a reference
count is the estate's opinion of itself, approval is the customer's stated one,
and no count lifts a source that disagrees with Git.

Lineage proximity is still deliberately withheld, because naming the declared ref
a candidate is proximate to would put a declared ref in a candidate's own output.
`resolution.status` does not move for any of this and `provenance_refs` gains
nothing.

**A second refusal carries the section, and it is the same question in its
purest form.** `unknown_domain` refuses when the caller names a domain no
configured context declares at all, so Git says nothing WHATSOEVER rather than
nothing about one term -- which is hy-gh-124's headline case, "no governed
source exists for churn", entire. A bundle refused that way now carries the same
ranked list over the same observed estate, and it can never carry a proposal,
for the reason the decline states below (hy-xq55). The refusal itself does not
move: still `unknown_domain`, still `no_match`, still nothing governed. A
directive naming several subjects of which one is unknown grows nothing, because
there is no single subject the list would answer for. `no_context_source` grows
nothing either: it is a request to fix the estate, and a ranked estate is not an
answer to one. (A directive naming several KNOWN domains no longer refuses at all
since slice 3 (hy-cnto): it resolves each in a `domains[]` entry, so the retired
`multiple_domains` is no longer among the refusals discovery is asked about.)

The two halves of this change answer the version question differently, and both
answers are stated because the wrong one is easy to read onto the other. Serving
the section on a second refusal does NOT move `SCHEMA_VERSION`: no field changed
shape, `assist` has been a section a bundle MAY carry since 4, and 4 is the
version that announced that conditionality, so extending which refusals meet it
adds no key. The fifth `proposal.outcome` value DOES move it, to 8, for the
reason the version history gives above -- default-deny is not published for that
field, so decision 5's precondition is not in force and an added value is a
break rather than an addition. Carried as release-note bead `hy-w9br` under ADR
0015's register, with the one behaviour a client can see change: a directive
naming a domain nothing declares used to return a bundle with no `assist` key at
all.

**One candidate may now be put forward, and it is never called canonical.**
hy-gh-124 asks for "a strong-signal canonical suggestion, labeled derived", and
ADR 0019 floor 1 -- the later ruling -- forbids `canonical` in a status, in a
field, and in prose. The thing the issue wanted survives the word: `proposal`
names the one candidate the evidence separated, in the vocabulary the section
already used for every rank. It reads the existing order and adds no signal, no
evidence and no new read; what it adds is an answer to the question a list of
five leaves open, which is whether anything actually stood out (hy-gh-124
slice 2).

Three conditions, each checkable by the reader against signals already on the
candidate and none of them a score: the candidate carries no disagreement; at
least one of `git_engagement` and `expression_agreement` is nonzero; and it is
strictly ahead of the next candidate that could itself be proposed. So the two
signals that are not Git-relative order candidates and can never promote one:
the newest source in an estate the commit says nothing about is rank 1 and is
not put forward, and neither is the one the most charts point at, because
`declared_references` is the estate's opinion of itself. A source the commit
prohibits, one that stopped reporting, or one
carrying an open finding stays ranked and stated (floor 6) and is never
proposed.

`proposal` is always present, and a decline says which of four reasons withheld
it: `no_git_relative_signal`, `not_separated_from_the_next_candidate`,
`every_candidate_disagrees_with_git`, or `no_governing_domain`. Those are
separate values because they are separate facts about the estate -- "nothing
stood out" and "everything here disagrees with your own Git context" are
different answers, and a caller deciding whether to go looking itself needs to
know which one it got. `no_governing_domain` is the exception that proves the
grouping: it is decided before a single candidate is read, because it is a fact
about the REQUEST against the corpus rather than about how the estate happened
to rank, and it is checked first. Another domain may well already approve a
source on the list, and that stays stated on the candidate -- but an approval
written against another domain is a governed fact about that domain, not a
reason to put its source forward for a claim nothing governs. On a decline `ref`
is null and `basis` is empty. The ranked list is identical either
way: only the proposal is withheld, never a candidate.

**It can be declined on the shared service, and NOT YET on any transport.**
`ContextDirective.assist` defaults to `true`; `false` drops the `assist` section
from a coverage refusal, leaving the governed answer alone including its silence.
The asymmetry is the decision rather than a convention: passing `true` is a no-op
by construction, because assist runs where governance is silent whether or not a
caller asked, so there is no request on which asking makes a section appear that
omission would not have produced.

**Not accepted on HTTP/MCP or the CLI yet: `assist` (hy-hj9g).** The directive
allow-list and the published MCP schema still take five keys, and a request
carrying `assist` is refused as `unknown_parameter`. Adding a property to the
directive schema moves `tools_hash` and re-rolls the evaluation arm, so it is
deliberately deferred to hy-hj9g's ratification pass, where the re-roll is taken
once with pins re-taken. Until then the beneficiaries ADR 0019 decision 1 names
-- an evaluation arm, a conformance run, a caller under a compliance obligation
-- reach it through `resolve_analytics_context` in process, not over a transport.
Saying otherwise here is what this paragraph got wrong on its first version, and
`test_the_documented_directive_fields_are_the_ones_a_transport_accepts` is the
guard that now compares this list against the allow-list and the dataclass.

Declining costs no discovery query -- refusing assist means governance alone was
computed, not that assist ran and its output was dropped. `SCHEMA_VERSION` does
not move: this changes what a caller may SEND, not the shape of the answer (ADR
0018). `request.directive` echoes `assist` only when it is `false`, so no answer
advertises a field the surface that produced it would refuse on input. Two
things a conformance run can check, and both are asserted: every
governed section is identical with and without assist, and `bundle_id` DOES move
between them, because it covers the request and the request genuinely differs.
What ADR 0019 promises byte-for-byte is the governed sections, not the identity
of an answer to a request nobody made.

Declining is not a coverage claim and does not relax the `concepts` pairing: a
domain named with nothing to verify it against is still refused.

**The status name for a wholly assist-derived answer is still not served.** ADR
0019 decision 3 proposes `assisted` and marks it PROPOSED pending Overseer
ratification; this change did not serve it, so `RESOLUTION_STATUSES` is
unchanged and a bundle carrying candidates summarises as `no_match`. That is
the conservative reading of decision 1 -- the status must never read better
than the answer's worst claim, and "nothing governed here" is the worst there
is -- and it keeps an unratified word out of a served enumeration. What that
ADR settles in advance, so four issues do not each invent it: the mode is a
property of each claim rather than of the request, so
a caller can never ask for a claim to be labelled governed; assist may order
and propose but may never produce an identity,
because assist output has no field that can hold the declared ref such a link
would need, and an ambiguity ADR 0017 refused to break is not an assist ranking
input -- the `ref_ambiguous` record is governance's, and assist may cite it by
code and ref but may not author, transform, filter, or reorder one -- so the two
rulings are non-composable rather than merely each correct; and assist
annotates a verdict rather than moving one, so a plan no
governed rule covers stays `unverifiable`. The section 7 entry and the
`release-note` bead for the status name belong to the change that first serves
it, which this one is not.

Every `resolution.warnings` entry is `{code, message}`. The `code` is a stable
identifier a client branches on; the `message` is prose for a person and may
be reworded at any time. The vocabulary is `no_context_source`,
`unknown_domain`, `multiple_domains`, `domain_ambiguous`,
`domain_does_not_declare`, `plan_first_required`,
`ref_outside_context`, `ref_malformed`, `ref_ambiguous`, `ref_not_observed`,
`ref_awaiting_sync`, `ref_corroborated_late`, `evidence_ref_unresolved`,
`projection_bounded`, `max_hops_not_applicable`,
`observed_payloads_omitted`, `over_context_budget`, exported as
`hyperset.bundle.WARNING_CODES`. A refusal a caller can act on differently
carries a different code: `ref_malformed` is fixed by editing the ref,
`ref_ambiguous` by qualifying it, `ref_awaiting_sync` by a connector sync,
`ref_not_observed` by none of those -- the connector read the estate and the
asset was not in it, so only the estate itself gaining that asset changes the
answer, and `ref_corroborated_late` is not a problem at all -- it says a gap the
snapshot discloses HAS since been corroborated, so the bundle is stating a
reconciliation rather than reporting a fault. Real estates sync out of order, so
a commit is routinely read before the evidence it cites exists; the ref is
re-resolved when a bundle is served rather than replayed from the snapshot, and
the immutable snapshot is never re-authored to delete the finding it recorded.
`domain_ambiguous` is an ESTATE fault, not a caller fault, and stays in the
vocabulary though `multiple_domains` (hy-gh-282) is now RETIRED as a refusal:
since slice 3 (hy-cnto) a caller naming several KNOWN domains RESOLVES (each in a
`domains[]` entry) rather than being refused, so the code is kept for older
clients but no longer emitted. `domain_ambiguous` is unaffected -- it is a
single requested domain claimed by more than one configured source, so no commit
can be the authority and the message names the CONFLICTING SOURCES and commits
rather than the directive. It still runs FIRST: a request for several domains
one of which is ambiguous is an ambiguity, not a multi-domain resolve. Sync
refuses a new collision at write time; this code discloses one an estate already
carries, and disabling all but one claimant (`hyperset context disable`) clears
it. Adding it did not move `SCHEMA_VERSION`: a new `resolution.warnings[].code`
is an added value in a field that publishes default-deny, additive under ADR
0018 decision 5 -- the `ref_awaiting_sync` case, not hy-gh-285's `valid_with_gaps`
status value, which moved the number because `status` publishes no default-deny.
Dropping the disclosure instead would make a reconciled bundle indistinguishable
from one whose snapshot never had the gap, while the snapshot it names still
lists it (hy-gh-118). `ref_awaiting_sync` is the other half of that: it is
`ref_not_observed`'s case split by what the absence is evidence OF.
`ref_not_observed` now means every connection of that connector last finished a
sync that succeeded and the asset still is not there -- the estate was read and
the asset is not in it. `ref_awaiting_sync` means it was not read: some
connection of that connector has never finished a sync or last failed, or no
connection of it is configured at all, so nothing measured whether the asset
exists. A caller can tell "come back after a sync" from "this asset does not
exist", which is the distinction a single code collapsed. Neither is retryable:
retryable means a caller can fix it by asking again, and both need a connector
sync no re-ask performs. The planner prompt now names both codes and the
`ref_awaiting_sync` rule explicitly says to report the ref as Git-approved but
uncorroborated rather than treating it as queryable. The deterministic
`unfixable_ref_not_retried` scorer remains the release check for the
`ref_not_observed` case; an awaiting-sync eval case should be added when that
arm is re-recorded, because teaching the prompt or changing tool descriptions
moves the pinned prompt/tool hashes. The two a caller can fix by asking again are exported as
`hyperset.bundle.RETRYABLE_WARNING_CODES`, so a client can act on a bundle
that came back usable and incomplete without restating the rule itself.

A request that cannot be answered as asked carries
`{"error": {"code", "message", "recovery"}}`: over HTTP as the response body,
and over MCP as the tool RESULT with `isError` set, not instead of a result.
The `code` is stable and the `message` is prose. Either transport can return
`invalid_params`, `unknown_parameter`, `directive_required`, `unauthorized`,
`internal_error`. `unknown_operation` is raised by `run_operation` and reaches
an in-process caller only: over the wire an unknown operation is refused by
the transport before it gets there, as HTTP 404 `unknown_route` or MCP
JSON-RPC -32602. The six are exported as
`hyperset.transport.operations.OPERATION_ERROR_CODES`. `unauthorized` is the
authorization gate's denial (ADR-0030): it appears only when
`HYPERSET_AUTHZ_ENABLED` is on -- off by default, so today's unauthenticated
deployment never sees it -- and it is FIXED and non-disclosing, the same answer
whether the resource named exists or not, so the denial signals no existence. An HTTP client can also
receive `invalid_json`, `invalid_request`, `request_too_large`,
`unknown_route`, `method_not_allowed`, exported as `HTTP_ERROR_CODES`: they
are failures of the HTTP envelope before any operation is reached, so an MCP
client never sees them. A ref problem is not an error -- a malformed,
ambiguous or unobserved ref is served as a bundle whose `resolution.warnings`
say so.

Plan validation is deterministic and does not execute SQL. No other agent-facing
MCP tool is P0.

It compares SQL fragments -- field expressions, filters, grain -- as
computations rather than as characters. Whitespace, punctuation spacing, the
case of keywords and unquoted identifiers, redundant outer parentheses, a
trailing output alias, and a one-element `IN` list are folded, because none of
them can change a value: a reformatted governed expression is not a
contradiction, and the manifest never has to be hand-resynced to the exact
characters a warehouse happens to print. The case and content of whatever
stands inside a delimiter the comparator holds are never folded, and the
delimiters are named rather than implied: `'`, `"`, `` ` ``, and Postgres
dollar-quoting (`$$...$$`, `$tag$...$tag$`). `'Completed'` and `'completed'`
are different rows, and so are `$$Completed$$` and `$$completed$$`. T-SQL's
`[...]` is deliberately not one of them, so `[Region]` and `[region]` compare
EQUIVALENT: `[` is valid Postgres as an array or JSON subscript, and lexing it
as a delimiter made a reformatted subscript a contradiction. Postgres is the
only warehouse dialect Hyperset ships against, which is what makes that
asymmetry safe rather than a false pass, and a second warehouse dialect reopens
it -- under a case-sensitive T-SQL collation the fold would be wrong. A
difference confined to table qualifiers
(`SUM(o.amount)` against `SUM(amount)`) or to casts (`posting_date` against
`posting_date::date`) is neither the same computation nor provably a different
one: settling it needs the warehouse schema, which Hyperset does not read, or
the query, which Hyperset does not run. It is disclosed as a `warning` stating
both forms -- `field_expression_undecidable`, `filter_undecidable`,
`grain_undecidable` -- rather than judged. Everything else is still an `error`,
operand order included wherever the operands are still distinguishable once
qualifiers AND casts are relaxed away: `a - b` and `b - a` read the same
columns and are not the same number (hy-gh-128). The exception is bought by
the undecidable band as a whole, not by either relaxation alone, and it is
stated rather than hidden: where two operands collapse to the same tokens
under those relaxations -- `SUM(gross.amount) - SUM(refunds.amount)` reversed,
or `SUM(amount::numeric) - SUM(amount)` reversed -- the reversal is disclosed
as `field_expression_undecidable` and the sign flip rides in a `warning`, not
an `error`. Tightening it would refuse `SUM(o.amount)` against
`SUM(orders.amount)` and `posting_date` against `posting_date::date`, the
cases the relaxations exist to serve, so the guarantee holds only for operands
the comparator can still tell apart (hy-70fk).

**A client that receives a violation `code` it does not recognise MUST treat
the plan as NOT APPROVED. An unrecognised code is never read as approval.**
That rule is what makes a new code safe to add rather than merely cheap: the
worst an old client can do is refuse to interpret, which is loud, instead of
reading a code it has never heard of as permission, which is silent. It has a
cost, and the cost is written here rather than left for a client to discover --
an older client applying it will REFUSE a plan Hyperset validates whenever it
meets an undecidable warning code it does not know. Additive on the wire,
conservative in the client, and it fails in the direction a governance product
should fail. The rule is normative and not advice: a client meets a new code
before it meets a new document, so default-deny is what a client has in the
window between the two.

Each `violations` entry is
`{code, severity, section, subject, message, recovery}`. The `code` is what a
client branches on; `section` names the instruction section or plan parameter
the finding is about and `subject` the element named, which is empty where the
finding is an omission rather than something the plan said; `message` and
`recovery` are prose for the caller and may be reworded at any time. `recovery`
is the move that answers the code -- the same obligation an error's `recovery`
carries, on the other response, because a plan told only what is wrong with it
has nothing to send next (hy-pvbu). It is one text per code, so the specifics
stay where they already are: `message` names the offending element, `subject`
names it structurally, and `checked_against` carries the two bundle ids.

The violation vocabulary is `no_governed_context`, `stale_bundle`,
`no_declared_sources`,
`prohibited_source`, `unapproved_source`, `observed_only_source`,
`disputed_field`, `unapproved_field`, `unapproved_filter`, `unapproved_join`,
`undeclared_field_source`, `missing_required_filter`, `missing_required_check`,
`field_expression_mismatch`, `field_source_mismatch`, `grain_mismatch`,
`grain_fanout`, `join_type_mismatch`, `field_expression_undecidable`,
`filter_undecidable`, `grain_undecidable`,
`classification_undisclosed`, `cross_domain_join_unverifiable`,
`ambiguous_source_component` and
`ambiguous_field_component`, exported as `hyperset.bundle.VIOLATION_CODES`. Nineteen
values were served while this document named four of them, in prose about
something else, and every mechanised check stayed green because they enumerate
the operation-error and warning fields, which are different fields (hy-ruui).
The list is gated in `PlanViolation` the way `warning()` gates a disclosure
code, so a value that reaches a client without being published here now fails
where it is constructed rather than on the wire; the register it is gated
against maps each code to its `recovery`, so a code with no remedy cannot be
declared. The first three say the check could not be made at all, because one
side of the comparison is missing: no governed context, a bundle the plan was
not built against, or a plan that declares no sources. All three answer
`unverifiable` rather than `invalid`, which is reserved for a plan that WAS
compared with governed context and contradicts it. `prohibited_source` through
`disputed_field` are about a source the context refuses or does not vouch for;
`unapproved_field` through `missing_required_check` are things the plan states
and the context does not, or the reverse; the four `_mismatch` codes are
elements both state differently; and the three `_undecidable` codes are the
disclosed band above, where the element named is not governed and no client may
present it as though it were.

A plan declaring no sources is refused as a whole rather than compared field by
field, and the difference is what a caller is sent to look at. Every governed
field requires a source such a plan does not list, so it used to be answered
with one `undeclared_field_source` per field: true of each field, and silent
about the single omission that produced all of them (hy-pvbu, measured on a real
agent run). `source_refs` stays optional in the signature above -- the refusal
is a verdict about the plan, on the response the caller already reads, not a new
required parameter.

`SCHEMA_VERSION` does not move for `no_declared_sources` either, and it does
move for `recovery`, which is one change carrying one of each kind. A new VALUE
in `violations[].code` is additive under the rule above -- a client that does not
know the code refuses the plan, which is the safe direction -- and a new KEY in
a `violations` entry is a shape change in what a caller receives, so that change
took `SCHEMA_VERSION` to 3 (ADR 0018 decisions 4 and 5) and the assist section
that shipped behind it took the 4. The two are stated
separately because they are separate rulings and the wrong one is easy to cite:
neither the code nor the remedy would have moved the number if the remedy had
been folded into `message`, and folding it there is what hy-pvbu measured the
cost of. Carried as release-note bead `hy-ltqz` under ADR 0015's register, which
also states the one behaviour a client can see change: a plan with no
`source_refs` was `invalid` with one `undeclared_field_source` per field and is
now `unverifiable` with one `no_declared_sources`.

`SCHEMA_VERSION` did not move for the three undecidable codes, and it took both
halves to say so: no field of `PlanValidation` changed shape, AND unknown values of `code`
are default-deny by the rule above. A new value in a served field is not a
shape change, and it is additive only where that rule is published for the
field; where it is not, the number moves. Publication is the whole of the claim
here, and the rest is named rather than assumed: ADR 0018 also binds
default-deny on the client surfaces Hyperset itself ships, and that half is now
in force: the planner prompt and the three served tool descriptions carry both
its halves, gated by
`tests/unit/test_default_deny_is_in_force_on_the_shipped_surfaces.py` (hy-9nrf).
It was carried as debt rather than as a reason to move the number while it was
open (hy-12oy). Not moving it is not
permission to ship silently -- the three are carried as release-note bead
`hy-buem` under ADR 0015's register, per ADR 0018 decision 3 (hy-tota, which
amends ADR 0018 decision 1's "a change to what a caller RECEIVES moves it" to
the shape of what a caller receives).

### Administrative surface

The v0 browser/API surface may show connection and Git-sync health, current
context commit, evidence/provenance, findings, evaluation state, and notification
replay. It is not a context CMS, approval UI, BI frontend, or agent workflow
orchestrator.

The administrative readiness response uses `ready`, `degraded`, `blocked`, and
`unknown` for operational outcomes, plus the informational states `disabled` and
`not_configured` for optional components. A missing or explicitly disabled optional
connector, analytics database, notification channel, or write-back target does not
lower `overall`; nor does an optional connector with no recorded liveness fact. Once
configured and enabled, its recorded `degraded` or `blocked` failure does. Model,
embeddings, database, Git context, and API remain required, and Ollama is required
when either selected provider uses it. These status values are default-deny under
ADR 0018: an unrecognised component status is surfaced and treated as at least
`unknown`, never as `ready`. The shipped admin UI recognises and displays all six.

### Tool-design requirements

- Parameters are unambiguous and examples are checked into generated tool docs.
- Errors explain recovery.
- Results disclose incomplete, stale, mixed, conflicting, or observed-only states.
- Tools return enough environmental feedback for an external agent to choose its next action.
- Tool-count expansion requires evaluator evidence and an ADR amendment.

## 8. Canonical v0 object decisions

### `ObservedAsset`

- Stable identity derives from connection, source kind, and source-native identity.
- Each materially distinct raw payload is an immutable version.
- Transport-native payloads remain lossless and transport-specific.

### `ContextSnapshot`

- Represents an immutable read of the configured Git context at one exact commit. The configured source may be a repository checkout, a remote Git URL, or a CI-produced Git bundle; runtime presence of a `.git` directory is not required.
- Identity includes repository, ref, path, and commit SHA.
- A packaged source must still carry the exact commit and tree. A directory of loose files with no commit provenance is not an authoritative context source.
- Normalized fields are runtime/compiler aids only; the Git snapshot remains the semantic authority.
- An unchanged commit is a no-op.
- A new commit creates a new immutable snapshot; it never mutates prior context history.
- Evidence resolution never decides whether a snapshot exists (ADR 0017). A declared ref with no observation behind it, or one observed on two connections, is recorded as a `{code, ref, message}` finding on the snapshot and resolves to no link; an unreachable repository, an invalid document, or a structurally unlinkable ref still fails the sync and leaves the last valid snapshot serving.

### Context format

V0 uses a narrow checked-in format, parsed by `hyperset.context.schema.parse_context`:

```text
playground/examples/revenue/
  manifest.yaml
  context.md
  evals.yaml
```

Only fields required by the revenue benchmark belong in the v0 schema. The
format is a customer-owned Git contract, not a Hyperset CMS model.

An unrecognized manifest field is an error, not a silent drop: a customer who
writes a key Hyperset ignores would otherwise believe governed context says
something it does not. The sync fails with every reason at once and the last
valid snapshot keeps serving, so a misspelling costs a failed sync rather than
governance that quietly does not exist.

`manifest.yaml` supports exactly these fields, exported as
`hyperset.context.schema.SUPPORTED_MANIFEST_FIELDS`:

| Field | What it declares |
| --- | --- |
| `schema_version` | Required. Must equal `1`; any other value is rejected. |
| `domain` | Required. The name a `ContextDirective` resolves and the catalog lists. |
| `title` | Human-readable domain name. Defaults to `domain`. |
| `parent` | Optional. The governed slug of this domain's parent domain (ADR-0031), casefolded like `domain`; absent means a root. Validated at sync against the whole estate — an unknown parent or a parent cycle is rejected and the last valid snapshot keeps serving. Surfaced into the served domain graph as a `contains` edge to the immediate parent and children (SCHEMA_VERSION 17, hy-gh-230); the emit re-validates the whole estate (`hierarchy.validate_forest`) and omits any edge whose endpoint is unverified, fail-closed. |
| `owners` | Owner refs. Recorded as `source: manifest`, distinct from refs the repository's own CODEOWNERS supplied. |
| `context_doc` | Required. Path to the human-readable guidance file, which must exist in the context path and be non-empty. |
| `evals` | Optional. Path to the customer-owned locked evaluation cases, which must exist and declare at least one case. Declare `evals: none` (or omit the key) to state honestly that the domain has no eval bank yet -- it validates and syncs, and is disclosed as unevaluated (`context_authority.unevaluated` in the bundle, `counts.eval_cases: 0` in the catalog). Pointing at a file that holds zero cases stays a hard error: that is a mistake, not a declaration. `none` is a reserved sentinel, so a file literally named `none` cannot be referenced here -- no bank is a stated fact, not a filename. |
| `definitions` | Concept definitions, each `{term, statement}`. Both are required; connector evidence does not define company meaning. |
| `approved_sources` | Tables or pipelines the domain approves, each `{ref, role, reason?, bi_override?}`. `ref` and `role` are required. This declares meaning and authorization, not connectivity; exact live corroboration appears separately in `linked_evidence.observed_assets`. An optional `bi_override` is `{ref, reason}` and explicitly governs one Superset dataset as another address for the source. |
| `prohibited_sources` | Tables or pipelines the domain forbids, each `{ref, reason, bi_override?}`. The reason is required: a prohibition an agent cannot explain to a human is not accepted. The optional override has the same shape and semantics as on an approved source. |
| `fields` | Field definitions, each `{name, source_ref, expression}`, all required. A `source_ref` naming an unapproved source is rejected. |
| `joins` | Join rules, each `{from, to, type}`, all required. |
| `filters` | Filter expressions the domain requires. |
| `grain` | The grain the domain's guidance assumes. |
| `checks` | Validation statements a result is expected to satisfy. |
| `caveats` | Warnings that travel with the guidance. |

A governed source `ref` is `<table|pipeline>:<platform>:<external_id>`: a
durable code-level identity such as
`table:postgres:analytics.public.finance_orders_daily`. Fields and locked
evaluation cases use that identity too. Superset and DataHub assets remain
observed evidence rather than the identity of company meaning. An organization
that intentionally governs a BI semantic object may add a reasoned
`bi_override`; v0 accepts exactly `superset:dataset:<external_id>`. The override
is linked by source-native identity and does not replace the table or pipeline
ref (ADR 0023).

### Domain graph

- Store ordinary versioned Postgres records/typed relationships only as a projection/index.
- Include only nodes needed by the benchmark: domain, concept, source, field, join, filter/grain rule, check, owner, provenance.
- Edges require explicit Git context, source evidence, or source-native lineage.
- Name similarity can create a finding candidate, never a factual edge.

### AI-assisted curation

Not required for v0. A future configured model may investigate a finding and
propose a Git patch/PR. It cannot create an independent authoritative context
version or approve/merge the change.

## 9. Build order and unlock gates

### Gate A — complementary real-source identity

Complete pinned Superset and DataHub environments and prove the shared revenue
domain across one supported read-only transport per source. Do not extract a
generic connector SDK yet.

### Gate B — Git-context integration and trusted retrieval

- sync one authoritative Git revenue context with exact commit identity;
- link it to the real Superset/DataHub evidence;
- create one deterministic contradiction/finding;
- use a real lightweight planner to select the revenue domain and concepts from
  hidden paraphrases among multiple plausible domains;
- resolve one deterministic `ContextBundle`;
- validate one proposed fetch through the same application-service path used by HTTP/MCP.

No curator or independent Hyperset authoring/approval workflow is required.

### Gate C — context-effectiveness and invalidation proof

Use Inspect AI for three task families:

1. correct governed fetch, result, validation, and evidence;
2. observed-only or no-match disclosure;
3. stale/conflicting/deprecated qualifier disclosure.

The first family contains multiple locked revenue questions so the comparison is
not a one-prompt anecdote. The questions include wording that does not name the
configured domain, and the corpus includes plausible decoy domains. Correct
domain/concept selection, irrelevant-context avoidance, and safe abstention are
critical deterministic predicates alongside source/rule/result correctness.
The governed arm exercises the real model runtime; a `ScriptedRuntime` test may
prove plumbing but cannot satisfy this gate. Each case has a reference
query/result and deterministic outcome graders. Manually inspect representative
traces.

Required benchmark arms:

1. pinned small Ollama model + governed Hyperset MCP;
2. the same small model + raw source/lake metadata;
3. a pinned frontier model + the same raw metadata for release evidence.

Required CI never needs hosted credentials, and it does not run a live model
either. Required per-PR CI scores a committed, versioned recording of each arm
with the deterministic scorers, and says so; the live arm-1 and arm-2 runs
against real Ollama happen on a schedule and commit their versions and
disclosures. A required gate that ran the local arms would take hours on a
GPU-less cloud runner, and the alternative — a self-hosted runner — would make
the gate unrunnable on a fork (ADR 0013). A public superiority claim requires a
fresh comparable run with exact model versions, identical questions/tools, and
full score/transcript disclosure.

Each case records the Git context snapshot and observed-version dependencies it
used. A changed dependency invalidates only affected cases, reruns them, and
creates one durable finding/maintenance task plus one generic webhook event when
a critical case fails. V0 does not include Slack, email, PagerDuty, or a
notification platform.

### Gate D — Docker/restart proof

The complete path runs from a clean checkout, survives restart, and reproduces
IDs, Git/source snapshot versions, retrieval, validation, scores, and notification state.

### Gate E — controlled breadth

Only after Gates A-D are green may work expand toward:

- AI-assisted Git patch/PR proposals;
- Slack or other notification channels;
- more processor rules;
- a larger evaluator suite;
- more MCP convenience tools;
- more context kinds;
- a third connector or extracted connector SDK;
- authoring/editor workflows;
- identity/RBAC/HA/enterprise-scale certification.

## 10. Definition of done for any P0 issue

A P0 PR must show:

- the exact walking-skeleton step it enables;
- the real or authoritative fixture used;
- before/after repository state;
- the public contract affected;
- deterministic tests at the narrowest useful layer;
- integration evidence through the shared application-service path;
- no new semantic source of truth;
- no unsupported compatibility claim;
- no agent-facing tool without evaluator/ADR evidence.

A PR that creates a broad abstraction without exercising the canonical scenario
should be split or deferred.

## 11. Drift checks before implementation

Before substantial work, answer:

1. Does this directly advance the walking skeleton or a recorded failure after it?
2. Is Git still the authority for business meaning?
3. Is source behavior backed by a real source contract?
4. Is Hyperset persisting operational evidence rather than inventing a second semantic lifecycle?
5. Could the same value be delivered through `ContextBundle` or plan validation rather than another tool?
6. Can success be graded as an observable outcome?
7. Are we adding complexity because evidence proved it necessary?
8. Would an external Claude/Codex client understand status, limitations, provenance, and next action?

If these answers are unclear, stop and update this contract or the relevant ADR
before coding.

## 12. Removed boundaries

Pre-pivot semantic, compatibility, bridge, owned-agent-runtime, artifact, trust,
and multi-tool MCP packages remain removed. Git history preserves them for
archaeology. Restoring one requires an accepted ADR showing why the walking
skeleton and `ContextBundle` cannot deliver the proven need.

## 13. What v0 proves

v0 succeeds when customer-owned Git context plus real Superset/DataHub evidence
lets a separate small pinned Ollama model select and validate the right revenue
fetch, return the reference answer with exact provenance, materially outperform
the same model using raw metadata alone, and fail visibly when a depended-on
source or context commit invalidates that answer.

Everything else is supporting infrastructure or post-proof expansion.
