# The contradiction/reconciliation engine -- design (hy-gh-125)

Status: DESIGN (2026-08-16, hy-gh-125). DESIGN-FIRST: this records the target shape
for the general contradiction/reconciliation engine and DECOMPOSES it into impl
slices. It builds NOTHING. The accepted base is
[ADR-0021](../adr/0021-a-contradiction-is-a-join-not-a-rule.md) ("a contradiction is
a join, not a rule", accepted 2026-08-01); this doc CITES it and does not re-derive
it. Where this doc designs something new (a uniform `conflicts[].severity`, one
join mechanism, a published register), that new surface -- and only that -- is the
work; each piece is a follow-on bead below.

## 1. What is already accepted and shipped (the base, per ADR-0021)

The single hand-coded rule is already partly generalised. Verified on `main`
(`81fb498`), not from memory:

- **Comparison over a join, not a rule table** (ADR-0021 dec 1-2). Expression drift
  compares Git's approved expression to the observed one with `compare_fragments`
  (`hyperset/processor/rules.py`), yielding three outcomes -- `EQUIVALENT`
  (agreement), `DIFFERENT` (contradiction), `UNDECIDED` (neither). A reformatting is
  not a contradiction.
- **Which side MOVED, never which side is WRONG** (ADR-0021 dec 3).
  `hyperset.processor.MOVED_SIDES` publishes `observed` / `git` / `both` /
  `undecidable`, measured against the version the commit pinned. There is no "wrong"
  field; ADR-0012 keeps that with the human.
- **Two reconciled dimensions built outside the processor** (ADR-0021 dec 4, 7).
  `hyperset/bundle/reconcile.py` emits `prohibited_but_referenced` and
  `source_deleted_while_governed` at bundle time, two-sided, each entry carrying
  `produced_by` (`processor_finding` | `bundle_reconciliation`). `CONFLICT_PRODUCERS`
  and `RECONCILED_KINDS` are gated at construction; `finding_id` is null on a
  reconciled entry.
- **Four dimensions REFUSED with a recorded reason** (ADR-0021 dec 4): grain, join
  cardinality, ownership, freshness -- each has no observed side, or needs warehouse
  SQL, or needs a declared identity bridge, or declares no threshold.

**What ADR-0021 explicitly leaves unbuilt** (dec 7, closing): "each dimension still
has its own pairing function ... 'a comparison over a JOIN, not an entry in a rule
table' is still a claim about shape that no single mechanism demonstrates." That gap,
plus a uniform severity and a published register, is this design.

## 2. Conflict-type taxonomy

One table, the authority for what the engine can and cannot emit. Reconcilable rows
are ADR-0021's; refused rows carry ADR-0021's recorded reason and the DECLARED
CONDITION under which each stops being refused (design only -- see slice beads).

| Kind | Git side | Observed side | Status | Producer | Unlock condition (if refused) |
|---|---|---|---|---|---|
| expression/definition drift | `fields[].expression` | `metrics[].expression` | reconcilable | `processor_finding` | -- |
| `source_deleted_while_governed` | `approved_sources[].ref` | `observed_assets.deleted_at` | reconcilable | `bundle_reconciliation` | -- |
| `prohibited_but_referenced` | `prohibited_sources[].ref` | live `asset_relationships` | reconcilable | `bundle_reconciliation` | -- |
| grain change | `grain` (prose) | *nothing* | REFUSED | -- | a connector projects a grain to compare (not an inferred one) |
| join cardinality drift | `joins[].type` | *nothing* | REFUSED | -- | never in v0 -- checking 1:1 needs warehouse SQL (hard boundary) |
| ownership mismatch | `owner_refs` | `owners` / `owner_urns` | REFUSED | -- | the customer DECLARES an identity bridge in Git (never inferred) |
| freshness beyond threshold | *no declared threshold* | `source_modified_at` | REFUSED | -- | the manifest declares a required freshness; else stays a `freshness` observation |

A refusal is a first-class output of the taxonomy, not an omission. "Definition
drift" over prose (`definitions[].statement` vs `description`) stays REFUSED under
ADR-0021 dec 2 (prose is display-name similarity, a finding candidate never a factual
edge) and is not the reconcilable `expression drift` row.

## 3. The one join mechanism (designs ADR-0021 dec 7's admitted gap)

**The join-not-rule principle, and why a rule table or a scoring model is
rejected, is ADR-0021 decision 1 and is NOT restated here.** This section specifies
only the DELTA ADR-0021 decision 7 left open: the three bespoke pairing functions
(`_conflict`, `prohibited_but_referenced`, `source_deleted_while_governed`) become
ONE dispatch, so the principle is demonstrated by a single mechanism rather than
asserted across three.

The delta, and only the delta:

- **One dispatch over the join.** For each join key present on both the declared and
  observed sides, the mechanism forms the pair, reads its value KIND, applies the
  comparator that kind selects, and emits a conflict on a disagreement. The three
  existing functions become value-kind bindings, not separate code paths.
- **`COMPARATORS` is a NEW registry keyed by value kind** (`expression` ->
  `compare_fragments`; `presence` -> existence; `identity` -> exact equality),
  published and gated the way `RECONCILED_KINDS` already is. This registry is the
  new artifact; the comparators it names already exist.
- **The observable consequence.** Adding a joinable FIELD is not a code change --
  its value kind already has a comparator, so it joins with none. Only adding a
  value KIND is a reviewed change, to the one registry. This is the property the
  section-8 gate tests.
- The two `bundle_reconciliation` kinds bind to the `presence` comparator (both
  sides are presence), folding into the same dispatch.

For WHY this shape (join not rule table, comparison not score) and its governance
rationale, read ADR-0021 decision 1; this doc does not re-argue it.

## 4. Severity model (F1 ruling: fixed per-kind, deterministic, default-deny)

`conflicts[].severity` does NOT exist today -- verified: `reconcile.conflict()` omits
it, `_conflict` does not project it, and a grep of the schema and tests finds zero.
This design ADDS it as a NEW served key on every conflict entry, assigned
deterministically and by provenance:

| Producer | How severity is assigned |
|---|---|
| `processor_finding` | INHERITS `finding.severity` (already `error`/`warning`, `rules.py:236`). A conflict projecting a finding carries the finding's own severity, unchanged. |
| `bundle_reconciliation` | a FIXED, DECLARED severity per kind, held in the `RECONCILED_KINDS` register next to the kind (e.g. `prohibited_but_referenced -> error`, `source_deleted_while_governed -> warning`). Not computed. |

- **No computed/banded severity.** A reference-count band or any data-derived score
  is a scoring model, which ADR-0021 dec 1 rejects for a governed conflict: it is
  assist, and assist content may not enter `bundle_id`. The reconciled severity is a
  governance CONSTANT the customer's own decision already justifies (a prohibition is
  an error; a governed source vanishing is a warning), not a measurement.
