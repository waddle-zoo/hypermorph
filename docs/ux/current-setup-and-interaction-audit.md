# Current setup and interaction audit

## 1. Scope and baseline

This is an engineer-to-product audit of the local mainline experience as of
2026-08-16.

| Item | Value |
| --- | --- |
| Repository | `https://github.com/waddle-zoo/hyperset` |
| Baseline | `origin/main` at `f06267c` |
| Local product | `http://localhost:8000/` |
| Primary setup path | Docker Desktop + `uv` + host Ollama + `make up-demo` |
| Primary product modes | Explorer at `/playground/`, reviewer at `/review/`, admin at `/admin/` |
| Governance assumption | Git owns meaning; Hyperset resolves and observes; humans merge proposals |

> [!NOTE]
> **Historical baseline.** The Ollama/Qwen setup described below was accurate
> for `f06267c`; it is not the current setup contract. The served demo now uses
> OpenAI/Luna and OpenAI embeddings, with no local model runtime dependency.
> Its statements that real authentication/login did not exist are historical
> too: OIDC bearer verification and PKCE login/session routes have since shipped,
> present-but-default-off. The loopback demo still runs with auth disabled.

The branch used for this audit is a new worktree named
`ux/audit-2026-08-16`. The audit intentionally distinguishes source-of-truth
documentation from the live playground implementation because the product is
pre-1.0 and the README warns that APIs and configuration can change.

## 2. The current setup contract

### 2.1 What the README promises

The README presents setup as three steps: have Docker Desktop, `uv`, and Ollama
running; copy `.env.example`; run `make up-demo`; then open the playground. It
also advertises Playground, Admin, and MCP entry points
(`README.md:44-67`). That is a useful front door, but the three-step framing
compresses a large dependency graph into one command.

`make up-demo` builds the migration image, starts Postgres, starts the demo
Superset, bootstraps it, creates and syncs the example `revenue` and
`supply_chain` Git context domains, then starts the API and hosted MCP
(`Makefile:20-30`, `Makefile:32-41`). It also requires a host-side Ollama
installation and pulls `nomic-embed-text` before startup
(`Makefile:12-18`). The API container reaches Ollama through
`host.docker.internal`, so the host/container boundary is a material part of
the product setup even though it is not visible in the browser.

### 2.2 The dependency graph an engineer must hold in mind

```text
Docker Desktop
  ├── Postgres + migrations
  ├── Hyperset API :8000
  ├── hosted MCP :8010
  ├── demo Superset :8088
  └── demo fixture / bootstrap jobs

Host Ollama :11434
  └── nomic-embed-text  ── reached from containers via host.docker.internal

Git context source
  ├── repository URL or container-visible local path
  ├── ref + context path
  └── add → receive source ID → sync → inspect status
```

The repository does document the Git source commands and the difference between
Git authority and observed metadata (`README.md:112-137`). However, the setup
journey requires the engineer to translate host paths into container-visible
paths, retain a generated source ID, and use a separate CLI loop for sync and
status. That is a reasonable developer API; it is not yet a low-friction
onboarding flow.

### 2.3 Current setup sequence, with interaction gaps

| Stage | Engineer action today | What is clear | Interaction gap |
| --- | --- | --- | --- |
| Install | Install Docker Desktop, `uv`, and Ollama | The prerequisites are named | No preflight command or UI reports versions, ports, Docker resources, Ollama reachability, or the embedding model before the long startup. |
| Configure | Copy `.env.example` to `.env` | The README gives the command | Required values and safe local defaults are not summarized at the moment of setup; an engineer discovers missing secrets through container behavior. |
| Boot | Run `make up-demo` | One command is memorable | The command is a multi-service orchestration and model-pull workflow. There is no single success/failure summary that maps a failed step to a recovery action. |
| Context seed | Wait for example repositories to be generated, added, and synced | The Makefile is explicit | The engineer does not see the source IDs, current commit, last sync, or validity state in the product's first-run surface. |
| Verify | Open one of three URLs | The URLs are printed by Make | The browser can open before every meaningful capability is ready. “Connected”, “streaming”, and disabled controls do not form a clear readiness contract. |
| First question | Choose an agent/model and ask a question | The explorer has a simple prompt | A slow discovery stage gives insufficient feedback and no visible timeout or error classification. |
| Review | Open `/review/` | The governance boundary is explicit | An empty queue has no seeded walkthrough, sample task, or explanation of how to create the first review event. |
| Admin | Open `/admin/` | Write-back target and secret handling are visible | Settings only configures the proposal target; operational health, connector sync, context source status, and recovery remain CLI/debug concerns. |
| Diagnose | Use `docker compose ps`, logs, `make context-status`, and source/debug views | The repository has useful low-level tools | The product does not join these facts into a role-appropriate diagnosis path. |
| Reset | Run `make reset` | The README warns that volumes are removed and confirmation is required | Recovery is destructive and command-line-only; there is no explanation of what can be reset safely versus what is authoritative in Git. |

