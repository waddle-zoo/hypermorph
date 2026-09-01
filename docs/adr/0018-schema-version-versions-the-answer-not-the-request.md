# 0018: `SCHEMA_VERSION` versions the answer, not the request

Status: accepted. Amended 2026-07-29 (hy-tota): decision 1's sentence corrected
to name the SHAPE of what a caller receives, and decision 5 added for the case
decisions 1 and 4 disagreed on -- an added value in an existing enumerated
served field.

## Context

PR #114 made `concepts` required whenever `domains` is named. A request that
returned a governed bundle the day before — `{"directive": {"domains":
["revenue"]}}`, the shape in the served examples and in `docs/v0-foundation.md`
section 7 — now returns `invalid_params`. Nothing about the response changed.

Section 7 answered the version question in an aside, mid-paragraph:
"**BREAKING at this version, and `SCHEMA_VERSION` does not move because the
bundle's shape did not.**" That is the right call and it was made under time
pressure, in a sentence about one change, in a document about one release. The
next person tightening a parameter will not find it there. `hy-zp1q` asked for
the policy rather than the instance.

Two facts constrain the answer:

- `SCHEMA_VERSION` is a field on **every served response and on nothing else**.
  `ContextBundle`, `PlanValidation` and `ContextCatalog` each carry it into
  their `to_dict()`, and `hyperset/transport/http.py` puts it in the HTTP
  envelope. Nothing on the request side reads it or sends it, so a reader who
  finds `schema_version: 1` is holding a response.

  It is also not the only constant wearing that name, which is the other
  reason not to widen this one: `hyperset/context/schema.py` has its own
  `SCHEMA_VERSION` for the Git manifest, and `hyperset/evals/recording.py` has
  `RECORDING_SCHEMA_VERSION` for a committed run. Three numbers, three
  artefacts, and none of them versions a request.
- The comment on the constant already concedes that 1 has covered two response
  shape changes (`hy-6ae`), and says why that was defensible and why the next
  one may not be. So the number is already weaker than a semantic version, and
  making it also carry request compatibility would overload a number that is
  not currently trustworthy for the one job it has.

## Decision

1. **`SCHEMA_VERSION` describes the shape of the answer.** A change to what a
   caller may SEND does not move it. A change to the SHAPE of what a caller
   RECEIVES does. A request-shape break is announced in section 7 and carried
   as a `release-note` bead; it is not encoded in a number the response
   carries.

   *Amended 2026-07-29 by decision 5, and the amendment is in place rather than
   in a superseding ADR because it corrects this ADR's own sentence rather than
   reversing its ruling.* As accepted, this read "A change to what a caller
   RECEIVES does." That is too wide to be useful: a reworded `message` string
   is something a caller receives and did not receive before, and a number that
   moves for every additive change trains clients to ignore it, which costs
   exactly when a real break happens. The word `SHAPE` is the correction.

   That example is not hypothetical, and it is what makes this a correction
   rather than a reversal. `docs/v0-foundation.md` section 7 already says of a
   `resolution.warnings` entry that "the `message` is prose for a person and may
   be reworded at any time" -- permitted outright, with no version move attached
   to it. So the sentence as accepted did not merely admit an unwanted reading;
   it already contradicted the binding contract this ADR points at. The
   amendment resolves a pre-existing inconsistency between the two documents
   rather than creating a rule that was not in force.
2. **Every request-shape break says the word "breaking" in section 7**, names
   the shape that stopped working, and says what the caller must now send. #114
   did this and it is the template.
3. **Every request-shape break gets a `release-note` bead**, per ADR 0015. That
   ADR decided there is no `CHANGELOG` and no release document until a
   publication event, and made the labelled beads the register. A wire break is
   precisely the obligation that register exists to hold, and #114's was not
   filed into it until `hy-zp1q` went looking.
