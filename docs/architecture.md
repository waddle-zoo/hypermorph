# V0 architecture

Status: **current**. Product contract lives in
[`v0-foundation.md`](v0-foundation.md).

## Boundary

The served demo runtime uses OpenAI/Luna for chat and authoring, with OpenAI
embeddings. Ollama/Qwen are retained only in the isolated evaluation benchmark;
they are not dependencies of the product runtime.

Hyperset v0 has three inputs, one scenario, one response, and three
release-gating evaluation families:

- customer-owned context: one configured Git repository/ref/path;
- observed sources: pinned Apache Superset 6.1.0 and DataHub OSS;
- scenario: recognized revenue by region;
- response: `ContextBundle`;
- agent operations: `list_context_catalog()`, `resolve_analytics_context(query,
  directive)`, and `validate_analytics_plan(...)` -- these three, with a fourth
  gated on evaluator evidence plus an ADR amendment (hy-9fq).

Git owns business meaning. Superset and DataHub provide observed evidence.
Hyperset owns the plumbing that snapshots, links, validates, evaluates, and
serves them together.

Each source transport is a separate contract. Passing one never claims support
for another version or transport.

## Superset transports

| Transport | Reads | Proven by |
|---|---|---|
| live REST (`base_url`) | databases and datasets, list for discovery and detail per asset | `tests/compose/test_superset_live_sync.py` against the pinned instance |
| export bundle (`bundle_path`) | databases, datasets, charts, dashboards from an official export ZIP | recorded official exports under `tests/fixtures/superset/6.1.0/revenue` and `.../usage` |

Neither shape is normalized into the other, and a snapshot never mixes them.
Every snapshot states its transport, the asset types it covered, and what the
transport did not disclose. A type a snapshot did not read is never inferred
deleted.

Live REST reads two of the four types because that is what this connector
implements, not because the source withholds the others: 6.1.0 serves chart and
dashboard identity and both of their references over REST, captured under
`tests/fixtures/superset/6.1.0/usage/rest/`. The REST snapshot's own warning
names what it did not read.

## Git context source

| Element | v0 rule |
|---|---|
| identity | one configured `repository` + `ref` + `path`, snapshotted at an exact commit SHA; `repository` may be a remote/local Git repository or a CI-produced Git bundle |
| layout | `manifest.yaml` (benchmark fields), `context.md` (guidance), `evals.yaml` (locked cases) |
| runtime packaging | a checkout is not required at runtime; a Git bundle is sufficient because it retains the exact commit and tree |
| owners | captured from the manifest and repository CODEOWNERS; never inferred |
| source refs | `<table|pipeline>:<platform>:<external_id>`; stable identities owned beside data code |
| BI overrides | optional reasoned `superset:dataset:<external_id>` refs, resolved against observations only |
| unchanged commit | no-op: no new snapshot, no rewrite |
| new commit | one new immutable snapshot; prior snapshots stay replayable |
| invalid content | recorded failure; the last valid snapshot keeps serving |

Proven by `tests/integration/test_git_context_source.py` (real repositories
built with the `git` CLI) and `tests/postgres/test_context_sync.py`.

## Runtime path

```text
configured Git revenue context
  -> immutable ContextSnapshot @ exact repo/ref/path/commit

Superset 6.1.0 + DataHub OSS
  -> connector snapshots
  -> ObservedAssets + immutable ObservedAssetVersions
  -> explicit cross-source/source-to-context evidence links
  -> ConnectorChange
  -> one deterministic contradiction/finding

ContextSnapshot + linked evidence + finding/eval state
  -> bounded catalog / derived discovery candidates
  -> real lightweight planner produces exact ContextDirective
  -> shared exact resolver
  -> identical ContextBundle through HTTP and MCP
  -> deterministic analytics-plan validation
  -> external read-only demo query tool
  -> Inspect AI benchmark: isolated small Ollama + Hyperset vs raw-metadata baselines
  -> dependency invalidation + generic webhook on critical failure
```

Postgres is the authority for Hyperset operational state: sync runs, immutable
snapshots, links, findings, evaluation attempts, notifications, and replay.
Postgres is **not** an independent authoring source for business meaning.

The configured Git commit is the semantic authority for v0 context. Hyperset may
normalize/cache it for retrieval, but every semantic field must remain traceable
to that exact Git snapshot.

## Active packages

