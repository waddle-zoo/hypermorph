# Hyperset agent usability and implementation guide

This is the short operating manual for an engineer or coding agent changing
the Hyperset product. Read [the v1 product UX specification](v1-product-ux-spec.md)
before editing a route, component, API response, or mockup.

The goal is not to make every screen look consistent. The goal is to make each
human job obvious, safe, reversible, and explainable.

## 1. Start every task with the job, not the component

Before changing code, write down:

```text
Persona:
Job:
Entry route:
One primary action:
Success signal:
What must remain hidden:
Authority boundary:
States to test:
```

If the answer is “everyone,” the task is too broad. Choose Explorer, reviewer,
Admin, anonymous visitor, Git owner, or agent/API integrator.

Use this route decision:

| If the user wants to… | Use | Do not send them to… |
| --- | --- | --- |
| Ask a business/context question | Chat | Developer diagnostics or Admin |
| Understand how meaning connects | Explore | Raw JSON or a reviewer queue |
| Connect an agent | MCP setup / docs | Workspace settings |
| Resolve a meaning gap | Review | Chat-only controls |
| Operate the workspace | Admin via profile/workspace menu | Public Home |
| Approve a semantic change | GitHub | Agent output or Hyperset “approve” |

## 2. Rules that override local implementation convenience

### Authority

- Git-owned context is authoritative.
- Observed sources are evidence and can be stale or conflicting.
- A UI attachment, selected graph node, or agent suggestion does not grant
  authority.
- No agent or UI button may imply that a proposal is approved or serving before
  GitHub merge and sync.

### Visibility

- Home is for orientation, not telemetry.
- Admin is protected and behind the profile/workspace menu.
- Review is visible to reviewers and assigned deep links, not to every visitor.
- Developer diagnostics are a support surface, not the regular-user IA.
- Advanced detail is disclosed at the moment it helps a decision.

### User control

- Preserve the question and form values on failure.
- Make long work stoppable.
- Make destructive actions confirmable or undoable.
- Do not silently switch from Governed only to observed/non-governed behavior.
- Do not change the meaning of an earlier answer when settings change.

## 3. Page implementation contracts

### Home

Render:

- brand and one-sentence product explanation;
- primary Start a question action;
- secondary Explore context and Connect MCP / read docs actions;
- Login for anonymous users;
- profile menu for authenticated users.

Do not render model pickers, graph counts, readiness cards, review queue items,
Admin, raw context, or connector internals by default.

### Login and invite

Implement destination preservation. If a user enters through a Slack review
link, authentication returns to that exact task, not to Home.

Show identity, workspace, role, domain scope, and invite expiry before invite
acceptance. Explicitly label local development mode. A route being reachable is
not evidence of authorization.

### Chat

Use a real thread model. The thread must support:

- stable thread identity and title;
- user/assistant message semantics;
- visible governed/observed/draft/blocked trust state;
- immutable per-answer run metadata;
- Run settings for agent/profile, model/provider, and next-message context
  policy;
- Stop, Retry, and recovery actions;
- reviewer-only proposed-context preview.

Required answer metadata:

```json
{
  "agent": "profile identifier",
  "model": "provider/model",
  "requested_policy": "governed_only | governed_observed",
  "effective_trust": "governed | warning | observed | blocked | stale | conflicting",
  "bundle_id": "immutable bundle identifier",
  "authority_commit": "serving Git commit"
}
```

The UI may summarize this in one compact row, but the data must remain
available for refresh, export, review, and audit.

### Explore

Use real catalog and resolver data. The minimum interaction is:

```text
Search → choose domain/concept → load → inspect graph → select node → understand → open Chat
```

Graph requirements:

- searchable nodes by label, kind, domain, and description;
- selected state that is visible without color alone;
- result list equivalent to graph selection;
- keyboard Enter/Space activation;
- accessible names for nodes and relationships;
- bounded canvas scrolling on narrow screens;
- details disclosure for raw payloads;
- explicit empty, error, no-match, and stale states.

Do not make the graph a decorative screenshot or a developer-only node dump.

### MCP setup and API docs

