# 0019: Assist mode may reason; governance may not

Status: accepted, with one element marked PROPOSED and awaiting Overseer
ratification (the served status name, decision 3).

Extends the standing invariant "Hyperset heuristics never interpret semantics"
by scoping it to governance rather than deleting it. It changes no part of ADR
0009's vertical-slice order, ADR 0012's authority model, or ADR 0017's two
corroboration invariants, all of which apply in both modes.

Out of scope, deliberately: the "external execution stays external" boundary
and hy-gh-127 (whether Hyperset may judge a result an agent supplies). That
needs its own ADR. Folding it in here would get the easy ruling accepted and
carry the hard one through on its back.

## Context

Hyperset is optimised end to end for one question: did the agent use what a
human pre-wrote in Git? Everything downstream of `parse_context` is exact --
exact domain names (`_select`), exact set membership for the coverage claim
(`_covered`), exact source-native identity for evidence (`ObservedEvidence
Resolver`), string equality for plan validation (`hyperset/bundle/plan.py`).
GitHub #70 deleted the one place a heuristic remained, and that deletion was
correct.

The cost of that design is now measured rather than suspected. It answers the
enumerated manifest, which the Overseer's audit puts at roughly a tenth of real
questions. On the rest -- a novel join, an unenumerated grain, a field derived
on the fly, a source Git never declared, a reconciliation across two domains --
the system returns `no_match`, `observed_only`, or `unverifiable`. It is not
wrong to do so. It is silent exactly where an analyst needs the most help, and
a substrate that says "invalid" to every hard question trains an agent to stop
asking them.

The human decision is an **extension, not a pivot**. Governance stays as it is
for the covered slice. A second mode is added for what the covered slice goes
quiet on. Four issues -- hy-gh-123 (assist plan validation), hy-gh-124
(discovery and candidate ranking), hy-gh-125 (a general reconciliation engine),
hy-gh-126 (semantic retrieval over the catalog) -- are all blocked on the
boundary between the two, plus hy-gh-129 (cross-domain composition), which is
where the boundary is hardest.

"Assist mode may reason, governance may not" is one sentence and four
unanswered questions. This ADR answers them, because a boundary that lives in
one person's head is not a boundary, and four issues about to be built against
it would each infer a different one.

## Decision

### 1. The mode is a property of each CLAIM. The answer reports the weakest one present.

Not of the request, and not of the answer as a whole.

Not the request, because a request-level flag makes the mode something a caller
can ask for. A caller that asks for governance gets silence where governance is
silent -- which is today, and is what the extension exists to fix -- and a
caller that asks for assist would be asking Hyperset to relabel what it
already knows. Neither is a question the caller is in a position to answer:
whether a claim is governed is a fact about the customer's Git commit, not a
preference.

Not the answer as a whole, because the interesting case is mixed and it is
already scheduled. hy-gh-129 composes two domains, one of which may declare the
join and the other not. A single answer-level label would have to round -- up,
which lies, or down, which discards the governed half a customer paid to
maintain.

So every unit of guidance a served answer carries is labelled with the
provenance class it came from. `linked_evidence.observed_assets` already does
this with `governance: git_linked | observed_only`; this generalises the idea
rather than inventing it. `resolution.status` stays what it is today -- a
summary -- and summarises to **the weakest class any claim in the answer
carries**. A reader that only branches on the status is never told the answer
is better than its worst part.

Two consequences a caller can rely on:

- **Assist is never requested into existence.** It runs where governance is
  silent, whether or not a caller asked.
- **Assist can be refused, never demanded.** A caller may opt out and receive
  the governed answer alone, including the silence. It may not ask for an
  assist claim to be labelled governed. Refusal is available because an
  evaluation arm, a conformance run, or a customer with a compliance
  obligation has a real reason to see governance alone; the opposite direction
  has no legitimate use.

### 2. Leakage is prevented by construction, and checked on the payload.

The failure mode is not assist mode existing. It is an assist-derived value
arriving in a governed field, where provenance, an authority, and a commit
say it came from the customer. Three mechanisms, in decreasing order of how
much they depend on anyone remembering:

