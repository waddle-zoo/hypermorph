# 0016: A declared coverage claim, because Hyperset does not read the question

Status: accepted.

## Context

The governed arm has a predicate named
`no_governed_answer_without_a_governed_domain`. Its name states the guarantee
everyone wants: a question nothing governs gets no governed context. It is red,
and this ADR records that it is expected to stay red under the current
architecture.

What was measured, live `qwen2.5:7b` on Ollama 0.32.4 at an observed
32,768-token window, seed 20260728, temperature 0, on the
`supply_chain_lead_time` case: asked "What is our average supplier lead time by
warehouse over the last quarter?", the arm resolved with `"domains":
["revenue"]` and `"concepts": ["recognized_revenue"]` and was served governed
context from the revenue domain.

hy-9lct (PR #114) made the coverage claim representable and verifiable, which is
as far as this architecture reaches:

- `ContextDirective.concepts` is required whenever `domains` is named. The two
  halves are one parameter, and either half alone is `invalid_params` before any
  retrieval runs.
- `_covered()` in `hyperset/bundle/resolver.py` checks the claimed terms against
  the domain's own Git declarations by exact set membership -- no similarity, no
  stemming, no synonyms -- and a term the domain does not declare is refused
  with `domain_does_not_declare`, serving no governed context.

That closed the failure PR #103 recorded, where a domain named with no claim at
all produced governed context. It did not make the predicate true, because the
claim the model made was TRUE OF THE DOMAIN and FALSE OF THE QUESTION: the
revenue domain really does declare `recognized_revenue`. Only the question
separates the two, and the resolver never reads `query`. Comparing question to
domain inside Hyperset is the keyword routing GitHub #70 deleted on purpose.

## Decision

State the guarantee Hyperset offers at its real strength, and keep #70.

**No governed context without a DECLARED, VERIFIED coverage claim.** Not: no
governed context for a question nothing governs. The verified half is real --
the claim is checked against Git-declared meaning and refused when it does not
hold -- and the declared half is the boundary: Hyperset verifies the claim, not
the question behind it.

The stronger form is out of reach rather than unimplemented. It requires
question-to-domain comparison inside Hyperset, which is what #70 removed.
Reversing that is a bigger decision than any bead, and one red predicate is not
the argument for it. This ADR does not propose reopening it.

`hyperset/evals/expected_failures.yaml` therefore keeps its
`supply_chain_lead_time` / `no_governed_answer_without_a_governed_domain` entry
against hy-9lct, and that entry is a **declared architectural limit**, not an
unfixed bug awaiting a patch.

## Consequences

- A lying caller still gets a bundle. What changed is that the lie is now on the
  record: the claim is a field in the directive, it is in the trace, and it is
  attributable to the caller that made it. Before hy-9lct there was no field in
  which to state it, so there was nothing to be wrong.
- The predicate keeps its name and its meaning. Renaming it, narrowing it, or
  replacing it so it goes green while the arm still answers a lead-time question
  from the revenue domain is exactly the demotion the ratchet exists to prevent
  -- and the ratchet fails in both directions, so a declared failure that stops
  failing is as red as an undeclared one.
- The entry does not expire. It is not a TODO with a bead attached; hy-9lct owns
  the record, not a pending fix, and the ratchet stays honest about what the
  governed arm does.
- `docs/v0-foundation.md` section 7 already states this boundary in the wire
  contract. This ADR is the decision behind that paragraph, so a reader who
  finds the red predicate first does not re-derive #70 from it.
- What would have to change for this to be revisited: a mechanism that
  establishes coverage without Hyperset interpreting the question. Until such a
  mechanism exists and is specified, the guarantee above is the whole of it.