4. **A response-shape break moves `SCHEMA_VERSION`.** No exception is granted
   here for the reason the constant's own comment gives: the two that did not
   move it were defensible only while nothing outside this repository consumed
   v0, and that excuse expires without announcing itself.
5. **An added VALUE in an existing enumerated served field does not move
   `SCHEMA_VERSION`, and the thing that makes that safe is default-deny.**

### 5. Why the number does not move, and what buys that

Decisions 1 and 4 disagreed on this case and both readings were defensible from
the text, which means the text did not decide it (hy-tota). By decision 1 as
first written it moves: the caller receives something it did not before. By
decision 4 it does not: every key keeps its type and an old client's parser
still parses. It gets a value its switch has no branch for, which is a semantic
surprise rather than a shape break.

The ruling is decision 4's outcome, and it is not free. On its own it produces
the failure hy-tota names: **old client, new value, silent misread** -- a client
that treats "not one of the refusals I know" as approval reads an unknown code
as permission. That is the same class as the silent-loss failures this
repository has spent its ADRs eliminating, so the ruling ships with the rule
that inverts it.

**The precondition: unknown values are DEFAULT-DENY.** A client that receives a
value it does not recognise in a served enumerated field never reads it as
approval. The number does not move for an added value only where this rule is
published for that field. Without it the ruling would be the other
way, because "the parser still parses" is not a safety argument in a product
whose output is a governance claim.

**What the rule applies to is stated per field, not by a category.** An earlier
form of this paragraph said "a governance-bearing field", and nothing defined
that set (hy-2fmp). A precondition whose applicability is a judgement call is not
a mechanism, which is the argument this ADR makes everywhere else. The served
enumerated fields are few enough to state over directly, and they divide by
whether the field CARRIES a verdict or QUALIFIES one:

| Field | Values | Class |
| --- | --- | --- |
| `resolution.status` | 4 | carries |
| `PlanValidation.status` | 4 | carries |
| `observed_assets[].governance` | 2 | carries |
| `violations[].code` | 20 | carries |
| operation error `code` | 5 | carries |
| HTTP error `code` | 5 | carries |
| `violations[].severity` | 2 | qualifies |
| `page.truncated[].reason` | 2 | qualifies |
| `resolution.warnings[].code` | 17 | qualifies |

Measured on `3519013`, which is `main` after PR #133 merged; this branch's diff
is documentation only and moves none of these counts. The measurement is pinned
to a SHA rather than to "this branch" because "this branch" becomes `main` the
moment the branch lands, and a count anchored to a moving referent goes stale by
construction -- the same defect class as the two records this amendment exists
to reconcile.

`violations[].code` was 16 before #133 and is 19 after it: the added values are
`field_expression_undecidable`, `filter_undecidable` and `grain_undecidable`,
carried as release-note bead hy-buem. They are the additions decision 5 exists to
authorise, and the class does not move with the count -- the field CARRIES a
verdict at 16 values or at 19.

The `resolution.warnings[].code` row reads 17 rather than the `3519013`
measurement of 14, and is re-counted from `WARNING_CODES` at the commit that
adds the value, for the same reason: `ref_awaiting_sync` and
`ref_corroborated_late` were added after `3519013` (hy-lcgq/hy-gh-118 and
hy-7ejr), and hy-gh-282 adds `domain_ambiguous` -- an estate ambiguity distinct
from `multiple_domains`. All three are the additions decision 5 exists to
authorise: the field QUALIFIES rather than carries, so an added code is additive
and moves no number at 14, 16, or 17. The class does not move with the count.

The table row reads 20 rather than 19 because hy-pvbu added
`no_declared_sources`, which is the same kind of addition and is carried as
release-note bead hy-ltqz. Every other row is still the `3519013` measurement; this
one is re-counted from `VIOLATION_CODES` at the commit that adds the value, for
the reason the paragraph above gives about moving referents. That change also
moved `SCHEMA_VERSION` to 3, and NOT for the code: every `violations` entry
gained a `recovery` key, which is a shape change under decision 4 and the one
half of that change decision 5 does not cover.