**(a) Governed sections are built from a snapshot and nothing else.** The
functions that produce `instructions`, `context_authority`, and every
`domain_graph` edge carrying `evidence: "git"` take one immutable
`ContextSnapshot` as their only semantic input. An assist producer has no
snapshot to pass and no parameter to pass one through, so an assist value
entering a governed field is not expressible rather than merely forbidden.
This is the shape `FindingCandidate` already uses to make "the processor
proposed an approval" unsayable. Those three are not the whole list of governed
sections -- (c) names the fourth, which this mechanism does not reach, and says
what covers it instead.

**(b) Assist output lives in its own section and is never merged into a
governed one.** It is not appended to `instructions`, not folded into
`linked_evidence.observed_assets` as `git_linked`, and not given a
`domain_graph` edge whose `evidence` is `git`. A caller that reads only the
governed sections of an assisted answer gets exactly the governed answer,
byte for byte, that it would have got with assist switched off. This is the
property to write the test against.

**(c) A conformance check on the served payload, not on the producer.** Stated
over DERIVATION rather than over presence:

> **Every value in a governed section of a served answer is a pure function of
> the pinned `ContextSnapshot`, the configured `ContextSource`, and -- for
> `git_linked` evidence entries only -- the observed asset versions pinned in
> that same answer's `provenance_refs`. Of nothing else.**

Still decidable, and it still catches an assist-produced value, which is a
function of none of the three. It catches a leak whatever path introduced it,
including a path nobody thought of when (a) and (b) were written. The four
assist issues may not delete or weaken this check; a change that makes it fail
is the bug.

**The governed sections are four**, and the fourth is why the rule has a third
input: `instructions`, `context_authority`, every `domain_graph` edge carrying
`evidence: "git"`, and **the `git_linked` entries of
`linked_evidence.observed_assets`**. The `observed_only` entries of that same
list are not governed -- they are labelled observation and (b) is what keeps
them from being relabelled.

The fourth was previously protected by (b) alone and named as governed nowhere,
which put it outside (c). That is the wrong place for it, because it is the
CITATION surface: the evaluator derives a recording's `source_refs` from
`linked_evidence.observed_assets[].ref` at record time (`hyperset/evals/run.py`,
`source_refs`) -- every entry, both classes. A leak there is cited as the source
a scored answer rested on. The check reaches the governed half of that list;
the `observed_only` half is cited as what it is, an observation nothing
approves, which is (b)'s job and not this rule's.
Leaving it outside the only check stated over derivation would repeat the shape
of the hy-gfs7 gap -- a surface left permissive because the rule that would have
reached it was scoped elsewhere -- and a backstop that does not cover the
citation surface is not the backstop this ADR claims.

It is also the only governed section mechanism (a) does not fully cover, which
is why listing it is not enough on its own. Measured on a mixed bundle at
`81b3b1b`, a `git_linked` entry carries `ref` and `connector` from the
snapshot's own `evidence_refs`, `linked_version_id` from that same declared
entry, and `observed_version_id`, `observed_version`, `content_sha256`,
`normalized`, `asset_id`, `connection_id`, `external_id` and `asset_type` from
the observation store: a third input, neither the snapshot nor the source.

**The bound on that third input is what keeps the check worth having.** It is
not "the observation store", which would admit any observation at all. It is
the versions the answer itself pins under invariant 8, in two halves:

> Each pinned `observed_version_id` must RESOLVE to a stored observed asset
> version, and every remaining field must be an attribute of one of those
> resolved versions or of the asset it is a version of.

**The pinning alone is not a check, and saying it was is the mistake this
paragraph now records.** An earlier draft offered as proof that every non-null
`observed_version_id` appears in `provenance_refs` necessarily, because
`_evidence_provenance` builds those refs from that same list. True, and it
proves nothing: `provenance_refs` is a PROJECTION of `observed_assets`, so
nothing in the list under check can fail to be pinned. Measured by handing that
projection a doctored entry:

