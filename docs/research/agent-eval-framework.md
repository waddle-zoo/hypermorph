# Evaluation framework for Hyperset context effectiveness

> [!NOTE]
> **Status: current evaluation research for the local v0. Last verified
> 2026-07-25.** Hyperset owns its evaluation case, attempt, result, and
> provenance schemas. External evaluation libraries are optional adapters, not
> the system of record. Issue #25 is the implementation contract.

## Research question

How can Hyperset prove that governed context improves an AI client's analytical
behavior without building or prescribing one proprietary agent loop?

## Conclusion

The v0 evaluator should compare the same versioned cases under different context
configurations:

1. raw connected Superset observations;
2. approved governed Hyperset context;
3. controlled ablations such as stale context, missing guidance, conflicts, or
   proposed edits.

Correctness should be expressed as required outcomes, prohibited behavior, and
accepted alternatives—not one exact artifact list or tool trajectory.

Deterministic graders should be the required release gate. Model-based judges
may supplement them only with versioned rubrics and human calibration.

## What the evaluator is testing

Hyperset is not evaluating whether it can render a dashboard or act as the
customer's production SQL engine. It is evaluating whether context helps an
external client:

- identify the correct business concept;
- select an approved source or observed asset;
- avoid prohibited or deprecated sources;
- disclose conflicts, lifecycle state, and freshness;
- apply required filters, joins, caveats, or validations;
- distinguish observed evidence from approved meaning;
- return complete provenance;
- avoid unsupported claims;
- optionally produce an equivalent fixture-backed analytical result.

## Native evaluation objects

### EvaluationCase

A versioned specification containing:

- stable ID and domain;
- natural-language question or task;
- fixture/source snapshot;
- required context/evidence;
- prohibited context/evidence;
- accepted alternatives;
- required outcome predicates;
- order constraints only when ordering is semantically required;
- required caveats and provenance;
- optional fixture-backed result predicate;
- grader versions and severity.

Example:

```yaml
id: revenue-region-driver
version: 1
domain: revenue
question: Which source should I use for recognized revenue by region?
fixture_snapshot: revenue-v1

required_context:
  - recognized_revenue
required_assets:
  - finance_orders_daily
prohibited_assets:
  - raw_payments
accepted_context_alternatives:
  - [recognized_revenue]
  - [recognized_revenue, refund_timing_caveat]
accepted_paths:
  - outcomes: [approved_source_selected, freshness_disclosed]
required_order_constraints: []
required_caveats:
  - exclude_test_customers
required_provenance:
  - connector_snapshot
  - governed_context_version
  - review_decision
result_predicate: null
```

### EvaluationAttempt

One execution of one case with one client/configuration. Persist:

- case and configuration versions;
- exact observed and governed context versions;
- client type and version;
- request/response;
- retrieved artifacts and tool calls;
- externally supplied SQL/result references when applicable;
- latency, tokens, cost when available;
- errors, retries, and run seed;
- evidence/provenance links.

### EvaluationResult

Stores individual grader outcomes rather than only an aggregate score:

- grader ID/version;
- pass/fail/partial/not-applicable;
- actual and expected evidence;
- failure category;
- explanation;
- deterministic versus model-judged status;
- confidence/calibration metadata where relevant.

### Scorecard

Aggregates results by configuration, domain, case type, failure category,
connector version, and context version. It should preserve per-case details so a
high average cannot hide a critical prohibited-source failure.

## Client boundary

The evaluator should use a narrow client contract so the same case can run
against:

- deterministic in-process reference client;
- public Hyperset HTTP/MCP client;
- optional Claude integration;
- optional Codex integration;
- another enterprise agent later.

The evaluator provides the task and available context interface. The client may
have its own query tools. Hyperset must not assume it owns the complete model
loop.

## Required deterministic graders

### Context/concept selection

Check required context, prohibited context, accepted combinations, lifecycle
state, and exact version pinning.

### Source/asset selection

Check approved datasets/metrics, prohibited or deprecated sources, unresolved
links, and raw fallback disclosure.

### Freshness and review state

Compare claims with pinned source timestamps, sync completeness, context review
deadlines, and current/deprecated state.

### Conflict and caveat handling

Require disclosure of known alternatives, disputed definitions, mandatory
filters, join warnings, and limitations.

### Provenance completeness

Verify that selected context can be traced to source observations and a human
review decision. Incomplete evidence should fail or receive explicit partial
status.

### Unsupported-claim detection

Deterministic checks should flag claims such as:

- using a prohibited source;
- asserting freshness contrary to pinned metadata;
- claiming a query/result was validated when no executor ran;
- causal explanation without business-event evidence;
- claiming Hyperset enforced external RLS/security behavior;
- omitting required fallback or uncertainty disclosure.

### Optional fixture-backed result equivalence

For selected local cases, execute or compare SQL against deterministic fixture
data. This validates result semantics without making production query execution
a product requirement.

Use exact equality where appropriate, numeric tolerances where justified, and
order-insensitive comparison for unordered result sets.

## Multiple valid paths

Evaluation should grade semantic outcomes, not stylistic conformity.

A case can specify:

- required evidence or outcome;
- prohibited evidence or outcome;
- several valid context combinations;
- optional tools;
- ordering constraints only for operations where order changes meaning.

For example, two agents may both be correct if one retrieves the metric and its
linked caveat in separate calls while another receives them in one context
response.

Exact tool-sequence grading is appropriate only when the sequence itself is the
policy—for example, approval must occur before context becomes current.

## Model-grader policy

