# ChatGPT reference study and adversarial review

Status: incorporated into the proposed end-of-v1 mockups
Date: 2026-08-16
Reviewers: Hubble and Linnaeus

This document records the external reference study and the two adversarial
passes requested before the revised vision was produced. It is a design input,
not a claim that Hyperset should copy ChatGPT's product or brand.

## Reference study

The public logged-out ChatGPT surface was inspected on 2026-08-16 at
`https://chatgpt.com/` and `https://chatgpt.com/auth/login`.

The useful interaction patterns were:

- one persistent left rail with New chat, Search chats, recent work, and low-
  priority Settings / Help;
- a sparse home centered on one composer instead of a dashboard of feature
  cards;
- a real chat transcript as the primary working surface, with the composer
  remaining visually dominant;
- a centered, single-purpose OIDC login flow with clear legal copy and an
  explicit loopback-demo auth bypass, never a local credential provider;
- a simple top content title inside the working surface rather than a second
  global navigation system;
- advanced or account actions behind the profile/workspace affordance.

Hyperset adopts the interaction shape, not the scope. Hyperset hosts a governed
analyst thread; it is not a general-purpose agent platform. The user should see
the trust state and authority boundary, while model/runtime controls remain in
protected admin or developer views.

## Adversarial review 1 — Hubble

Hubble was asked to review the current proposal as a skeptical product/design
reviewer using the ChatGPT reference above. The reviewer identified:

1. Home was still a routing dashboard with a shell, workspace badge, status,
   heading, copy, and multiple actions. The fix is a composer-led Home with
   MCP/docs and graph links as quiet secondary actions.
2. The global top navigation and the chat conversation sidebar duplicated
   navigation. The fix is one persistent rail across the product.
3. Regular Chat exposed agent/model selectors, governed toggles, asset tagging,
   evaluator surfaces, and runtime controls. The fix is one read-only “Hyperset
   analyst” identity in Chat; reviewer testing is disclosed and developer
   diagnostics stay out of the regular path.
4. Thread continuity, search, and context handoff were not concrete enough. The
   fix is durable recent-thread navigation plus explicit “Search context” and
   “Use this bundle in chat” handoffs.
5. Reviewer comparison was not decision-ready. The fix is current-vs-proposed
   meaning, semantic delta, provenance, freshness, preview results, and one
   proposal action.
6. Login was a two-column invite/auth page. The fix is a centered auth flow;
   invite context is a small message and the original deep link is preserved.
7. The visual language was still too card/pill/telemetry heavy. The fix is
   flatter surfaces, dividers, fewer status chips, and navy used for action or
   authority rather than decoration.
8. Loading, stale, blocked, offline, permission, and server-error states need
   explicit designs rather than empty results or misleading “connected” copy.

## Adversarial review 2 — Linnaeus

Linnaeus was asked to challenge the proposal from an engineering onboarding,
governance, and trust-boundary perspective. The reviewer identified:

1. Explorer needed durable thread continuity and a searchable history, not only
   local component state.
2. Bundle search could be mistaken for selecting authority. The fix is to call
   it Search context, explain that discovery is a hint, and show repository/ref/
   commit/snapshot after resolution.
3. Search failures must be distinct from valid empty results. The product needs
   loading, empty, offline, permission, and server-error states with Retry that
   preserves the query.
4. Reviewer editing needed a safe proposed-context preview before saving or
   proposing. The fix is an ephemeral overlay with representative questions,
   regression checks, semantic delta, and a persistent “draft only / not
   serving” statement.
5. The mutation boundary was unclear. “Save” must not look like it changes
   serving authority; the only authority-changing path is a merged Git commit
   followed by a validated sync.
6. Admin labels alone are not an authorization boundary. The fix is verified
   identity/role context and a protected operational destination with sections
   for authority, connections, syncs, health, environment, and recovery.
7. “Settings” should be reserved for personal preferences where possible;
   operational configuration should be called Admin / workspace or Operations.
8. Queue empty states must explain whether there are no candidates, whether the
   processor is healthy, and when the queue was checked.

## What changed in the mockups

