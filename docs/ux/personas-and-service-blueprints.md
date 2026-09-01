# Personas and service blueprints

## 1. Persona strategy

Hyperset has three human product personas. The earlier model over-separated
engineers, operators, stewards, integrators, and evaluators even though most of
those people arrive through the same regular-user surface or the same protected
admin surface.

The v1 product should make these three jobs unmistakable:

1. **Explorer / general question asker** — opens Chat, inspects context bundles,
   and can connect an MCP client or read the integration docs.
2. **Context reviewer** — reviews governed meaning when a question exposes a
   gap, conflict, or proposed definition change.
3. **Admin / context steward** — owns workspace setup, context sources, users,
   integrations, and policy from a protected settings area.

MCP onboarding, documentation, Git ownership, agent activity, and maintenance
are jobs or actors within those three experiences—not additional top-level
personas. The Git owner remains the final external approver, while the agent is
an actor that can discover, draft, and notify.

The current Playground already contains the useful regular-user building
blocks: Live chat, catalog, discovery, bundle resolution, plan validation, and
the domain graph. The UX proposal should organize those capabilities around a
quiet Home, a real Chat thread, and a separate context-bundle explorer instead
of presenting all debug views as equal product personas.

## 2. Persona inventory

| Persona | Priority | Primary job | Main surface | Authority boundary |
| --- | --- | --- | --- | --- |
| Explorer / general question asker | Primary | Use Chat, inspect context bundles, and connect/read about MCP | `/playground/`, `/playground/graph/`, `/getting-started` | Consumes governed context; can request review but cannot change authority. |
| Context reviewer / domain expert | Primary | Decide whether a proposed meaning is semantically useful and propose a reviewable Git change | `/review/` + Git PR | Can refine and propose; human/Git workflow owns merge authority. |
| Admin / context steward | Primary | Manage context sources, users, integrations, readiness, and workspace policy | Protected `/admin/` | Can configure the deployment and access model; does not approve or merge meaning by default. |
| Git owner | Supporting actor | Approve and merge governed meaning | Git provider PR | Git is the canonical semantic approval workflow. |
| Agent | Supporting actor | Discover, draft, create a review task, and notify | Background/API/MCP actor | Cannot silently approve, merge, or mutate governed authority. |
| Engineer / maintainer | Supporting job | Install, boot, diagnose, and extend the local stack | README, CLI, diagnostics | Operates the environment; uses admin/maintain tooling when needed. |
| API/MCP integrator | Explorer job | Connect an agent through HTTP/MCP and preserve contract/provenance | Get started, MCP docs, API recipes | Consumes bounded context; does not author meaning. |

## 3. Primary persona blueprints

### 3.1 Explorer / general question asker

**Archetype.** A person who wants to ask a question, test whether Hyperset has
the right meaning, inspect the context graph, or connect an agent without first
learning the repository's internal architecture.

**Trigger.** “I want to ask a question, understand the source of the answer,
test a proposed context update, or connect my agent.”

**Top tasks.**

- open Chat and ask a natural-language question;
- search for a context bundle and inspect the full graph;
- understand the answer's provenance, freshness, and unresolved parts;
- connect an MCP client or read the integration docs;
- open a review task when context is missing or conflicting;
- recover from a slow, unavailable, or unresolved answer.

**Mental model today.** The Playground already has chat, catalog, discovery,
bundle resolution, validation, and graph views, but they are presented as
debug tabs. A regular user needs a quiet Home, a real Chat thread, and an Explore
path for searching context bundles.

**What this persona needs to see.**

- a real Chat thread with conversation history and a compact trust state;
- a quiet Home with one centered question composer that opens a durable Chat thread;
- visible links to **Connect MCP** and **Read docs** without forcing a user into
  admin settings;
- a first-class **Find a context bundle** surface for search, graph, and provenance;
- context and answer states that say governed, observed, stale, conflicting, or
  unresolved;
- an easy “Ask a reviewer” handoff when the answer exposes a meaning gap;
- a reviewer-only context preview that compares current and proposed answers
  without changing the serving snapshot;
- advanced traces and evaluator controls behind an explicit advanced link.

**Success signals.** Time to first useful Chat answer; percent of users who can
find a context bundle; MCP setup completion; provenance comprehension; successful
recovery from ambiguity.

**Anti-goal.** Do not make the engineer infer semantic authority from a generic
“connected” badge or fix a runtime issue by changing a Git-controlled meaning.

### 3.2 Context reviewer / domain expert

**Archetype.** A revenue, supply chain, finance, or analytics domain expert who
can judge whether a proposed definition matches organizational meaning.

**Trigger.** “A question exposed an ambiguity. Is this draft safe and useful to
propose?”

**Top tasks.**