- **Default-deny (ADR-0018 dec 5).** `severity` is an enumerated qualifying field.
  Its published rule: an UNKNOWN severity value is treated as the MOST severe and is
  always surfaced -- never silently downgraded or dropped. This is what makes adding
  a future severity value safe without a further `SCHEMA_VERSION` move.
- **Label-by-provenance (ADR-0019).** Severity travels with `produced_by`, so a
  reader always knows whether a severity was inherited from a persisted finding or is
  a governance constant. Assist may CITE a conflict's severity; it may not author,
  transform, or reorder one.

## 5. Reconciliation output shape

One entry shape, both producers, `severity` added (new key in **bold**):

```json
{
  "kind": "prohibited_but_referenced",
  "produced_by": "bundle_reconciliation",
  "severity": "error",
  "finding_id": null,
  "ref": "superset://dataset/legacy_revenue",
  "field": null,
  "context_says": "prohibited: superseded by recognized_revenue",
  "source_says": "3 live references: chart:..., dashboard:...",
  "unresolved_since_commit": "b107dc6"
}
```

Invariants the shape must keep (all from ADR-0021 / ADR-0019, unchanged): a reconciled
entry has `finding_id: null` and a `RECONCILED_KINDS` kind; a `processor_finding`
entry carries its `finding_id`; movement (`MOVED_SIDES`) is per-field and absent on
presence kinds; there is no "wrong" field.

## 6. Governed-not-assist -- never upgrade an unverifiable claim

- **Determinism gate (ADR-0019 floor 8).** Everything in `conflicts` is deterministic
  for a pinned commit + repository state + directive, because `ContextBundle._content`
  hashes `linked_evidence` into `bundle_id`. A model-judged disagreement goes to
  `assist` (its own identity), never here.
- **`UNDECIDED` stays undecided.** The comparator's third outcome is never promoted to
  a contradiction; it is neither agreement nor disagreement (ADR-0021 dec 2). An
  unverifiable claim is not upgraded into a governed error just because a rule could
  fire.
- **Which side MOVED, not which side is WRONG** (ADR-0021 dec 3). The engine states a
  fact about the data; the human owns the verdict (ADR-0012). hy-1a6j is the
  precedent: a remedy must not tell a caller to proceed on a verdict that refused it.
- **Detection, not rewriting** (ADR-0012). A conflict is a finding/disclosure; the
  engine never edits Git or opens a parallel approval lifecycle.

## 7. Served-contract impact

- **`SCHEMA_VERSION` 19 -> 20** for the NEW `conflicts[].severity` KEY. A new key is a
  shape change (a strict consumer validating against 19 misreads a 20 payload -- same
  precedent as `finding_id: null` in ADR-0021 dec 7). The bump LANDS in impl-slice
  (i) under its own review, NOT in this design-write.