### 2.4 Setup surfaces that are currently easy to miss

The Makefile explicitly marks `process` as blocked on issue #38 and `eval` as
blocked on issue #25 (`Makefile:179-185`). `make status` also prints that the
processor and review UI are not yet runnable in that CLI contract
(`Makefile:203-209`), even though the local UI includes `/review/` and an
admin/debug surface. This is not necessarily a functional defect, but it is a
documentation and mental-model mismatch: an engineer following the Makefile
can conclude that review UI does not exist while the live route does.

The `playground-ui` target is another hidden onboarding dependency for a clean
checkout. It installs Node packages and builds the ignored bundle before the
served-playground test passes (`Makefile:187-194`). The README's standard
development verification commands do not place this step in the main setup
sequence. A new engineer therefore has two different definitions of “the UI is
available”: source mode for local browsing and built-bundle mode for gate/test
validation.

### 2.5 Delegated cross-checks

The delegated onboarding report added three useful setup risks that are
consistent with the current mainline contract and should be made explicit in
the product plan:

- liveness is not the same as readiness; a responding API process does not prove
  that the database, embedding model, or context snapshot can serve a governed
  answer;
- the DataHub environment is a separate, heavier Compose profile, so the basic
  demo path and the two-source evaluation path are different journeys and
  should be named as such;
- local credentials, model availability, source IDs, and container-visible
  repository paths are setup state, not incidental troubleshooting details.

The cross-check also confirmed that the current project contains an intentional
dead-end marker for `make eval` (`make process` now runs the offline processor
over the latest completed sync run -- hy-jp0gq). Such honesty signals are
useful, but they should be presented beside the supported workflow instead of
being discoverable only after a command fails.

There is a second versioning mismatch in `.env.example`: its API-port comment
still says the review UI is not implemented (`.env.example:69-74`), while the
same mainline checkout serves `/review/` and the README advertises it. Setup
documentation should be generated from the actual route inventory or checked as
part of the docs gate.

Finally, the Compose API healthcheck calls `/v0/health`
(`docker-compose.yml:148-164`). That is useful liveness, but it is not the same
as “ready for a governed answer”: the healthcheck does not establish model,
embedding, connector, or current-context readiness. The browser needs a second,
explicit readiness contract rather than reusing a green container badge.

## 3. Current product information architecture

### 3.1 Routes observed

| Route | Visible job | Initial/live state observed | Current risk |
| --- | --- | --- | --- |
| `/` | Product landing page | “Context that knows how it connects.” with links to Playground, Review, and Settings | Strong orientation, but no setup/readiness path for an engineer arriving from a cold install. |
| `/playground/` | Natural-language exploration | Starts with a governed-only chat, agent/model controls, stage observability, and developer views in a `Views` selector | End-user exploration and developer diagnostics share one information architecture. |
| `/playground/environment/` | Runtime environment inspection | “Nothing loaded yet” until the user explicitly refreshes | A user can see an empty panel even while background runtime status is polling. |
| `/playground/catalog/` | Context catalog inspection | Manual refresh/debug output | Useful for developers; too raw for a first-time admin or explorer. |
| `/playground/discover-candidates/`, `/bundle-resolver/`, `/plan-validation/` | Test discovery, resolution, and validation | Debug controls and raw JSON | Important trust concepts are exposed as harness panels rather than connected to the explorer's answer journey. |
| `/playground/agent-builder/`, `/agent-evaluator/`, `/domain-graph/` | Agent contract, comparison, and graph testing | Developer/test views | Valuable future power surface; currently increases route complexity and role ambiguity. |
| `/review/` | Review drafted context and propose to Git | After loading, empty state: “You’re all caught up.” | No queue evidence or seeded first task; proposal state is browser-local. |
| `/admin/` | Configure Git write-back target | Write-back form plus read-only summaries of connections, agents, and models | Labelled Settings but not a full operational admin surface. |