- understand why a review task exists;
- compare current governed meaning with the draft and observed evidence;
- refine wording, filters, joins, caveats, or ownership;
- see an exact diff and propose a PR;
- track GitHub review and sync state;
- defer, duplicate, request evidence, or assign the task.

**Mental model today.** The review surface already has readable cards, inline
editing, and a proposal-only action. It should be the only place where meaning
is reviewed; admin should not absorb this job.

**What this persona needs to see.**

- the triggering question and user intent;
- current Git meaning, proposed meaning, exact diff, and observed evidence;
- finding type, severity, freshness, confidence, and affected concepts;
- durable dispositions such as defer, duplicate, needs evidence, or assign;
- a durable PR/proposal state independent of browser storage;
- no action that implies Hyperset approved or merged the definition.

**Success signals.** Time to first confident decision; reviewer agreement on
whether a proposal is safe; percent of proposals with evidence and owner;
proposal-to-PR success; low rate of duplicate or reverted proposals.

**Anti-goal.** Do not bury the reviewer in raw JSON or make them manage runtime
secrets and users before they can judge meaning.

### 3.3 Admin / context steward

**Archetype.** The person responsible for making one Hyperset workspace safe,
usable, and correctly connected for other people. This persona includes the
context steward and the person who manages users and roles.

**Trigger.** “Is the workspace ready, is the right context being served, and
who is allowed to use or review it?”

**Top tasks.**

- inspect readiness, sync, connector, and model state;
- add or sync governed Git context sources;
- manage users, roles, reviewer routing, and workspace access;
- configure MCP/API connections, notifications, and write-back target;
- test credentials without exposing secrets;
- understand which user experiences are degraded by a dependency;
- export a redacted diagnostic bundle for an engineer.

**Mental model today.** The current `/admin/` route is already a distinct
settings surface, while the public Playground exposes the regular-user tools.
The main UX problem is discoverability and boundary placement: settings should
be behind a protected profile menu and should combine context stewardship with
user management.

**What this persona needs to see.**

- a protected `/admin/` entry that is not part of the regular-user primary nav;
- a readiness overview with owner and recovery for every blocker;
- context sources, users/roles, connections, notifications, and write-back under
  one settings IA;
- secret-source explanation, rotation state, and permission labels;
- a proposal lifecycle card showing that Hyperset proposes but does not merge;
- an obvious handoff into Review without making Admin responsible for meaning.

**Success signals.** Time to identify a blocked dependency or missing user;
successful context sync; no secret exposure; correct reviewer routing; fewer
context switches to CLI for routine setup.

**Anti-goal.** Do not put Admin in the primary Playground nav or turn the admin
into a semantic approval screen.

### 3.4 Explorer / analyst or agent user

**Archetype.** A person asking a business question, or an agent acting on that
person's behalf, who needs a useful answer and enough provenance to decide
whether to trust or investigate it.

**Trigger.** “What is the answer, which definition was used, and what remains
uncertain?”

**Top tasks.**

- ask a natural-language question;
- choose or confirm a domain/context when needed;
- understand whether the answer is governed, observed, stale, conflicting, or
  unresolved;
- inspect evidence without reading a raw trace;
- refine a question or open a review task when context is missing;
- stop and recover from slow/unavailable work.

**Mental model today.** The explorer sees a friendly prompt wrapped around
developer controls. The product must make the trust state as legible as the
answer itself.

**What this persona needs to see.**

- one clear question path;
- context selected, exact commit/version, and governing definition;
- evidence source and freshness;
- explicit qualifiers and unresolved assumptions;
- next actions for no-match, stale, conflict, or missing-context cases;
- visible elapsed time and terminal state for slow work;
- advanced diagnostics only when requested.

**Success signals.** Time to first useful answer; percent of answers with
understandable provenance; successful recovery from ambiguity; low rate of
answers misread as governed when they are observed-only; user confidence after
answer review.

**Anti-goal.** Do not imply that “governed only” means “always complete” or that
an answer is approved merely because a model produced it.

## 4. Secondary persona blueprints

### Agent/runtime integrator

Needs copyable HTTP/MCP examples, contract versions, deterministic behavior,
bounded directives, and provenance-preserving bundle output. They should not
need the browser's chat UI to verify integration. The product should expose a
small integration console with request replay and response inspection, while
keeping the same trust surface as REST and MCP.

### Context author / analytics engineer

Needs a Git-native authoring loop: manifest and context guidance, validation,
locked eval cases, owner metadata, and a reviewable PR. Hyperset can help draft
or propose but should not become the canonical editor of governed meaning.

### Connector/data steward

Needs connection health, observed asset freshness, sync history, drift
classification, and clear separation between observed evidence and governed
definitions. Their primary action is repair/sync/escalate, not edit semantics in
the browser.

