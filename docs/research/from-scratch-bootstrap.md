# Local from-scratch bootstrap for Hyperset v0

> [!NOTE]
> **Status: current research for the local v0. Last verified 2026-07-25.**
> This replaces the rejected proposal to emulate a Superset backend or copy its
> metadata schema. The current implementation contract is tracked by issues
> #27, #28, #34, and #37.

## Research question

How should a developer start a complete Hyperset environment from a clean
checkout when no Superset installation, cloud account, or model credential is
available?

## Conclusion

Run a real, pinned Apache Superset instance as an external source system and run
Hyperset as a separate application with its own Postgres database.

```text
Docker Compose
├── analytics-db       deterministic revenue warehouse fixture
├── superset-db        Superset-owned metadata database
├── superset           pinned Apache Superset 6.1.0
├── superset-init      supported migrations/admin/bootstrap
├── hyperset-postgres  Hyperset system of record
├── hyperset-api       HTTP context service
├── hyperset-mcp       MCP transport if separate from API
├── hyperset-worker    offline processor
└── review-ui          minimal human-review interface
```

Hyperset reads Superset through official export and REST interfaces. It does not
share Superset's metadata database, impersonate its APIs, render its frontend,
or reproduce its authorization model.

## Why a real source instance is required

Schema parity would not provide behavioral compatibility. Superset behavior also
depends on:

- application migrations and model invariants;
- REST list and detail schemas;
- import/export transformations;
- feature flags and configuration;
- authentication and authorization;
- source UUID generation and relationship repair;
- upstream initialization commands.

A hand-written YAML payload can test a parser, but it cannot prove that the
payload is produced by Superset or that export and live API modes agree.

## Pinned source contract

The repository should pin:

- Apache Superset version `6.1.0`;
- exact image tag and preferably digest;
- relevant feature flags and local configuration;
- metadata database image/version;
- initialization and admin-user commands;
- analytics database connection procedure;
- source asset bootstrap procedure;
- official export commands;
- expected connector capability metadata.

Startup must fail clearly when the running Superset version does not match the
version the connector contract suite claims to support.

## Bootstrap sequence

### 1. Start infrastructure

Start the Hyperset Postgres, analytics fixture database, and Superset metadata
database. Wait on health checks rather than fixed sleeps.

### 2. Initialize Superset through supported commands

Run Superset's own database upgrade and initialization commands, create the local
admin, and load only the configuration required by the demo. Hyperset migrations
must remain completely separate.

### 3. Seed deterministic analytics data

Create the revenue fixture tables and rows with source-controlled SQL. Record
expected row counts and checksums so a dirty or partial seed cannot look healthy.

### 4. Create real Superset assets

Use supported Superset APIs, CLI/bootstrap scripts, or imports to create the
physical and virtual datasets, calculated columns, metrics, charts, and
dashboards specified by issue #28.

The bootstrap must be idempotent: running it twice must not produce duplicate
source assets.

### 5. Generate official source evidence

Generate an unmodified official export ZIP from the real instance and record:

- Superset version;
- generation time;
- enabled relevant capabilities;
- selected asset IDs/UUIDs;
- archive hash;
- bootstrap revision.

The same source assets must also be available through REST list and detail
endpoints for the live connector path.

### 6. Initialize Hyperset

Run Hyperset's Alembic migrations, seed initial approved governed context and
review history, create the local Superset connection, and execute the first sync.

### 7. Process and review

Run the offline processor, inspect generated review tasks in the UI, approve or
reject context, and execute the deterministic evaluator.

## Command contract

The exact implementation may vary, but the repository should expose stable,
documented commands such as:

```bash
make up
make up-demo
make migrate
make demo-bootstrap-superset
make demo-generate-export
make seed
make sync
make process
make eval
make status
make down
make reset
```

Required semantics:

- `up` starts the minimum Hyperset services;
- `up-demo` starts the full real-source environment;
- migrations and bootstrap are idempotent;
- `down` preserves named volumes;
- `reset` is explicitly destructive;
- generated source evidence is reproducible;
- no external model key is required.

## Health and dependency ordering

Services should report readiness only after required dependencies are usable:

- Hyperset API after migrations succeed and Postgres is reachable;
- worker after repositories and queues are ready;
- review UI after API health succeeds;
- Superset after its database upgrade and application readiness succeed;
- connector/evaluator one-shot services after the exact source version is
  verified.

A failed migration must prevent the API from advertising readiness.

## State and reset behavior

Normal restarts must preserve:

- Superset assets and UUIDs;
- Hyperset connections and sync history;
- observed and governed versions;
- review decisions;
- evaluations and evidence.

A clean reset should recreate deterministic business fixtures and stable source
identity where the upstream source allows it. If a source generates a new UUID
on every rebuild, the bootstrap must persist or explicitly map that behavior
rather than relying on names.

## Secrets

Local defaults may be convenient, but active credentials must not appear in:

- committed files;
- generated Superset exports;
- observed raw payloads;
- logs, traces, evaluation artifacts, or API responses.

Use environment variables or a local secret/config boundary for Superset login,
database credentials, and connector encryption keys. Exported database URIs may
be masked; the connector must not assume they contain reusable secrets.

## Required bootstrap tests

1. Clean startup reaches healthy state.
2. Exact Superset version assertion passes.
3. Bootstrap can run twice without duplicates.
4. Official export is produced from the real source instance.
5. REST and export modes reference the same source assets.
6. Hyperset migrations run against real Postgres.
7. First and repeated syncs are deterministic.
8. Normal restart preserves state.
9. Reset reconstructs the expected demo.
10. No cloud or external model credential is required.
11. Secrets are absent from logs, payloads, and artifacts.

## Rejected historical approach

The earlier proposal attempted to create a new Hyperset database with a
Superset-compatible metadata schema and API surface. That is rejected because:

- Superset's schema is not its complete behavioral contract;
- copying tables would not reproduce APIs, exports, security, feature flags, or
  frontend assumptions;
- it would center the product on replacing Superset rather than connecting to
  existing systems;
- it would prevent the first connector from proving a reusable observation
  boundary.

Superset's ORM and migrations remain useful research inputs for understanding
source behavior. They are not Hyperset's persistence design.

## Primary sources

- Superset 6.1 API reference: https://superset.apache.org/developer-docs/6.1.0/api/
- Superset installation documentation: https://superset.apache.org/docs/installation/installing-superset-using-docker-compose/
- Superset repository/tag: https://github.com/apache/superset/tree/6.1.0
- Superset database export API: https://superset.apache.org/developer-docs/6.1.0/api/database/
- Superset dataset API: https://superset.apache.org/developer-docs/6.1.0/api/datasets/
- Superset chart API: https://superset.apache.org/developer-docs/6.1.0/api/charts/

## Remaining implementation decisions

- Whether the generated ZIP is committed as a small fixture or regenerated in
  slow CI.
- Which feature flags materially affect the connector payloads.
- Whether API and MCP share one process locally.
- Which deterministic IDs must be supplied by bootstrap versus mapped after
  source creation.

These decisions must be recorded by the implementation and proven by issue
#37's Compose tests; they do not change the architecture above.