The route implementation confirms that admin and review are single pages while
the public playground canonicalizes nine debug tabs into paths
(`playground/ui/src/main.jsx:443-455`, `621-636`). This is technically tidy but
places very different jobs behind a shared top-level `Views` control.

### 3.2 Common interaction model

The app polls `/v0/playground/status` every 15 seconds and derives a backend
health boolean, default agent, default model, and runtime summaries
(`playground/ui/src/main.jsx:583-605`). That gives the UI a useful substrate, but
it is not presented as a user-facing readiness model. The browser can show a
chat surface while the backend, model, embedding service, or context source is
not ready, and the user must infer readiness from control state and small status
labels.

The main request helper turns API failures into a generic error message
(`playground/ui/src/main.jsx:97-99`). The UI provides local “Try again” or error
banners in several places, but the error vocabulary is not consistently mapped
to “fix setup”, “retry”, “wait”, or “this answer is unavailable”.

## 4. Persona-specific experience audit

### 4.1 Admin / deployment operator

#### What exists

The admin page clearly labels itself “ADMIN · SETTINGS” and “admin only”. It
configures a write-back repository, base ref, manifest path, and one of three
token sources. Tokens and private keys are write-only from the browser, and the
page explicitly says the setting only enables proposal targets; it never
approves or merges (`playground/ui/src/main.jsx:458-510`). That is excellent
boundary-setting and appropriately conservative secret handling.

The page also summarizes connections, agents, and models as deployment-level,
read-only values (`playground/ui/src/main.jsx:507-510`).

#### Gaps

1. **The page is called Settings but the admin job is larger.** An operator needs
   to know whether the database, API, model, embedding model, Superset/DataHub
   connectors, Git context source, and review write-back are healthy. Only the
   last of those has an interactive configuration surface.
2. **The form has weak validation guidance.** The save button is disabled until
   repository and manifest path are non-empty, but the fields are not visibly
   marked as required and there is no inline explanation of why the action is
   unavailable (`main.jsx:491-505`). URL/local-path differences, ref syntax,
   manifest identity, and server-side token naming are explained in a long
   status paragraph rather than at the point of decision.
3. **There is no “test connection” or dry-run.** An operator can save a target
   without seeing whether the repo/ref/path can be read or whether the token can
   perform the eventual proposal operation.
4. **Runtime summaries have no freshness or provenance.** “Connections”,
   “Agents”, and “Models” are compact summaries, not a health state with last
   probe, version, source, or next action. A delegated live check also found
   that the named connection summary can represent available connector types
   rather than configured connection rows; the UI should never make those look
   equivalent.
5. **The admin-to-review handoff is weak.** Review surfaces link to Settings
   when no write-back repo is configured, but Settings does not show the pending
   reviewer queue, the last proposal, or the exact effect of enabling the target.
6. **The permission boundary is asserted, not operationalized.** The “admin
   only” label is helpful, but there is no visible identity, permission source,
   or explanation of what local route protection means in this pre-1.0 setup.
   The repository's own ADRs state that loopback and the surface gate are not
   real authentication (`docs/adr/0027-github-app-writeback-auth.md:86-88`,
   `docs/adr/0030-the-authorization-boundary.md:31-35`). This is acceptable for
   local development only and becomes a P0 if the route is network-exposed.
7. **Operational dead ends live outside the labelled admin surface.** Context
   add/sync/status, connection creation, sync runs, checkpoints, and recovery
   exist in the CLI/backend, while the admin page has no corresponding action.
   The bundle/debug view can tell an operator to sync a connection but cannot
   take them to a configured connection or sync action.

#### What the admin experience should look like

Make `/admin/` a compact setup and readiness console with four zones:

1. **System readiness** — API, Postgres, Ollama, embedding, model, connectors,
   and context snapshot, each with `ready`, `degraded`, `blocked`, or `unknown`,
   last checked, and one recovery action.
2. **Governed context** — repository, ref, manifest/domain, current commit,
   last sync, validation result, source ID, and a safe “sync now” action that
   preserves Git as authority.
3. **Review write-back** — target details, secret source, connection test,
   proposal permissions, last proposal, and a clear “this creates a PR; it does
   not merge” explanation.
4. **Deployment summary** — agents, models, connector versions, API version, and
   copyable diagnostics for an engineer.