```text
doctored: one git_linked entry with an invented observed_version_id
provenance_refs then built: ['observed_version:oav-FABRICATED-BY-ASSIST']
is the invented id pinned in that same answer's provenance_refs? True
```

An implementer who reads the pinning as a membership test writes a check that
passes a fabricated entry. What catches it is RESOLUTION: a fabricated id has no
stored version behind it, so no `observed_version`, `content_sha256`,
`normalized`, `asset_id`, `connection_id`, `external_id` or `asset_type` can be
an attribute of it, and the entry fails on its attributes rather than on its
identifier. The pinning narrows WHICH observations are admissible; resolution is
what makes them real. Neither half is the check on its own.

**Is the rest of it structural, or a coincidence in today's code?** Asked
because the wrong answer is the hy-t8nz shape: a guarantee that is really an
accident scheduled for removal. Three parts, and only one of them is structural.

The part that does not depend on the code: the rule is stated over the SERVED
PAYLOAD, so it is checked rather than assumed. A `git_linked` entry whose value
derives from a version the answer does not pin, or pins and cannot resolve,
fails this check. Where hy-t8nz's protection would have vanished silently on the
day the disclosure improved, this one reports on the day the relation breaks.

The part that is true today, measured from the WRITERS rather than the readers,
because "which constructors did you check" is the weaker question:
`_evidence_provenance` is called at exactly two sites; one place builds a
non-empty `observed_assets` and `_empty_evidence()` builds the empty one
`_no_match` serves with no provenance at all; and exactly one step mutates the
list afterwards, the context-budget reduction, a comprehension that empties each
entry's `normalized` and preserves every entry and every id. `ContextBundle(`
appearing twice is not a second assembly point: one of the two is the inner
`build()` closure that `_bundle` calls on trimmed and reduced evidence, both
times passing the same `provenance_refs` through unchanged. Nothing adds an
entry after the projection.

The part that is NOT structural, said plainly: nothing in the `ContextBundle`
type states any of this. hy-c4bk carries the one-way assertion -- every non-null
`observed_version_id` in `observed_assets` appears in `provenance_refs` -- and
that assertion is a guard on the projection STAYING a projection, not a check on
whether a pinned id is real. Only the resolution half catches a fabricated one,
and only a conformance implementation performs it.

That assertion is now stated, over every constructor rather than over one
dataset:
`tests/postgres/test_context_bundle.py::test_every_observed_version_served_as_evidence_is_pinned_in_provenance`
resolves `governed`, `mixed`, `observed_only`, `no_match` and a budget-reduced
governed answer, and requires the relation of each. It is green at base and
cannot be otherwise while the projection holds, which is what a preservation
guard is; it was demonstrated red by appending one entry to `observed_assets`
after `_evidence_provenance` runs, reporting
`assert ['superset:dataset:PROOF'] == []`. What this converts is "the
conformance check would report it" into "CI catches it" -- better, and not the
same thing.

**This adds an input to the derivation rule and changes nothing about how a
link is made.** `git_linked` is assigned by the governed linker on exact
source-native identity: in `hyperset/bundle/resolver.py` only refs the snapshot
declared and re-resolved snapshot gaps receive it, and every ref a caller named
receives `observed_only`. Never on similarity, never by assist. Widening what
the conformance rule admits as an INPUT is not licence to widen what earns the
LABEL.

The first draft of this ADR said "must be PRESENT in the pinned snapshot's own
stored content", which sounds stricter and is not implementable: measured
against a real bundle on `bd14bac`, three classes of legitimate governed value
fail a literal presence test, and none of them is a leak.

- **The configured source's own identity.** `context_authority.repository` and
  `.path` come from the `ContextSource` row, not from the snapshot. The
  `ContextSource` is in the derivation rule for exactly this reason.
- **Snapshot identity and metadata.** `context_snapshot_id`, `content_sha256`,
  `commit_sha`, `committed_at` and `type` are facts ABOUT the snapshot rather
  than content IN it, so they appear in neither the stored files nor the
  normalized projection.
