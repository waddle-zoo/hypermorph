# 0010: Prove a two-source governed drafting and evaluation loop

Status: accepted.

Extends ADR 0009 and supersedes its one-connector sequencing constraint.

## Context

The one-source walking skeleton proved useful boundaries, but it did not prove
the actual product: combining operational BI evidence with catalog/governance
evidence, helping humans document the result, and detecting when approved
claims stop working.

Superset and DataHub are complementary. Superset exposes datasets, charts,
dashboards, and analytical configuration. DataHub exposes catalog identity,
domains, ownership, glossary terms, and lineage. Neither source is approved
business truth merely because it contains metadata.

LLMs can help draft documentation from bounded evidence, but Anthropic's
published analytics experience warns that model-generated definitions are not
a substitute for a small human-owned context layer. Evaluation must measure
observable behavior and remain useful without model credentials.

## Decision

Hyperset v0 proves one shared revenue domain through this path:

```text
pinned Superset + pinned DataHub
  -> lossless observations and explicit cross-source links
  -> deterministic finding
  -> configurable server-side curator
  -> human review and approval through UI or Git
  -> typed revenue domain graph in ContextBundle
  -> context resolution + deterministic plan validation over HTTP/MCP
  -> small Ollama model vs raw-metadata baselines in Inspect AI
  -> dependency change, affected-case rerun, persisted failure
  -> one generic webhook notification
  -> Docker restart and replay
```

V0 uses the smallest source-specific adapters needed to prove the path.
Connector conformance is extracted only after both adapters expose a repeated
contract; a general connector SDK is not a v0 deliverable.

ADR 0011 supersedes the optional single-drafter scope with a configurable
server-side curator and dual UI/Git governance. Curator output remains a
proposal with exact evidence references and no approval capability.

Inspect AI is the v0 evaluation runner because it is open source and provides
composable tasks, datasets, solvers, and scorers. Hyperset retains ownership of
case-to-claim dependencies, stored attempts, stale state, and notifications.
Deterministic scorers are the release gate; model graders remain optional.

The primary product benchmark compares a pinned small Ollama model using
governed Hyperset with the same model using raw source/lake metadata. A release
evidence run also compares against a pinned frontier model using the identical
raw baseline. All arms receive the same controlled, read-only demo query tool.
Scorers check source choice, fetch correctness, result equivalence, required
rules, plan validation, provenance, and safe stale/no-match behavior.

The domain graph is a typed projection over normal Postgres records, not a new
database or authority. It makes approved relationships between domain,
concepts, sources, fields, joins, rules, checks, owners, and provenance
explicit enough for a small model to follow.

A deterministic scripted expert may drive the real proposal/review service in
tests. It is labeled as a test actor and cannot create an alternate approval
path. Human experts own reference answers and manually review representative
blind traces.

A changed observed or governed dependency marks affected cases stale. Critical
failures create a persisted review task and emit one generic webhook event.
Channel-specific notification integrations are deferred.

## Consequences

- Issues must target one revenue-domain sequence, not independent horizontal
  subsystems.
- Superset and DataHub each require a real pinned contract test.
- Cross-source linking requires native identity or explicit lineage evidence;
  a name match is only a reviewable candidate.
- Humans remain the only approval authority.
- A third connector, connector SDK, multi-agent curation, broader evaluation
  families, and notification channels wait until the full loop is green.
- The public agent contract remains `ContextBundle`; this decision adds no MCP
  resources. It adds one deterministic `validate_analytics_plan` operation
  because the benchmark requires a pre-execution fact check.

## Rejected alternatives

- Keep DataHub post-v0 and prove only Superset metadata.
- Treat DataHub domains, ownership, or glossary text as automatically approved.
- Let an LLM write directly to approved context.
- Build a proprietary evaluation framework.
- Add a graph database before the typed Postgres projection proves useful.
- Build email, Slack, and PagerDuty integrations before one persisted webhook
  event proves the invalidation loop.
