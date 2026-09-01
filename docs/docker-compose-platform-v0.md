# Local Docker Compose Platform v0 (hy-gh-37)

Status: implemented and manually verified end to end. Tracks hy-gh-37
(Build the Pinned Local Docker Compose Platform).

## What exists

`docker compose up` (core): `postgres` (Hyperset's system of record) +
`hyperset-migrate` (one-shot `hyperset db upgrade`).

`docker compose --profile demo up -d superset` (full): adds `superset-db`,
`analytics-db` (deterministic revenue fixture data, `docker/analytics-db
/init.sql`), `superset-init` (version assertion + Superset's own official
`docker-init.sh`), `superset` (real, pinned `apache/superset:6.1.0`), and
`superset-demo-bootstrap` (idempotent: creates the stable-UUID `analytics`
database connection plus the three datasets required by the canonical revenue
scenario via Superset's own REST API). `make up-demo` runs
the whole chain.

`make demo-generate-export` calls Superset's own export REST API and
writes a real, unmodified `export.zip` + `metadata.json` — never
hand-authored.

`make demo-generate-evidence` writes the Gate A contract under
`tests/fixtures/superset/6.1.0/revenue/`: unmodified official export and REST
list/detail captures for the same objects, stable native IDs/UUIDs and content
hashes, a credential scan, and one metric-expression drift followed by a
verified restore. `manifest.json` is the shared downstream scenario and
identity contract.

`make demo-bootstrap-usage` seeds charts and one dashboard onto those same
datasets, and `make demo-generate-usage-evidence` captures them under
`tests/fixtures/superset/6.1.0/usage/`: the unmodified dashboard export the
instance produced, plus the chart and dashboard REST bodies. Both are
idempotent and additive — no dataset is created, so the revenue captures'
pinned hashes are untouched.

## NOT included

When this Superset-foundation record was written, ADR 0010/0011 also required a
curator worker and dual UI/Git governance. ADR 0012 later removed both from V0:
customer Git now owns meaning and the UI is operational only. Pinned DataHub,
the Git context source, the Ollama benchmark, and the webhook/replay proof are
tracked by their current issues and the binding V0 foundation.

`worker` (#38) and `review-ui` (#39) have no service here because the Python
code they'd run doesn't exist yet. Adding a compose service with no real
entrypoint would be a fake placeholder, not a working service. `make status`
reports this explicitly. `make process` now runs the offline processor over the
most-recent completed sync run (hy-jp0gq), or prints a clear message and no-ops
when none exists; `make eval` still prints a clear "blocked on #25" message and
exits non-zero rather than silently no-op.

`api` and `mcp` were in that list when this record was written. Both now exist
and are added to the same compose file (hy-oih): `api` joins the core stack and
serves `list_context_catalog`/`resolve_analytics_context`/
`validate_analytics_plan` over HTTP; `mcp` serves the same three operations over
MCP stdio and is profile-gated, because
stdio is a process a client spawns (`docker compose run --rm -T mcp`) rather
than a port to wait on.

## Real problems found and fixed during verification

Every one of these was caught by actually running the stack, not by
writing the compose file and assuming it would work:

1. **Missing `README.md` in the Docker build context** — `pyproject.toml`
   requires it for the package build; the Dockerfile didn't `COPY` it.
2. **Scripts not executable** — bind-mounted `.sh`/`.py` files need the
   execute bit on the host; `chmod +x` was missing.
3. **No Postgres driver in the base Superset image** —
   `apache/superset:6.1.0` doesn't bundle `psycopg2`. Its own
   `docker-bootstrap.sh` installs one at container start when
   `DATABASE_DIALECT=postgres`, but only for services using the image's
   default entrypoint dispatch, which this platform's custom
   `superset-init`/`superset` commands don't use. Fixed with a small
   derived image (`docker/superset/Superset.Dockerfile`, `FROM` the exact
   pinned digest) that installs `psycopg2-binary` once at build time —
   simpler and faster than reinstalling on every container start.
4. **`pip install` landed in the wrong Python** — the image runs Superset
   from `/app/.venv`, not the system Python; had to target
   `uv pip install --python /app/.venv/bin/python`.
5. **Three services silently shared no build cache** — without an
   explicit `image:` tag on the shared build config, `superset-init`/
   `superset`/`demo-export` each got their own separately-cached image,
   so rebuilding one left the others stale. Fixed by giving the shared
   `x-superset-image` anchor one explicit tag.
6. **Circular dependency**: `demo_bootstrap.py` needs Superset's REST API,
   which only exists once the `superset` webserver is running — but
   `superset` itself depends on `superset-init` completing first
   (migrations must exist before the webserver starts). Split into two
   services: `superset-init` (migrations + admin + roles, no HTTP
   dependency) and `superset-demo-bootstrap` (runs after `superset` is
   healthy).
7. **`datetime.UTC` doesn't exist in Python 3.10** — the Superset image
   runs 3.10; `demo_generate_export.py` used the 3.11+ spelling.
8. **The real export ZIP structure was different from what was assumed**:
   a real Superset 6.1.0 export wraps every file in a
   `<type>_export_<timestamp>/` directory. `load_export_bundle` (reused,
   unchanged) already handled this correctly (it matches any path
   component, not just the first), but this platform's own
   "unsupported asset type" warning detector in
   `hyperset.connectors.superset.connector._unsupported_asset_dirs` only
   checked `parts[0]` — which is that wrapper directory, never the real
   asset-type directory — producing a false-positive warning on every
   real export. Fixed to mirror `load_export_bundle`'s matching exactly.
   Caught only because the real generated export was fed back through
   the actual `SupersetConnector` as part of verification, not assumed
   to work from the hand-written fixture tests alone.

Finding #8 is exactly what `docs/research/FACT_CHECK_2026-07-25.md` Blocker C
warned about: hand-written fixtures don't prove real-source compatibility.
This platform's value is making that real-source test actually possible.

## Verified manually, end to end

- `make up-demo`: full stack boots, version assertion passes
  (`Superset version assertion passed: 6.1.0`), demo dataset bootstrap
  creates 1 database + 3 scenario datasets.
- Idempotency: re-running `make demo-bootstrap-superset` resolves the same
  UUIDs/native IDs and normalizes the three existing datasets — no duplicates.
- `make demo-generate-export`: produces a real 9.6KB export ZIP with
  correctly `database_uuid`-linked datasets and a masked
  (`XXXXXXXXXX`) database password — never a recoverable credential.
- `make demo-generate-evidence`: captures baseline/drift/restored snapshots,
  changes only `recognized_revenue.expression`, restores its baseline hash,
  and passes the plaintext-credential scan.
- That real export, fed through `hyperset.connectors.superset
  .SupersetConnector`, correctly parses and normalizes all 4 assets with
  zero warnings (after fix #8 above).
- `make down`: containers removed, named volumes (`hyperset-postgres-data`,
  `superset-db-data`, `analytics-db-data`, `superset-home`) survive.
- Restart from those volumes: the same database/dataset ids are still
  present — `superset-demo-bootstrap` correctly reports them as already
  existing rather than recreating.
- `make reset` (typed `yes` confirmation): all four named volumes actually
  removed.

## Automated coverage

`tests/compose/` (marker `compose`, auto-skipped without Docker):
compose config validates for both the core and `demo` profiles; the core
stack (`postgres` + `hyperset-migrate`) actually starts and migrates;
`down` preserves volumes. The full Superset demo profile (~2-3 minutes:
real migrations, admin creation, webserver boot) is not run on every CI
push — verified manually as above instead. `#36` (CI/release gates) owns
wiring a scheduled or pre-release job for the full real-Superset contract
suite this platform now makes possible.

## Non-goals (unchanged from the bead)

Kubernetes/cloud deployment, DynamoDB, production secret management/SSO,
multiple Superset versions in v0, synthetic fixtures as a substitute for
this real-source generation path.
