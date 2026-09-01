# 0022: Natural-language selection precedes exact governed resolution

Status: accepted.

Extends ADR 0016 and ADR 0019 without weakening either. It amends one sentence
of ADR 0020 decision 1: the reference planner path is no longer benchmark-only;
it is a supported product integration, while the model runtime and broader
agent loop remain customer-owned and replaceable. The exact resolver
still does not interpret a question, and assist output still cannot create
identity or authority. This ADR decides what must happen immediately before
that resolver so the exact contract is usable by people who do not already
know an internal domain name.

## Context

The V0 trust kernel is intentionally exact. A `ContextDirective` names a Git
domain and the concept terms the answer needs; the resolver verifies exact set
membership and compiles governed instructions from the pinned commit. That is
the right authority boundary.

It is not a complete user path. The currently served catalog is positionally
paged and internally capped. The planner must copy exact identifiers from it,
and one bundle resolves one domain. With one revenue domain this proves the
resolver. It does not prove that an MCP agent can choose the right context for
an ordinary question in a realistic estate.

GitHub #70 already assigns semantic interpretation to a real model and keeps
truth in deterministic code. GitHub #126 (after the bounded V0 work split to
#206) identifies the remaining scaling
failure: a flat capped catalog can hide the relevant domain or concept from the
model. The V0 benchmark must test the first boundary before it hardens the
second one as a V1-only concern.

## Decision

### 1. The exact resolver remains the governance kernel

`resolve_analytics_context` continues to retrieve only what an exact
`ContextDirective` names. It does not read the question, expand synonyms,
score similarity, or choose the nearest domain. Exact Git membership,
source-native identity, pinned versions, provenance, and plan validation remain
deterministic.

### 2. V0 includes a supported natural-language selection path

A real lightweight planner receives the user's question, inspects bounded
discovery candidates, and produces the exact directive consumed by the
resolver. Hyperset supports the path through its narrow runtime adapters and a
reference skill/client; it does not require customers to implement the prompt
and retry rules independently for every MCP integration.

This is a supported integration path, not a Hyperset-owned agent framework.
The model, SDK, prompts, and final analysis loop remain replaceable, consistent
with ADR 0020 as amended above.

### 3. Semantic discovery is assist-class and cannot confer authority

Discovery may rank domain and concept candidates by relevance to the question.
Every candidate is labeled derived/non-authoritative and discloses the signals
that ranked it. Discovery cannot emit governed instructions, create a
source-to-context identity link, populate `provenance_refs`, or move a governed
verdict.

The planner chooses among candidates and sends exact names back through the
existing resolver. A relevance score is evidence about where to look, never
evidence that a business definition is approved.

### 4. Embedding providers are deployment choices, not product authority

Semantic discovery uses a provider-neutral `EmbeddingProvider` boundary. V0
ships with a pinned local-safe implementation and a deterministic test double;
deployments may instead configure OpenAI, Cohere, an OpenAI-compatible endpoint,
or another adapter without changing the discovery or resolver contracts. No
customer context is sent to a hosted provider unless an administrator selects
and configures that provider explicitly.

Every derived index version records the provider, exact model identifier,
dimensions, input-projection version, source-text hash, and Git snapshot/commit
that produced it. Changing any incompatible value creates a new index version;
vectors produced by different model spaces are never mixed. Secrets are
referenced through the deployment's secret-provider boundary and never stored
in the context or candidate payload.

Provider choice may change candidate ranking quality, latency, cost, and data
residency. It cannot change exact membership, identity, provenance, governed
instructions, or validation. Candidate and trace metadata disclose the index
version and embedding model that produced the ranking so evaluations remain
reproducible.

### 5. Split semantic retrieval by proof, not by architecture

The V0 slice of GitHub #206 (split from #126) is limited to domain/concept
selection over a bounded multi-domain corpus. It exists to prove that ordinary wording can
reach the correct governed slice and that ambiguity/no-match produces
abstention.

V1 retains estate-scale indexing and retrieval over large asset, document,
lineage, and source corpora. It also owns hosted-provider hardening, batching,
rate limiting, re-embedding, and atomic index activation. Candidate-source
ranking, uncovered joins, cross-domain composition, reasoning-assist
validation, and result trust remain V1 work under ADR 0019.

### 6. Selection is release evidence, not a demo assumption

Before V0 closes, the governed benchmark must include:

- at least three plausible governed domains;
- hidden paraphrases that do not contain configured domain names;
- ambiguous and true no-match questions;
- the real pinned model/runtime rather than a scripted directive;
- critical deterministic scoring of the selected domain and concepts;
- trace evidence for candidates, the exact directive, retrieved versions, and
  abstention or expansion decisions.

A scripted runtime remains appropriate for tool-loop and refusal unit tests. It
cannot prove semantic selection.

## Consequences

- `ContextDirective` becomes the typed handoff between reasoning and
  governance, not the first abstraction a normal user must understand.
- GitHub #70 and the bounded V0 slice of #206 block the benchmark claim in #25.
- #25 then blocks invalidation, clean-checkout proof, release gates, and the V0
  umbrella (#33, #34, #36, #42).
- The governed bundle stays byte-identical whether its directive was written by
  a person, a planner reading the small catalog, or a planner using semantic
  candidate discovery.
- Enterprises can select a hosted or local embedding provider without creating
  a provider-specific context or resolver contract.
- No alias table, regex router, stemming system, or hand-tuned keyword scorer is
  introduced as a substitute for model reasoning.

## Rejected alternatives

- **Require callers to know the domain name.** Safe as an expert escape hatch,
  but not the product path promised to ordinary users.
- **Let the resolver infer the domain.** Mixes probabilistic relevance with the
  authority boundary and makes a wrong guess look governed.
- **Move all semantic retrieval to V1.** Allows V0 to pass while proving only
  that a model can consume a bundle somebody else selected.
- **Move the full estate search platform into V0.** Expands the slice before the
  bounded selection failure is measured and violates ADR 0009.
- **Build custom deterministic NLP.** Recreates model reasoning through aliases,
  regexes, and weighted keywords, the failure GitHub #70 already rejected.