The setup UI must generate a recipe from selected client and transport. A
recipe is not a successful connection.

Test semantics:

| UI state | Truthful meaning |
| --- | --- |
| Ready to test | Inputs are complete; no network request has succeeded |
| Endpoint responded | The endpoint returned an HTTP response; auth/capabilities may still fail |
| Handshake complete | MCP initialization and capability exchange succeeded |
| Auth required | Endpoint is reachable but credentials are missing/invalid |
| Could not reach | Browser/server could not establish a connection |
| stdio command ready | The local command is formatted; it has not run in the browser |

The API/MCP guide always demonstrates discover → resolve → validate, with
request IDs, bundle IDs, authority commit, errors, abstention, and retry. Never
tell an integrator to bypass governed policy because a resolver returned no
match.

### Reviewer

The reviewer task must be server-authoritative. Do not use browser storage for
proposal, PR, merge, or queue lifecycle.

Required task fields:

- task ID, owner, assignee, role/domain scope, created/updated timestamps;
- original question and affected assets;
- processor finding, severity, confidence, and reason for human review;
- governed source/commit;
- observed evidence with freshness and conflict state;
- current definition, proposed definition, semantic diff, and regression result;
- row version/ETag for conflict detection;
- durable lifecycle state.

Disposition actions:

- Request evidence;
- Defer with reason;
- Duplicate/not actionable with reason;
- Reject draft with reason;
- Propose to GitHub after preview;
- Open the existing PR/status.

“Propose to Git” is an external mutation. Before the action, show repository,
base ref, path, baseline commit, changed files, exact diff, preview status, and
the statement “GitHub approval and merge remain human-owned.”

### Admin

Admin pages need real authorization and workspace scope. The UI should show
identity, workspace, role, and scope at the boundary.

Minimum surfaces:

1. Overview/readiness: API, database, model, embeddings, connectors, Git
   context, sync, and write-back checks.
2. Context sources: repository/ref/path, serving commit, validation, sync,
   last-known-good snapshot, and recovery.
3. Users/roles: invite, revoke, role, domain scope, reviewer routing, audit.
4. Connections/models: configured vs reachable, freshness, capability, impact,
   bounded test, and secret-safe metadata.
5. Write-back/notifications: proposal-only policy, reviewer group, Slack/GitHub
   destinations, delivery state, and retry.
6. Audit/diagnostics: actor, action, resource, timestamp, result, and safe
   links to logs.

Never echo credentials. Never describe a write-only credential field as proof
of authentication. Never imply that Admin status approves meaning.

## 4. State matrix every agent must implement

For every async request, write down the state transitions before writing JSX or
API code:

```text
idle → loading(phase) → success
                    ↘ empty(reason)
                    ↘ blocked(permission)
                    ↘ failed(category, retry)
                    ↘ cancelled(by user)
                    ↘ stale/conflicting(needs attention)
```

Each state must answer:

- What happened?
- Is anything authoritative or serving?
- What can the user do now?
- What will happen if they do it?
- Who owns the next step?

Do not use an indefinite spinner as a terminal state. Do not use a generic
“Something went wrong” when the system knows whether the problem is auth,
connectivity, timeout, validation, no match, stale evidence, or conflict.

## 5. Component and copy patterns

### Primary action

Use one verb and one object: “Start a question,” “Load graph,” “Copy config,”
“Run checks,” “Preview proposed context,” “Create GitHub PR.” Avoid “Continue,”
“Submit,” or “Run” without an object when the consequence is not obvious.

### Status and trust

Always include text. Pair color or icon with a label such as “Governed,”
“Observed evidence,” “Draft only,” or “Blocked.” Put the relevant action beside
the status: View context, Retry, Stop, Request evidence, or Open GitHub.

### Progressive disclosure

Default view: the answer, state, owner, and next action.

Disclosure: source list, commit, bundle ID, raw graph details, traces,
request/response JSON, and diagnostic commands.

Never use disclosure to hide a required decision, permission boundary, error, or
trust qualifier.

### Forms

- label every field explicitly;
- show required/optional state;
- validate on submit and preserve input;
- identify the field and recovery action;
- show last successful check and target identity;
- prevent duplicate external mutations;
- protect unsaved changes on navigation.

