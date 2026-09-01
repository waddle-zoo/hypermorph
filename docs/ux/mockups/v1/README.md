# Hyperset v1 page mockups

These are standalone page-level wireframes for the proposed end-of-v1
experience. Each file has one root, one page surface, and one primary persona or
handoff. They are intentionally static: the goal is to make the information
architecture, state vocabulary, and authority boundaries easy to review before
implementation.

The surface rule is deliberately strict: one page, one job, one primary action.
The regular-user shell has one persistent left rail, a sparse composer-led Home,
and a focused Chat thread. Executive-facing defaults show only state, owner,
decision, and next action. Raw payloads, provenance, routing mechanics, and
diagnostics are disclosed on demand or kept inside protected Admin / workspace
pages. The context graph is the intentional exception: exploration requires the
whole graph, plus search, domain filtering, node selection, and a compact node
detail panel.

Read the accompanying [page map and persona flows](../../v1-page-map-and-persona-flows.md)
for the end-state definition and the Mermaid diagram for every page.

## Page set

| File | Use it to review |
| --- | --- |
| [home-role-router.html](home-role-router.html) | Explorer home with a quiet route to Chat, MCP setup, and bundles |
| [playground-chat-thread.html](playground-chat-thread.html) | Real chat thread, compact trust state, Run settings for agent/model/policy, and context handoff |
| [login-and-invite.html](login-and-invite.html) | OIDC login, loopback-demo auth bypass, invite acceptance, and deep-link resume |
| [context-explorer.html](context-explorer.html) | Context-bundle search with full graph, filtering, node selection, and provenance |
| [admin-overview.html](admin-overview.html) | Admin readiness and operational overview |
| [admin-context-sources.html](admin-context-sources.html) | Git-owned context configuration and serving commit |
| [admin-users.html](admin-users.html) | Protected admin user/role management and reviewer routing |
| [admin-connections-and-models.html](admin-connections-and-models.html) | Observed systems, agent runtime, and embedding readiness |
| [admin-writeback-settings.html](admin-writeback-settings.html) | GitHub target, reviewer routing, Slack, and agent permissions |
| [reviewer-queue.html](reviewer-queue.html) | Review task queue, ownership, urgency, and empty states |
| [reviewer-task-and-github.html](reviewer-task-and-github.html) | Evidence, draft diff, propose-to-Git, and GitHub handoff |
| [reviewer-context-preview.html](reviewer-context-preview.html) | Safe ephemeral comparison before a GitHub proposal |
| [reviewer-notifications.html](reviewer-notifications.html) | Slack connection, pings, escalation, and PR notifications |
| [mcp-setup-wizard.html](mcp-setup-wizard.html) | New-user MCP onboarding and first governed call |
| [api-mcp-integration.html](api-mcp-integration.html) | Human-readable HTTP/MCP contract and safe abstention |
| [evaluator-and-maintainer.html](evaluator-and-maintainer.html) | Smoke suite, sync health, issue ownership, and diagnostics |

## How the roles fit together

The page set is intentionally organized around three human personas:

```text
Regular user / Explorer
  ├─ Home → one composer → Chat
  ├─ Chat → ask a question → trust state → Search context
  └─ Explore → full context graph → Use this bundle in chat
Context reviewer
  └─ Assigned task → evidence → Preview draft → propose to Git → GitHub review
Admin / context steward
  └─ Profile menu → protected Admin / workspace → context / users / policy / readiness
```

The authority boundaries are visible on the relevant pages:

- Hyperset gathers evidence, explains provenance, drafts, and synchronizes.
- Slack routes attention and shows current notification state.
- GitHub is the approval and merge authority for governed meaning.
- External query runtimes execute queries after a consumer validates the plan.
- An agent never auto-approves or auto-merges in the safe v1 default.
- Admin is not a regular-user primary navigation item; it is a protected
  workspace/profile-menu destination.
- Review is visible to reviewer accounts or reached through an assignment/deep
  link, not presented as a default Explorer job.

## Preview

The fragments are easiest to inspect from a local checkout. The shared
stylesheet is [mockup.css](mockup.css). For a local preview with the shared
stylesheet loaded:

```bash
cd docs/ux/mockups/v1
python3 -m http.server 8766
# open http://localhost:8766/home-role-router.html
```
