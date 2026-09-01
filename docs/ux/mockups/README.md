# UX mockups

The prototype in this folder is a static, self-contained wireframe for the
proposed Hyperset experience. It does not call the running API and does not
represent a production implementation. Its purpose is to make the information
architecture and state vocabulary reviewable before UI code changes begin.

Open [hyperset-ux-prototype.html](hyperset-ux-prototype.html) for the earlier
composite overview. The current page-level proposal is organized around the
three real human personas, a quiet Home, and a hosted Chat surface:

| If you are… | Choose… | Your first job |
| --- | --- | --- |
| General question asker / Explorer | `Chat` | Use a real thread to ask questions, inspect the trust state, and test a context update when needed. |
| Context reviewer | `Review` from an assignment or reviewer account | Read why a task exists, inspect evidence and diff, then disposition or propose a PR. |
| Admin / context steward | Profile menu → `Admin / workspace` | Manage context sources, users, integrations, readiness, and policy. |

Regular-user navigation prioritizes the actual jobs:

- Get started — connect MCP, read docs, and understand the HTTP/MCP contract;
- Home — a quiet route to the three useful next actions;
- Chat — the hosted agent thread with advanced context-testing tools behind an
  explicit disclosure;
- Explore — context-bundle search, graph, provenance, and source history;
- Review — visible to reviewers or reached from an assigned task;
- Admin / workspace — hidden behind the profile menu and protected by
  role.

The intended Explorer path is: **Home → Chat → inspect trust → Search context →
Use this bundle in chat**. The intended MCP path is: **Get started → connect MCP
/ read docs → test → return to Chat**. The intended admin path is:
**profile menu → Admin / workspace → context + users + policy**.

In the current product, these map to `/playground/` for the Explorer chat and
advanced tools, `/playground/graph/` for graph exploration, `/review/`
for reviewers, `/admin/` for the protected Admin / workspace surface, and the
HTTP/MCP endpoints plus docs for Get started. The page-level mockups make Admin
hidden in the regular-user shell because settings is a protected workspace job,
not a fourth public persona.

The mockup deliberately shows warning and abstention states rather than only a
green happy path. Those states are the product's trust model made visible.

## End-of-v1 page set

The composite prototype above is useful for reviewing the overall navigation.
The page-level review should use the standalone mockups in [v1](v1/README.md):
each file represents one page, one primary persona, and one primary handoff.

| Page mockup | Primary persona | Core decision or handoff |
| --- | --- | --- |
| [Explorer home](v1/home-role-router.html) | General question asker | Can I choose the right next action without seeing internal machinery? |
| [Playground chat thread](v1/playground-chat-thread.html) | General question asker / reviewer | Can I ask a question, understand the trust state, and test a context update? |
| [Login and invite](v1/login-and-invite.html) | Any authenticated user | Can I resume the exact deep link after sign-in? |
| [Context bundle explorer](v1/context-explorer.html) | Explorer / reviewer | Can I search for a bundle and inspect its full graph? |
| [Admin overview](v1/admin-overview.html) | Workspace admin | Is the workspace ready, and who owns the blocker? |
| [Context sources](v1/admin-context-sources.html) | Context steward | Which Git commit is being served? |
| [Admin users and roles](v1/admin-users.html) | Admin / context steward | Who can explore, review, or administer? |
| [Connections and models](v1/admin-connections-and-models.html) | Operator | Which dependency is ready, degraded, or stale? |
| [Write-back settings](v1/admin-writeback-settings.html) | Admin / Git owner | What may the agent do, who is notified, and who approves? |
| [Reviewer queue](v1/reviewer-queue.html) | Domain reviewer | What needs a human decision now? |
| [Reviewer task and GitHub proposal](v1/reviewer-task-and-github.html) | Reviewer / Git owner | Can this evidence-backed diff become a PR? |
| [Reviewer context preview](v1/reviewer-context-preview.html) | Domain reviewer | Does the proposed meaning improve the answer without changing authority? |
| [Reviewer notifications](v1/reviewer-notifications.html) | Reviewer | How do Slack, Hyperset, and GitHub divide responsibility? |
| [MCP setup wizard](v1/mcp-setup-wizard.html) | New integrator | Can I connect a client and complete one governed call? |
| [API / MCP integration](v1/api-mcp-integration.html) | Developer | How do discover, resolve, validate, and abstention fit together? |
| [Evaluator / maintainer](v1/evaluator-and-maintainer.html) | Maintainer | Which stage failed, what is the impact, and how do I recover? |

The accompanying [v1 page map and persona flows](../v1-page-map-and-persona-flows.md)
contains the Mermaid diagrams, authority model, permission matrix, notification
contract, and end-of-v1 acceptance checklist. The page files are static HTML
fragments with a shared, local stylesheet; they do not call the running API.

The visual reference and two adversarial reviews that shaped the latest pass are
captured in [chatgpt-reference-and-adversarial-review.md](../chatgpt-reference-and-adversarial-review.md).

To preview one locally with the shared stylesheet loaded, serve the folder as
static files:

```bash
cd docs/ux/mockups/v1
python3 -m http.server 8766
# open http://localhost:8766/reviewer-task-and-github.html
```