The first screen should answer “Can this deployment safely answer a governed
question?” before asking the operator to interpret configuration.

### 4.2 Reviewer / domain expert

#### What exists

The reviewer lede is unusually clear: confirm a drafted definition, propose it
to Git, and have a human merge the proposal; nothing in the surface approves or
merges (`playground/ui/src/main.jsx:514-518`, `540-556`). The page supports loading
tasks, inline editing, proposing, PR-link persistence in the browser, retrying
errors, and an opt-in raw JSON view. It also displays whether a write-back repo
is configured.

#### Gaps

1. **The empty queue is truthful but not useful for onboarding.** “You’re all
   caught up” does not show when the queue was last checked, where tasks come
   from, how to generate a task, or what a good review looks like.
2. **Review evidence is not the primary information architecture.** A reviewer
   needs the drafted definition, why it was proposed, the triggering question,
   governed alternatives, observed source evidence, freshness, ownership, and
   the exact Git diff. The current page only exposes these if the returned task
   card happens to contain them; there is no explicit evidence layout or review
   checklist in the shell.
3. **Proposal state is stored in `localStorage`.** The implementation comments
   explicitly say the backend does not persist a `proposed` status and the UI
   keeps confirmation and PR link locally (`main.jsx:519-530`). This can diverge
   across browsers, users, cleared storage, or a changed backend queue. It also
   means the displayed open count is partly a property of the current browser
   (`main.jsx:537-551`).
4. **The lifecycle after proposing is under-specified.** A reviewer should see
   proposal created, PR link, branch/ref, commit, checks, merge state, and what
   to do if the PR is edited or closed. “Propose” alone does not describe the
   handoff.
5. **Queue triage is minimal.** There is no visible filter by domain, owner,
   age, severity, confidence, duplicate, or status; no assignment; and no
   explicit distinction between a new ambiguity and a stale or contradictory
   governed definition.
6. **No reviewer identity or reason capture.** Domain experts need a lightweight
   rationale and ownership trail even when Git remains the approval authority.
7. **The normal card omits decision-critical finding evidence.** The current
   card renders the miss, draft definition, gathered sources, fields, and joins
   (`playground/ui/src/main.jsx:297-367`), but not processor evidence, finding
   type, severity, confidence, affected assets, rule version, evaluation impact,
   current Git baseline, or an explicit before/after diff.
8. **Evidence types are flattened.** Gathered observed sources and proposed
   `approved_sources` are combined into a single list labelled “observed source”
   (`main.jsx:303-307`, `351-354`). A reviewer can therefore mistake a draft
   recommendation for corroborating source evidence.
9. **Reviewer dispositions are missing.** The visible actions are Edit
   definition and Propose to Git (`main.jsx:360-367`). A reviewer cannot mark a
   task duplicate, defer it, request more evidence, assign it, or dismiss it
   with a reason. A backend refine workflow is also not exposed in the card.
10. **Concurrent edits and failed edits are risky.** The UI closes the editor
    immediately after invoking the asynchronous save (`main.jsx:314-320`), and
    the current task payload does not provide a visible version/ETag conflict
    path. A failed validation can discard a reviewer's typed work, while two
    reviewers can overwrite one another without a useful conflict state.
11. **Write-back readiness is optimistic.** The Propose action is enabled when a
    write-back configuration exists, not when repository/ref/path and credential
    access have been preflighted. The error arrives only after the reviewer
    clicks the action.

#### What the reviewer experience should look like

Make `/review/` a work queue whose cards are evidence bundles, not just draft
forms. Each card should show:

- the triggering question and detected ambiguity;
- current governed meaning, if any, with exact source and commit;
- observed Superset/DataHub evidence, freshness, and conflicts;
- proposed definition with an inline diff against the current Git version;
- owner/domain, confidence, age, duplicate status, and reviewer notes;
- actions: refine draft, mark duplicate/not actionable, propose PR;
- proposal state: PR URL, branch, commit/check state, and the next human action.

The surface must keep the existing boundary: Hyperset can draft and propose, but
the Git workflow remains the approval and merge authority.

### 4.3 Explorer / analyst or agent user

#### What exists

The explorer opens with a natural-language prompt, a `Governed only` control
checked by default, agent/model selectors, “Add governed asset”, and observable
stage messaging. The helper text advertises Enter to send, Shift+Enter for a new
line, `@` tagging, and `+` pinning. The product copy positions the surface as
read-only, governed, and observable.

