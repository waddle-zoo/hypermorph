# V1 mainline gap matrix

Read-only measurement of `origin/main` against the V1 ship contract from
hq-33hj. This refresh updates the original hy-ykms measurement after its
implementation slices landed.

- **Measured at:** `origin/main` commit `174680d` (tree measured on disk).
- **Method:** inspected the routes, operations, UI components, configuration
  accessors, migrations, and tests present at that commit. Documentation and
  HTML mockups were not treated as proof of shipped behavior.
- **Status vocabulary:** SHIPPED, PARTIAL, or MISSING. PARTIAL names the exact
  remainder; a completed implementation bead is not itself proof.

## What landed since the 770e9ba measurement

Every implementation slice filed by the original matrix landed on main:

- explorer run modes/thread settings and first-class trust states (#475, #448);
- reviewer queue/detail/refine/request-evidence/preview/self-describing proposal
  slices (#461, #467–#469, #449), plus assignment and lifecycle work;
- context-source compare/rollback, provider probes, audit correlation/export,
  API/MCP console/replay, and maintainer diagnostics (#470–#474);
- secret-reference resolution and runtime configuration wiring (#476, #480–#485);
- grep and semantic `search_knowledge`, trace/citation/feedback linkage, and the
  search-to-review proposal path (#500, #503–#505, #519, #521, #524, #530);
- bounded graph walking and an actionable graph explorer (#511, #514, #522).

The remaining gaps below are policy or platform breadth, not unlanded versions
of those closed slices.

## Surface matrices

### Explorer / regular user

| # | Contract item | Status | Evidence at 174680d | Remainder |
|---|---|---|---|---|
| 1 | Quiet Home, one primary “Start a question” | SHIPPED | `HomePage`; explorer shell contract tests | -- |
| 2 | Persistent shell and regular-user navigation | SHIPPED | `SurfaceNav`, `Header` | -- |
| 3 | Real chat thread with history, roles, and next-message settings | SHIPPED | `HypersetChat`; chat UI contract tests | -- |
| 4 | Named agent/model and Governed-only vs Governed+observed modes | SHIPPED | named `contextPolicy`; #475 | -- |
| 5 | Immutable trust/provenance per answer | SHIPPED | first-class trust panel; #448 | -- |
| 6 | Context explorer with exact governed resolution | SHIPPED | `ContextExplorer`, `ExplorerDetail` | -- |
| 7 | Explicit governed/observed-only/no-match/stale/conflict/timeout/error states | SHIPPED | labeled terminal-state rendering; #448 | -- |
| 8 | MCP/docs onboarding without Admin | SHIPPED | `DocsPage`, `McpSetupWizard` | -- |
| 9 | Persisted threads with immutable per-message settings | SHIPPED | local storage restore and message settings; #475 | -- |
| 10 | Searchable graph with selected-node explanation and provenance | SHIPPED | graph navigation/legend; #514 and #522 | -- |

### Reviewer

| # | Contract item | Status | Evidence at 174680d | Remainder |
|---|---|---|---|---|
| 1 | Queue states, filters, ownership, urgency, evidence count, status | SHIPPED | queue filters/derived stale state; #455–#461 | -- |
| 2 | Detail with reason, evidence, uncertainty, current/proposed meaning, diff | SHIPPED | task detail contract; #467 | -- |
| 3 | Refine, edit, request evidence, re-run | SHIPPED | Review UI + HTTP request-evidence operation; #469 | -- |
| 4 | Ephemeral proposed-context preview that is not served as authority | SHIPPED | current/proposed preview and regression checks; #468 | -- |
| 5 | GitHub proposal carries task/evidence/source/diff/preview/backlink | SHIPPED | guarded self-describing PR payload; #449 | -- |
| 6 | GitHub remains approval/merge authority | SHIPPED | proposal-only write path and human merge boundary | -- |
| 7 | Lifecycle follows task → PR → merge → sync; Slack deep-link notification | PARTIAL | lifecycle reconcile ships | Slack delivery remains unbuilt and policy-gated. |
| 8 | Assignment, urgency/evidence filters, notifications, lifecycle | PARTIAL | assignment and filters ship | Slack notification remains unbuilt. |

### Admin / context steward

| # | Contract item | Status | Evidence at 174680d | Remainder |
|---|---|---|---|---|
| 1 | Protected login/session/role boundary; local demo posture visible | SHIPPED | authz/login routes and fail-closed startup | -- |
| 2 | Login/invite/workspace selection with deep-link preservation | PARTIAL | login and safe return target ship | Invite and workspace-selection policy/surface are unbuilt. |
| 3 | Honest four-state readiness with impact and recovery | SHIPPED | `admin_readiness`; optional/required severity and connector roll-up tests | -- |
| 4 | Source add/validate/sync/pin/compare/rollback | SHIPPED | compare and rollback; #474 | -- |
| 5 | Bounded live connection/model/embedding probes | SHIPPED | provider probes and truthful roll-up; #471, #523, #533 | -- |
| 6 | Invite/revoke plus role/domain scope, routing, audit | PARTIAL | scope, routing, and audit ship | Invite/revoke lifecycle policy is unbuilt. |
| 7 | Write-back and notification policy | PARTIAL | Git targets, reviewer groups, proposal-only gating, audit ship | Slack destination/triggers/test notification are unbuilt. |
| 8 | Correlated, redacted audit/diagnostic export | SHIPPED | request IDs and export; #470 | -- |

### Integrator / maintainer

| # | Contract item | Status | Evidence at 174680d | Remainder |
|---|---|---|---|---|
| 1 | MCP setup wizard and classified connection failures | SHIPPED | `McpSetupWizard`; MCP probe tests | -- |
| 2 | API/MCP discover→resolve→validate recipes and replay | SHIPPED | console and client-side replay; #472 | -- |
| 3 | Named maintainer failure classification | SHIPPED | five-class diagnostics view; #473 | -- |
| 4 | Redacted diagnostics and API/MCP replay console | SHIPPED | audit export plus console/replay; #470 and #472 | -- |

### Production configuration

| # | Contract item | Status | Evidence at 174680d | Remainder |
|---|---|---|---|---|
| 1 | Customer overlays without editing upstream defaults | SHIPPED | base + ordered overlay loader | -- |
| 2 | Typed, fail-closed schema and documented precedence | SHIPPED | closed `SCHEMA`; loader/startup tests | -- |
| 3 | Secret references with path-only redaction | SHIPPED | `${env:}`/`${secret:}` resolution; #476 | -- |
| 4 | Runtime wiring for config domains | SHIPPED | context, DB, model/provider, playground, connection, feature accessors; #480–#485 | -- |
| 5 | Compose plus credible K8s ConfigMap/Secret path | PARTIAL | Compose path ships | K8s/Helm artifacts remain `hy-7ifv`. |
| 6 | `.env` allowlisted to break-glass/secret-reference inputs | MISSING | unknown `HYPERSET_*` variables are not rejected | `hy-nm4mp`. |

### Knowledge graph — ADR 0041 flexible-yet-governed MVP

| # | Contract item | Status | Evidence at 174680d | Remainder |
|---|---|---|---|---|
| 1 | Bounded typed walk across governed, observed, and proposed knowledge | PARTIAL | cycle-safe root walk and UI ship; provenance/evidence classes remain explicit | A complete six-kind observed/proposed graph projection is not served. |
| 2 | Grep + semantic retrieval over the corpus | SHIPPED | `search_knowledge` grep and semantic modes; #500 and #524 | -- |
| 3 | Trace-aware suggestions | SHIPPED | session/correlation trace, citations, feedback, and proposal linkage; #503–#505 and #530 | -- |
| 4 | Proposal-only review flow with optional human PR action | SHIPPED | search hits create an unapproved ReviewTask; PR open remains a separate human action | -- |
| 5 | Observed/proposed never silently become canonical | SHIPPED (invariant) | evidence class + Git authority boundary remain enforced | -- |

## Remaining decisions and implementation gaps

- **Slack notification delivery:** reviewer/admin notification rows remain
  PARTIAL. Slack routes and informs only; it cannot approve or merge.
- **User lifecycle:** invite, revoke, and workspace selection need an explicit
  grant-store/security-policy decision before implementation.
- **Kubernetes packaging:** `hy-7ifv` remains the deployment artifact gap.
- **Environment allowlist:** `hy-nm4mp` remains the reject-unknown-input gap.
- **Full flexible graph projection:** bounded walking ships, but the complete
  observed/proposed six-kind layer remains broader than the served MVP.

## Measurement limit

This matrix measures present routes, code, and contract tests. It is not a
browser acceptance report. A V1 completion claim still requires a live
role-by-role acceptance pass, including the remaining PARTIAL/MISSING rows.
