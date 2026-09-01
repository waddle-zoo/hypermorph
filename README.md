<div align="center">

# Hyperset Hive-Mind

<img src="docs/assets/hyperset-logo.svg" alt="Hyperset — governed context for AI analytics" width="720">

<p><strong>A flexible-yet-governed analytics knowledge graph that improves through use.</strong><br>
Connect Superset and DataHub evidence to business meaning owned in Git;<br>
agents can explore and propose, while humans retain approval in Git.</p>

<p>
  <a href="https://github.com/waddle-zoo/hyperset/actions/workflows/ci.yml"><img src="https://github.com/waddle-zoo/hyperset/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/python-%E2%89%A53.11-3776AB" alt="Python 3.11 or newer"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-2F6F9F.svg" alt="Apache License 2.0"></a>
  <a href="docs/research/superset-version-compat.md"><img src="https://img.shields.io/badge/Superset-6.1.0-20A6C9" alt="Superset 6.1.0"></a>
  <a href="docs/research/datahub-graphql-v1.md"><img src="https://img.shields.io/badge/DataHub%20OSS-v1.6.0-4C5FD5" alt="DataHub OSS v1.6.0"></a>
</p>

<p>
  <a href="#-start-in-three-steps">Start here</a> ·
  <a href="docs/getting-started.md">Getting started</a> ·
  <a href="docs/v0-foundation.md">V0 contract</a> ·
  <a href="docs/architecture.md">Architecture</a> ·
  <a href="docs/vision-roadmap.md">Roadmap</a> ·
  <a href="AGENTS.md">Agent guide</a> ·
  <a href="https://github.com/waddle-zoo/hyperset/issues">Issues</a>
</p>

</div>

> [!WARNING]
> Hyperset is pre-1.0 and changing quickly. The local Docker playground is the
> supported product path today; APIs and configuration may change before the
> first release.

<table>
  <tr>
    <td align="center" width="25%"><br>🧠<br><b>AI-first</b><br><sub>Ask normal questions.<br>Let the agent navigate context.</sub><br><br></td>
    <td align="center" width="25%"><br>🛡️<br><b>Governed</b><br><sub>Git owns meaning.<br>Humans own approval.</sub><br><br></td>
    <td align="center" width="25%"><br>🔎<br><b>Traceable</b><br><sub>Every claim carries<br>evidence and provenance.</sub><br><br></td>
    <td align="center" width="25%"><br>⚡<br><b>Open</b><br><sub>HTTP, MCP, Python,<br>Apache-2.0.</sub><br><br></td>
  </tr>
</table>

## ✨ Start in three steps

