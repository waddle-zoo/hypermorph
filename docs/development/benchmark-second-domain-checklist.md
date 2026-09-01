# Second benchmark domain -- authoring checklist (hy-unks, for hy-gh-25 / hy-esp)

Status: CHECKLIST (2026-08-16, hy-esp). The benchmark's second governed domain
and its locked cases are **HUMAN-OWNED**: GitHub #25 is explicit that Hyperset
does not generate the exam it is graded against, so no model authors this
fixture. This document is the requirements a human (Brandon, bead **hy-unks**)
fills; the harness that CONSUMES the fixture is built and merged (hy-esp) and
tolerates the fixture's absence until it lands.

The target domain agreed with the mayor is **billing / invoicing** -- confusable
with `revenue` on purpose: an overlapping source (orders), an adjacent concept
(invoiced/billed amount vs recognized revenue), and a metric a reasonable person
routes either way, so a paraphrase like "what did we actually earn last month?"
is genuinely ambiguous. The CONTENT below is the human's to write; this file only
says what must be true for a case to be scorable and the benchmark to be valid.

## A. The governed domain fixture

Author a second governed domain under the benchmark's context fixtures with:

- [ ] a manifest whose `domain` is NOT `revenue` and is genuinely confusable with
      it -- adjacent concepts, at least one OVERLAPPING source with the revenue
      domain, and a metric a reasonable analyst could route either way;
- [ ] an observed side that makes the two domains **distinguishable in the
      catalog**, not only in Git -- so domain selection is a real choice, not a
      trivially-correct one (the bead's "prerequisite that is also the work");
- [ ] every `must_cite` / `must_not_cite` ref is a real identifier in the pinned
      sources (Superset 6.1.0 dataset UUIDs, DataHub v1.6.0 URNs) -- both arms
      must be able to produce it, so cite the UUID/URN, never the governed prefix;
- [ ] the exact governed rule text (`must_state`) as the manifest writes it (an
      expression, a filter, a grain) -- this is where the governed substrate is
      supposed to win and raw metadata to lose.

## B. The locked cases (`cases/billing.yaml`)

`schema_version: 1`, `suite: billing`, and a small fixed set covering the three
families, each scorable (a case that cannot fail is not a case):

- [ ] **governed_fetch** -- names an `expected_domain`; carries `must_state`,
      `must_cite`, `must_not_cite`, and `requires_plan_validation` where a plan
      applies;
- [ ] **no_match** -- names NO `expected_domain` (an answer that is a domain is
      not a no-match case);
- [ ] **stale_governed_context** -- names an `expected_domain` and is scored
      against a drifted state carrying a persisted finding.

## C. Hidden paraphrases vs controls -- the real test

Every case declares `probe: paraphrase` or `probe: control` (default `control`):

- [ ] at least one **paraphrase** per family: expresses the governed intent
      WITHOUT naming any configured domain literally (#70's examples: "What did we
      actually earn in Canada last month?"; "Finance wants quarterly performance
      by market. Which number should I use?"). The loader **rejects** a paraphrase
      whose question contains any configured domain literal, so these must be
      genuinely oblique;
- [ ] the **control** cases (which name the domain) exist to show the paraphrases
      are hard; keep both -- they are reported separately;
- [ ] `domain_literals:` -- list any ALIAS a human knows would leak the domain
      (beyond the suite name and each `expected_domain`), so the guard blocks it
      in paraphrases across every suite.

## D. Invariants the harness already enforces (do not fight them)

- [ ] `Case.prompt()` hands the arm the QUESTION and nothing else; the expected
      domain and refs are scorer-side. Never encode the domain in the question of
      a paraphrase, and never narrow the domain set per case -- if a case is only
      passable because the agent was told where to look, delete it (the bead's
      BINDING).
- [ ] Case ids are UNIQUE across suites (a recording names its case).
- [ ] Scoring stays **deterministic** (ADR 0007): no model judges a step. Exact
      strings for `must_state`; a paraphrase check on the ANSWER would be a model
      judging a model.
- [ ] Authoring this fixture moves **no served contract**: `tools_hash` stays
      `sha256:fe930a003b731211` and the bundle `SCHEMA_VERSION` does not move.
      If a case seems to require either, STOP and re-fork with the mayor.

## E. After the fixture lands

- [ ] record both arms over the new suite and commit the recordings (the same
      pinned model/seed/temperature the revenue suite uses);
- [ ] the report will then show `by_probe` for both `control` and `paraphrase`,
      per step and per arm, across both domains -- which is the finding #25 asks
      for: WHERE the governed arm wins, not just that it does.
