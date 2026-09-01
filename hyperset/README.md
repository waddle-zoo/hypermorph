# Python package

Active v0 code:

- `connectors/` observes sources without granting authority;
- `context/` reads the configured Git repository/ref/path and snapshots the
  exact commit; it never authors, edits, or writes back context;
- `db/` owns SQLAlchemy models and Alembic migrations;
- `repositories/` owns DTO/protocol boundaries and Postgres persistence;
- `processor/` runs one deterministic rule over a completed sync and records
  explainable findings; it proposes reviews in the customer's Git repository
  and cannot approve, edit, or apply anything;
- `bundle/` defines the one public `ContextBundle` shape, compiles it from
  the pinned Git commit plus linked evidence for exactly what a
  `ContextDirective` names, lists what exists through a read-only bounded
  catalog,
  and checks a proposed analytics plan against exactly that bundle; it is
  read-only, executes no SQL, interprets no natural language, and is the only
  thing HTTP, MCP, and evaluation clients may consume;
- `transport/` serves exactly three agent-facing operations,
  `list_context_catalog`, `resolve_analytics_context`, and
  `validate_analytics_plan`, over HTTP and over MCP stdio; both adapters
  decode through one operation registry and serialize what the application
  services returned, so neither owns a response shape and neither can drift
  from the other;
- `planner/` is an adapter and a tool contract over those three operations,
  never an agent framework: a runtime translates the served descriptions into
  an SDK's shape and drives that SDK's own loop, and the model does the
  semantic work of turning a question into a `ContextDirective`. It reads no
  question text, executes nothing, and reaches the served surface through
  `run_operation`, the same entry point both transports use;
- `cli.py` exposes local DB, connection, and sync commands for both
  Superset transports, `context add|sync|status|show|history` for the
  Git-owned context source, `process sync` for the offline processor,
  `bundle catalog|resolve` for the discovery surface and the public bundle,
  and `serve http|mcp` for the two transports.

Evaluation and notification packages enter only when their walking-skeleton
issue is implemented and tested. Historical semantic, compatibility,
owned-agent, and multi-tool MCP packages were removed; `transport/` is a fresh
surface of exactly three operations over the merged contract, not their
return. `planner/` is not the owned agent returning either: the removed
`hyperset/agent` owned the reasoning, while `planner/` is a client of the same
three operations any caller gets, and `hyperset/agent` stays on the
`scripts/check_docs.py --section repo-shape` denylist that keeps it deleted.