You need Docker Desktop, [`uv`](https://docs.astral.sh/uv/), and an OpenAI
service-account key.

```bash
cp .env.example .env
# Set OPENAI_API_KEY and HYPERSET_EMBEDDING_API_KEY in .env.
make up-demo
```

Then open the four product surfaces:

<table>
  <tr>
    <td align="center"><b>💬 Live chat</b><br><a href="http://localhost:8000/playground/">localhost:8000/playground/</a><br><sub>Ask questions using governed context</sub></td>
    <td align="center"><b>🧠 Explore the Hive-Mind</b><br><a href="http://localhost:8000/playground/explore/">localhost:8000/playground/explore/</a><br><sub>Search and inspect connected knowledge</sub></td>
    <td align="center"><b>📝 Review</b><br><a href="http://localhost:8000/review/">localhost:8000/review/</a><br><sub>Evaluate proposal-only context repairs</sub></td>
    <td align="center"><b>⚙️ Settings</b><br><a href="http://localhost:8000/admin/">localhost:8000/admin/</a><br><sub>Inspect sources, runtime, and readiness</sub></td>
  </tr>
</table>

`make up-demo` starts Postgres, the API, pinned Superset 6.1.0, the analytics
fixture database, two example Git context domains, and hosted MCP. It pulls
no local model runtime; chat, authoring, and embeddings use the server-side
OpenAI/Luna settings in `.env`. The hosted MCP endpoint remains available at
<http://localhost:8010/mcp>. Stop with `make down`;
`make reset` removes named volumes and asks for confirmation.

The default demo is **unauthenticated but host-loopback-only** (`:8000`/`:8010`
are published on `127.0.0.1`; the containers listen on `0.0.0.0`). It is a
local demonstration, not a secure deployment. The
full [getting-started guide](docs/getting-started.md) covers every surface's URL
and auth state, the Compose-profile → surfaces table, and troubleshooting.

## Why agents use Hyperset

Raw metadata can tell an agent that a dataset exists. Hyperset tells it what a
company approved that dataset to mean, which joins and filters are required,
what changed, and which parts of the answer remain observed or uncertain.

```text
ordinary question
      │
      ▼
agent selects bounded candidates
      │  exact ContextDirective
      ▼
Hyperset resolves Git meaning + source evidence
      │  one ContextBundle
      ├───────────────┬───────────────┐
      ▼               ▼               ▼
     HTTP            MCP          eval harness
```

The model may choose where to look. Deterministic Hyperset code decides what is
governed, preserves exact commit/version provenance, and validates the proposed
analytical plan. The governance kernel does not execute warehouse SQL.

## The small trust surface

Three operations form the deterministic v0 trust core. Discovery and
proposal-only review operations are served separately; the full agent-facing
surface is documented in the [v0 foundation](docs/v0-foundation.md) and
[ADR 0025](docs/adr/0025-review-ops-expand-the-mcp-trust-surface.md):

| Operation | What it does |
| --- | --- |
| `list_context_catalog` | Discovers bounded domain, concept, document, source, and graph identifiers. |
| `resolve_analytics_context` | Resolves an exact directive into a versioned `ContextBundle`. |
| `validate_analytics_plan` | Checks fields, joins, filters, grain, and validations against that bundle. |

These deterministic operations return the same bytes over REST and MCP.
Proposal/model operations preserve contract parity but can produce fresh
drafts. Observed Superset/DataHub assets can corroborate or contradict Git
context; they never silently become governed meaning.

<details>
<summary><b>📦 Configure a Git context source</b></summary>

Git owns definitions, approved sources, joins, filters, caveats, and ownership.
Hyperset records the exact commit and keeps prior snapshots replayable. A local
checkout is optional: CI can ship the reviewed tree as a Git bundle.

```bash
make context-add \
  REPOSITORY=/path/to/analytics-context \
  CONTEXT_PATH=playground/examples/revenue

make context-sync SOURCE_ID=<printed-id>
make context-status
```

Context layout:

```text
manifest.yaml   # domains, definitions, sources, joins, filters, ownership
context.md      # human guidance and caveats
evals.yaml      # locked evaluation cases
```

Read [ADR 0012](docs/adr/0012-git-owned-context-authority.md) for the authority
boundary and [ADR 0023](docs/adr/0023-table-and-pipeline-context-identity.md)
for source identity.

</details>

<details>
<summary><b>🔌 Call the API or MCP</b></summary>

Exact REST resolution:

```bash
curl -sS -X POST http://localhost:8000/v0/resolve_analytics_context \
  -H 'content-type: application/json' \
  -d '{
    "query": "Which source and rules should an analyst use for recognized revenue by region?",
    "directive": {
      "domains": ["revenue"],
      "concepts": ["recognized_revenue"]
    }
  }'
```

MCP stdio:

```bash
docker compose run --rm -T mcp
# or: uv run hyperset serve mcp
```

Hosted MCP is available at `http://localhost:8010/mcp` after `make up-demo`.

</details>

<details>
<summary><b>🧪 Develop and verify</b></summary>

```bash
uv sync --all-extras --all-groups
uv run ruff check hyperset tests
uv run ruff format --check hyperset tests
uv run python scripts/check_docs.py
uv run python scripts/gate.py
```

Optional service-backed checks require Docker:

```bash
uv run pytest tests/postgres -q
uv run pytest tests/compose -q
uv run hyperset evals score
```

</details>

<details>
<summary><b>🗺️ Read the product direction</b></summary>

- **V0 — Prove:** governed Git context improves an answer over raw metadata,
  end to end. The reproducibility benchmark uses an isolated pinned local-model
  arm; the shipped demo runtime uses OpenAI/Luna.
- **V1 — Reach:** bounded assist, estate-scale retrieval, and navigable
  multi-domain context expansion with explicit provenance.
- **V2 — Agent Home:** customer-written agents inherit Hyperset's trust
  properties without Hyperset becoming an agent framework.

The [vision and roadmap](docs/vision-roadmap.md) is directional. The
[v0-foundation contract](docs/v0-foundation.md) and accepted ADRs are binding.

</details>

## 🧭 Project map

| Directory | Role |
| --- | --- |
| `hyperset/context` | Git context parsing, validation, and snapshots |
| `hyperset/connectors` | Read-only Superset and DataHub observation transports |
| `hyperset/bundle` | `ContextBundle` and plan-validation contracts |
| `hyperset/planner` | Replaceable question-to-directive runtime adapters |
| `hyperset/transport` | HTTP and MCP adapters |
| `hyperset/evals` | Locked cases, recordings, and deterministic scorers |
| `playground/examples` | Demo context domains |

## UX research and proposals

The current setup, role journeys, interaction gaps, and proposed mockups are
documented in [`docs/ux/`](docs/ux/README.md). Start with the
[setup and interaction audit](docs/ux/current-setup-and-interaction-audit.md),
then review the [persona blueprints](docs/ux/personas-and-service-blueprints.md),
[flow proposals](docs/ux/flows-and-service-blueprints.md), and
[recommendations roadmap](docs/ux/recommendations-roadmap.md). The earlier
[composite UX prototype](docs/ux/mockups/hyperset-ux-prototype.html) remains
available for overview; the current [v1 page mockups](docs/ux/mockups/v1/README.md)
organize the product around Explorer, Context reviewer, and protected Admin.

## License

Hyperset is licensed under the [Apache License, Version 2.0](LICENSE).
