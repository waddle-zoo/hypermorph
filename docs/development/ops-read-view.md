# Read-only operator view -- design (hy-gh-72, GitHub #72)

Status: DESIGN, 2026-08-17 (hy-gh-72). No code is built here. This decomposes the
view into impl slices (section 6) and files ONE served-surface option as a fork
(section 5). The buildable-now path (a CLI read view, section 2) needs no fork:
it adds no served endpoint, no response shape, no `SCHEMA_VERSION` move, and no
`tools_hash` change.

## Outcome

An operator sees, without a psql prompt, whether Hyperset is telling the truth
right now: which Git commit the governed context is pinned to and whether it is
current, when each source last synced and whether it succeeded, what evidence is
linked, which findings are open, and what the last evaluation run said. It
SURFACES truth; it never asserts it. Read-only: no write path, no triage, no
context edit, no approval. Governed meaning stays Git-owned; this view must not
become a second place it can be edited (ADR 0012).

## 1. What it surfaces, and where each field is READ from

Every datum is already readable through an existing repository reader or
application service -- NO new governed input, NO new manifest field, NO new
observed state -- and never raw SQL.

Two levels of "already exists", and the distinction matters for accuracy. FOUR
surfaces already have a SERVED counterpart that reads them, so the view reuses the
SAME readers those served handlers use (single source of truth): sync health
(`_get_playground_status`), git context (`get_context_history`), linked evidence
(`_linked_evidence`, served in every `RESOLVE` bundle), and findings (the
resolver's `list_findings(state="current", ...)`). The FIFTH, persisted eval
state, is read-AVAILABLE but NOT served today: NO HTTP handler or `run_operation`
path reads `PostgresEvaluationRepository` (`list_cases`/`list_runs`), so S5 is a
NEW thin additive read-only reader over the evaluation repository, not the reuse
of an existing served assembly (see the eval-state row and section 6, S5).

### Sync health, per connection
| field | read from |
| --- | --- |
| connection id, type, name, enabled, health status/at/detail | `PostgresConnectionRepository.list(enabled_only=False)` -> `ConnectionRecord` |
| all sync runs (status, started/finished, counters, warnings, errors, mode) | `PostgresSyncRepository.list_runs(connection_id)` -> `list[SyncRunRecord]` |
| latest terminal outcome (`succeeded`/`failed`/never) | `PostgresSyncRepository.latest_finished_status(connection_id)` |
| current source_version / checkpoint | `PostgresSyncRepository.get_checkpoint(connection_id)` |

There is no "latest run per connection" aggregator; the latest finished run is
derived at the call site exactly as `http.py::_get_playground_status` already
does (`list_runs`, filter `finished_at is not None`, `max(key=finished_at)`), or
a thin `latest_run(connection_id)` reader is added (additive, read-only -- a
slice, section 6).

### Current Git context
| field | read from |
| --- | --- |
| repository, ref, path, display_name, enabled | `PostgresContextRepository.list_sources()` -> `ContextSourceRecord` |
| pinned commit sha, committed_at, snapshot id, content hash, domain, title | `ContextSourceRecord.current_snapshot` (`ContextSnapshotRecord`) |
| last sync attempt status/at, last attempted commit, last error | `ContextSourceRecord.last_attempt_status/at`, `last_attempted_commit_sha`, `last_error` |
| snapshot timeline (audit) | `PostgresContextRepository.history(source_id)` / `context/history.py::get_context_history` |

These are the exact fields `cli.py::cmd_context_status`/`cmd_context_show`
already print. The Git-context store is `PostgresContextRepository` (the served
snapshot path), NOT `PostgresGovernedContextRepository` (the ADR-0011 dual store
whose reader path is unbuilt and serves nothing).

CURRENCY ("is the pinned commit current?") is the one datum NOT already stored:
it is a comparison of the pinned `commit_sha` against the upstream ref's current
HEAD, which is a read against the Git remote (`git ls-remote <repo> <ref>`, no
fetch, no mutation). It is scoped to its own slice (section 6, S2) and is
optional/network-gated; the pinned commit + `committed_at` + `last_attempt` are
always shown even when currency cannot be checked offline.

### Linked evidence
| field | read from |
| --- | --- |
| observed_assets (ref, connector, type, observed_version, content_sha256, governance) | `bundle/resolver.py::_linked_evidence(snapshot, directive)` -> `evidence["observed_assets"]` |
| freshness (last_observed_at, observed_version_at, source_modified_at, deleted_at) | same, `evidence["freshness"]` (from `ObservedAssetRecord.last_seen_at`/`current_version.created_at`/`source_modified_at`/`deleted_at`) |
| deprecations (`source_deleted`, `prohibited_by_context`) | same, `evidence["deprecations"]` |
| per-asset current version / last_seen / deleted | `PostgresObservedAssetRepository.get(asset_id)` / `.get_by_external_id(...)` -> `ObservedAssetRecord` |

`_linked_evidence` is the application service that produces the exact per-ref
evidence projection the bundle serves; the view reuses it per resolved domain
rather than re-deriving. Freshness here is the OBSERVED freshness of an asset
version, not a governed freshness policy (no such column is read).

### Findings
| field | read from |
| --- | --- |
| open/current findings, optionally by asset/type/run | `PostgresProcessorRepository.list_findings(state="current", ...)` -> `list[FindingRecord]` |
| the rule that produced each | `FindingRecord.finding_type` + `.rule_version` |
| affected asset / snapshot / governed-context | `FindingRecord.affected_asset_id` / `affected_context_snapshot_id` / `affected_context_id` |
| processor run status/counters/errors | `PostgresProcessorRepository.get_run(run_id)` -> `ProcessorRunRecord` |

This is the same reader the resolver uses live
(`list_findings(state="current", affected_asset_id=...)`), so "open findings
against the bundle's assets" is the findings for the asset ids the resolved
domain's evidence names.

### Eval state
| field | read from |
| --- | --- |
| cases (name, version, question, expected, domain) | `PostgresEvaluationRepository.list_cases(domain=)` -> `EvaluationCaseRecord` |
| runs per case (last run, scorecard, pass/fail, context versions used, started/finished) | `PostgresEvaluationRepository.list_runs(case_id)` -> `list[EvaluationRunRecord]` |
| staleness (did this run read today's context?) | `EvaluationRunRecord.context_versions_used` vs the current snapshot version |
| "domain declares no eval bank" | `context/schema.py::is_unevaluated(snapshot.normalized)` (bundle authority `unevaluated: True`) |

NOT SERVED TODAY: unlike the four surfaces above, NO HTTP handler or
`run_operation` path reads `PostgresEvaluationRepository`, so S5 does not reuse an
existing served assembly -- it is a NEW thin additive read-only reader over that
repository. "Last eval run" is the newest of `list_runs(case_id)`; there is no
cross-case aggregator, so a `latest_run(case_id)` / per-domain last-run reader is
part of that additive slice (section 6, S5). The committed-recording #25 benchmark
(`evals/report.py`, reads fixtures at import, not the DB) is a DIFFERENT system
and is NOT this surface -- the operator view reports the PERSISTED evaluation
state, not the CI benchmark.

## 2. The shape: a CLI read view now; a served endpoint is a fork

RECOMMENDED, buildable now, no fork: a read-only CLI view --
`hyperset ops status` (a new `ops` subparser, or `hyperset status`) following the
exact `cmd_context_status` shape (build a session factory, call the readers in
section 1, print). It composes the five surfaces into one operator snapshot,
satisfies the outcome ("without a psql prompt"), and touches no served contract:

- no new served endpoint or response shape;
- no `SCHEMA_VERSION` move (`bundle/schema.py` `= 20`);
- no `tools_hash` change -- it adds nothing to
  `planner.loop.RESOLVE_PATH_OPERATIONS = (CATALOG, RESOLVE, VALIDATE)`, the only
  ops `tool_specs`/`tools_hash` iterate;
- nothing mounted on MCP.

"Reuse the same application services the HTTP/MCP surface calls" (the issue's
note) is honoured for the FOUR surfaces that have a served counterpart, by reusing
the repository readers and `_linked_evidence` / `get_context_history` that the
served handlers themselves call. It is NOT honoured for eval state, because eval
state is not served at all today: NO HTTP handler or `run_operation` path reads
`PostgresEvaluationRepository`, so S5 reads that repository directly (a new
additive read-only reader), not through a served assembly. There is likewise no
single existing served operation that returns all five surfaces:
`transport/operations.py` serves only the analytics/governance ops (`CATALOG`,
`DISCOVER`, `RESOLVE`, `VALIDATE`, `EXPAND`, review), and the only place sync
health and git-context are already assembled application-side is the
NON-`run_operation` HTTP handlers (`_get_playground_status`,
`get_context_history`), which read repositories directly. The CLI view assembles
from those same readers for four surfaces and from the evaluation repository
directly for the fifth.

## 3. Read-only and governed guarantees

- Every reader used is a SELECT-only repository method; the view constructs no
  writer and calls none (`begin_run`, `finish_run`, `record_run`,
  `resolve_finding`, `upsert`, `register_source`, `record_health`, ... are all
  absent from its call graph).
- Guard (ships with S1): a structural test that the ops module's call graph
  names only the reader methods enumerated in section 1 -- so a future edit that
  reaches for a writer (a "quick triage from the status view") goes red. This is
  the read-only boundary as code, not a comment.
- It adds no governed manifest field and reads no governed input the bundle does
  not already read; it cannot change what a bundle serves.

## 4. What it must NOT do

No write path (including finding triage and context edits); no conversational or
analytics UI (that is the calling agent's, not Hyperset's); no authentication
beyond what the local v0 deployment already assumes. It surfaces truth; asserting
it stays with the governed Git-owned path.

## 5. Served-surface option -- FORK (ASK-ON-FORK, do not self-ratify)

A served operator read endpoint is a NEW served surface / response shape, so per
this bead's ASK-ON-FORK rule it is filed for the mayor/overseer rather than
self-approved (filed as hy-9c1i). Options, with measured impact:

- Option A (CLI only): ship section 2, no served endpoint. Zero served-contract
  impact. RECOMMENDED as the first delivery; the served endpoint can follow.
- Option B (non-ROUTES GET, precedented): a read-only
  `GET /v0/ops/status`-style endpoint OUTSIDE `ROUTES` and outside
  `RESOLVE_PATH_OPERATIONS`, exactly the pattern `/v0/playground/status`,
  `/v0/context/history`, and `/v0/health` already follow -- NOT an MCP tool, and
  moving NEITHER `tools_hash` NOR `SCHEMA_VERSION`. Impact: a new served GET route
  and a new JSON response shape (versioned by the existing `SCHEMA_VERSION` stamp
  the other operator reads already carry). Still a served-surface addition ->
  needs a ruling.
- Option C (a served RESOLVE-path operation): rejected here as the wrong shape --
  it would add to the analytics tool contract and move `tools_hash`, and an
  operator health read is not an assist tool. Noted only to record it was
  considered.

Byte/impact evidence above is SUPPORTING only; the served-surface addition is not
self-approved.

## 6. Decomposition into impl slices (build none)

A walking skeleton over the CLI shape (section 2), one surface at a time; each is
read-only and additive, and none is built here.

- S1 (hy-9vji) -- CLI skeleton + sync health + the read-only guard (section 3).
  Establishes `hyperset ops status`, prints connections + latest finished sync
  run/outcome, and lands the structural read-only guard the later slices inherit.
- S2 (hy-3yri) -- Git context surface: sources + current snapshot (pinned commit,
  committed_at, content hash, snapshot id) + last-attempt health. Includes the
  optional currency check (`git ls-remote` compare, network-gated, read-only);
  offline it shows the pin without the currency verdict.
- S3 (hy-4dke) -- Linked evidence surface: reuse `_linked_evidence` per resolved
  domain to show observed asset versions, freshness, and deprecations the context
  points at.
- S4 (hy-4djd) -- Findings surface: `list_findings(state="current")` with the
  rule (`finding_type`/`rule_version`) and affected asset/snapshot for the
  bundle's assets.
- S5 (hy-qsic) -- Eval state surface: last persisted evaluation run per
  case/domain (pass/fail, scorecard), staleness from `context_versions_used`, and
  `unevaluated` where a domain declares no eval bank. Adds the thin latest-run
  reader (additive).
- FORK (hy-9c1i) -- served operator read endpoint (Option B), gated on the
  section-5 ruling; build nothing until it lands.

Slice dependencies are recorded on the beads (S2-S5 depend on S1 hy-9vji).

Dependency: S1 first (skeleton + guard); S2-S5 are independent additive surfaces
on top; the FORK is independent and ruling-gated.