| Review finding | Revised artifact | Result |
| --- | --- | --- |
| Duplicate navigation | `mockups/v1/home-role-router.html`, `playground-chat-thread.html`, reviewer/admin pages | One role-aware left rail; no global top nav plus local sidebar |
| Home bloat | `mockups/v1/home-role-router.html` | One centered composer; MCP/docs and graph are secondary links |
| Chat looked like an agent console | `mockups/v1/playground-chat-thread.html` | Hyperset analyst is the only regular-user identity; reviewer tooling is disclosed |
| Context handoff was ambiguous | `mockups/v1/context-explorer.html`, Chat | Search and full graph are distinct; selected bundle has an explicit “Use this bundle in chat” action |
| Reviewer authority boundary | `mockups/v1/reviewer-task-and-github.html` | Current vs proposed meaning, “Draft only · not serving,” evidence, freshness, and one next step |
| Safe review testing | `mockups/v1/reviewer-context-preview.html` | Same-question comparison, semantic delta, checks, and proposal handoff without changing serving authority |
| Login did too much | `mockups/v1/login-and-invite.html` | Centered, single-purpose OIDC auth with small invite context and explicit loopback-demo bypass |
| Admin too visible / too vague | `mockups/v1/admin-overview.html` plus admin detail pages | Admin is a protected workspace destination with verified identity and operational readiness |
| Visual noise | `mockups/v1/mockup.css` | Flatter shell, lower radius, reduced decorative state, black/white/dark navy emphasis |

## Remaining implementation obligations

These are requirements for the product build, not reasons to add more visible
UI to the regular-user shell:

- Persist thread IDs and searchable history server-side.
- Make context selection a side sheet or contextual action from a thread; keep
  the full graph as a separate read-only deep dive.
- Return distinct API/MCP states for loading, empty, offline, permission,
  server error, no-match, stale, observed-only, conflict, and timeout.
- Enforce role and workspace authorization server-side before rendering or
  executing admin/reviewer mutations.
- Make proposed-context preview use an ephemeral overlay and prevent it from
  changing the serving snapshot.
- Open a single GitHub PR containing diff, evidence, preview results, source
  commit, and a backlink; sync only after merge and validation.
- Route Slack notifications to the same task and PR URLs, with explicit
  assignment, escalation, and sync-failed events.

## Adversarial review pass 2 — screenshot and run configuration

The screenshot of the Chat thread was reviewed again by Hubble and Linnaeus.
Both agreed that the previous cleanup removed too much of the runtime contract:
the response warning was oversized, the composer controls were visually
detached, and there was no visible path to choose an agent, model, or context
policy.

The second-pass conclusions were:

- Keep the response trust treatment compact for normal answers, but never hide a
  blocker, no-match, conflict, or abstention. The next action must remain
  visible.
- Do not expose unrestricted raw agent profiles in regular Chat. Show a curated
  analyst choice only when there are multiple meaningful workspace profiles;
  otherwise show the workspace analyst as read-only. Agent authoring remains an
  Admin/developer job.
- Put agent, provider/model, and context policy in one calm **Run settings**
  popover in the Chat header. Do not put bundle search in that popover.
- Use explicit policy copy: `Governed only — Answer from Git-owned context.
  Default.` and `Governed + observed — Include connected-system evidence; it
  may be stale or conflict.`
- Scope Run settings to the next message in the current thread. Preserve the
  selected agent, provider/model, requested policy, effective trust state,
  bundle ID, and authority commit on every response.
- Remove telemetry phrases such as “Read-only, governed, observable” and
  “Testing only” from the regular composer. The reviewer preview belongs in the
  Review route.

The revised Chat mockup implements that second pass in
`mockups/v1/playground-chat-thread.html` and `mockups/v1/mockup.css`.

Implementation note: the reusable chat component already sends the selected
agent, provider/model, and `governed_only` value with each streamed request.
The UX change moves those controls from the detached composer toolbar into the
header popover. The next implementation pass should make the backend policy an
explicit enum with a fail-closed `governed_only` default, while preserving the
existing per-request snapshot semantics.
