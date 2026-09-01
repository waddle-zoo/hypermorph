# Hyperset UX audit

Audit date: 2026-08-16
Repository baseline: `origin/main` at `f06267c`
Live surface tested: `http://localhost:8000/`

This folder defines the current setup contract, interaction gaps, personas, and
the product shape Hyperset should grow toward. It is written for the local
Docker playground and the three human product personas: Explorer / general
question asker, Context reviewer, and Admin / context steward. It also covers
MCP/docs onboarding and the engineer or maintainer job without promoting those
supporting jobs into extra top-level personas.

The UX folder is the product source of truth. The current frontend is an
implementation slice and should not be mistaken for the complete v1 shell:
Home, Login/invite, protected Admin, reviewer lifecycle, and the role
boundaries are defined here before they are implemented.

## Read in this order

1. [v1 product UX specification](v1-product-ux-spec.md) — the complete route,
   persona, state, authority, accessibility, and v1 exit contract.
2. [Agent usability and implementation guide](agent-usability-guide.md) — the
   operating rules and PR checklist for engineers and coding agents.
3. [Current setup and interaction audit](current-setup-and-interaction-audit.md)
   — what exists today, how a new engineer encounters it, observed UI behavior,
   and the gap inventory.
4. [Personas and service blueprints](personas-and-service-blueprints.md) — the
   role taxonomy, boundaries, primary jobs, and what each experience should look
   like.
5. [Human-centered flows and service blueprints](flows-and-service-blueprints.md)
   — concrete setup, API/MCP, admin, reviewer, explorer, and evaluation
   journeys.
6. [Recommendations and roadmap](recommendations-roadmap.md) — prioritized
   changes, acceptance criteria, and a role-based UX scorecard.
7. [Interactive mockups](mockups/README.md) — the composite wireframe plus the
   page-level v1 mockups.
8. [v1 page map and persona flows](v1-page-map-and-persona-flows.md) — the
   ideal end state, page catalog, Mermaid diagrams, Slack/GitHub review loop,
   MCP onboarding, permissions, and acceptance checklist.

## Executive readout

Hyperset has a coherent trust model: Git owns governed meaning, deterministic
resolution returns versioned context, observed systems provide evidence, and a
human merges any proposed Git change. The product UI explains that boundary
better than many early-stage tools do.

The main weakness is not the trust model; it is the path through it. The current
experience still feels like a developer test harness wrapped around a powerful
kernel. An engineer must understand Docker, Compose profiles, hosted model
credentials, the container-visible Git path, context source IDs, and
several routes before the first useful question. Once inside the product, the
admin surface is mostly a write-back form, the reviewer surface has no useful
empty-state path when the queue is empty, and the explorer can remain in a
long-running stage without a clear terminal-state contract.

### Highest-priority gaps

| Priority | Gap | User consequence |
| --- | --- | --- |
| P0 | No end-to-end readiness and preflight experience | Engineers cannot tell whether Docker, API, database, OpenAI/Luna, embedding, connectors, and seeded context are all ready. |
| P0 | Explorer turns lack a visible timeout, phase detail, retry, or failure classification | A question can look stuck even though the system is still working; users do not know whether to wait, stop, or fix setup. |
| P0 | Review proposal state is browser-local | A proposal can look different across browsers or after backend changes, undermining reviewer trust in queue state. |
| P1 | Admin, runtime operations, and developer diagnostics are split into an unclear information architecture | Admins cannot operate the deployment from the place labelled Settings; developers and end users see adjacent concepts without clear role framing. |
| P1 | Evidence, provenance, freshness, and observed-versus-governed comparison are not first-class in the main user flows | Explorers and reviewers have to infer why a result is trustworthy and what action is safe. |
| P1 | API/MCP responses are contractually strong but not human-operable enough | Integrators need a discover → resolve → validate walkthrough, response anatomy, and safe abstention guidance before writing code. |
| P1 | Async and accessibility semantics are inconsistent | “Streaming” can describe health rather than active work; loading can look empty; dynamic progress and dialogs need stronger semantics. |

## Evidence basis

The audit combines:

- source inspection of the setup and product contract in `README.md`,
  `Makefile`, `docker-compose.yml`, and `playground/ui/`;
- direct browsing of `/`, `/playground/`, `/playground/environment/`,
  `/review/`, and `/admin/` in the running local app;
- a live explorer turn that was allowed to run, then stopped after the UI
  remained in a discovery stage for approximately 16 seconds;
- a direct inspection of the route, state, and persistence behavior in
  `playground/ui/src/main.jsx` and the layout rules in
  `playground/ui/src/styles.css`.

Seven scoped parallel research prompts were requested for onboarding, admin,
review, explorer, personas, accessibility, and cross-cutting product gaps. The
delegation service was saturated at first, then returned several completed
reports. Their findings are used here as a second opinion and were checked
against the requested `origin/main` baseline where branch-sensitive. Direct
browser observations are labelled as such; delegated reports are not treated as
live evidence when they came from a nearby older checkout.

## Product boundary used in this audit

This audit treats Hyperset as:

- a local, governed context system for agents;
- a read-only observer of Superset/DataHub and other connected systems;
- a Git-owned semantic authority with human approval outside Hyperset;
- a resolver and validator that should make provenance and uncertainty legible.

It does not assume Hyperset should become a warehouse SQL editor, a Git approval
system, a general-purpose agent framework, or a production secrets-management
console. Those boundaries come from the repository's v0 foundation and ADRs and
are reflected in the recommendations rather than weakened by them.
