# Getting started: run the local Hyperset demo

This guide takes a **clean checkout to a working local demo** with no tribal
knowledge. It is deliberately honest about what the demo does and does not do:
every surface below states its URL and its authentication state, and the claims
here are cross-checked against the
[feature-parity audit](development/feature-parity-audit.md) (what Hyperset
*advertises* versus what a clean deployment can actually demonstrate). If this
guide and the audit ever disagree, the audit is the source of truth.

> [!WARNING]
> **The demo runs on LOOPBACK, and a network bind FAILS CLOSED.** `make up-demo`
> publishes the HTTP API (`:8000`) and the MCP HTTP surface (`:8010`) on
> `127.0.0.1` only, so they are reachable from **this host's browser but not the
> LAN**. The API is unauthenticated by design on that loopback-only topology
> (`localhost` cannot be reached off the host). What changed (hy-w5ld,
> [ADR-0035](adr/0035-layered-deployment-configuration.md) §5): a genuinely
> **non-loopback** bind now refuses to start unless authorization is configured
> (`HYPERSET_AUTHZ_ENABLED` + the `HYPERSET_OIDC_*` settings) -- an unauthenticated
> network service never ships, and there is no insecure override. A server told to
> bind `0.0.0.0` on a network-reachable publish exits with an `InsecureBindError`
> (see [Troubleshooting](#troubleshooting)). The verifier-less playground UI proxy
> is loopback-only. See [Authentication reality](#authentication-reality).

## Prerequisites

Install and start these on the **host** before you begin:

| Tool | Why | Check |
|---|---|---|
| **Docker Desktop** (Compose v2) | Runs the whole stack | `docker compose version` |
| **OpenAI service-account key** | The deployed model, authoring/refine, and semantic embedding paths use the server-side OpenAI contract. Put it in `.env` as `OPENAI_API_KEY` and `HYPERSET_EMBEDDING_API_KEY`; it is never sent to the browser. | `grep -q '^OPENAI_API_KEY=.' .env` |
| **`python3` on the host** | `make up-demo` runs `playground/bootstrap_contexts.py` **on the host** (via `make playground-contexts`) to snapshot the two example context domains. Any Python 3 is fine. | `python3 --version` |
| **[`uv`](https://docs.astral.sh/uv/)** | Only for host-side development/tests (see [Develop against the code](#develop-against-the-code)), not for the Docker demo | `uv --version` |
| **Node.js / `npm`** | Only for `make playground-ui` when serving **directly on the host** -- the Docker image builds the UI bundle itself, so the demo does not need it | `npm --version` |

The containers carry their own Python interpreter; the host `python3` above is
needed only for the one context-bootstrap step, not to run Hyperset itself.

## The startup path that actually works

From a clean checkout:

```bash
cp .env.example .env      # required: Compose reads secrets/ports from it
# Edit .env: set OPENAI_API_KEY and HYPERSET_EMBEDDING_API_KEY.
make up-demo
```

`make up-demo` (`Makefile:20`):

1. builds and starts Postgres + runs DB migrations,
2. brings up pinned **Superset 6.1.0** and the analytics fixture database, and
   runs the demo **dataset** bootstrap (`superset-demo-bootstrap`; the separate
   usage bootstrap is not part of `up-demo`),
3. runs `python3 playground/bootstrap_contexts.py` **on the host** to snapshot
   two example Git context domains into Hyperset,
4. starts the `api` and `mcp-http` services with the playground enabled, then
   prints every URL.

When it finishes, open:

| Surface | URL | Auth state |
|---|---|---|
| Chat playground | http://localhost:8000/playground/ | **Unauthenticated** (demo-only, behind `HYPERSET_PLAYGROUND_ENABLED`) |
| Review | http://localhost:8000/review/ | **Unauthenticated** (demo-only) |
| Admin / Settings | http://localhost:8000/admin/ | **Unauthenticated on the loopback demo (gate off).** No longer "only a routing split": the admin write paths (write-back target, context-source add/sync) now enforce an admin `configure` grant **server-side** when `HYPERSET_AUTHZ_ENABLED` is on (hy-2nqb, #416), and the admin read surfaces (readiness, context sources, append-only audit trail #421) are authz-gated too. See [Authentication reality](#authentication-reality). |
| HTTP API | http://localhost:8000/v0/resolve_analytics_context | **Unauthenticated** by default (`POST`) |
| MCP (HTTP) | http://localhost:8010/mcp | **Unauthenticated**; Streamable HTTP -- connect a fresh MCP client here |
| Superset | http://localhost:8088/ | Superset's own login: `admin` / `admin-local-dev` (from `.env.example`) |

The bearer **OIDC verifier** authenticates API/MCP callers when the gate is on
([ADR-0030](adr/0030-the-authorization-boundary.md)). A browser **OIDC
Authorization-Code + PKCE login** (`/login` → IdP → `/callback`, a signed HttpOnly
session cookie, `/logout`) has **landed** (#423) — **present-but-not-default**: it is
inert off the authz gate and the production enable-flip stays human-gated
(hy-nt89/hy-ia9n). There is still **no user-management / invite surface**, and per-user
access policy remains a **proposed** (not implemented), Git-owned model (BYOKG,
ADR-0036). The regular-user **Explorer shell** (Home/Explore IA, #415) has **landed** too.

### Verify it is up

```bash
make status                 # docker compose ps + what is intentionally not runnable yet
curl -sS -X POST http://localhost:8000/v0/resolve_analytics_context \
  -H 'content-type: application/json' \
  -d '{"query":"recognized revenue by region","directive":{"domains":["revenue"],"concepts":["recognized_revenue"]}}'
```

Stop with `make down` (keeps data). `make reset` destroys every named volume and
asks for typed confirmation.

## How the pieces fit

```text
 browser --> playground / review / admin  (React SPA, served by the api image)
                    |  same-origin /playground/api, /admin/api
                    v
        Hyperset api (:8000)  <-- MCP client --> mcp-http (:8010/mcp)
                    |  catalog . resolve . validate . discover . expand . review
                    v
        deterministic resolver + validator (no warehouse SQL)
                    |
        +-----------+------------+
        v                        v
   Git-owned context      Superset / DataHub evidence
   (authoritative)        (observed; corroborates, never governs)
```

The same three deterministic operations (`list_context_catalog`,
`resolve_analytics_context`, `validate_analytics_plan`) return identical bytes
over REST and MCP. The model chooses *where to look*; Hyperset code decides what
is governed and validates the plan.

## Compose profiles: what actually comes up

Services are gated by Compose `--profile`. `make up-demo` composes the base file
with `docker-compose.demo.yml` and the `demo` profile.

| Command | Services it starts | Surfaces |
|---|---|---|
| `make up` | `postgres` + `hyperset-migrate` **only** — it does **not** start `api` | **No HTTP surface** (DB + migrations only; a base for the other targets) |
| `make serve` | base **+** `api` | Headless API on `:8000` with the **playground OFF** |
| `make up-demo` | base **+** `superset`, the **dataset** bootstrap, host context snapshot, `api`, `mcp-http` (playground ON) | Playground/Review/Admin on `:8000`, MCP on `:8010/mcp`, Superset on `:8088` |
| `docker compose run --rm -T mcp` | one-shot `mcp` stdio process (see [MCP](#connecting-an-mcp-client)) | MCP over **stdio** (spawned per client, no port) |
| `make up-datahub` | base **+** `datahub-gms` + `datahub-seed` (the full DataHub dependency stack — MySQL, Elasticsearch, ZooKeeper, broker, schema-registry — comes up as its dependencies) | DataHub GMS on `:8090` |

`api` is not in any Compose profile: `make up` never starts it, and there is no
`make` target that starts the stdio `mcp` service as a long-running listener —
you run it on demand (below).

Published host ports (override via the `.env` variables shown):

| Service | Host port | `.env` var |
|---|---|---|
| Hyperset API + playground | `8000` | `HYPERSET_API_PORT` |
| MCP HTTP | `8010` | `HYPERSET_MCP_HTTP_PORT` |
| Superset | `8088` | `SUPERSET_PORT_HOST` |
| DataHub GMS | `8090` | `DATAHUB_GMS_PORT_HOST` |
| Hyperset Postgres | `55432` | `HYPERSET_DB_PORT` |
| Analytics fixture DB | `55434` | `ANALYTICS_DB_PORT_HOST` |

## Connecting an MCP client

Hyperset serves MCP two ways — pick by how your client connects:

- **Streamable HTTP (hosted):** `make up-demo` publishes it at
  http://localhost:8010/mcp. Point a fresh MCP client (MCP Inspector, a Claude
  Streamable HTTP connection) here. This is the usual demo path.
- **stdio (per-client, on demand):** run
  ```bash
  docker compose run --rm -T mcp
  ```
  This spawns a one-shot `mcp` process wired to your terminal's stdio. Use `-T`
  (no TTY) so the client owns stdin. Do **not** use `docker compose --profile mcp
  up`: that starts the stdio service detached, where it reads a closed stdin and
  is not usable as a client transport. `docker compose run --rm -T mcp` is the
  only **self-contained** clean-checkout stdio path — it inherits the demo
  Postgres DSN from Compose. (A bare host `uv run hyperset serve mcp` is not
  self-contained: it requires `HYPERSET_DATABASE_URL` exported to a reachable
  Postgres, or it fails immediately — see [Develop against the code](#develop-against-the-code).)

## What the demo does *not* do out of the box

A clean `make up-demo` demonstrates the WIRED trust core (catalog / resolve /
validate / expand) and the playground. Several advertised capabilities are
**configure-** or **task-before-use** and stay inert until you take an explicit
step -- this is the honest reality from the [parity audit](development/feature-parity-audit.md):

- **Superset connector sync** -- the demo bootstraps Superset *datasets* but
  creates no connection and syncs no observations. That is a separate manual
  step: `make connection-live` then `make sync-live CONNECTION_ID=...`.
- **DataHub** -- only under the separate `datahub` profile (`make up-datahub`).
- **Review tasks** -- the `list_review_tasks` route responds (empty), but a clean
  demo runs no connector sync and no processor, so there is **no task** for
  `get_review_task` / `edit_review_draft` to act on.
- **Propose to Git** -- off until a write-back repo and token are configured; it
  is additionally PII-guarded.
- **Processor findings / evaluation harness** -- real code, but no `make up-demo`
  service invokes them; they are dev/CI capabilities, not demo surfaces.

## Authentication reality

The authorization gate and OIDC verifier are real code but **default-OFF** on a
LOOPBACK bind ([audit sec 2](development/feature-parity-audit.md),
[ADR-0030](adr/0030-the-authorization-boundary.md)): a purely local, loopback-bound
run answers unauthenticated, because `localhost` cannot be reached off the host.

A **non-loopback bind is a different matter and now FAILS CLOSED**
([ADR-0035](adr/0035-layered-deployment-configuration.md) section 5, hy-w5ld): a
server told to bind `0.0.0.0` refuses to start unless `HYPERSET_AUTHZ_ENABLED=true`
plus a full `HYPERSET_OIDC_ISSUER` / `HYPERSET_OIDC_AUDIENCE` /
`HYPERSET_OIDC_JWKS_URL` are set. There is **no insecure override** -- an
unauthenticated network service never ships. The verifier-less playground UI proxy
(`HYPERSET_UI_HOST`) is loopback-only: a non-loopback UI host is always fatal.

**How the demo runs anyway (the loopback-published topology).** A Docker container
*must* bind `0.0.0.0` to answer a published port at all, so `make up-demo` cannot
bind `127.0.0.1` inside the container. Instead it publishes the port on the **host's
loopback only** (`127.0.0.1:8000:8080` in `docker-compose.yml`) and sets
`HYPERSET_LOOPBACK_PUBLISHED=1`, which asserts that topology and lets the guard permit
the in-container `0.0.0.0` bind. The effective exposure is loopback -- nothing is
LAN-reachable -- so it satisfies the same rule (loopback/local = auth-off OK). This is
a narrow topology assertion, **not** a blanket bypass: a test forces any service that
sets it to publish on `127.0.0.1` only, so it can never ship alongside a LAN publish.
For a real network deployment, delete the signal, publish on the network, and
configure auth. Admin write-path role authentication has **landed** (hy-2nqb, #416): the
write-back-target and context-source writes require an admin `configure` grant
server-side when the gate is on, and every admin config action is recorded to an
append-only **audit trail** (#421). Beyond the baseline `reader`, the role vocabulary is
`reader` / `explorer` / `reviewer` / `admin` / `git_owner` / `service` (hy-dq0r, #411),
with a machine-only `service` role gated to genuine client-credentials tokens (#417) and
an in-process `system` identity that no token can mint. For the full production posture —
the layered config model, the OIDC verifier and PKCE login, RBAC, and the readiness/audit
surfaces — see [Production deployment](production-deployment.md). Do not treat a loopback
dev run as a secure deployment.

## Develop against the code

Host-side, without the full stack:

```bash
uv sync --all-extras --all-groups
uv run ruff check hyperset tests
uv run python scripts/gate.py            # the named gate (unit + integration)
uv run pytest tests/postgres -q          # service-backed; needs Docker
```

If you run the server **directly on the host** (`uv run hyperset serve http`, or
the served-playground unit test) you must first build the UI bundle:

```bash
make playground-ui       # needs npm/Node; builds playground/ui/dist/
```

Without it the host server falls back to the source `index.html` (which
references `/src/main.jsx`) and the page is broken (CLAUDE.md, hy-r8jd). **The
Docker demo does not need this** -- `docker/hyperset.Dockerfile` builds the bundle
into the `api` image.

Any host-run Hyperset command (`serve http`, `serve mcp`, the CLI) also needs
`HYPERSET_DATABASE_URL` — there is **no local default**, so it fails immediately
if unset (`db/engine.py:26`). `.env.example` sets it only inside Compose, not on
the host. Point it at the demo Postgres published on `:55432` (match your `.env`
credentials):

```bash
export HYPERSET_DATABASE_URL=postgresql+psycopg://hyperset:hyperset-local-dev@localhost:55432/hyperset
```

This is why `docker compose run --rm -T mcp` is the self-contained clean-checkout
path for MCP stdio: the container inherits the DSN from Compose.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| A server exits at startup with `InsecureBindError: ... refuses to bind the non-loopback host '0.0.0.0' without authentication` | Fail-closed guard (ADR-0035 §5, hy-w5ld): a non-loopback bind requires auth | Pick one: (a) bind a loopback host (`--host 127.0.0.1`) for a purely local run; (b) if the port is published on the host's loopback only (like the demo -- `127.0.0.1:PORT:...`), set `HYPERSET_LOOPBACK_PUBLISHED=1` to assert that topology; (c) for a real network deployment, configure `HYPERSET_AUTHZ_ENABLED=true` **and** `HYPERSET_OIDC_ISSUER` / `HYPERSET_OIDC_AUDIENCE` / `HYPERSET_OIDC_JWKS_URL`. The playground UI proxy (`HYPERSET_UI_HOST`) is loopback-only and rejects any non-loopback host outright -- the signal does not apply to it. |
| Host `hyperset serve http` shows a broken playground / a request for `/src/main.jsx` | No `playground/ui/dist/` bundle | Run `make playground-ui` first. Not applicable to the Docker demo (the image builds it). |
| `discover` returns HTTP 500 | OpenAI embedding credential/model mismatch | Set `HYPERSET_EMBEDDING_API_KEY`, `HYPERSET_EMBEDDING_MODEL=text-embedding-3-small`, and `HYPERSET_EMBEDDING_DIMENSIONS=768` in `.env`. |
| Playground/refine reports the hosted model unavailable | OpenAI model credential, endpoint, or model mismatch | Set `OPENAI_API_KEY`, `HYPERSET_OPENAI_BASE_URL`, and `HYPERSET_OPENAI_MODEL` in `.env`; the default model is `gpt-5.6-luna`. |
| A published port fails to bind (e.g. `8000`, `8088`, `55432`) | Port already in use | Change the matching `.env` var (table above) and re-run, or free the port. |
| API returns 5xx right after startup / migrations look unapplied | Postgres not ready yet | Wait for the `postgres` healthcheck; `make up-demo` uses `--wait`, but on a slow first pull re-run `make up`. |
| MCP stdio client hangs or sees closed stdin | `docker compose --profile mcp up` starts the stdio service detached, reading a closed stdin | Run it on demand instead: `docker compose run --rm -T mcp` (the self-contained path). Use `:8010/mcp` for HTTP transport. See [Connecting an MCP client](#connecting-an-mcp-client). |
| Host `uv run hyperset serve ...` exits: `$HYPERSET_DATABASE_URL is not set` | The host command has no DSN; `.env.example` does not set one (the containers get it from Compose) | Export a DSN to the demo Postgres first, e.g. `export HYPERSET_DATABASE_URL=postgresql+psycopg://hyperset:hyperset-local-dev@localhost:55432/hyperset` (match your `.env`). See [Develop against the code](#develop-against-the-code). |
| GitHub Actions / CI is red, or a dependabot vulnerability banner appears | CI and dependency scanning are project-health signals, **not** a product runtime failure | The local demo is unaffected; treat these as repo maintenance, not a broken build. |
| Reset leaves stale state | Volumes preserved by `make down` | Use `make reset` (destructive; typed confirmation) to drop all named volumes. |

## See also

- [Feature-parity audit](development/feature-parity-audit.md) -- advertised vs actually running.
- [V0 foundation contract](v0-foundation.md) -- the agent-facing surface and its guarantees.
- [Architecture](architecture.md) -- how governance, resolution, and validation fit.
- [ADR-0030](adr/0030-the-authorization-boundary.md) -- why authorization is default-off.
