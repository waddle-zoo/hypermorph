# UX recommendations and roadmap

## 1. Prioritization model

- **P0:** blocks a trustworthy first use, creates misleading system state, or
  threatens reviewer confidence in governance.
- **P1:** creates repeated friction for a primary persona but has a safe manual
  workaround.
- **P2:** improves scale, polish, or advanced workflows after the core trust
  journey is legible.

The order below is intentionally state- and evidence-first. Hyperset has enough
surface area; the next release should make the existing trust model easier to
operate before adding more controls.

## 2. P0 recommendations

### UX-00 — Make the admin security posture explicit and fail closed when exposed

**Problem.** The visible `admin only` label is a route/surface distinction, not
authentication. The repository ADRs explicitly say loopback is a mitigation and
that the write-back surface has no real admin authentication. A local-only demo
can accept that tradeoff; a network-exposed deployment cannot.

**Recommendation.** Keep the local developer path obvious, but bind admin
write-back to an authenticated administrator or refuse to start the write path
when the deployment is not loopback/local. Show the active security posture in
the UI and diagnostics.

**Acceptance criteria.**

- The deployment reports `local-only`, `authenticated`, or `blocked` for admin
  write-back.
- A caller cannot set a GitHub token/App key merely by reaching `/admin/` on a
  network-exposed deployment.
- The UI shows signed-in identity and the authorized scope when real auth exists.
- Secret encryption/write-only behavior is not presented as authentication.
- The product documentation names the current local-only limitation beside the
  setup command.

### UX-01 — Add a joined readiness and preflight contract

**Problem.** The engineer and admin cannot tell whether Docker, Postgres, API,
OpenAI/Luna, embedding, connectors, and context snapshots are ready from one
place.

**Recommendation.** Add a machine-readable readiness endpoint and a shared UI
readiness component. Provide a CLI preflight that runs before `make up-demo` and
a post-boot checklist at `/admin/`.

**Acceptance criteria.**

- Each dependency reports `ready`, `degraded`, `blocked`, or `unknown`.
- Each non-ready dependency names the failing check, owner, and recovery action.
- Readiness includes required model names, current Git context commit, last sync,
  and API version.
- `/playground/` does not imply answer readiness while a required dependency is
  blocked.
- The same status vocabulary appears in CLI, browser, and diagnostics.

### UX-02 — Turn setup into a verifiable first-success journey

**Problem.** `make up-demo` is memorable but hides a long dependency sequence,
host/container assumptions, and context seeding.

**Recommendation.** Keep the one-command path, but add a setup guide and
post-boot wizard that show stages: prerequisites, boot, demo services, context
snapshot, model, first resolution, first answer.

**Acceptance criteria.**

- A clean engineer can follow one canonical path from clone to first governed
  answer without reading source files.
- OpenAI runtime and embedding credentials/models are checked before the long boot.
- Container-visible path guidance appears beside context-source inputs.
- The first-success checklist ends with a cited domain, concept, and commit.
- Recovery instructions link to `docker compose ps`, logs, `make context-status`,
  and safe stop/reset choices.

### UX-03 — Give every explorer turn a progress and terminal-state contract

**Problem.** The live test showed a turn in “Discovering governed context” for
approximately 16 seconds with no elapsed time, timeout expectation, phase detail,
or retry path.

**Recommendation.** Display elapsed time, named phases, a soft timeout, stop,
retry, and categorized failure states. Preserve the transcript and make the
distinction between user stop, timeout, model failure, connector failure, and
no governed match explicit. Treat `no_match`, `observed_only`, and unresolved
bundles as non-standard outcomes even when the response object is technically
non-empty. Add bounded discovery abstention instead of forcing the best-ranked
candidate.

**Acceptance criteria.**

- Every running turn shows phase, elapsed time, and a clear stop action.
- A soft timeout explains whether the system is still working and offers retry.
- Every terminal state has a user action and a developer diagnostic disclosure.
- No-match, observed-only, stale, contradictory, and dependency-failure states
  are distinct.
- Candidate discovery can say “none of these” and exposes assist-only ranking
  provenance rather than implying identity.
- A stopped turn is truthful and remains in the transcript, as the current UI
  already does.

### UX-04 — Persist review proposal lifecycle server-side

**Problem.** Review proposal confirmation and PR links are stored in
`localStorage`, so queue counts and status can diverge across browsers or users.

**Recommendation.** Add durable proposal state to the review service with task,
  proposal, PR, branch/ref, commit, checks, and external lifecycle state. Keep
  browser storage only as an optional transient cache.

**Acceptance criteria.**

