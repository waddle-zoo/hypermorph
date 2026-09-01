# 0007: Evaluation is a context-quality control, not a Hyperset-owned agent product

Status: accepted (design); implementation not yet built (`#25`).

## Context

`docs/research/agent-eval-framework.md`'s original matrix scoped evaluation
around a Hyperset-owned agent/dashboard product, with dashboard
generation and exact-trajectory matching as release gates. That framing
doesn't fit a connector-driven context system: Hyperset isn't the agent
and isn't the BI frontend (`docs/research/FACT_CHECK_2026-07-25.md` §7).

## Decision

The evaluator compares the same questions run through a raw-connected-
assets configuration vs. a Hyperset-governed-context configuration (plus
ablations: context without procedural guidance, stale snapshots, missing/
conflicting owners, before/after a proposed review change). Deterministic
graders (context/source selection, prohibited-source avoidance, freshness/
lifecycle, conflict/deprecation disclosure, provenance completeness,
required validations, fixture-backed result equivalence) are the release
gate. Model graders are optional/advisory, never the sole gate. Cases
support required/prohibited/optional-alternative context rather than one
exact tool trajectory. Dashboard aesthetics/generation is not a v0
release gate.

## Consequences

- A local/test SQL executor may back deterministic cases; that's test
  infrastructure, not Hyperset claiming to own production query
  execution.
- Evaluation results feed the human review process; they do not
  auto-approve context (ADR 0005 still applies).
- `#38`/release-gate CI (`#36`) consume the evaluator's deterministic
  graders directly, without depending on any single hosted eval vendor as
  system of record.
