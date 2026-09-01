# 0008: Local Docker v0 before cloud deployment

Status: accepted; implementation not yet built (`#37`/`#34`).

## Context

Cloud hosting, multi-tenant isolation, and production authentication are
real eventual requirements but expand scope before the core product loop
(connect -> observe -> review -> govern -> retrieve -> evaluate) is proven
to work at all.

## Decision

v0 is a local Docker Compose stack: Postgres, a real pinned Superset
6.1.0, Hyperset's API/MCP service, an offline worker, and a review UI,
runnable with one command and requiring no cloud infrastructure or
production credentials (`MANIFESTO.md` "v0: A Local Docker Proof").
`hyperset db upgrade/downgrade/reset/seed-demo` and the Postgres
repository layer (`hy-gh-26`) and Superset connector bundle mode
(`hy-gh-27` Phase A/B) already work standalone, independent of the full
Compose stack, so this ADR doesn't block starting those — only the
"complete platform in one command" milestone.

`#34` is the release gate: connector -> observation -> processor ->
review -> governed context -> HTTP/MCP -> evaluation must run end to end
after a container restart before v0 is considered done.

## Consequences

- No DynamoDB, multi-tenant billing, enterprise SSO/RBAC, or cloud hosting
  work happens before `#34` closes.
- Every other v0 issue's "done" is measured against the local Docker
  stack and the exact public API/MCP/connector boundary it owns, not unit
  tests against invented payloads alone (`AGENTS.md` "Completion
  standard").