- Two browsers and two users see the same task/proposal lifecycle according to
  authorization.
- Refreshing or clearing browser storage does not lose proposal state.
- The review queue distinguishes open, proposed, PR updated, merged, closed,
  stale, and needs-attention.
- The UI links to the PR and states the next human action.
- The implementation continues to prevent approval/merge inside Hyperset.

### UX-05 — Split explorer IA from developer tools

**Problem.** The public explorer exposes nine debug/testing views as peers in a
  `Views` selector. This increases cognitive load and blurs user roles.

**Recommendation.** Keep Live chat as the default explorer surface. Put catalog,
  discovery, bundle, validation, builder, evaluator, and graph into an explicit
  Developer tools workspace, linked from admin or a deliberate developer toggle.

**Acceptance criteria.**

- A first-time explorer sees one primary question path and no raw JSON by
  default.
- Developers can reach every current debug view without losing deep links.
- The product labels testing-only behavior and destructive/experimental limits.
- Navigation distinguishes user work, review work, administration, and tooling.

### UX-06b — Surface actual trust state, not synthetic reassurance

**Problem.** The backend can report governed, mixed, observed-only, no-match,
stale, and conflicting states, but the main answer shell hides much of that in
collapsed trace/payload details. Synthetic graph summaries can also imply
relationships before a real bundle exists.

**Recommendation.** Render answer-level trust badges and per-source labels from
the actual response. Do not render graph relationships until a real bundle is
present. Label pins and ranked candidates as scope hints/assist only.

**Acceptance criteria.**

- Every answer exposes resolution status, Git authority, observed-source state,
  freshness, warnings, and unresolved claims without opening raw JSON.
- `no_match` cannot be displayed as an ordinary usable governed bundle.
- Plan validation reads “plan conforms to governed rules” and always says that
  Hyperset did not execute the query.
- Observed, governed, proposed, conflicting, and missing evidence are visually
  and semantically distinct.

## 3. P1 recommendations

### UX-06 — Expand admin into an operational readiness console

Add cards for system health, configured connections, available connector types,
Git context sources, model/embedding status, write-back target, and recent
events. Each card should include last checked, exact version/commit where
applicable, and one safe action. Keep “available connector type” separate from
“configured connection”. Move environment health out of the public developer
views and include source sync/checkpoint status here.

### UX-07 — Make trust state a first-class answer component

Every answer should show a compact trust card: governed definition used, domain/
concept, exact Git commit, observed evidence, source freshness, qualifiers, and
unresolved pieces. The card should link to a readable explanation before it
offers raw JSON or a trace.

### UX-08 — Design the reviewer queue for evidence and triage

Add filters for domain, age, owner, confidence, duplicate, stale, conflict, and
status. Give each task a short “why this exists” summary and a structured
evidence checklist. Render finding type, severity, confidence, affected assets,
rule version, current Git baseline, and a before/after diff. Keep observed,
governed, proposed, conflicting, and unresolved evidence as distinct types.
Allow defer/duplicate/needs-evidence outcomes with reasons, and expose the
backend refine workflow without making raw JSON the primary editing surface.

### UX-09 — Make context source operations discoverable

Keep CLI operations as the canonical developer interface, but expose source ID,
repo/ref/path, current commit, last attempt, validation state, and sync command
in admin diagnostics. Give engineers a copyable command rather than requiring
them to infer container paths from the Makefile.

### UX-10 — Establish an accessibility and responsive QA pass

Test keyboard-only use, focus order, focus visibility, screen-reader names for
chat stages and stop/send controls, `aria-live` progress/errors, page headings,
skip navigation, modal focus trapping/restoration, zoom to 200%, dark/light
contrast, 620px and 920px breakpoints, and long error/trace content. Verify that
the absolute chat composer never hides the last message or review action and
that agent/model controls remain unambiguous at 390px.

### UX-11 — Add a review walkthrough for the empty state

When the queue is empty, show last refresh, source of review tasks, a sample
review card or replayable fixture, and the path from an unresolved explorer
question to a review task. Keep the factual “all caught up” state, but make it
instructional.

### UX-12 — Map infrastructure errors to user decisions

Classify errors into: fix setup, retry now, wait, refine question, inspect
evidence, or contact operator. Keep raw exception detail behind a developer
disclosure. The same mapping should appear in chat, review, admin, and debug
views.

### UX-13 — Add reviewer concurrency and edit recovery

Return a task version/ETag, require it for edit/refine/propose, and show a
refresh/merge conflict when another reviewer has changed the task. Keep editor
values until a save succeeds, and show validation errors inline. This prevents
silent overwrites and avoids discarding a domain expert's work after a failed
request.

