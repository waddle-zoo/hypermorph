# 0032: The SQL execution boundary — Hyperset serves governed context and never runs, generates, or validates the query

Status: PROPOSED (2026-08-14). Brandon ruled the boundary (hq-aqn3). This is a
CONTRACT CLARIFICATION: it consolidates a line the code already enforces into a
permanent PLATFORM boundary, draws the precise seam with the existing
`validate_analytics_plan`, and re-scopes #127 — returned for review, changing no
served shape. It records a decision, not a build.

Extends ADR 0019 (assist may reason, governance may not — its "No execution" holds),
ADR 0020 (Hyperset does not own the agent; execution and result-trust were parked
outside it), and ADR 0012 (a human Git merge owns meaning). It adds no served
operation and changes no served field: the `execution` disclosure already exists and
its values do not move, so `tools_hash` (`sha256:fe930a003b731211`) and
`SCHEMA_VERSION` (16) are unaffected — a doc/ADR clarification touches neither.

## Context

Hyperset's boundary against running the customer's SQL is stated in many places —
invariant 6 (`docs/v0-foundation.md`), the plan validator's module doc, the
`execution` disclosure on every bundle — but it is framed as a v0 "default" and
scattered, and #127 (hy-gh-127) proposed widening it: let an agent submit its result
(and the SQL it ran) for Hyperset to JUDGE. Brandon ruled the platform boundary
explicit and permanent, so this ADR states it once, reconciles it with the one
existing "validation" surface, and re-scopes #127.

## Decisions

### 1. Hyperset's core is governed context, not a query engine

Hyperset's core is the governed `ContextBundle` and its observation / review /
feedback loop. As a PERMANENT platform responsibility — not a v0 default — the core
does NOT generate, suggest, execute, or VALIDATE production SQL. No core SQL tool is
added, and no core path reads a warehouse, opens a cursor, or runs a query. This
supersedes the v0-scoped phrasing ("in v0", "the default is `false`", "unless a test
evaluator explicitly did so") wherever it reads as if core execution were merely
deferred rather than out of scope by design.

### 2. Consumers execute SQL with their own tools; the bundle stays useful without becoming an engine

An agent built ON Hyperset is an OPTIONAL consumer that may generate and execute SQL
with ITS OWN tools, credentials, policies, and runtime. The bundle stays useful for
that precisely by exposing the governed material — definitions, approved sources,
joins, grain, caveats, validations, prohibitions, provenance — and the
execution/validation STATUS, WITHOUT running anything. The reference consumer is the
playground demo: its `run_read_only_sql` tool (`playground/ui/app.py`, `_run_demo_sql`
against a demo analytics DB) is the AGENT's own tool, and the served bundle still
discloses `performed_by_hyperset: false`. A demo/benchmark query tool is a CONSUMER
capability, never a core one, and nothing may imply Hyperset executed or validated a
query when a consumer did.

### 3. The execution disclosure is permanently false in core

`execution.performed_by_hyperset` and `execution.result_validated_by_hyperset` are
`False` on every response and stay that way by this boundary. They are hard-`False`
at their only two writers — the bundle default and every `PlanValidation` — with no
truthy writer anywhere in the core, and a guard test pins that no core code ever sets
either true. "Every response states whether Hyperset executed or validated" is exact:
the honest answer is permanently no, and the fields are an invariant, not a default a
future core change may flip.

### 4. The precise seam: plan-vs-governance is in-core; SQL execution and result-trust are out

The existing `validate_analytics_plan` is IN-CORE and stays. It checks a proposed
analytics PLAN against the governed bundle DETERMINISTICALLY — approved sources,
joins, filters, prohibitions, grain, and field/filter expressions compared at the
token level (`equivalence.compare_fragments`) — a plan-vs-governance CONTRADICTION
check that reads no database and runs no query. A wrong plan is caught by
contradiction with governed context, never by executing it.

OUT of core, and a consumer concern: SQL CORRECTNESS, EXECUTION, and
RESULT-VALIDATION. Hyperset does not check whether the query is syntactically valid,
does not run it, and does not judge the number it returns. The line is not "Hyperset
validates SQL" versus "does not" — it is "Hyperset validates a PLAN against
governance" (a structural contradiction check) versus "Hyperset runs or judges a
query" (which it never does).

### 5. #127 (result-trust) re-scopes to the boundary; result-observation is a consumer concern

#127 / hy-gh-127 proposed a result-trust step where an agent submits its result — and
optionally the SQL it ran — for Hyperset to judge. That would make core READ the query
and the number, crossing this boundary. Proposed re-scope (returned for Brandon):
narrow #127 to the boundary STATEMENT — Hyperset does not judge an executed result —
and DEFER or close its core-SQL-judging ambition as out-of-core. A consumer that wants
its answer judged runs that check with its own tools; if Hyperset ever offers a
result-facing affordance, it is a NEW consumer-facing surface under its own ADR and
the enterprise access model (ADR-0030), not a core query capability.

## What this clarifies, and does not change

No served field, no new tool, no code behaviour changes: the boundary is already
enforced, and this consolidates it, makes it permanent, and re-scopes #127. It repairs
the "v0 default false" framing wherever that reads as deferral rather than design
(the targeted doc edits accompanying this ADR: `docs/v0-foundation.md` invariant 6 and
the benchmark-tool wording; an addendum on ADR 0019). It does not build a result-trust
feature, does not add a SQL tool, and does not touch the plan validator's behaviour.

## Consequences

- **Blast radius, stated plainly.** Nothing runs differently — this is a contract
  clarification over a boundary the code already holds. A guard test now makes
  "permanently false" enforced rather than merely stated, so a future change that tried
  to flip an execution field or run a query in core would fail the gate.
- It does not move the approval boundary (a human Git merge remains the sole authority,
  ADR 0012), the authz boundary (ADR-0030), or the hierarchy contract (ADR 0031).
- `tools_hash` and the MCP trust surface are unaffected — no served operation's
  name/description/input_schema changes and no served field is added; the `execution`
  disclosure and its values are unchanged.
- The #127 re-scope is Brandon's to ratify; no implementation of a result-trust
  affordance lands against this ADR.