- **Constructed graph identifiers.** `domain_graph` node ids and edge relation
  names -- `domain:revenue`, `owns`, `defined_in`, `approved_for` -- are a
  deterministic projection Hyperset builds. Every one of them fails a literal
  presence test and every one of them is a pure function of the snapshot.

Written the first way, an implementer gets a check that fails on day one, and
the only exits are to weaken the check the ADR forbids weakening, or to start
carving exceptions into the thing whose job is to catch what nobody
anticipated. Both are worse than the sentence being right.

**Ranking is not linking, and this is where the tension is.**

ADR 0017 forbids resolving an ambiguous ref by picking a match and forbids
linking anything on display-name similarity. "Assist may rank" and "no false
name-similarity links" read as compatible and are not. The line:

> **Assist may order and propose. It may not produce an identity.**

A link is an assertion that a declared ref names this observed asset. Assist
may not make one, by any signal, including a signal that is right most of the
time. What assist may do is return candidates -- ordered, each with the signal
that ordered it stated -- and the candidate set keeps its label all the way to
the caller.

A candidate set of length one is still a candidate set. That sentence is not
worth writing unless the shape enforces it, because if the only thing a
consumer ever does is take element zero, "proposal" and "identification" are
the same operation under two names and the distinction is a formatting
convention. So it is enforced by what the assist shape CANNOT hold:

> **A governed link is the ordered pair (declared ref, observed asset version).
> Assist output has no field that can hold a declared ref in that position.**

A candidate names an observed asset and the request it answers -- a question, a
concept, a domain. It never names the manifest ref it would resolve, because
there is no slot for one. "This declared ref means that asset" is therefore not
expressible in assist output whatever the ranking concluded, and length one
changes nothing: the missing field is still missing when the list has one
entry. This is mechanism (a) again, pointed at the specific failure.

Two consequences follow, and they are protected by DIFFERENT mechanisms. The
difference is the whole safety argument, so it is spelled out rather than
summarised as "enforced by shape":

- **By shape: a candidate contributes nothing to `provenance_refs`, and the
  evaluator never sees one.** Invariant 8 is that returned context pins the
  exact Git commit and every selected observed version. A substituted ref
  appears nowhere in `provenance_refs`, and the evaluator's `source_refs` are
  derived at record time from `linked_evidence.observed_assets`, which a
  candidate never enters. A consumer that took element zero and cited it would
  have to invent the citation, because assist never put one there.
- **By value: plan validation is permissive by shape and refuses on content.**
  `AnalyticsPlan.source_refs` is an untyped `list`, so a candidate's
  observed-asset ref drops straight in and nothing is missing. What refuses it
  is a comparison against `instructions.approved_sources`, which is
  snapshot-derived: `unapproved_source` when the plan alone names it, and
  `observed_only_source` when the caller also put it in the directive so the
  bundle carries it as an observation. Both are ERROR, so the verdict is
  `invalid` either way. The boundary holds. It holds by a value check.

That distinction is not pedantry, because the two fail differently. A consumer
protected by shape stays safe however permissive its caller is. A consumer
protected by value stays safe only while it keeps checking against
snapshot-derived content. Nothing today is permissive by shape AND unchecked by
value. Something built FOR assist could be, precisely because assist output is
the thing being handed around, which is why the reviewer check below asks about
it directly.

The operational test, for a reviewer deciding a case this text did not
anticipate: could a downstream consumer substitute this output for a resolved
link and lose nothing? If yes, it is a link, and assist may not produce it.

**ADR 0017 and this ADR must be non-composable, not merely mutually silent.**

The attack is obvious once stated, and it is reached from the side neither
document is looking at. ADR 0017 refuses a declared ref observed on two
connections: `ref_ambiguous`, resolving to nothing, because two connections
carrying one native identity are two assets. Assist then ranks those two by
name similarity or by usage and returns the winner. The caller takes it. The
net effect is the auto-pick ADR 0017 forbids, with one step of indirection, and
both documents were individually obeyed.