- **A field that CARRIES a verdict denies the verdict on an unknown value.** The
  client treats the answer as NOT APPROVED: not governed, not valid, not
  git-linked, the plan not approved, the error not recovered from. This is the
  named cost below, and it is why an old client refuses a plan Hyperset
  validated.
- **A field that QUALIFIES a verdict does not invalidate a verdict the client
  did recognise.** An unknown value there is an UNDISCHARGED CAVEAT, and the
  obligation is positive rather than merely restrictive: the client MUST SURFACE
  it with the answer -- carried through to whatever it shows a human, never
  silently discarded -- and it must not act on the value as though it understood
  it. The answer it rides on stays what it was. For `severity` specifically,
  surfacing is not sufficient on its own: an unrecognised severity is treated as
  no less blocking than the strictest severity the client knows.

The `warnings[].code` row is what forced the split. A warning rides on an answer
that stays `governed` -- `docs/v0-foundation.md` section 7 says so normatively
for an uncorroborated ref. Under the other reading, where an unknown value
anywhere invalidates the whole answer, adding one warning code would break every
client for every governed bundle carrying it: exactly the "an added value is
actually breaking" outcome decision 5 exists to rule out, arrived at through the
precondition that is supposed to make it safe. The narrow reading is the ruling.
It is also already true structurally for the one case that can act on a warning
-- `fixable_warnings` filters by `RETRYABLE_WARNING_CODES`, so an unknown code is
not retried -- and the discipline this adds is that it is not dropped either.

"Surfaced, and treated as no less blocking than the strictest known value" is a
sentence an implementer can fail and a test can catch. "Not ignored" was not.

**Default-deny binds in two places, because a rule about a party we do not
control is not a mechanism.** This repository has rejected that shape twice --
`warning()` gates its vocabulary rather than trusting call sites, and
`FindingCandidate` makes an approval unsayable rather than forbidden -- and ADR
0019 cites both. So:

- **Published normatively in `docs/v0-foundation.md` section 7**, per field, by
  the change that first serves a value into it. That is what a client we do not
  control gets, and it is the honest limit of what we can do for it.
- **In force on the client surfaces Hyperset itself ships, and GATED.** The
  planner prompt (`hyperset/planner/prompts/planner.md`) and the served tool
  descriptions (`hyperset/transport/operations.py`) are instructions to a real
  client, and measured on `81b3b1b` neither carries an unknown-value rule: the
  prompt enumerates six warning codes, says "act on the code and not on the
  wording", names `no_match`, `observed_only`, `warnings` and `invalid`, and
  says nothing about a value it does not recognise. Grepping `hyperset` for any
  unrecognised-value language returns nothing. Writing the sentence is not
  enough: an ungated surface drifts exactly the way the violation-code field
  drifted for sixteen codes. It gets the `WARNING_CODES` treatment -- an
  assertion over those surfaces plus a companion negative test proving the
  assertion can fail.

**The obligation that is NOT a precondition: publishing the enumeration.** An
earlier form of this ruling made "the field is published as an exhaustive
enumeration" the condition. Measured, that condition is false where it matters:
`hyperset/bundle/plan.py` emits sixteen violation codes on `81b3b1b` and
`docs/v0-foundation.md` names none of them; the whole document mentions one,
`prohibited_source`, and not as a vocabulary. Under the earlier form the number
would have had to move for three added codes on a documentation technicality,
which buys no client anything -- a client that never had the enumeration could
not have branched on the closed set either way. So publication is an obligation
with a filed bead (hy-ruui), not a gate. It buys documentation; default-deny
buys the safety, and it covers the unpublished field too.