### UX-14 — Create a human API/MCP integration workspace

Give integrators one place to understand and replay the discover → resolve →
validate contract over HTTP and MCP. Show copyable requests, response anatomy,
provenance, status/error recovery, and the explicit handoff to external query
execution. Preserve transport parity and keep replay read-only.

**Acceptance criteria.**

- HTTP and MCP examples use the same operation names, input semantics, response
  fields, status vocabulary, and error codes.
- A human can replay a request without entering a browser secret.
- Every response example shows authority, bundle/version, freshness, warnings,
  and whether a query was executed externally.
- `no_match`, `observed_only`, stale, conflict, awaiting-sync, invalid-plan, and
  unverifiable states each have a safe consumer action.
- MCP Inspector, Streamable HTTP, and stdio setup instructions are copyable and
  clearly labelled local-only when unauthenticated.

### UX-15 — Separate loading, empty, unavailable, and failed states

Replace generic “Nothing loaded yet” output with explicit state components for
catalog, bundle, history, review, and environment views. Disable actions that
require a selection, such as loading history without a domain, and expose retry
and last-checked context.

## 4. P2 recommendations

- Add saved question/replay links that retain agent, model, directive, context
  commit, and answer evidence.
- Add reviewer ownership and team-level queue summaries after durable lifecycle
  state exists.
- Add an exportable diagnostic bundle with redacted configuration, dependency
  states, request IDs, and context source metadata.
- Add a guided context-author handoff that opens the right Git files and explains
  manifest, context guidance, and eval cases without making the browser the
  authority.
- Add a read-only trust posture summary for team leads: unresolved gaps,
  freshness, proposal throughput, and observed/governed conflicts.
- Add a developer trace timeline with request IDs and phase durations for model,
  discovery, resolution, validation, and evidence retrieval.

## 5. Suggested delivery sequence

### Slice A — Make state truthful

Ship UX-01, UX-03, and UX-04 together. This establishes one readiness vocabulary,
one turn lifecycle, and one durable review lifecycle. These three changes reduce
the biggest trust failures without changing the product's governance boundary.

### Slice B — Make the roles legible

Ship UX-02, UX-05, UX-06, and UX-11. This gives the engineer a first-success
path, the admin an operational home, the reviewer an empty-state entry point,
and the explorer a focused surface.

### Slice C — Make evidence actionable

Ship UX-07, UX-08, UX-09, and UX-12. This joins the trust kernel to the human
decision points: answer, triage, sync, and recovery.

### Slice D — Scale quality and adoption

Ship UX-10 and the P2 work after the core journeys are measurable.

## 6. Role-based UX scorecard

Run this scorecard on every release candidate with a clean environment and a
seeded review fixture.

| Test | Pass condition |
| --- | --- |
| Engineer first success | A new engineer reaches a governed answer with domain, concept, evidence, and commit without reading implementation source. |
| Engineer failure recovery | Given a missing model or unavailable connector, the engineer identifies the failing dependency and recovery action in under five minutes. |
| Admin readiness | An operator can tell whether the deployment is ready, degraded, or blocked and can safely test the write-back target. |
| Admin handoff | An operator can provide a reviewer with a working proposal path without implying merge authority. |
| Explorer trust | An explorer can state which governed context and evidence produced an answer and identify any unresolved qualifier. |
| Explorer recovery | An explorer can distinguish wait, stop, retry, refine, and operator-needed outcomes. |
| Reviewer confidence | A reviewer can explain why a task exists, compare current/proposed meaning, inspect evidence, and create a traceable proposal. |
| Reviewer persistence | Proposal state is identical after refresh, browser change, and user change within the permitted authorization scope. |
| Developer access | An engineer can still reach the raw bundle, graph, validation, evaluator, and trace views through an intentional developer path. |
| Governance boundary | No UI action implies that Hyperset approved or merged governed meaning. |

## 7. Instrumentation to add before measuring improvement

Measure events without capturing sensitive question content by default:

- setup preflight started/completed/blocked by dependency;
- time from boot to first ready state;
- time from ready state to first governed answer;
- turn phase durations and terminal-state categories;
- stop, timeout, retry, and recovery outcomes;
- answer trust-card expansion and evidence inspection;
- review task age, first-view time, refine, defer, duplicate, propose, and PR
  lifecycle transitions;
- cross-browser proposal-state consistency checks;
- admin connection test and context sync outcomes.

The north-star measures are not clicks. They are time to trustworthy first use,
percent of turns with a clear terminal state, percent of answers whose evidence
is understood, and time from ambiguity to a traceable Git proposal.