- **`tools_hash` UNAFFECTED** -- stays `sha256:fe930a003b731211`. `conflicts` is bundle
  OUTPUT, not a tool name/description/input schema; none of the served operation
  signatures change.
- **The kind/producer register stays UNPUBLISHED in this work.** Serving the register
  as a self-declaring enumeration is a response-shape change ADR-0021 dec 6 declined;
  it is its own follow-on bead (F2 ruling), with its own review and likely operator
  sign-off, and it is NOT in this design-write.

## 8. Independent-instrument acceptance test (REQUIRED, hq-xneo)

hq-xneo: a reconciliation engine compared against the fixtures it was written from
cannot come out differently. A test the AUTHOR labels "not designed against" proves
nothing -- author and test can be co-designed. So slice (ii)'s gate is defined by
VERIFIABLE PROVENANCE, in a form co-design cannot satisfy. Slice (ii) MUST carry at
least ONE of the following two gates; author assertion alone is rejected in review.

**Gate A -- a property test, quantified over pairs the author did not enumerate.**
The gate is a property-based test (Hypothesis) whose claim is universal, not a
fixture: *for any declared field carrying a value of a kind already in
`COMPARATORS`, paired with a same-key observed projection, the mechanism emits
exactly the verdict that comparator returns, with no per-field branch.* A property
that ranges over generated field names and values cannot be tailored to one hand-
picked case -- that is what defeats co-design, not a label. It fails if any
generated pair needs a new `if` or a new list entry to be seen.

**Gate B -- a held-out fixture with git-checkable ordering.** A concrete field pair
(a governed expression-bearing field the current rule does NOT read -- e.g.
`filters[].expression` / `validations[].expression` -- plus its observed
projection) is committed BEFORE the mechanism refactor, in a commit that is red
against today's per-dimension code (which cannot see it) and stays UNCHANGED through
the refactor commit that turns it green. Provenance is enforced mechanically, not
trusted: the refactor commit's diff MUST NOT touch the fixture/test file (a
`--no-renames` diff over that path is empty), and the red-before / green-after
ordering is in git history. Equivalently, the held-out pair may be authored by a
REVIEW seat (adversary), so the implementer never sees it before the mechanism is
frozen.

**What either gate proves.** That "a contradiction is a join, not a rule" holds for
a pair not in hand while writing the mechanism (ADR-0021 dec 7's admitted gap) --
established by the property's universality (A) or by git-verifiable authorship
order (B), never by the author's own say-so.

## 9. Impl-slice decomposition (follow-on beads -- DESIGN-FIRST, build none here)

Ordered; each is a separate reviewed slice, none built in this doc:

1. **(i) `conflicts[].severity` + `SCHEMA_VERSION` 19->20** (bead **hy-xfhh**). Add the key on both
   producers -- processor conflicts inherit `finding.severity`; reconciled kinds read
   a fixed severity from `RECONCILED_KINDS`. Publish the default-deny rule for the
   field. Bump SV to 20; `tools_hash` unchanged. Determinism test: two resolves of the
   same commit produce byte-identical `severity` and an unchanged `bundle_id` modulo
   the version.
2. **(ii) The one join mechanism** (bead **hy-gl39**). Collapse `_conflict`,
   `prohibited_but_referenced`, and `source_deleted_while_governed` into a single
   join+`COMPARATORS`-by-value-kind dispatch; the per-dimension functions become
   value-kind bindings. Ships the section-8 independent-instrument test as its
   acceptance gate. No served-shape change (behaviour-preserving refactor).
3. **(iii) Publish the conflict kind/producer register** (bead **hy-rmm1**). Serve `RECONCILED_KINDS` /
   `CONFLICT_PRODUCERS` (and finding types) as a self-declaring enumeration --
   response-shape change, own review, its own `SCHEMA_VERSION` decision (ADR-0021 dec
   6 / F2). Not started before (i)/(ii) land.
4. **(iv) Refused-dimension unlock triggers** (bead **hy-imr0**). One bead PER refused dimension that has
   a declared unlock (ownership: a Git-declared identity bridge; grain: a connector
   grain projection; freshness: a manifest-declared threshold). Each designs the
   trigger and its comparator binding; join cardinality is explicitly NOT one (needs
   SQL, hard boundary).
5. **(v) Eval invalidation (#33) pointer** (bead **hy-ayxz**). Out of scope for hy-gh-125; a conflict
   routing to eval invalidation is the existing #33 path. Named as a follow-on, not
   built.

## 10. See also

- [ADR-0021](../adr/0021-a-contradiction-is-a-join-not-a-rule.md) -- the accepted base.
- [ADR-0019](../adr/0019-assist-mode-may-reason-governance-may-not.md) -- label-by-provenance; assist may cite, not author.
- [ADR-0018](../adr/0018-schema-version-versions-the-answer-not-the-request.md) -- what moves `SCHEMA_VERSION`; default-deny.
- [ADR-0012](../adr/0012-git-owned-context-authority.md) -- detection, not rewriting.
