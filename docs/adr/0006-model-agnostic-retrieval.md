# 0006: Model-agnostic HTTP/MCP retrieval

> **Amendment FLAGGED (not taken) by [ADR-0036](0036-bring-your-own-knowledge-graph-authority-adapters.md) (PROPOSED).** A later slice gives the served `context_authority` a discriminated `type` (`git` | `kg` | …) plus native-id / authority-identity / native-revision / degraded fields. That moves `SCHEMA_VERSION` (currently `20`) by merge order with a full served-surface sweep; ADR-0036 is design-first and does not take it. The Git instance stays byte-identical (`type:"git"`).

Status: accepted in principle; **tool-shape section superseded by ADR 0009**.

## Context

Hyperset should expose context through stable HTTP and MCP surfaces rather than owning a proprietary chat runtime, conversational UI, or model loop.

The first version of this ADR also concluded that the pre-pivot six-tool MCP shape should be reused against the Postgres repositories. Further audit showed that this preserved accidental complexity from the old semantic-layer runtime and encouraged later issues to grow the P0 surface to eight or nine tools before one end-to-end task was proven.

## Decision that remains accepted

- HTTP and MCP are transports over shared application services.
- Hyperset is model-agnostic.
- governed context is the default tier;
- observed-only fallback is explicit and disclosed;
- freshness, lifecycle, conflicts, deprecations, and provenance travel with retrieval results;
- Hyperset does not own external agent planning or warehouse execution.

## Superseded decision

The old six-tool surface is not reused as an architecture requirement.

ADR 0009 and `docs/v0-foundation.md` establish one canonical public response, `ContextBundle`, and one initial agent operation:

```text
resolve_analytics_context(...) -> ContextBundle
```

A provenance drill-down is the only optional second P0 tool. New agent-facing tools require an ADR amendment and evaluation evidence that the bundle cannot support a real task.

Administrative HTTP endpoints may support sync, review, health, and evaluation without becoming agent tools.

## Consequences

- issue #31 builds the shared `ContextBundle` resolver before broad resource or administration APIs;
- issue #29 starts from one context-resolution tool;
- HTTP, MCP, deterministic clients, and evaluator attempts consume the same schema;
- legacy `hyperset.mcp` and `AgentRuntime` contracts are migration references only;
- tool design is derived from real tasks and outcome evaluations rather than historical code inventory.