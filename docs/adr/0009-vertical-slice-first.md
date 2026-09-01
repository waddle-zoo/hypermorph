# 0009: Build one real vertical slice before horizontal platform breadth

Status: accepted.

Supersedes the tool-surface reuse assumption in ADR 0006 and constrains the sequencing described by ADRs 0003, 0004, 0006, 0007, and 0008.

## Context

The connector-driven pivot corrected Hyperset's product direction, but the remaining P0 plan still allowed broad components to be built independently:

- the API/MCP issue described a large resource and administration surface;
- the agent integration issue listed nine tools;
- the processor issue proposed ten rules against synthetic fixtures before real connector integration;
- the evaluator issue proposed twenty cases before one public retrieval contract was proven;
- implemented artifact adapters continued to preserve pre-pivot semantic and agent abstractions as parallel authorities.

This creates a high risk of building internally consistent subsystems that validate the wrong source contract or disagree on lifecycle, identity, and response shape.

Anthropic's engineering guidance reinforces a simpler approach: start with the smallest composable interface, design tools around concrete tasks, use environmental feedback, grade observable outcomes rather than rigid internal trajectories, and add complexity only after evaluation demonstrates value.

## Decision

Hyperset v0 development is vertical-slice-first.

Before broadening any P0 subsystem, the repository must prove one real Superset 6.1.0 revenue scenario through this complete path:

```text
real source asset
  -> immutable observation
  -> real connector change
  -> deterministic finding
  -> human review decision
  -> approved governed context
  -> one public ContextBundle
  -> raw-vs-governed deterministic evaluation
  -> Docker restart and replay
```

`docs/v0-foundation.md` is the binding operational contract for this decision.

The v0 agent-facing surface begins with one task-oriented operation:

```text
resolve_analytics_context(...) -> ContextBundle
```

A provenance drill-down operation is the only optional second P0 tool. Resource inspection, sync administration, review decisions, and UI operations may use HTTP endpoints but are not separate agent tools by default.

The `ContextBundle` is the shared public response used by HTTP, MCP, deterministic clients, integrations, and evaluator attempts. It must disclose lifecycle, freshness, conflicts, fallback, exact versions, provenance references, and the fact that Hyperset did not execute or validate external SQL unless a test evaluator explicitly did so.

No connector or adapter may create approved governed context. Approval requires a persisted human `ReviewDecision`.

No new v0 code may treat `hyperset.semantic`, compatibility extraction, or the legacy `AgentRuntime` as a parallel system of record. Existing adapters are migration scaffolding only.

## Unlock gates

1. **Real source identity:** one asset is proven against the pinned upstream environment.
2. **Persisted governance loop:** one change creates one review task and one human-approved context version.
3. **Context-effectiveness proof:** at least three unambiguous raw-vs-governed tasks pass deterministic outcome graders.
4. **Operational proof:** the complete path runs from a clean checkout and survives restart.
5. **Breadth:** only after gates 1-4 may the project expand toward more tools, rules, cases, context kinds, UI, or connectors.

## Consequences

- ADR 0006's statement that the old six-tool shape should be reused is superseded. Tool shape is derived from the current task and evaluator evidence.
- Issue #31 must implement the shared `ContextBundle` application service before broad resource/admin breadth.
- Issue #29 starts with one context-resolution tool rather than a nine-tool inventory.
- Issue #38 starts from a real connector change and one deterministic rule; synthetic unit fixtures remain supplemental.
- Issue #25 defines the first three gating tasks before expanding to the full suite.
- Issue #34 is the integration owner and defines the canonical scenario and identifiers.
- Existing union artifact types may remain for compatibility, but they are not the public compatibility boundary.
- Adding an agent-facing tool requires an ADR amendment and an evaluation demonstrating why the bundle cannot satisfy the task.

## Rejected alternatives

- Build all repository protocols, resource APIs, processor rules, and evaluator schemas independently and integrate later.
- Reuse pre-pivot tool contracts solely because code already exists.
- Treat synthetic fixtures as sufficient evidence for upstream behavior.
- Create a broad agent orchestration runtime inside Hyperset.
- Generalize a connector SDK before the first connector completes the product loop.