The clause that closes it:

> **This ADR grants assist no input that ADR 0017 refused to governance. Where
> ADR 0017 produced an ambiguity, the ambiguous members are not an assist
> ranking input.**

Assist may DESCRIBE the ambiguity -- it is already served as a finding, naming
both connections -- and **may not order its members by any signal about which
one is right.** Ordering them that way is the pick. "Describe" is narrowed to
citation by code and ref below, under "the guarantee is ownership"; read that
before building on this sentence.

The qualifier is doing real work and an earlier draft omitted it. Hyperset
already orders those members: `hyperset/context/evidence.py` builds the
sentence with `', '.join(sorted(...))` over connection ids. A flat "may not
order" is therefore false the moment someone greps for `sorted(`, and a rule
that reads as violated by existing correct code is a rule people learn to
discount. A determinism sort over opaque identifiers carries no signal about
correctness. A ranking does. Only the second is the pick.

**What protects this today is not the rule, and the thing that does protect it
is scheduled for removal.** The ambiguity's members live inside an English
sentence. There is no machine-readable member collection, so there is nothing
for assist to rank and nothing for a caller to index into without parsing prose
this codebase forbids parsing everywhere else. That is a better guarantee than
the rule, and nobody designed it -- it is a side effect of an unstructured
disclosure, and hy-rvh1 is open to structure exactly that, correctly, because a
client cannot act on an identifier that exists only in prose.

So the clause is written now to survive that fix rather than to describe
today's accident:

> **When an ambiguity's members become a structured collection, that collection
> is ordered by construction. Assist may not REORDER it and may not attach a
> ranking, a score, or a confidence to its members.**

Written now this is one clause. Discovered later by whoever implements hy-rvh1,
it is an argument about whether ADR 0019 was already violated. hy-rvh1 is
blocked on this wording for that reason.

**That clause is defence in depth. The guarantee is ownership.**

Surviving hy-rvh1 is all the clause does. On the day the members become a
structured collection, they are orderable, and the only thing standing between
an assist implementation and a ranking is a sentence no consumer has ever
tested. The first attempt to make it structural pointed the pair rule at this
case: assist output has no field that can hold a declared ref, so a ranked pair
could not become the pick. That attempt fails on a measurement. The ambiguity
record ALREADY CARRIES a declared ref, supplied by governance, because saying
which declared ref is ambiguous is the record's entire purpose:

```text
{'code': 'ref_ambiguous',
 'ref': 'superset:dataset:ae48881d-334f-54a7-94e8-1ffcc73866e2',
 'message': "evidence ref '...' is ambiguous: observed on connections ..."}
```

That `ref` is the manifest's declared `EvidenceRef`, verbatim: the finding is
built from the ref the resolver was asked to resolve
(`ObservedEvidenceResolver.resolve` in `hyperset/context/evidence.py`).
Post-hy-rvh1 the record becomes `(declared ref, members[...])`.

So the pair rule protects ASSIST'S OWN output types. It does not reach a
GOVERNANCE-DEFINED record type that already has the field -- and that record is
precisely the one this ADR invites assist to "describe". Narrowing its members
to one entry yields exactly the forbidden pair, in a shape governance itself
defined, by a verb -- select, filter, omit -- that none of the prohibitions
above names. This is a different hole from the one reviewer question 3 closes:
that one is a permissive CONSUMER, this one is a permissive RECORD TYPE.

What closes it is one sentence about ownership, not a wider pair rule:

> **The ambiguity record is governance's. Assist may cite it by code and ref.
> It may not author one, transform one, filter its members, or reorder them.**

Reference-only, and that is what makes the guarantee structural again: assist
never holds the collection, so there is nothing to reorder, rank, or narrow,
whatever shape hy-rvh1 gives it. The ordering clause above stays, against a
consumer that builds its own copy of the members and orders that -- but it is
defence in depth and not the guarantee, and this sentence says so because a
reader who mistakes the two will be watching the rule instead of the ownership
on the day the disclosure becomes structured.

