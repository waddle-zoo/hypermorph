# 0038: Role-aware surface navigation and focused operational views

Status: Accepted (2026-08-22). This records the current local v0 product
direction; authorization remains a server-side concern and is not implied by
client-side navigation visibility.

Extends [ADR-0009](0009-vertical-slice-first.md)'s vertical-slice rule and
complements [ADR-0030](0030-the-authorization-boundary.md)'s authorization
boundary. It documents the shell implemented in the local Docker playground,
not a visual mockup or a claim that every v1 backend capability is complete.

## Context

The frontend had accumulated several parallel navigation models: a top-level
shell, hidden Review and Settings destinations, empty diagnostic tabs, and a
large Settings page that rendered unrelated operational panels together. The
result made working features hard to find and made unfinished capabilities look
like promised product areas. Documentation linked to GitHub instead of serving
the local getting-started material from the product.

The chat surface has the same information-density risk. Raw SQL and MCP/tool
transcripts are useful diagnostics, but they should not compete with the answer
or be mistaken for the assistant's response. Advanced detail must remain
available without becoming the default reading order.

## Decision

1. **Keep the three product surfaces first class.** The user shell exposes
   Home, New chat, Explore context, Recent threads, Docs, Help, Profile,
   Review, and Settings. Review and Settings are visible from Playground so a
   user does not need to guess that they exist. Server-side authorization still
   gates protected operations; navigation visibility is not permission.
2. **Use one compact Playground dropdown for secondary views.** The root
   Playground selects **Live chat** and the dropdown exposes the available
   diagnostic/exploration routes, including the context graph. The selector is
   native and URL-backed so it is keyboard accessible and deep-linkable.
3. **Make Settings a focused, URL-addressable view.** Settings uses one native
   dropdown with Readiness, Connections, Context sources, Audit trail,
   Configuration, and Write-back targets. Selecting a tab navigates to
   `/admin/<tab>/` and renders that panel as the page's primary job; it does not
   pretend the other panels are implemented by rendering an empty stack.
4. **Serve product documentation locally.** The Docs destination uses the
   packaged repository documentation through the local `/playground/docs/`
   route, with a getting-started entry point. It is not merely a link to the
   GitHub repository.
5. **Keep chat answer-first.** The assistant answer is the primary stream.
   SQL, provenance, and tool/MCP activity are collapsed disclosures or compact
   work summaries, not a second transcript pinned below the answer. Context
   tokens remain editable with normal keyboard behavior, including Backspace.
6. **Keep the knowledge graph discoverable, but do not conflate it with
   Settings.** Explore context remains a product destination and the graph is
   reached through the Playground view selector. Native knowledge-graph
   authority and write-back adapters remain governed by [ADR-0036](0036-bring-your-own-knowledge-graph-authority-adapters.md)
   and are not represented as shipped functionality by this shell decision.

## Consequences

- Users can move between the working surfaces without relying on hidden profile
  menus or empty top-level tabs.
- The URL is the state for Playground and Settings subviews, which makes
  browser refreshes and deep links predictable.
- Settings has a smaller cognitive footprint and a single primary job at a time.
- Diagnostics remain available for debugging without making raw implementation
  detail the chat experience.
- The shell still needs a future role/tenant-aware navigation policy once
  production identity and authorization are wired. Until then, the UI must
  describe unavailable behavior honestly rather than implying that a visible
  route is operational.

## Rejected alternatives

- Hiding Review and Settings behind a profile menu: discoverability was poor in
  the local product and contradicted the actual available routes.
- Rendering every Settings panel at once: it made the page bloated and made
  incomplete features look functional.
- Keeping Playground views as a row of empty tabs: it consumed space without
  communicating what each route did.
- Treating the HTML mockup as the implementation contract: mockups are visual
  references; the existing working surface, backend contracts, and browser
  behavior are the source of truth.
- Linking Docs to GitHub: local Docker users need an in-product onboarding path.

## Verification gate

The shell change is accepted for this local v0 slice when the UI unit tests,
production build, static route contracts, and browser checks cover:

- Playground root, Playground view selection, Review navigation, and Settings
  navigation;
- focused Settings panels for each dropdown option;
- local Docs routing and packaged documentation;
- answer-first chat disclosures and editable context tokens; and
- no browser console errors on the exercised routes.