The implementation also exposes a stop action while a turn is running. In the
live test, a question was submitted, the UI showed “Discovering governed
context”, and a stop button remained available. After stopping, the transcript
truthfully retained the question and reported “Stopped by you.” This is a sound
failure-recovery primitive.

#### Gaps observed in the live flow

1. **The readiness state is not legible.** The chat initially showed a
   “not connected”/loading state and later a “streaming” state while controls
   changed. There was no single explanation of which dependency was unavailable
   or whether the user should wait.
2. **A long-running turn looks stuck.** The live question remained in a
   discovery stage for approximately 16 seconds without visible elapsed time,
   phase detail, timeout threshold, retry action, or “still working because…”
   explanation. The stop button helped, but stopping is not the same as a
   recovery path.
3. **The default audience is ambiguous.** The public playground exposes nine
   debug/testing views through a `Views` selector. That is useful for developers
   but makes the explorer wonder whether they are in a product or a test bench.
4. **Trust is not surfaced at answer time.** Governed-only is a checkbox, while
   governed, observed, stale, contradictory, or unresolved evidence states need
   to be visible on every answer as a compact trust summary.
5. **Advanced interactions are hidden in helper text.** Tagging with `@`, pinning
   with `+`, and adding governed assets are discoverable only after the user has
   read the small helper line; there is no first-use affordance or keyboard
   confirmation.
6. **Manual debug refresh is easy to misread.** Environment and catalog views
   can show “Nothing loaded yet” until the user explicitly refreshes, even while
   the app is polling general runtime status.
7. **Terminal states need a shared contract.** No answer, no governed match,
   observed-only evidence, stale context, connector failure, model failure,
   timeout, and user stop should each tell the user what happened and what to do
   next.
8. **Answer-level trust status is hidden.** The backend can distinguish governed,
   mixed, observed-only, and no-match outcomes, but the normal answer shell
   emphasizes generic stages and collapsed trace/payload details. A synthetic
   graph summary can also imply a governed → Superset → DataHub relationship
   before a real bundle exists. Trust state must be based on the actual response,
   not a reassuring diagram.
9. **No-match and validation semantics can be misunderstood.** Candidate
   discovery can return irrelevant ranked suggestions without a clear “none of
   these” threshold. Plan validation should say “plan conforms to governed
   rules”; it must always state that Hyperset did not execute the query.
10. **Pinned assets need clearer semantics.** “Add governed asset” and debug
    domain selection can make users think they selected the final answer context.
    Label pins as scope hints and separately show the exact resolved domain,
    concept, authority, and evidence state.

#### What the explorer experience should look like

Keep the simple question composer, but make the result a governed answer card:

```text
Question
  ↓
Context selected: revenue · recognized_revenue · commit abc123
  ↓
Evidence: governed Git definition + observed Superset asset · checked 2m ago
  ↓
Answer / qualification / unresolved parts
  ↓
Next action: refine question · inspect evidence · open review task
```

The advanced trace, catalog, graph, bundle, validation, and evaluator controls
should be available as an explicit “Developer tools” mode or an admin/developer
workspace, not as peer options to Live chat for every explorer.

## 5. Cross-cutting interaction and accessibility notes

### Strengths

- The UI uses native controls in key places, including the views selector; the
  source comments explicitly prioritize accessible native selection
  (`main.jsx:443-455`).
- Labels and placeholders are concrete: repository URL/local path, base ref,
  manifest path, token source, and server-side environment variable name.
- The app uses explicit loading, disabled, stop, error-banner, and retry states
  in core flows.
- The public default is governed-only, aligning the default interaction with the
  product's trust model.
- Responsive breakpoints and mobile layout rules exist in the stylesheet; the
  codebase has at least a foundation for narrow screens.

### Risks to validate in the next test pass

- The visual language uses small uppercase monospace metadata and dense debug
  controls. This helps an engineering audience but may lower scanability for
  reviewers and analysts.
- The chat layout uses body overflow constraints and an absolutely positioned
  composer (`playground/ui/src/styles.css:41`, `211-229`). Keyboard, zoom, and
  narrow-viewport testing should verify that messages, errors, and the composer
  never overlap or become unreachable.
- Inputs use custom focus styling and some reset-like rules. A keyboard-only pass
  should verify focus visibility, focus order, Escape behavior, and whether the
  stop/send controls are announced correctly.