The same clause is what keeps display-name similarity harmless as a ranking
signal. Assist may use it, and for discovery over an unenumerated estate it
will be one of the better signals available. It cannot become a name-similarity
link, because the output it feeds has no slot for the declared ref that would
make it one. The ban survives composition because it is enforced by shape at
the other end rather than by a rule about signals at this end.

**The composition is stronger than the paragraphs above claim, and the extra
strength is in the code rather than in this document.** Said out loud so nobody
weakens it not knowing it was load-bearing: a collapsed candidate that a caller
sends back as `asset_refs` can be neither laundered into governance nor done
anonymously. `hyperset/bundle/resolver.py` assigns `git_linked` only to refs the
snapshot declared and to re-resolved snapshot gaps; every ref a caller named is
`observed_only`; and the directive is echoed verbatim into `request` on the
answer. So the round trip is labelled as observation and is on the record as
the caller's own move. An ADR that understates its own guarantee invites
someone to delete the line that provides it.

**Reviewer check for a future assist feature**, three questions, in order:

1. Can its output name a declared ref, in the position that would make it a
   link?
2. Is its input a set governance already refused to disambiguate?
3. Does its consumer either require a field assist cannot fill, or check its
   input against snapshot-derived content?

Yes to 1 or 2 is the finding. So is **no to 3** -- a consumer that is permissive
by shape and performs no value check is the hole neither of the first two
questions can see, and it is the one an assist-shaped consumer is most likely
to be.

### 3. The served vocabulary: one proposed value, and one verdict that does not move.

Two enumerations are served today. They are treated differently, and the
difference is the point.

**`resolution.status` (`governed | mixed | observed_only | no_match`) gains one
value: `assisted`** -- PROPOSED, requiring Overseer ratification. It names an
answer whose claims are assist-derived and which carries no governed content.
The blended case does not need a second new name: `mixed` already means "more
than one provenance class in this answer, each part labelled", which is
precisely governed-plus-assist. Decision 1 makes the per-claim labels the
load-bearing part, so the summary does not have to enumerate every combination.

The name is proposed rather than decided. The Overseer was asked to name it and
has not answered, and four issues inventing four names is worse than one
unratified proposal they can all read. A reviewer who prefers `derived`,
`reasoned`, or `advisory` should say so on this ADR; the ruling that matters
is that there is exactly one new value and that `mixed` absorbs the blend.

**`PLAN_STATUSES` (`valid | valid_with_gaps | warnings | invalid |
unverifiable`) gains nothing FROM ASSIST.** A plan that no governed rule covers
is `unverifiable` today, and it stays `unverifiable` after assist mode ships,
because the word is true: Hyperset did not verify it. An assist risk assessment
does not verify a plan; it reasons about one. (`valid_with_gaps` was added later
by hy-gh-285 as a deterministic validation disclosure -- an otherwise-valid plan
against a domain that declares nothing in some section -- not by assist, which
still adds no status and softens no verdict.) hy-gh-123's value is the reasoning that rides alongside
the verdict, not a better-sounding verdict, and a status that softened under
assist would be the leak of decision 2 wearing a different hat.

**Assist never changes a governed verdict.** It annotates. That is the general
form of the previous paragraph and it is the rule.

**On `SCHEMA_VERSION`.** ADR 0018 rules that a change to what a caller RECEIVES
moves the number. For this extension the question is moot: assist output
arrives in a new section, which is a shape change under anyone's reading, so
the number moves when the first assist surface ships. The general question --
whether a new VALUE in an existing served enumeration moves it, when the shape
is untouched -- is left open here rather than settled in passing. ADR 0018's
decision 1 and decision 4 can be read to disagree about it, and it deserves its
own ruling rather than a clause in an ADR about something else.

Documentation obligations, per ADR 0018 and ADR 0015: the section 7 entry and
the `release-note` bead are filed by the change that first SERVES the value,
not by this ADR. Nothing is served yet, and a document promising a status no
response carries is the defect
`tests/unit/test_section_7_matches_the_served_contract.py` exists to catch.