**That obligation comes due at the first real external consumer.** The argument
above -- moving the number for an unpublished field buys no consumer anything --
narrows the door but does not close it, because a field can stay unpublished
indefinitely and the argument still holds, which would leave the debt owed in
principle and never owed on a date. So it gets the same trigger this ADR gives
the rejected vocabulary-version option below: the first consumer outside this
repository is the event that makes publication due, and that is not today. Until
then the debt is real and default-deny is what carries the field.

What publication costs is worth stating precisely, because "write a doc section
and hope" is the wrong estimate. The section 7 contract test already asserts
equality in BOTH directions between that document and the served vocabularies --
for `WARNING_CODES`, `OPERATION_ERROR_CODES` and `HTTP_ERROR_CODES`. It has
never been pointed at the violation-code field, which is why every gate stayed
green while sixteen codes went undocumented. So the mechanism is proven and the
work is to extend it to one more field, not to invent enforcement. Said that way
because the first version of this paragraph said the enforcement already existed,
which was true of the shape and false of the field.

**A new value is still a wire change.** Section 7 entry plus a `release-note`
bead, per decisions 2 and 3 and ADR 0015's register. Not moving the number is
not permission to ship silently.

**The honest cost, written here rather than discovered by a client.** An old
client applying default-deny will REFUSE a plan that Hyperset validated, when it
meets a violation code it does not know. Additive on the wire, conservative in
the client, and it fails in the direction a governance product should fail --
but it is a real cost and it lands on real users. **Hyperset's own planner is
the first client to pay it.** Concretely: until the shipped-surface bead lands,
hy-gh-128's benefit does not reach our planner, because it will meet
`field_expression_undecidable`, `filter_undecidable` and `grain_undecidable` and
have no branch for them. That is not a regression -- those cases are `invalid`
today -- but it is a gap that would otherwise be found in production rather than
in this paragraph.

**The third answer, considered and rejected: a separate vocabulary version
alongside `SCHEMA_VERSION`.** It gives a client a number to compare, and it is a
second number nobody consumes: ADR 0015 says there is no publication event, so
it would ship unread. Default-deny buys the same protection with no new field.
The trigger to revisit is a real external consumer, and that is not today.

## Consequences

- The question "does this break move the number?" is answered by asking which
  direction changed, which is a fact about the diff rather than a judgement
  about severity.
- A client cannot detect a request-shape break by reading a version off a
  response. It detects it by being refused, which is why point 2 requires the
  refusal to be documented, and why a request-shape constraint belongs in the
  served schema wherever the schema can express it (`hy-3dtc`): a client that
  generates its calls from the schema should not be able to construct the
  broken shape at all.
- `SCHEMA_VERSION` stays 1 for the coverage-claim change. This ADR is what a
  reader wondering why is pointed at.
- If v0 ever needs a request-contract version, it is a **new** field on the
  request or the catalog, not a second meaning for this one.
- The question "does an added value move the number?" is now answered by asking
  whether default-deny is published for that field, which is a fact
  about the contract rather than a judgement about the value. Where it is not,
  the added value is a break.
- Two obligations land outside this ADR and are filed rather than assumed:
  hy-ruui for the undocumented violation-code vocabulary and the contract test
  that would have caught it, and hy-9nrf for default-deny in the planner prompt
  and the tool descriptions with the gate that keeps it there. Both are named
  here rather than one named and one described, because a reader of the ADR
  could find hy-ruui and could not find the other, while
  `docs/adr/README.md` named both: the index was more traceable than the
  document it indexes. Neither is a precondition for decision 5; both are debts
  decision 5 creates and names, and hy-ruui's falls due at the first consumer
  outside this repository rather than in principle.
- A mechanised gate cannot decide a case an ADR has not decided. Before this
  amendment, whether the section 7 check fired on a branch adding a served value
  depended on which reading of decisions 1 and 4 the author held: under one the
  constant moves and the check demands the announcement, under the other it does
  not and the branch passes in silence. Two branches doing the same thing got
  different treatment for a reason that was not about either branch.