A model grader is useful for nuanced natural-language behavior that deterministic
predicates cannot capture adequately, such as whether a caveat was communicated
clearly without overstating certainty.

Requirements:

- version the rubric, prompt, model, and decoding configuration;
- calibrate against human-reviewed examples;
- record false-positive and false-negative observations;
- keep deterministic results visible separately;
- do not make a nondeterministic model grader the only required CI gate;
- rerun enough trials to characterize variance before setting a threshold.

"LLM-as-judge is unavoidable" is not a valid design principle. Use it only when
its incremental value is demonstrated.

## Current external framework landscape

### Ragas

Current Ragas documentation includes agent/tool-use metrics such as tool-call
accuracy, tool-call F1, and agent goal accuracy, plus SQL execution/equivalence
metrics. It is not accurately described as RAG-only.

Useful ideas:

- separate ordered tool accuracy from unordered tool-set correctness;
- goal/outcome grading;
- reusable metric interfaces.

Hyperset should not adopt Ragas as its persistence or case schema.

### DeepEval

DeepEval documents Pytest-oriented evaluation, tool correctness/tool use, and
MCP-related evaluation.

Useful ideas:

- test-runner integration;
- explicit tool-call expectations;
- local regression workflows.

Again, it is an optional adapter, not the source of truth.

### Arize Phoenix

Phoenix combines tracing, datasets, experiments, and evaluation with local or
self-hosted options. It may be useful for visual trace analysis or exporting
OpenTelemetry-compatible spans.

Hyperset still needs domain-specific context, review, and provenance graders.

### LangSmith

LangSmith provides hosted tracing, datasets, experiments, and agent evaluation.
It may be useful for teams already using its ecosystem, but the local v0 cannot
require a hosted service.

### OpenAI Evals and Graders APIs

Current official API documentation exposes Evals and Graders endpoints. OpenAI
has also announced lifecycle changes for specific AgentKit/hosted products, so
availability must be rechecked before building an adapter.

Hyperset should not depend on a hosted OpenAI evaluation product as its system of
record.

## Initial v0 suite

Create at least 20 deterministic revenue cases covering:

- canonical metric/source selection;
- multiple valid context combinations;
- mandatory test-customer filters;
- approved and dangerous joins;
- prohibited `raw_payments` use;
- conflicting recognized/net/gross revenue definitions;
- missing owner or description;
- stale source observation;
- expired context review;
- connector rename, deletion, and partial-sync behavior;
- unresolved relationship warnings;
- proposed context that causes regression;
- provenance completeness;
- unsupported causal/security claims;
- optional SQL result equivalence.

Separate stable regression cases from exploratory capability cases.

## Processor and review integration

The evaluator should support targeted before/after runs for a proposed context
change:

```text
current approved context
        ↓ same affected cases
candidate context
        ↓
score and failure-category delta
        ↓
human reviewer sees evidence
```

Evaluation may advise a reviewer, but it cannot approve context automatically.
An accepted correction should eventually produce a permanent regression case.

## Freshness and connector regression

Cases should pin source snapshots and connector versions. The suite must detect:

- a connector silently dropping a field or relationship;
- stale context after source drift;
- incorrect deletion inference from a partial snapshot;
- changed source behavior under the same claimed version/capability;
- a normalization change that breaks stable identity;
- synthetic fixtures passing while the real-source contract fails.

## Required outputs

Each run should produce:

- machine-readable JSON results;
- concise Markdown scorecard;
- per-case selected context/assets;
- actual accepted alternative used;
- satisfied and failed predicates;
- prohibited behavior observed;
- exact source/context/client/grader versions;
- latency/token/tool diagnostics;
- failure category and provenance links;
- raw-versus-governed and before-versus-after deltas.

## CI strategy

Fast required CI:

- case schema tests;
- deterministic grader tests;
- a credential-free regression subset;
- Postgres persistence/replay;
- synthetic parser edge cases.

Slow required connector/release validation:

- real pinned Superset source;
- generated export and live REST;
- full context-effectiveness suite;
- restart/drift scenarios.

Optional scheduled/manual jobs:

- Claude/Codex trials;
- model-grader calibration;
- repeated nondeterministic capability runs;
- external framework adapters.

## Non-goals

- dashboard layout/aesthetic grading;
- one mandatory agent trajectory;
- production warehouse execution;
- requiring paid model calls in CI;
- using one aggregate confidence score to hide individual trust failures;
- automatic context approval from an evaluation pass.

## Primary sources

- Ragas agent metrics: https://docs.ragas.io/en/v0.4.2/concepts/metrics/available_metrics/agents/
- Ragas SQL metrics: https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/sql/
- DeepEval tool correctness: https://deepeval.com/docs/metrics-tool-correctness
- DeepEval tool use: https://deepeval.com/docs/metrics-tool-use
- DeepEval MCP evaluation: https://deepeval.com/docs/metrics-mcp-use
- Phoenix documentation: https://arize.com/docs/phoenix/
- LangSmith documentation: https://docs.smith.langchain.com/
- OpenAI Evals API: https://platform.openai.com/docs/api-reference/evals
- OpenAI Graders API: https://platform.openai.com/docs/api-reference/graders

## Implementation ownership

- #25 implements the native evaluator.
- #28 supplies deterministic scenarios and expected behavior.
- #31 supplies the final HTTP/MCP interface.
- #38 requests targeted proposal evaluations.
- #30 stores exact evidence and version links.
- #36 runs required credential-free and real-source gates.
- #34 treats context improvement—not dashboard generation—as the product proof.