### 4. Floors: what assist may not do, even in assist mode.

The extension framing means these hold in both modes. They are not a summary of
the above; each one is reachable by a plausible assist feature.

1. **No governed label.** Nothing assist produces is `governed`, `approved`,
   `canonical`, or `trusted`, in a status, in a field, or in prose. `v0-
   foundation` invariant 7 already says this for `observed_only`; assist
   inherits it.
2. **No identity.** ADR 0017 holds in both modes: no link on similarity, no
   ambiguity auto-picked, no candidate set collapsed to a resolution. Enforced
   by the shape rather than by this line -- assist output has no field that can
   hold the declared ref such a link would need -- and, for the one record that
   already carries a declared ref, by the ambiguity record being governance's
   to hold: assist cites it, and never holds it.
3. **No execution.** Invariant 6 holds. `execution.performed_by_hyperset` and
   `result_validated_by_hyperset` stay `false`. Assist reasons over metadata
   and observations; it does not run the customer's SQL to find out. ADR 0032
   consolidates this into a permanent PLATFORM boundary (not a v0 default) and
   re-scopes the result-trust ambition (#127) as a consumer concern, out of core.
4. **No authority by accumulation.** Assist output is never persisted as
   governed context. The only path from a proposal to authority remains a human
   Git change, and `ReviewRepository.approve` remains the only approval call
   (`CLAUDE.md`). A ranked candidate that has been right a thousand times is
   still a candidate.
5. **No overriding a governed answer.** Assist runs where governance is silent.
   Where governance speaks, its answer is served, and assist may annotate it --
   it may not replace it, reorder it, or argue with it in the governed section.
   A disagreement between the two is a disclosure, which is what
   `linked_evidence.conflicts` is for.
6. **No suppressing a disclosure.** A prohibition, caveat, conflict, or
   uncorroborated ref is not dropped, softened, or ranked out of view because
   assist scored something else higher. Prohibitions are already exempt from
   every bound in the catalog and the context budget; assist does not get to be
   the first thing that can hide one.
7. **No question-reading in the governed path.** GitHub #70's deletion stands.
   Assist may read the question -- that is most of what it is for -- and may
   PROPOSE a domain from it. The governed selector still requires the exact
   name in the directive, so a proposal becomes a selection only by the caller
   sending it back.
8. **No borrowing the determinism guarantee.** `bundle_id` is a content hash,
   and `docs/v0-foundation.md` section 6 promises the bundle is deterministic
   for a pinned commit, repository state, and directive. Assist output need not
   be deterministic; if it is folded into that hash, the guarantee is gone for
   the governed slice too, and caching, equality, and recorded evaluation
   comparisons go with it. So `bundle_id` keeps hashing the governed answer
   only, and assist content carries its own identity.
9. **No unattributed reasoning.** Every assist claim names what produced it --
   the evidence it read and the reasoning artefact, model and prompt included.
   Assist need not be reproducible; it must be accountable, which is the same
   standard `Recording` already holds the evaluation arms to.

## Consequences

- Four blocked issues can proceed. Each one now has an answer to "where does my
  output go and what may it claim": hy-gh-124's ranking is candidates, never
  links; hy-gh-125's typed disagreements are disclosures that may reach
  `linked_evidence.conflicts` because a disagreement is not a claim about
  meaning; hy-gh-126's retrieval proposes and never selects; hy-gh-123's
  reasoning rides beside a verdict that does not move.
- The per-claim rule of decision 1 has a cost, and it lands on hy-gh-129: a
  composed answer has to carry provenance per domain rather than per bundle.
  That work is real and is not avoided by this ADR; it is made explicit by it.
- The conformance check of decision 2(c) is a new obligation on every future
  serving change, not only the assist ones. It now covers the `git_linked`
  entries of `linked_evidence.observed_assets`, so a change to how that section
  is built has to say which of the three inputs each new field comes from.
- The status name in decision 3 may change under ratification. Nothing else in
  this ADR depends on which word wins.
- `mixed` is now doing more work than it was defined for. If it turns out that
  callers need to distinguish governed-plus-observed from governed-plus-assist,
  that is a second value and a second ADR, and it will be visible as a real
  need rather than guessed at now.
- The audit's "90%" is a claim about the question mix, not a measurement this
  repository has made. Nothing here should be read as evidence for the number;
  it is the reason the decision was taken, and the evaluation work is where a
  number would come from.

## Rejected alternatives

- **Delete the no-semantics invariant.** It is the reason the governed answer
  is worth anything. An agent that cannot tell which half of an answer a human
  approved has been given a more confident version of the raw-metadata baseline
  the evaluation exists to beat.
- **Make assist a request flag.** Decision 1 gives the reason: it makes a fact
  about the customer's commit into a caller preference, and it lets a caller
  ask for a label rather than for an answer.
- **One label for the whole answer.** Rounds up or discards the governed half.
  The mixed case is not exotic; it is hy-gh-129, already filed.
- **Let assist resolve an ambiguous ref when its confidence is high enough.**
  This is ADR 0017's rejected alternative with a threshold attached. Two
  connections carrying one native identity are two assets in the real world,
  and a confident guess about which one a commit meant is still a guess about
  the customer's intent.
- **State "a candidate set of length one is still a candidate set" and leave it
  at that.** It was the first draft of decision 2 and it does not survive
  contact with a consumer that takes element zero: under that consumer the
  sentence describes a naming convention rather than a boundary. A rule whose
  only enforcement is that everyone reads the rule is the shape this repository
  rejects for warning codes and for approval, and an assist layer is a larger
  surface than either.
- **Rely on ADR 0017 and this ADR each being individually correct.** Each is;
  the composition is not. Ranking the members of a set ADR 0017 refused to
  disambiguate reaches the forbidden result while obeying both documents, which
  is why the non-composability clause is stated rather than left to follow.
- **Say the boundary is "enforced by shape at the consuming end" and stop
  there.** True for `provenance_refs` and for the evaluator, false for plan
  validation, which is permissive by shape and refuses on value. One summary
  covering both would have hidden the only class of hole either of the first
  two reviewer questions misses, which is the reason there is a third.
- **Point the pair rule at the ambiguity case.** The previous round's answer to
  "what makes the ordering clause structural": assist output has no slot for a
  declared ref, so a ranked pair of members could not become the pick. It does
  not close it, and the measurement is one line -- the ambiguity record already
  carries the declared ref, because that is what the record is FOR. The pair
  rule governs assist's own output types; this record is governance's, and the
  sentence that reaches it is about who owns it.
- **Say 2(b) covers the `git_linked` evidence entries and 2(c) does not.** It
  is the cheaper edit -- no third input, no weakening of a check whose strength
  is that it names its inputs. It also puts the field the evaluator derives
  `source_refs` from outside the only rule stated over derivation, which is the
  rule that catches a path nobody anticipated. The cost of the third input is
  paid back by bounding it to versions the answer already pins.
- **Describe the ambiguity protection as it works today.** Today it works
  because the members are prose and there is nothing to rank. That is an
  accident of an unstructured disclosure, hy-rvh1 is open to remove it for good
  reasons, and a rule written against the accident expires silently on the day
  the disclosure improves.
- **Let assist upgrade `unverifiable` to `warnings`.** The verdict would then
  mean two different things depending on a mode the caller cannot see, and the
  agent would learn that a hard question can be made easy by asking differently.
- **Prevent leakage by convention and code review.** This repository has
  already rejected that shape twice -- `warning()` gates its vocabulary rather
  than trusting call sites, and `FindingCandidate` makes an approval
  unsayable rather than forbidden. An assist layer is a larger surface than
  either.
- **Settle the "new enum value" version question here.** It is a real question
  and it is not this ADR's. Answering it in passing, in a document nobody will
  think to search for it in, is how the request-versus-response question ended
  up buried in a section 7 aside until ADR 0018 went looking.