| Package | Owns | Must not own |
|---|---|---|
| `hyperset.connectors` | read-only source transport, lossless snapshot, normalization | canonical business meaning, agent tools |
| `hyperset.context` | read-only Git access, context schema, evidence resolution, immutable snapshots | authoring, editing, approving, or writing back context |
| `hyperset.db` | persisted schema and migrations | application policy |
| `hyperset.repositories` | DTO/protocol boundary and Postgres persistence | source parsing, HTTP/MCP shapes |
| `hyperset.processor` | deterministic rules over persisted evidence, findings, and Git-review proposals | editing Git, approving context, executing SQL, model calls |
| `hyperset.bundle` | the public `ContextBundle` shape and its deterministic compilation from Git context plus evidence | persistence, authoring, approval, SQL execution, a second response shape |
| `hyperset.transport` | HTTP and MCP adapters over the three deterministic trust operations, and the one decoding of their parameters | a response shape, semantic authority, persistence, authoring, approval, SQL execution |
| `hyperset.planner` | replaceable runtime adapters and the supported question-to-directive path | business truth, source identity, governed fields, a proprietary agent framework |
| `hyperset.flywheel` | the assist-class flywheel steps -- an agent-drafted UNAPPROVED candidate definition, the read-only live-lookup that feeds it, and a proposal-only Git-PR write-back that opens a PR and stops | approval, merging, advancing governed context, a governed section, warehouse SQL, a served HTTP/MCP operation |
| `hyperset.evals` | locked cases, recordings, deterministic outcome scorers, and selection evidence | generated release truth, model-only approval |
| `hyperset.cli` | local DB, connection, sync, Git-context, serve, and walking-skeleton entry points | hidden product behavior |

Both transports call one `run_operation` and serialize what it returns, so
`ContextBundle` and `PlanValidation` parity between them is structural rather
than a promise. Evaluation and notification packages are added only by their
gated issues. The context-operations surface is still
`hyperset context status|show|history` -- read-only with respect to Git, and not
a place to author or approve context. Removed packages
(`semantic`, `compat`, `bridge`, `agent`, legacy `mcp`, `artifacts`, `trust`)
must not return as parallel stores or public contracts.

The exact operations are the governance kernel, not the whole user journey.
ADR 0022 requires V0 to prove a real lightweight model can select the relevant
domain and concepts from ordinary wording before it calls the exact resolver.
Semantic discovery may rank derived candidates, but it cannot write governed
sections or bypass `ContextDirective`.

Discovery is embedding-provider neutral. A deployment may use the pinned local
default, OpenAI, Cohere, an OpenAI-compatible endpoint, or a customer adapter
behind one `EmbeddingProvider` contract. Each derived index version records its
provider, exact model, dimensions, input-projection version, source-text hash,
and Git snapshot/commit. Incompatible embedding spaces are never queried as one
index. Hosted providers are opt-in and receive no context until an administrator
configures the provider and its secret reference. Provider choice changes
ranking behavior, not the exact resolver or governed bundle.

The playground also uses `GET /v0/context/history` as an HTTP-only operator
read. It accepts the repository, ref, and path from a catalog entry and
returns immutable snapshot metadata (commit, timestamps, content hash, and
evidence/finding counts). It is intentionally outside `OPERATIONS`, so it is
not an additional MCP tool or agent-facing meaning surface.

## Context authority invariant

For v0:

- one configured Git repository/ref/path owns revenue-domain meaning;
- a new commit creates a new immutable `ContextSnapshot`;
- an unchanged commit is a no-op;
- Hyperset-local edits cannot create a new authoritative context version;
- Superset/DataHub evidence can corroborate or contradict Git context but cannot
  silently replace it;
- the operations surface is read-only with respect to canonical context;
- a future AI curator may propose a Git patch/PR, but cannot approve or merge it.

Existing `ReviewDecision`/`GovernedContext` persistence may remain while the pivot
lands, but new v0 behavior cannot depend on Hyperset-local approval. Where that
persistence is still used, `ReviewRepository.approve` remains the only code path
that may mark a `GovernedContext` approved; no connector, processor, curator, or
UI surface writes an approved row directly.

## Tests

| Directory | Evidence |
|---|---|
| `tests/unit` | parsers and pure behavior |
| `tests/integration` | checked-in source/Git evidence contracts |
| `tests/postgres` | repository transactions and migrations on Postgres 16 |
| `tests/compose` | clean local stack lifecycle and pinned source integration; opt-in live Superset sync (`HYPERSET_COMPOSE_DEMO=1`) and live DataHub sync (`HYPERSET_COMPOSE_DATAHUB=1`) |

Those two opt-ins state that the stack is **already running**, so they also arm a
backstop: a session that sets one and completes no arm of the matching module
exits non-zero instead of reporting a green all-skip (`stalled_live_suites` in
`tests/compose/conftest.py`, hy-kaud). Unset, both modules skip green, which is
what a machine without the stack should get. Same shape as
`tests/evals/conftest.py`; there the arming variable is a separate
`HYPERSET_REQUIRE_LIVE` because those arms have no opt-in of their own.

Synthetic payloads test parser branches only. Compatibility and acceptance claims
require real pinned Superset/DataHub captures or an exact Git fixture/commit.

## Deliberate deferrals

No AI curator, context CMS/editor, independent Hyperset approval lifecycle,
Slack/email/PagerDuty integration, auth/RBAC, multitenancy, HA, warehouse
execution, scheduler platform, third connector, generic connector SDK,
generalized rule library, or broad model matrix belongs in v0. Add one only
after the walking skeleton is green and a recorded failure/customer need proves
it necessary.