- Error messages are primarily generated from API exceptions. They should be
  mapped into user-facing categories with a diagnostic detail disclosure rather
  than exposing infrastructure wording as the main action prompt.
- Theme switching is available globally, but contrast and code/JSON readability
  need an explicit light/dark accessibility check.
- The persistent public status pill currently uses “streaming” when the backend
  is healthy (`packages/chat-ui/src/index.jsx:429`); it should say “Connected”
  unless a turn is actively working. The live test also showed this can remain
  misleading after cancellation.
- Public chat has no clear page-level heading, and dynamic queued/streaming/error
  content lacks consistent live-region semantics. Add a skip link, one `h1`,
  `role="status"`/`aria-live` for progress, and `role="alert"` for actionable
  errors.
- The governed-asset dialog has a role and focus entry point but needs
  `aria-modal`, an explicit input label, Escape handling at dialog level, focus
  trapping, and focus restoration.
- At narrow widths, agent/model controls truncate to ambiguous values. Stack
  them below the composer or give them full-width rows at the mobile breakpoint.

## 6. Current-to-needed capability matrix

| Capability | Explorer | Context reviewer | Admin / context steward | Current state |
| --- | ---: | ---: | ---: | --- |
| Connect MCP / read docs | ✓ | ✓ | ✓ | HTTP/MCP contract exists; onboarding should be a first-class Get started path. |
| Ask a natural-language question | ✓ | ✓ | ✓ | Present in Playground. |
| Browse catalog and context graph | ✓ | ✓ | ✓ | Present as public debug tabs; should be grouped under Explore. |
| Understand evidence and provenance | ✓ | ✓ | ✓ | Core contract exists; primary UI expression is incomplete. |
| Triage ambiguity | Requests | ✓ | Routes | Review queue exists; empty state and filters are thin. |
| Refine a draft without changing Git authority |  | ✓ | Configures | Present in review API/UI; lifecycle context is thin. |
| Propose a Git PR without merging | Can request | ✓ | Configures target | Present; target config and proposal persistence need hardening. |
| Configure a governed Git source |  |  | ✓ | CLI only for source add/sync; admin UI is write-back target. |
| Manage users and reviewer routing |  |  | ✓ | Missing as a first-class admin page. |
| Know whether the stack is ready |  |  | ✓ | Partial; inferred from controls and small runtime state. |
| Recover from a failed or slow turn | ✓ | ✓ | Diagnoses | Stop and retry primitives exist; timeout/diagnosis contract is missing. |
| Inspect advanced resolution/debug state | Opt-in | Opt-in | ✓ | Present as public debug tabs; role separation is weak. |

## 7. Research log and limits

### Direct test log

| Test | Result |
| --- | --- |
| Open `/` | Loaded product landing page and three main links. |
| Open `/admin/` and wait | Loaded write-back configuration, deployment summaries, and no console errors. |
| Open `/review/` and wait | Loaded empty queue, refresh control, and developer JSON toggle; no console errors. |
| Open `/playground/` and wait | Loaded governed-only explorer with agent/model controls and stage UI. |
| Open `/playground/environment/` | Loaded manual environment debug panel; initially “Nothing loaded yet”. |
| Submit “How much revenue did we make by region?” | Entered “Discovering governed context” and remained active long enough to expose missing progress/timeout guidance. |
| Stop the turn | Correctly preserved the transcript and reported “Stopped by you.” |

### Limitations

- The review queue had no tasks in the tested local state, so task-card evidence
  quality could only be assessed from source structure and the surrounding shell,
  not a populated card. Delegated source-backed review inspection supplied the
  populated-card omissions listed above.
- No admin write-back target was saved during the audit; the form was inspected
  without mutating configuration.
- No external GitHub PR, real connector drift, mobile viewport, screen reader,
  or multi-user/browser persistence test was available in this pass.
- The local browser was the source of truth for app behavior; shell-level access
  to the same host/port was not reliable in the test environment.

## 8. Summary diagnosis

The product's semantic and governance boundaries are stronger than its
operational UX. The next UX investment should make system state and evidence
visible before adding more surface area:

1. help an engineer reach a verified first success;
2. help an admin know what is healthy and what action is safe;
3. help an explorer understand what context and evidence produced an answer;
4. help a reviewer make a confident, traceable proposal;
5. move developer diagnostics behind a deliberate mode boundary.