### Navigation

- Use one shell per role/surface.
- Keep current role/workspace visible after deep links.
- Preserve destination and thread context across Login.
- Use `aria-current` for active routes.
- Do not hide Admin because it is unimportant; hide it because it is protected
  and role-specific.

## 6. Agent workflow for making a change

1. Read the relevant page contract in the product spec and the closest mockup.
2. Confirm the persona, route, primary action, authority boundary, and success
   signal.
3. Inspect the current route, backend operation, persistence model, and test
   coverage. Do not assume a visible label is an authorization boundary.
4. Define loading, empty, error, blocked, stale/conflict, success, and
   cancellation states.
5. Make the smallest implementation that satisfies the contract. Keep raw
   detail behind disclosure and keep Admin/reviewer controls out of the wrong
   shell.
6. Add or update accessible names, focus behavior, live status semantics, and
   keyboard paths.
7. Run the persona task test at desktop, 390px, keyboard-only, and a failure
   state.
8. Ask an advocate: “Can a first-time user complete the job?”
9. Ask an adversary: “Could a user misunderstand authority, permission,
   completion, or recovery?”
10. Record evidence, remaining gaps, and the exact next owner in the PR.

## 7. Evaluation rubric

Score each changed page from 0 to 2. A v1 page cannot ship with a zero in
Authority, Recovery, Accessibility, or Permission.

| Dimension | 0 | 1 | 2 |
| --- | --- | --- | --- |
| Job clarity | User cannot tell why they are here | Job is inferable | Job and primary action are immediate |
| Trust/authority | Authority is ambiguous or false | Caveat is present but easy to miss | Authority and evidence are explicit at decision point |
| Recovery | Spinner/error strands the user | Generic retry exists | Cause, owner, next action, and preserved work are clear |
| Permission | UI hiding is the only boundary | Some server checks exist | Server scope and UI role framing agree |
| Accessibility | Keyboard/screen reader path fails | Basic labels/focus | Complete keyboard, status, contrast, and focus behavior |
| Information density | Repeated dashboards/raw internals | Some progressive disclosure | One job, minimal default, useful details on demand |
| Persistence | State is browser-local or lost | Partial persistence | Server-authoritative state survives refresh and users |
| Human handoff | Next owner is unclear | Link exists | Owner, timestamp, reason, and durable deep link exist |

## 8. Required PR handoff

Every UX or UI PR should include:

```markdown
## Persona and job

## Page contract
- Route:
- Primary action:
- Success signal:
- Hidden/progressive details:

## Authority and permission

## States tested
- idle/loading:
- success:
- empty:
- error/retry:
- blocked:
- stale/conflict:
- cancellation:

## Accessibility tested

## Advocate result

## Adversary result

## Evidence

## Remaining gaps and next owner
```

## 9. Anti-patterns to reject in review

- A public route that replaces Home, Login, or Admin with a developer panel.
- A hidden Admin route that is not protected server-side.
- A “Tested” button that only changes local UI state.
- A green dot that hides stale/conflicting evidence.
- A browser-local review state for a multi-user workflow.
- An agent-generated PR that looks approved or serving.
- A chat answer with no durable model/policy/provenance stamp.
- A graph that is visual but not searchable, selectable, or keyboard accessible.
- A generic error that loses the user's question or entered form values.
- A page that repeats the same status in multiple cards instead of exposing one
  clear next action.

## 10. Reference material

- [Hyperset v1 product UX specification](v1-product-ux-spec.md)
- [v1 page map and persona flows](v1-page-map-and-persona-flows.md)
- [Personas and service blueprints](personas-and-service-blueprints.md)
- [Human-centered flows](flows-and-service-blueprints.md)
- [v1 mockups](mockups/v1/README.md)
- [Nielsen Norman Group usability heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/)
- [W3C WCAG 2.2](https://www.w3.org/WAI/WCAG22/understanding/)
- [GOV.UK Service Standard: make the service simple to use](https://www.gov.uk/service-manual/service-standard/point-4-make-the-service-simple-to-use)
