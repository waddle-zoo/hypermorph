# ADR 0021: A contradiction is a join, not a rule -- and four of the seven classes we listed have no observed side

- Status: accepted 2026-08-01 (retroactively)
- Date: 2026-07-30
- Shipped by (all on main, each verified 2026-08-13 to still describe the landed
  reality): `b107dc6` compares expressions as computations and decides what a
  contradiction is (hy-803q; decisions 1-2 -- `compare_fragments` now the
  processor's comparator in `hyperset/processor/rules.py`); `e8da766` lets the
  `UNDECIDED` verdict survive into the candidate order (hy-qbii; decision 2);
  `da12fde` says which side left the version the commit linked and publishes the
  four values (hy-qfyn; decision 3 -- `hyperset.processor.MOVED_SIDES`); `39efbb6`
  serves both reconcilable dimensions and names each conflict's producer (hy-llk4;
  decision 7 -- `hyperset/bundle/reconcile.py` with `CONFLICT_PRODUCERS`,
  `RECONCILED_KINDS`, and the `produced_by` label).
- Bead: hy-gh-125 (GitHub #125), under the hy-gh-122 assist epic
- Authorised by ADR 0019's consequences, which name this issue: "hy-gh-125's
  typed disagreements are disclosures that may reach `linked_evidence.conflicts`
  because a disagreement is not a claim about meaning"
  (`docs/adr/0019-assist-mode-may-reason-governance-may-not.md`, Consequences).
- Constrained by ADR 0012 (detection, not rewriting) and ADR 0009 gate 5
  (breadth after the slice is green).

## Context

Contradiction detection is one hand-coded rule. `approved_expression_drift`
(`hyperset/processor/rules.py`) fires when a Git-approved field expression
differs from the observed one, and `_conflict` (`hyperset/bundle/resolver.py`)
projects a finding onto `linked_evidence.conflicts` only when the finding
carries an `expression` on both sides. hy-inbr measured what that amounts to:
one rule, one evidence shape, one fixture.

#125 proposed a general engine over seven classes: expression drift, grain
change, source deletion while governed, prohibited-but-popular, ownership
mismatch, join cardinality drift, freshness beyond a threshold.

**One measurement changes what "general" has to mean.** The shipped rule
compares expressions with `==`. Three other places in this repository answer the
same question with `compare_fragments`, and `docs/v0-foundation.md` states the
rule they follow: "a reformatted governed expression is not a contradiction".
Run on one pair, in one process:

    git      = "SUM(gross_amount - tax_amount)"
    observed = "sum( gross_amount-tax_amount )"

    compare_fragments(...)          -> equivalent
    approved_expression_drift(...)  -> 1 finding, severity=error

So a reformatting produces an error finding whose explanation says an agent
"would report a number the source does not produce", a
`linked_evidence.conflicts` entry, and -- because `open_finding` sinks a
candidate before any signal is read -- a sunk candidate in discovery whose own
`expression_agreement` signal simultaneously reports that the source computes
what Git declares. One candidate says the source both agrees and disagrees with
Git, from the same two strings (hy-803q).

A general engine layered on that would generalise the false positive. So the
first decision is about comparison, not coverage.

## Decision

### 1. A contradiction is a comparison over a JOIN, not an entry in a rule table

The engine is: **join what Git declares to what a connector observed on a shared
key, then compare the joined pair with a comparator chosen by the VALUE KIND.**

- The join key is a declared identifier that also exists in observation: a field
  name, a source ref. Not a similarity match, not a score.
- The comparator is chosen by what the value *is* -- an expression by
  `compare_fragments`, an identity by exact equality, a presence by presence --
  never by which case we are in.
- A dimension exists when, and only when, both sides carry the key. A new
  declared field or a new observed projection makes new pairs joinable without a
  new rule.

**Why not a rule table.** Its failure mode is silence: it sees the classes
someone enumerated, which is the state we are in. And each entry brings its own
comparison, which is how this codebase came to hold two answers to "do these
expressions agree".

**Why not a scoring model.** A score cannot be checked against evidence, and a
contradiction is a stronger claim than a rank -- it reaches a human as an error.
It would also make detection non-reproducible, which ADR 0012's review loop
depends on.

**Semantic judgement stays inside a comparator**, for the value kind that needs
it, through the one shared comparator. That is #125's "shares I6's equivalence
check" clause, honoured by having one comparator rather than a second caller of a
model.

**One comparison, three consumers.** `compare_fragments` is already used by plan
validation and by discovery's ranking. The processor becomes the third. Nothing
new is invented: the three-way semantics below are what `plan.py` already serves
and what `docs/v0-foundation.md` already documents.

### 2. What it must refuse to call a contradiction

- **No shared key, no contradiction.** A field Git declares and the source does
  not report is an absence, not a difference. The existing rule already gets
  this right and keeps it.
- **An undecided comparison is not a contradiction, and not agreement either.**
  `compare_fragments` has three verdicts. `EQUIVALENT` is agreement. `DIFFERENT`
  is a contradiction. `UNDECIDED` -- a difference confined to table qualifiers or
  casts -- is neither: settling it needs the warehouse schema Hyperset does not
  read or the query it does not run. `plan.py` already serves this as a WARNING
  named `field_expression_undecidable`; the processor gains the same third
  outcome rather than a second rule.
- **Cosmetic difference is the comparator's business.** Whitespace, keyword
  case, redundant parens, a trailing alias. Nothing else decides agreement.
- **Prose is not compared.** A Git `definitions[].statement` against an observed
  `description` is display-name similarity with extra steps, which
  `docs/v0-foundation.md` already limits to "a finding candidate, never a factual
  edge". "Definition drift" as #125 lists it is out of scope for that reason.

### 3. It says which side MOVED. It does not say which side is wrong

hy-1a6j was closed on a remedy that told a caller to proceed on a verdict that
refused it. So each disagreement states both sides labelled with where they were
read from, and **which side moved** -- knowable, because an observed asset's
version chain and the commit's `linked_version_id` together say whether the
observation changed after the commit linked it or the commit changed against an
unchanged observation.

**Measured against the link point, and stated in four values.** The link point is
the version the commit's evidence ref pinned when the context was synced. Both
comparisons are the one comparator, because both ask whether two computations
agree, and the version's *expressions* are read rather than its id alone: a
version is written for an asset and a finding is about one field, so an id
comparison would report movement in a field nothing touched. The published set is
`hyperset.processor.MOVED_SIDES`, gated where a candidate is constructed:

| value | what was measured |
|---|---|
| `observed` | Git agrees with the linked version; the source has changed since |
| `git` | the source still computes what the linked version computed; the approved expression differs from it |
| `both` | neither side matches the linked version |
| `undecidable` | the commit pinned no version for the ref, or the pinned version is no longer readable |

Two things this deliberately does not say. **It never claims a commit was
edited**: this system reads one commit, not the history of one, so the `git`
value is stated as the approved expression differing from the version the commit
itself linked. And **`neither` is absent because it is unreachable**, not because
it is unbuilt -- `EQUIVALENT` is equality of the comparator's canonical form and
therefore transitive, so Git agreeing with the link point and the link point
agreeing with the current expression make the two sides agree, and the rule
returns before a candidate exists to carry a side.

There is no field for "wrong". The remedy names the file to open and the choices,
as `_proposal` already does. "Which side moved" is a fact about the data; "which
side is wrong" is the human's, and ADR 0012 keeps it there.

### 4. Four of #125's seven classes have no observed side

| class | Git side | observed side | verdict |
|---|---|---|---|
| expression drift | `fields[].expression` | `metrics[].expression` | **reconcilable**, and currently wrong (hy-803q) |
| source deleted while governed | `approved_sources[].ref` | `observed_assets.deleted_at` | **reconcilable**; served today as a `deprecation`, not two-sided |
| prohibited but referenced | `prohibited_sources[].ref` | live `asset_relationships` count | **reconcilable**, newly, via hy-g1y8 |
| grain change | `grain` (prose) | *nothing* | **refused** |
| join cardinality drift | `joins[].type` | *nothing* | **refused** |
| ownership mismatch | `owner_refs` (`team:finance-data`) | `owners` / `owner_urns` (`urn:li:corpuser:...`) | **refused** |
| freshness beyond a threshold | *no declared threshold* | `source_modified_at` | **refused** |

The refusals are not "not yet". Each names what is missing:

- **grain**: a free-text manifest string, and no connector projects a grain.
  Inferring one from `column_names` is a guess, and a guessed contradiction is
  exactly decision 2's failure mode.
- **join cardinality**: `joins[].type` is declared, but checking whether a join
  still holds 1:1 requires executing SQL against the warehouse. Hyperset does
  not, by hard boundary. Not observable without breaking a larger rule.
- **ownership**: both sides carry owners, in different identifier spaces.
  Discovery already refused to bridge them for *ranking*, where being wrong
  costs a worse ordering; here it would cost a false error finding, so the
  refusal is stronger. It becomes reconcilable when the customer declares a
  bridge in Git -- never when we infer one.
- **freshness**: nothing declares how fresh a source must be, so a stale source
  contradicts nothing. It is already served as `freshness`, an observation.
  Calling it a contradiction puts a clock in front of a human as an error.

**"Prohibited but popular" is renamed "prohibited but referenced".** No source
discloses an execution count (hy-d7xh), so popularity is not observable; what is
observable is how many live assets declare a reference to it (hy-g1y8). The class
survives; its name stops claiming a measurement we do not have.

### 5. Where the output goes, and the two constraints that decide it

ADR 0019 authorises typed disagreements to reach `linked_evidence.conflicts`.
Two constraints bound that, and both are structural rather than stylistic:

- **`linked_evidence` is inside `bundle_id`.** `ContextBundle._content()` hashes
  it. ADR 0019 floor 8 forbids assist content from entering that hash, so
  anything reaching `conflicts` must be deterministic for a pinned commit,
  repository state and directive. A model-judged disagreement may NOT go there;
  it goes in `assist`, which carries its own identity. This is the sharpest
  reason decision 1 rejects a scoring model: the placement #125 asks for is only
  available to a deterministic comparison.
- **A conflict names a declared ref, and that is governance's record to hold.**
  Today's entry carries `ref`, `context_says`, `source_says` -- built from a
  persisted processor finding. ADR 0019 rules that a governance-defined record
  type which already has the declared-ref field stays governance's: assist "may
  cite it by code and ref. It may not author one, transform one, filter its
  members, or reorder them." So the deterministic comparison may produce these;
  an assist-mode reasoner may only cite them.

**Consequence for the processor.** The processor stays the only component that
creates a `Finding`. This ADR adds **no processor rule**: `CLAUDE.md` forbids a
second one before the walking skeleton is green, and it is not green
(`README.md` lists full replay as not implemented). One rule, three outcomes, is
not a second rule -- and the two new dimensions in decision 4 are specified here
and deliberately not built, because building them inside the processor would be
exactly the breadth ADR 0009 gate 5 defers.

### 6. The conflict-kind vocabulary is ungated, and that is fixed here

`conflicts[].kind` is `finding.finding_type` passed straight through. There is no
published register and no gate. `docs/v0-foundation.md` records this exact
failure mode having already happened once: "Nineteen values were served while
this document named four of them, in prose about something else, and every
mechanised check stayed green."

So finding types are declared as a register and gated where they are constructed,
the way `PlanViolation` gates `VIOLATION_CODES` and `warning()` gates
`WARNING_CODES`. A type that reaches a client without being published fails at
construction rather than on the wire.

The vocabulary is **not** self-declaring: no served payload enumerates finding
types or conflict kinds, and adding an enumeration is a response-shape change
this ADR does not make. `SCHEMA_VERSION` does not move -- by the precedent set on
hy-g1y8, a version bumps when a consumer validating against version N would
MISREAD an N+1 payload, and a consumer here sees an unfamiliar `kind` string in a
list it already parses. Publishing the register is the obligation that replaces
the bump, and a follow-up bead holds serving it.

### 7. The two unbuilt dimensions get a bundle-time emitter, and every entry names its producer

Amendment, hy-llk4. This answers reviewer question 3 below, which decision 5
left open on purpose. The answer is the second option: **a deterministic
reconciler outside the processor writes to `conflicts` directly**, in
`hyperset/bundle/reconcile.py`, called by the resolver where it already holds
both sides.

Why not the processor, beyond the breadth gate: a processor finding is
persisted, carries a rule version, and opens a review task. It is a claim about
one asset that outlives the request. Both dimensions here are recomputed from
current state on every resolve and hold no judgement a reviewer settles -- the
prohibition is already the customer's decision, and the deletion is already
disclosed. They are disclosures about a pair, and the bundle is where
disclosures about a pair already live. Waiting for a gate about processor
breadth would make them wait on an event that has nothing to do with them.

The cost is real and is paid explicitly: `linked_evidence.conflicts` becomes
**mixed-provenance**. Some entries project a persisted finding; some are
computed at bundle time from Git plus current observation. So:

- every entry carries `produced_by`, one of `processor_finding` or
  `bundle_reconciliation`, published as
  `hyperset.bundle.reconcile.CONFLICT_PRODUCERS` and gated at construction, the
  shape decision 6 established;
- `finding_id` is **null** on an entry no finding stands behind, where it was
  previously an id on every entry;
- the reconciled kinds are their own register,
  `hyperset.bundle.reconcile.RECONCILED_KINDS`, deliberately not unioned with
  `FINDING_TYPES`: each register has one owner, and a second copy of the
  processor's list here would be the "two answers to one question" failure this
  ADR was written about.

`SCHEMA_VERSION` moves for that null (ADR 0018, allocated by merge order per
hy-fhtr). Decision 6 declined a bump for an unfamiliar `kind` string in a list a
client already parses; this is not that case. A client that read `finding_id` as
always present breaks on a value rather than on a key.

Neither dimension needs a comparator -- both sides are presence, so decision 1's
"comparator chosen by the value kind" resolves to existence -- and neither
carries a `field` or a moved side, because movement is measured per field
against a pinned version and a prohibition and a deletion are about the source.
Decision 2's refusals extend to presence accordingly: an absence, an unreferenced
prohibition, an unapproved deletion, and a deletion of a source the commit
prohibits are all silence.

**What this does not deliver.** Each dimension still has its own pairing
function. The emitter question is answered and the two-sidedness is built, but
decision 1's "a contradiction is a comparison over a JOIN, not an entry in a rule
table" is still a claim about shape that no single mechanism demonstrates.

## Consequences

- Detection stops depending on someone having written a rule for the shape and
  starts depending on both sides declaring a shared key, which a reader can
  inspect and use to predict the output.
- hy-803q is fixed as this ADR's first instance; the fixture covering the single
  rule today keeps passing unchanged.
- #125's acceptance box "at least the dimensions above are detected" cannot be
  met for four of the seven, and this ADR is the argument that meeting it would
  require inventing observations. #125 should be amended, not closed as
  delivered.
- Two dimensions are specified and unbuilt. That is a deliberate stop, not an
  omission: they need either the processor's breadth gate to open or a
  deterministic non-processor emitter, and choosing between those is a decision
  this ADR does not need to make to fix the comparison. **Superseded by decision
  7 (hy-llk4): both are built, outside the processor.**
- `linked_evidence.conflicts` is mixed-provenance from `SCHEMA_VERSION` 7 on, and
  a reader who does not check `produced_by` cannot tell a persisted finding from
  a bundle-time computation. That is the cost of decision 7, and the label is
  what makes it payable.

## Reviewer questions

1. Is "which side moved" enough, or may a contradiction carry a *recommended*
   side when the commit predates every observation of it? We say no -- an old
   commit is not a wrong commit -- but it is the closest call here.
2. The ownership refusal depends on there being no declared identity bridge.
   Should the manifest gain one, or should ownership stay refused in v0?
3. The two unbuilt dimensions need an emitter. Does "prohibited but referenced"
   wait for the processor's breadth gate, or does a deterministic reconciler
   outside the processor write to `conflicts` directly -- which needs a
   provenance label on each entry, since `observed_assets` carries one and
   `conflicts` does not? **Answered by decision 7 (hy-llk4): the reconciler,
   with the provenance label the question predicted.**
