# 0002: Postgres is the v0 system of record

Status: accepted.

## Context

The product needs versioned assets and context, ownership and review
queues, conflicts and relationships, evaluation history, provenance/
change tracking, and transactional human approval — all relational
workflows. The historical architecture treated hand-authored YAML as the
runtime system of record (`docs/research/FACT_CHECK_2026-07-25.md` §1), which
doesn't support transactional multi-record approval or queryable
provenance at scale.

## Decision

Postgres is the v0 system of record (`hy-gh-26`). JSONB preserves
connector-specific raw payloads losslessly; typed relational columns
support search, review, and governance. Access goes only through narrow
`hyperset.repositories` Protocol contracts, never SQLAlchemy directly
outside `hyperset.db` — so a future backend (§ADR 0006 in the
`hy-gh-40` DynamoDB sense) can implement the capabilities it supports
without forcing SQL-join and key-value access patterns into one
interface. Full schema: `docs/postgres-persistence-v0.md`.

YAML remains legitimate for fixtures, import/export, and reviewable seed
data (`hyperset.db.seed`) — it is not the runtime store.

## Consequences

- Transactional review approval (context version + decision together) is
  possible and tested.
- Optimistic concurrency and full-text search are available where needed.
- Adds an operational dependency (a running Postgres instance) that a
  pure-YAML approach wouldn't have — accepted; `hy-gh-37` provisions it
  locally via Docker Compose.