### Security/compliance observer

Needs evidence that secrets are write-only or externally referenced, proposal
operations are scoped, no merge authority exists in Hyperset, and operational
events can be audited. They need readable controls and exportable diagnostics,
not a chat transcript.

### Evaluation / quality engineer

Needs reproducible questions, baseline/no-context comparison, context-selected
comparison, trace stages, deterministic scores, and fixture/version identity.
The current Agent Evaluator and harness views are the seed of this experience,
but should be clearly labelled as developer/quality tools.

### Team lead / decision owner

Needs a read-only rollup of unresolved ambiguity, source freshness, proposal
throughput, trust incidents, and answer coverage. This is a future reporting
surface, not a reason to overload the v0 admin or explorer UI.

## 5. Responsibility and permission model

| Action | Explorer | Context reviewer | Admin / context steward | Git owner / agent |
| --- | ---: | ---: | ---: | ---: |
| Ask a question in Chat | Owns | Can use | Can use | Agent can execute |
| Browse catalog and context graph | Owns | Needs evidence | Operates sources | Agent can consume |
| Connect MCP / read integration docs | Owns | Can use | Configures workspace policy | Agent consumes bundle |
| Create a review task | Requests explicitly | Owns | Can create/route | Agent can draft + notify |
| Edit a proposed definition | No | Owns draft refinement | Can configure target | Agent drafts only |
| Add/sync a Git context source | No | No | Owns | Service action |
| Manage users and roles | No | No | Owns | No |
| Propose PR from Hyperset | Can request | Owns proposal action | Configures permission | Agent only if policy-gated |
| Approve/merge Git PR | No | No by default | No by default | Git owner owns |
| Inspect runtime diagnostics | Opt-in | Opt-in | Owns | Agent telemetry |

The product should make this table true through labels, routes, and action
availability. The regular Chat should not expose Admin controls, and the
Admin route should not become a second reviewer queue. A disabled action should
say which role or system state is missing; it should not simply disappear when
the action is important to the user's mental model.

## 6. End-to-end service blueprint

### Happy path: explorer to governed answer to review proposal

```text
Explorer        open Chat → ask question → inspect context bundle/provenance
System          discover → resolve → answer with provenance
Explorer        discover missing meaning → request review
System          create review task with triggering question and evidence
Reviewer        compare draft/current meaning → refine → propose PR
Git/owner       review checks → human merges authoritative change
System          sync new commit → answer now cites the new version
```

### Where the current service breaks

| Journey point | Current break | Needed service behavior |
| --- | --- | --- |
| Preflight | No joined dependency readiness | Report each dependency and recovery action before boot. |
| Boot | Long `make up-demo` has no product-visible timeline | Stream named stages and end with a machine-readable summary. |
| Verify | Browser opens before semantic capabilities are clearly ready | Show “ready for governed answers” only after API/model/context checks pass. |
| Explore | Discovery can look stuck | Show phase, elapsed time, timeout, stop, retry, and cause. |
| Evidence | Trust is distributed across trace/debug views | Put provenance and evidence in the answer shell. |
| Review creation | Empty queue cannot teach the loop | Provide a seeded walkthrough or explicit “how to generate a review task”. |
| Propose | Browser-local state tracks proposal | Persist proposal status and PR linkage in the service. |
| Merge/sync | The handoff is outside the product with no return signal | Show external PR state and the synced commit when it becomes authoritative. |

## 7. Persona-specific “what good looks like” checklist

### Engineer

- [ ] Clone-to-first-success has one canonical path.
- [ ] Every prerequisite has a preflight result and recovery command.
- [ ] Host/container path rules are shown where a path is requested.
- [ ] Seeded context source, commit, and sync status are visible.
- [ ] Known blocked commands and intentional boundaries are documented beside
  the commands that remain supported.

### Admin

- [ ] One page answers whether the deployment is safe to use.
- [ ] Every operational state has freshness and a next action.
- [ ] Write-back configuration has test/dry-run and secret-source clarity.
- [ ] Admin can hand off a diagnostic bundle without exposing secrets.
- [ ] No admin action implies merge or semantic approval.

### Reviewer

- [ ] Every draft has question, evidence, current meaning, proposed diff,
  ownership, and freshness.
- [ ] Queue state is durable and consistent across users/browsers.
- [ ] Propose state shows PR lifecycle and next action.
- [ ] Duplicate/defer/needs-evidence outcomes are explicit.
- [ ] Git remains the approval and merge authority.

### Explorer

- [ ] First screen is a clear question path, not a debug menu.
- [ ] Every answer states governed/observed/unresolved status.
- [ ] Commit/version and evidence are one click away.
- [ ] Slow and failed work has an understandable terminal state.
- [ ] Missing context leads to a review task or refinement path.
