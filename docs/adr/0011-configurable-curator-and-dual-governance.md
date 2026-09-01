# 0011: Run a configurable curator and support UI or Git governance

Status: accepted.

Extends ADR 0010. Supersedes its optional single-drafter and minimal-review-UI
scope.

## Context

Hyperset's core leverage is not asking the consumer model to discover an
organization from raw metadata. A stronger server-side model should synthesize
bounded source evidence and expert knowledge into a compact domain pack that a
smaller model can follow reliably.

Open-source deployments must choose their own model and API. Teams also govern
analytics knowledge in different places: some need an approachable admin UI;
others require reviewed files in Git. Building independent UI and Git
lifecycles would create conflicting sources of approval.

## Decision

V0 includes one durable curator worker and one canonical `DomainPack`.

The worker consumes a version-pinned evidence packet and produces:

- typed domain graph nodes and relations;
- human-readable domain documentation;
- approved-source, field, join, grain, and filter proposals;
- deterministic fact-check proposals;
- candidate evaluation cases;
- exact source evidence references.

`CuratorModelProfile` configures adapter, base URL, model, secret reference,
prompt/instruction version, generation limits, and provider options. V0 ships
Anthropic and OpenAI-compatible adapters plus a documented adapter entry point.
No provider is part of the persisted domain contract.

The curation model is deliberately separate from the small Ollama model used
as the blind consumer benchmark. Curator-generated evals remain candidates;
independently human-owned held-out cases grade the product.

Humans govern the same `DomainPack` through either:

1. the admin UI; or
2. a configured Git repository/ref using `manifest.yaml`, `context.md`, and
   `evals.yaml`.

Both paths call the same application services. UI approval records the UI
reviewer and version. Git approval records repository, ref, commit SHA, mapped
author, pack hash, and explicit approval metadata. Git may instead be
configured to import commits as candidates.

Postgres remains runtime authority. Git is a versioned authoring/approval
transport. Both paths use expected-base versions and reject divergent
last-write-wins updates.

The v0 admin UI contains only:

- connection and sync health;
- domain curation inbox;
- evidence/provenance viewer;
- domain graph and document editor with change comparison;
- approve/reject/defer controls;
- evaluation/staleness status and notification replay.

## Consequences

- The curator is a required product component, while provider credentials are
  optional for deterministic CI.
- Model configuration and secrets stay deployment concerns; approved packs are
  provider-neutral.
- UI and Git cannot define different schemas, validation, or approval rules.
- V0 must prove lossless Git import/export and equivalent UI/Git decisions.
- The UI is a real curation control plane, not a BI or analytics frontend.
- Multi-agent curation, prompt marketplaces, GitHub-specific apps/webhooks,
  collaborative rich-text editing, and arbitrary workflow engines remain
  deferred.

## Rejected alternatives

- Use the small consumer model as the curator.
- Make one hosted model provider mandatory.
- Let curator output become approved automatically.
- Treat Git and Postgres as competing runtime authorities.
- Build separate UI and Git domain models.
