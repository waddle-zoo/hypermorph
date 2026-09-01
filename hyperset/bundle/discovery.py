"""Ranked candidate sources for a claim the governed corpus does not cover
(hy-gh-124 slice 1, under ADR 0019).

The governed path answers the enumerated manifest and goes silent everywhere
else. `_covered` is the sharpest instance: a caller names a domain and states
the concept terms its answer needs, one of those terms is not declared, and
the whole request is refused with `domain_does_not_declare` and an empty
bundle. Git genuinely says nothing -- so governance is right to say nothing --
but the estate is not empty, and "no governed source exists for this, and here
is what the estate actually carries" is a better answer than silence.

The other entry point is maximal silence rather than the sharpest (hy-xq55).
`_select` refuses `unknown_domain` when the caller names a domain no configured
context declares, so Git says nothing WHATSOEVER rather than nothing about one
term -- which is hy-gh-124's headline case, "no governed source exists for
churn", in its purest form. It grows the same ranked list, under
`domain_is_configured=False`, and can never carry a proposal; see `_proposal`
for why that is the claim and not a gap. The remaining two refusals stay silent
on purpose and `_discovered_candidates` says which and why.

This module produces that second half, and nothing else. It is assist output
in ADR 0019's sense, so three properties are structural rather than asserted:

**It cannot produce an identity.** A governed link is the ordered pair
(declared ref, observed asset version). A candidate carries exactly one `ref`
and it is the OBSERVED asset's own source-native identity -- there is no second
field, so the pair is not expressible whatever the ranking concluded. That is
also why lineage proximity, which ADR 0017 and hy-gh-124 both name as a
plausible signal, is deliberately absent here: stating that a candidate is
downstream of a ref the commit declared would put a declared ref in a
candidate's own output, beside an observed asset, which is the shape the pair
rule exists to refuse. It needs its own design, not a field on this one.

**It cannot be handed an ambiguity governance refused to break.** The
`ref_ambiguous` record is governance's. Nothing here reads one, cites one, or
orders its members; candidates are drawn from observed assets directly.

**It ranks on stated signal or it does not rank.** Every candidate carries the
signals that placed it, each with the value it was read from, and every
disagreement with Git that was found. Display-name similarity is not a signal
here even though ADR 0019 permits it: hy-gh-124 asked for observed signal, and
a rank a reader cannot check against evidence is a confidence number wearing
an ordinal.

## The proposal, and the word hy-gh-124 asked for that it cannot use

hy-gh-124's acceptance asks for "a strong-signal canonical suggestion, labeled
derived". ADR 0019 floor 1 forbids `canonical` in a status, in a field, and in
prose, and the ADR is the later ruling. The thing the issue wanted survives the
word: `proposal` names the candidate the evidence separated, if one did, and
says so in the vocabulary the section already used for every rank.

It reads the existing order and adds no signal, no evidence, and no new read.
What it adds is an ANSWER to the question a list of five leaves open -- did
anything stand out, or is this a stable sequence over sources nothing
distinguishes. Rank 1 is served either way; only the proposal is withheld, and
a withheld proposal states which of the three reasons withheld it.

Only `PROPOSING_SIGNALS` can carry one, and the exclusion is the claim: a
proposal rests on the customer's own commit engaging the source, never on
`source_freshness`, because a timestamp says a source moved and not that it is
the right one, and never on `declared_references`, because the estate pointing
at a source is the estate's opinion of itself -- the same reason that signal
already ranks below both Git ones. A source carrying any disagreement is never
proposed, which
takes nothing off the list -- floor 6 keeps it ranked and stated -- and a tie
between two equally-engaged sources is left as a tie, because breaking it would
mean promoting the freshness tiebreak that merely ordered them into a finding.

## Two of hy-gh-124's five signals are not here, and why

Said in the module rather than in a commit message, because a reader deciding
whether to trust a rank needs to know what it could not see:

- **lineage proximity: observable and withheld**, for the pair-rule reason
  above. DataHub datasets carry `normalized.upstream_dataset_urns`. hy-o64i.
- **ownership match: not attempted.** Manifest owner refs (`team:finance-data`)
  and DataHub owner urns (`urn:li:corpuser:...`) are different identifier
  spaces, and bridging them is display-name similarity with extra steps.

## What `declared_references` is, and the thing it is not

hy-gh-124 asked for usage frequency and this module used to say, correctly, that
no such thing was observable. Half of that is now wrong and the other half still
stands, so both halves are stated rather than one being quietly dropped.

What exists: `AssetRelationship` is written (hy-d7xh), so the references a
source's own payload declares are readable -- a Superset chart naming the dataset
it queries. What does not exist anywhere in either source: an execution count. No
projection carries how many times anyone ran a query, opened a dashboard, or read
a dataset, and nothing here infers one.

So the signal is named `declared_references` and its statements say "declares a
reference to", never "queries it often". A ranking input named for a thing it does
not measure is worse than a missing one: the reader cannot tell it apart from the
signal they wanted. hy-d7xh's usage half remains unserved.

Three limits, each a decision rather than an omission:

- **Direct references only.** A chart that queries the dataset counts. A
  dashboard containing that chart is a second hop, and counting hops is lineage
  proximity wearing a different name -- hy-o64i's shape, and not foreclosed here.
- **Live endpoints only.** The projection deliberately keeps rows whose endpoints
  were soft-deleted, so counting them would let a deleted chart's dataset outrank
  a live one. The store answers this with a join.
- **It ranks below both Git signals.** A reference count is the estate's opinion
  of itself; Git engagement is the customer's stated one. A source the customer
  already approved outranks one that merely has charts pointing at it, and moving
  the count can never lift a source the customer's own context prohibits.

So the ranking rests on the two Git-relative signals, this observed one, and the
temporal one, and `produced_by.signals` names them on every response rather than
implying a richer basis than it has.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from hyperset.bundle.equivalence import EQUIVALENT, compare_fragments
from hyperset.bundle.schema import canonical_json

# The kind of assist claim this module makes. A second kind gets its own value
# rather than widening this one, so a client can branch before it parses.
CANDIDATE_SOURCES = "candidate_sources"

# Deterministic and model-free, and named anyway: ADR 0019 floor 9 requires
# every assist claim to name what produced it, and "no model was involved" is
# the most useful thing that attribution can say.
PRODUCER = "deterministic_ranking/1"

# What a candidate is: the label every entry carries, so nothing here can be
# read as governed, approved, canonical, or trusted (ADR 0019 floor 1).
OBSERVED = "observed"

# Enough to choose between, few enough that the answer stays an answer. The
# full count of what was considered travels beside it, so a bound is never
# mistaken for an absence.
CANDIDATE_LIMIT = 5

# The signals, in the order they order candidates.
GIT_ENGAGEMENT = "git_engagement"
EXPRESSION_AGREEMENT = "expression_agreement"
# Named for what it counts -- references a source's own payload declares -- and
# not for the usage frequency hy-gh-124 asked for, which no source discloses.
DECLARED_REFERENCES = "declared_references"
SOURCE_FRESHNESS = "source_freshness"
SIGNALS = (GIT_ENGAGEMENT, EXPRESSION_AGREEMENT, DECLARED_REFERENCES, SOURCE_FRESHNESS)

# What disagrees with Git. Never a filter: a disagreement sinks a candidate to
# the bottom of the order and is stated on it, because ADR 0019 floor 6 forbids
# ranking a prohibition out of view, and a source hidden for being prohibited
# is a source the caller will find again on its own with the reason missing.
PROHIBITED_BY_CONTEXT = "prohibited_by_context"
SOURCE_DELETED = "source_deleted"
OPEN_FINDING = "open_finding"
DISAGREEMENTS = (PROHIBITED_BY_CONTEXT, SOURCE_DELETED, OPEN_FINDING)

# How much a disagreement counts, and WHO DECIDED that. These are the
# processor's own severity words, from `hyperset.db.models.FINDING_SEVERITIES`
# -- read here, never re-derived. The alternative was to key on the finding TYPE
# and treat `approved_expression_undecidable` specially, which would put a second
# copy of the processor's type register in this module and give two components
# different answers to "how much does this disagreement count" (hy-qbii; the same
# shape as hy-803q one level over, and the reason `reconcile.py` refuses to
# re-list `FINDING_TYPES` too).
#
# ADR 0021 decision 2 is what makes the distinction load-bearing: an UNDECIDED
# comparison "is not a contradiction, and not agreement either". `rules.py`
# serves that verdict as a `warning` under its own finding type, so it lands
# outside this tuple -- for that reason and no other. If the processor ever
# graded an undecided pair an `error`, the order here would follow it. That
# coupling is intended: the processor owns how much its own finding counts.
DECIDED_SEVERITIES = ("error", "critical")

# The three places `_order` can put a candidate, and the middle one is the whole
# point. Internal to the ordering and deliberately NOT served: a reader checking
# a rank reads the disagreements already on the candidate -- each states its
# kind, and an `open_finding` states its severity -- so the tier is derivable
# from what ships rather than being a fourth number to trust.
AGREES = 0
UNDECIDED = 1
DECIDED = 2

# What the proposal decided, published for the reason `DISAGREEMENTS` is: a
# client branches on the outcome, so it is a stable identifier rather than a
# sentence, and it is gated where the proposal is built so a value that reaches
# a caller unpublished fails at construction rather than on the wire.
#
# Four of the five are declines, and they are separate values because they are
# separate facts about the estate. "Nothing stood out" and "everything here
# disagrees with your own Git context" are different answers to the same
# question, and a caller deciding whether to go look for itself needs to know
# which one it got.
PROPOSED = "proposed"
NO_GIT_RELATIVE_SIGNAL = "no_git_relative_signal"
NOT_SEPARATED = "not_separated_from_the_next_candidate"
EVERY_CANDIDATE_DISAGREES = "every_candidate_disagrees_with_git"
# The caller named a domain no configured context declares, so no domain
# governs the question at all (hy-xq55). Distinct from every other decline
# because it is decided before a single candidate is read: it is a fact about
# the REQUEST against the corpus, not about how the estate happened to rank.
NO_GOVERNING_DOMAIN = "no_governing_domain"
PROPOSAL_OUTCOMES = (
    PROPOSED,
    NO_GIT_RELATIVE_SIGNAL,
    NOT_SEPARATED,
    EVERY_CANDIDATE_DISAGREES,
    NO_GOVERNING_DOMAIN,
)

# The only two signals that can carry a proposal, and the exclusion is the
# claim. Both are Git-relative: they say the customer's own commit already
# approves this source, cites it as evidence, or declares an expression it
# computes. `source_freshness` is neither -- a timestamp says a source moved,
# not that it is the right one -- so it orders candidates and can never promote
# one to a proposal.
PROPOSING_SIGNALS = (GIT_ENGAGEMENT, EXPRESSION_AGREEMENT)


@dataclass(frozen=True)
class ObservedSource:
    """One observed asset, flattened to the facts a rank can be read from.

    A plain value with no session behind it: the ranking is pure, so it is
    unit-testable without Postgres and cannot reach for a fact it did not
    declare it needed.
    """

    ref: str
    connector: str
    asset_type: str
    external_id: str
    asset_id: str
    connection_id: str
    observed_version_id: str | None = None
    source_modified_at: datetime | None = None
    deleted_at: datetime | None = None
    # `normalized["metrics"][]["expression"]` -- what this source computes, as
    # the source itself reported it.
    metric_expressions: tuple[str, ...] = ()
    # Current findings against this asset: `{finding_id, finding_type,
    # severity}`, already produced by the governed processor. Cited, not
    # authored.
    findings: tuple[dict, ...] = ()
    # Live references INTO this asset: `{ref, asset_type, relation}` per
    # referring OBSERVED asset. Every ref here is an observed asset's own
    # source-native identity, never a declared one, so naming them adds no
    # second half to the pair the rule above refuses -- an observed asset citing
    # an observed asset is what the connector saw. Already filtered to live
    # endpoints by the caller, because "how many things reference this" is a
    # question about the estate now (hy-g1y8).
    referenced_by: tuple[dict, ...] = ()


@dataclass(frozen=True)
class GovernedFacts:
    """What the configured Git corpus already says, keyed by ref.

    Read from context snapshots by the caller and passed in, so this module
    holds no opinion about how a snapshot is stored and cannot be tempted to
    read one it was not given.
    """

    # ref -> ({domain, role}, ...) from `approved_sources`
    approved: dict[str, tuple[dict, ...]] = field(default_factory=dict)
    # ref -> ({domain, term}, ...) from `definitions[].evidence_refs`
    evidence: dict[str, tuple[dict, ...]] = field(default_factory=dict)
    # ref -> ({domain, reason}, ...) from `prohibited_sources`
    prohibited: dict[str, tuple[dict, ...]] = field(default_factory=dict)
    # `{name, expression}` for the fields the NAMED domain declares. Only that
    # domain's: an expression another domain governs says nothing about whether
    # this source belongs to the question the caller asked here.
    field_expressions: tuple[dict, ...] = ()


def candidate_sources(
    *,
    domain: str,
    undeclared: list[str],
    sources: list[ObservedSource],
    governed: GovernedFacts,
    domain_is_configured: bool,
    limit: int = CANDIDATE_LIMIT,
) -> dict | None:
    """Rank `sources` for a claim `domain` does not declare.

    `domain_is_configured` says whether any configured context declares
    `domain` at all, and it is required rather than defaulted because the two
    values are the system's two kinds of governance silence and neither is the
    safe one to assume. True is the coverage refusal: the domain exists and
    does not declare these terms. False is hy-xq55's case, where the caller
    named a domain nothing configured declares, so Git says nothing whatsoever
    rather than nothing about one term -- and then no proposal may issue at
    all, however the ranking comes out. `_proposal` holds that line; this
    parameter is also what separates the two disclosures below, because a
    reader told "no governed context covers 'churn_rate' in 'churn'" would take
    the domain for a real one that happens to be thin.

    Returns the assist section, or `None` when there is nothing observed to
    rank -- an empty candidate list is not a discovery, and serving an empty
    section would make "assist ran and found nothing" indistinguishable from
    "assist ran" to a client reading for the key.
    """
    if not sources:
        return None
    terms = sorted(set(undeclared))
    ranked = sorted(
        (_candidate(source, domain=domain, governed=governed) for source in sources),
        key=_order,
    )
    served = ranked[:limit]
    for position, candidate in enumerate(served, start=1):
        candidate["rank"] = position
    section = {
        "kind": CANDIDATE_SOURCES,
        "produced_by": {"producer": PRODUCER, "model": None, "signals": list(SIGNALS)},
        "answers": {"domain": domain, "undeclared_concepts": terms},
        "candidates": [_served(candidate) for candidate in served],
        # `ranked`, never `served`: the proposal is a claim about the estate,
        # and a serving bound must not be able to make one (hy-ica2).
        "proposal": _proposal(
            ranked,
            served=len(served),
            domain=domain,
            domain_is_configured=domain_is_configured,
        ),
        "considered": len(ranked),
        "returned": len(served),
        "bound": limit,
        # Only the first clause varies. The rest is the disclosure ADR 0019
        # requires on every candidate list, so it is one string reached both
        # ways rather than two that can drift apart.
        "disclosure": (
            f"{_silence(domain, terms, domain_is_configured)}, and nothing below changes that: "
            f"these are observed sources, ranked by "
            f"stated signal, and not approved, canonical, or validated business meaning for "
            f"this or any other claim. Each rank is a proposal for a human or a Git change, "
            f"never an identification of the source this request needs."
        ),
    }
    # Its own identity, never the bundle's. `bundle_id` hashes the governed
    # answer alone (ADR 0019 floor 8): folding assist content into it would
    # spend the determinism guarantee the governed slice depends on -- caching,
    # equality, and recorded evaluation comparisons all read that hash.
    return {"assist_id": f"as-{_content_hash(section)[:16]}", **section}


def _content_hash(section: dict) -> str:
    return hashlib.sha256(canonical_json(section).encode()).hexdigest()


def _silence(domain: str, terms: list[str], domain_is_configured: bool) -> str:
    """What kind of silence this is, in the disclosure's own voice.

    The two are not degrees of the same thing. A configured domain that does
    not declare a term has an owner, a commit and a place to add it; a domain
    nothing declares has none of those, and telling a reader that "no governed
    context covers 'churn_rate' in the 'churn' domain" invites them to go read
    a 'churn' domain that is not there.
    """
    named = ", ".join(repr(term) for term in terms)
    if not domain_is_configured:
        return (
            f"No configured Git context declares the {domain!r} domain at all, so nothing "
            f"governs {named} -- or any other claim in it"
        )
    return f"No governed context covers {named} in the {domain!r} domain"


def _proposal(ranked: list[dict], *, served: int, domain: str, domain_is_configured: bool) -> dict:
    """The one candidate the evidence separated, or a stated refusal to name one.

    hy-gh-124's acceptance asks for this and asks for it under a word ADR 0019
    floor 1 forbids outright -- "canonical", in a status, in a field, OR IN
    PROSE. The issue predates the ADR, the ADR is the later ruling, and the
    thing the issue wanted is servable without the word: a proposal for a human
    or a Git change, which is what the ordering was always for and what the
    section's own disclosure already calls each rank.

    So this adds no signal and no evidence. It reads the order that already
    exists and answers the question a ranked list of five leaves open -- did
    anything actually stand out, or is this five sources in an arbitrary but
    stable sequence. Rank 1 is served either way; only the proposal is withheld.

    Three conditions, all checkable by the reader against signals already on the
    candidate, none of them a score:

    - **No disagreement.** A source the customer's Git context prohibits, one
      the source stopped reporting, or one carrying an open finding is never
      proposed. It stays ranked and stays visible with its reason (floor 6);
      what it does not get is Hyperset putting it forward. Because `_order`
      sinks every disagreement below every clean candidate, rank 1 disagreeing
      means all of them do, and that is its own outcome.
    - **A Git-relative signal, nonzero.** `PROPOSING_SIGNALS` is the gate: the
      commit approves it, cites it, or declares an expression it computes.
      Freshness cannot carry a proposal, so the newest source in an estate Git
      says nothing about is rank 1 and is not proposed.
    - **Separation from rank 2.** Strictly ahead on `(engagement, agreements)`,
      compared in that precedence because that is the precedence `_order`
      already applies. A tie is a tie: two sources the commit engages equally
      are a question for a human, and picking one on the freshness tiebreak
      that ordered them would dress a stable sort as a finding.

    A lone candidate has nothing to be separated from, so the third condition
    is vacuous for it and the first two still bind -- which is the intended
    reading: one source the commit already approves, with nothing against it,
    is exactly the case worth proposing. A leader whose only company disagrees
    with Git is in the same position, because separation is measured against
    the candidates that could themselves be proposed and a disqualified one is
    not among them.

    DECIDED OVER THE WHOLE RANKING, not over the truncated list, and the
    property is that the proposal must not depend on how many candidates were
    served (hy-ica2). Handed `served` it was not: two runs over the same estate,
    two sources the commit approves equally, differed only in the bound, and at
    limit=1 the tie partner fell off the list, the separation scan found nothing
    to compare against, and a decline became a proposal whose statement --
    "the one observed source the configured Git context separates from the
    rest" -- was false. Not reachable today, since the one caller passes no
    limit, but hy-uh9q wants assist to honour `context_budget` and the obvious
    implementation feeds a budget into exactly this bound. A mechanism that
    proposes something on thin evidence is the failure mode this whole section
    is built against, so a parameter must not be able to produce one.

    THE COST, taken deliberately rather than inherited: a decline may now name a
    ref the client cannot see, when the runner-up that blocked the proposal sits
    past the bound. That is the right side to fail on -- the statement says why
    nothing was put forward and names what it weighed, which is checkable by
    asking for a larger bound, while the alternative is a confident sentence
    about evidence the bound hid.

    `served` is still read for one thing: nothing is put forward that the reader
    was not given. At every bound >= 1 the leader is served by construction, so
    this only bites at a bound of zero.

    THE UNGOVERNED CASE IS DECIDED FIRST AND WITHOUT READING A CANDIDATE
    (hy-xq55). When no configured context declares the domain the caller named,
    no proposal may issue whatever the ranking says. It is checked ahead of
    every other condition because it is the only one that is a property of the
    request rather than of the estate: the others ask how the candidates came
    out, and this one says the question has no governed subject for a proposal
    to be about. A caller still gets the ranked list -- silence where governance
    is silent is the defect ADR 0019 exists to fix -- and the list carries a
    permanent decline, which is the list telling the truth about itself.

    Ordered ahead of `EVERY_CANDIDATE_DISAGREES` deliberately, and it costs the
    reader nothing: each candidate states its own disagreements either way, so
    the fact is still on the wire, while the outcome names the reason nothing
    could have been put forward rather than the reason this particular ranking
    did not.

    WHY THE EXISTING DECLINE WOULD NOT DO, which is the correction this made to
    the ruling that authorised it. `NO_GIT_RELATIVE_SIGNAL` was the named
    mechanism, on the premise that both proposing signals go structurally zero
    without a named domain. Only `expression_agreement` does: `git_engagement`
    is built from EVERY configured source (`_governed_facts`), so another
    domain's approval of a source is readable here and nonzero. Measured, not
    reasoned: wiring this refusal with the outcome unchanged returns `proposed`
    on a cross-domain approval, and the `NO_GIT_RELATIVE_SIGNAL` sentence --
    "no observed source here is engaged by ... configured Git context" -- is
    false on exactly the estates where the decline matters. So the honest
    decline is a separate outcome naming the real ground: an approval written
    against another domain is a governed fact about THAT domain, and letting it
    license a proposal here would round the claim up (ADR 0019 decision 1) and
    make an assist value a function of an inference rather than of the pinned
    snapshot (decision 2(c)).
    """
    if not domain_is_configured:
        return _declined(
            NO_GOVERNING_DOMAIN,
            f"no configured Git context declares the {domain!r} domain, so no domain governs "
            f"this question and nothing is put forward. Another domain may already engage a "
            f"source listed below, and that is stated on the candidate -- but an approval "
            f"written against another domain is a governed fact about that domain, not a "
            f"reason to put its source forward for a claim nothing governs. The list is "
            f"ranked and each rank stays checkable against the signals on it",
        )
    if not ranked or not served:
        # `not ranked` is unreachable through `candidate_sources`, which returns
        # early on an empty estate; `not served` is a bound of zero and is
        # covered. The pragma that used to sit here stopped being true when the
        # second became reachable, so it is gone rather than left as a claim
        # nobody rechecks.
        return _declined(NO_GIT_RELATIVE_SIGNAL, "no candidate was served")
    leader = ranked[0]
    # The next candidate that could ITSELF be proposed, which is not always the
    # next one on the list. A source carrying a disagreement is disqualified
    # above, so letting it tie with the leader would let a deleted source veto
    # the proposal of a live one the commit engages equally -- found by probing
    # that pair rather than by reasoning about it, and the sunk source is not
    # even served as the alternative it would have been blocking on behalf of.
    # Scanned rather than read off `ranked[1]`: `_order` does sink every
    # disagreement below every clean candidate, so the scan and the index agree
    # today, and stating the intent here keeps them agreeing if that changes.
    runner_up = next(
        (candidate for candidate in ranked[1:] if not candidate["disagrees_with_git"]), None
    )

    if leader["disagrees_with_git"]:
        return _declined(
            EVERY_CANDIDATE_DISAGREES,
            f"every observed source ranked here disagrees with the configured Git context, so "
            f"none is put forward; the highest-ranked one, {leader['source'].ref!r}, carries "
            f"{len(leader['disagrees_with_git'])} disagreement(s) stated on the candidate itself",
        )
    if _git_relative(leader) == (0, 0):
        return _declined(
            NO_GIT_RELATIVE_SIGNAL,
            f"no observed source here is engaged by the {domain!r} domain's configured Git "
            f"context or computes an expression it declares, so nothing is put forward: the "
            f"order rests on freshness alone, which says a source moved and not that it is the "
            f"one this claim needs",
        )
    if runner_up is not None and _git_relative(leader) <= _git_relative(runner_up):
        return _declined(
            NOT_SEPARATED,
            f"{leader['source'].ref!r} and {runner_up['source'].ref!r} are engaged equally by "
            f"the configured Git context, so nothing is put forward; separating them would mean "
            f"ranking on the tiebreak that ordered them, which carries no claim about which is "
            f"right",
        )

    # Non-empty by construction: `_git_relative(leader)` is strictly greater as
    # a pair, and a pair is only strictly greater when a component is.
    basis = [
        signal
        for signal in PROPOSING_SIGNALS
        if _signal_of(leader, signal) > _signal_of(runner_up, signal)
    ]
    return {
        "outcome": PROPOSED,
        # The candidate's own ref, and the only field here that names anything.
        # A proposal carries no second identifier, so it can no more be read as
        # a link than the candidate it points at can (ADR 0019 floor 2).
        "ref": leader["source"].ref,
        "governance": OBSERVED,
        # Which of the two Git-relative signals separated it, so the reader goes
        # to that signal on the candidate and checks the proposal against the
        # evidence rather than against this sentence.
        "basis": basis,
        "statement": (
            f"{leader['source'].ref!r} is the one observed source the configured Git context "
            f"separates from the rest, on {' and '.join(basis)}. It is put forward for a "
            f"human or a Git "
            f"change and nothing more: it is observed, it is not approved or validated business "
            f"meaning for the {domain!r} claim this request asked about, and Hyperset has not "
            f"resolved this request to it"
        ),
    }


def _declined(outcome: str, statement: str) -> dict:
    """A decline is the same shape as a proposal with `ref` null, so a client
    reads one key path either way and can never miss the section by branching
    on a key that is sometimes absent -- the failure the `assist` section's own
    presence rule exists to avoid one level up."""
    if outcome not in PROPOSAL_OUTCOMES:
        raise ValueError(f"{outcome!r} is not a published proposal outcome: {PROPOSAL_OUTCOMES}")
    return {
        "outcome": outcome,
        "ref": None,
        "governance": OBSERVED,
        "basis": [],
        "statement": statement,
    }


def _git_relative(candidate: dict) -> tuple[int, int]:
    """The two proposing signals as a comparable pair, in `_order`'s precedence."""
    return (candidate["engagement"], candidate["agreements"])


def _signal_of(candidate: dict | None, signal: str) -> int:
    if candidate is None:
        return 0
    return {GIT_ENGAGEMENT: candidate["engagement"], EXPRESSION_AGREEMENT: candidate["agreements"]}[
        signal
    ]


def _candidate(source: ObservedSource, *, domain: str, governed: GovernedFacts) -> dict:
    approved = governed.approved.get(source.ref, ())
    evidence = governed.evidence.get(source.ref, ())
    prohibited = governed.prohibited.get(source.ref, ())
    agreements = _expression_agreement(source, governed.field_expressions)

    signals = [
        {
            "signal": GIT_ENGAGEMENT,
            "value": {"approved_by": list(approved), "cited_as_evidence_by": list(evidence)},
            "statement": _git_engagement_statement(approved, evidence),
        },
        {
            "signal": EXPRESSION_AGREEMENT,
            "value": {"fields": [entry["name"] for entry in agreements]},
            "statement": _expression_statement(agreements, domain, source),
        },
        {
            "signal": DECLARED_REFERENCES,
            "value": {
                "count": len(source.referenced_by),
                "by_relation": _by_relation(source.referenced_by),
                # Named, not just counted: a reader checking this rank goes to
                # these refs in the source and sees the reference for themselves.
                "referenced_by": [
                    {"ref": entry["ref"], "relation": entry["relation"]}
                    for entry in source.referenced_by
                ],
            },
            "statement": _reference_statement(source),
        },
        {
            "signal": SOURCE_FRESHNESS,
            "value": _iso(source.source_modified_at),
            "statement": (
                f"the source last reported a modification at {_iso(source.source_modified_at)}"
                if source.source_modified_at is not None
                else "the transport this source was read through discloses no modification "
                "time, so freshness is unknown rather than old"
            ),
        },
    ]

    disagreements = [
        {
            "kind": PROHIBITED_BY_CONTEXT,
            "value": dict(entry),
            "statement": (
                f"the {entry['domain']!r} domain's Git context prohibits this source: "
                f"{entry['reason']}"
            ),
        }
        for entry in prohibited
    ]
    if source.deleted_at is not None:
        disagreements.append(
            {
                "kind": SOURCE_DELETED,
                "value": {"deleted_at": _iso(source.deleted_at)},
                "statement": (
                    f"the source stopped reporting this {source.asset_type} at "
                    f"{_iso(source.deleted_at)}"
                ),
            }
        )
    disagreements.extend(
        {
            "kind": OPEN_FINDING,
            "value": dict(finding),
            "statement": (
                f"an open {finding['finding_type']!r} finding ({finding['severity']}) stands "
                f"against this source: {finding['finding_id']}"
            ),
        }
        for finding in source.findings
    )

    return {
        "source": source,
        "engagement": len(approved) + len(evidence),
        "agreements": len(agreements),
        "references": len(source.referenced_by),
        "signals": signals,
        "disagrees_with_git": disagreements,
        "prohibited": bool(prohibited),
    }


def _expression_agreement(source: ObservedSource, declared: tuple[dict, ...]) -> list[dict]:
    """Which of the domain's declared field expressions this source computes.

    Compared as computations, by the same comparator plan validation uses
    (hy-gh-128), so a reformatted manifest does not read as a different source.
    Only `EQUIVALENT` counts: `UNDECIDED` means the difference is confined to
    qualifiers or casts and the warehouse would have to settle it, and a rank
    resting on a difference Hyperset could not decide is a guess with a number
    on it.
    """
    matched = []
    for entry in declared:
        expression = entry.get("expression")
        if not expression:
            continue
        if any(
            compare_fragments(expression, observed) == EQUIVALENT
            for observed in source.metric_expressions
        ):
            matched.append(entry)
    return matched


def _by_relation(referenced_by: tuple[dict, ...]) -> dict[str, int]:
    """Counts per connector word, so the relation is never averaged away.

    "two charts query it" and "one chart queries it, one dashboard contains it"
    are different facts about a source, and a single total states neither.
    """
    counts: dict[str, int] = {}
    for entry in referenced_by:
        counts[entry["relation"]] = counts.get(entry["relation"], 0) + 1
    return dict(sorted(counts.items()))


def _reference_statement(source: ObservedSource) -> str:
    """The signal's own honest wording, and the reason it is worded that way.

    Says "declares a reference to", never "queries it often". The count is of
    references a source's payload declares, which is a fact about the estate's
    structure; how often anyone RAN anything is not observable in either source
    (hy-d7xh), and a statement that blurred the two would leave a reader unable
    to tell this signal from the one they wanted.
    """
    if not source.referenced_by:
        return (
            "no live observed asset declares a reference to this source, which is an absence "
            "of declared references and not evidence that nobody queries it -- no execution "
            "count exists in either source"
        )
    named = ", ".join(
        f"{entry['ref']!r} {entry['relation']} it"
        for entry in sorted(
            source.referenced_by, key=lambda entry: (entry["relation"], entry["ref"])
        )
    )
    return (
        f"{len(source.referenced_by)} live observed asset(s) declare a reference to this "
        f"source: {named}. That is what the sources declare about each other, not how often "
        f"anyone queried it -- no execution count exists in either source"
    )


def _git_engagement_statement(approved: tuple[dict, ...], evidence: tuple[dict, ...]) -> str:
    if not approved and not evidence:
        return (
            "no configured Git context names this source at all: it is in the estate and "
            "outside governance entirely"
        )
    parts = []
    if approved:
        parts.append(
            "approved by "
            + ", ".join(
                f"{entry['domain']!r} as {entry['role']!r}"
                for entry in sorted(approved, key=lambda entry: (entry["domain"], entry["role"]))
            )
        )
    if evidence:
        parts.append(
            "cited as evidence for "
            + ", ".join(
                f"{entry['term']!r} in {entry['domain']!r}"
                for entry in sorted(evidence, key=lambda entry: (entry["domain"], entry["term"]))
            )
        )
    return (
        "configured Git context already engages this source -- "
        + "; ".join(parts)
        + " -- for claims other than the one asked here"
    )


def _expression_statement(agreements: list[dict], domain: str, source: ObservedSource) -> str:
    if not agreements:
        return (
            f"this source reports {len(source.metric_expressions)} metric expression(s), none "
            f"equivalent to a field expression the {domain!r} domain declares"
        )
    names = ", ".join(repr(entry["name"]) for entry in agreements)
    return (
        f"this source computes an expression equivalent to the one the {domain!r} domain "
        f"declares for {names}; the match is on the computation, never on a name"
    )


def _disagreement_tier(candidate: dict) -> int:
    """Agreement, a disagreement nothing could settle, or a settled one.

    ADR 0021 decision 2's third outcome, carried into the order. `EQUIVALENT` is
    agreement, `DIFFERENT` is a contradiction, and `UNDECIDED` -- a difference
    confined to table qualifiers or casts -- is neither, because settling it
    needs the warehouse schema Hyperset does not read or the query it does not
    run. The processor already serves all three; this module used to collapse
    the last two the moment it sorted.

    ONE decided disagreement is enough. Not a count and not a majority: a source
    the customer prohibits is a bad answer whatever else is true of it, and
    averaging that against an undecided expression would be a score.

    Deletion and prohibition are both DECIDED, and neither is decided by
    Hyperset -- the customer's commit refuses the source, and the connector
    stopped seeing it. Nothing is pending on either, so there is nothing for the
    middle tier to mean.

    An unpublished disagreement kind counts as decided. That is the conservative
    direction: a new kind sinks fully until someone decides otherwise, so the
    failure mode of forgetting to place one is a candidate ranked too low with
    its reason stated, never one ranked too high.
    """
    disagreements = candidate["disagrees_with_git"]
    if not disagreements:
        return AGREES
    if any(_is_decided(entry) for entry in disagreements):
        return DECIDED
    return UNDECIDED


def _is_decided(entry: dict) -> bool:
    if entry["kind"] == OPEN_FINDING:
        return entry["value"]["severity"] in DECIDED_SEVERITIES
    return True


def _order(candidate: dict):
    """Deterministic, and every component is one of the stated signals.

    ANY disagreement sorts below every clean candidate before a signal is read:
    a source the customer's own Git context refuses, one the source stopped
    reporting, and one carrying an open finding are each a worse answer than a
    clean source, and none of the three is made better by being fresh or engaged.
    The defect that fixes: a deleted source could outrank every live one and be
    served as rank 1, the strongest recommendation this section makes, with the
    deletion disclosed underneath it.

    BUT NOT ALL TO THE SAME PLACE, which is `_disagreement_tier`. Sinking every
    disagreement into one bottom tier made an undecided comparison and a
    contradiction indistinguishable in the order, and ADR 0021 decision 2 says
    they are not the same thing -- an undecided pair "is not a contradiction, and
    not agreement either". This module's entire output is an order, so a
    distinction that does not reach the order does not reach the caller at all,
    whatever the statements say (hy-qbii).

    Nothing is filtered and nothing is lifted. Floor 6 still holds: an undecided
    candidate stays below every clean one and keeps its stated reason. What it
    stops doing is sharing a tier with a source the customer prohibited.

    Prohibition still grades WITHIN the sunk group, because refusing a source
    is the customer's own statement and outranks a fact the connector observed.
    Freshness breaks ties last and only among sources that disclose one;
    `ref` breaks the rest, so two identically-signalled sources have a stable
    order that carries no claim about which is right.

    `references` sits BELOW both Git signals and ABOVE freshness, and the
    position is the claim: a reference count is the estate's opinion of itself,
    Git engagement is the customer's stated one, and a timestamp is neither. So a
    source the customer approved outranks one that merely has charts pointing at
    it, a widely-referenced source still sinks under any disagreement, and among
    sources Git says nothing about the count decides -- which is the only place
    it could decide anything without overruling a human (hy-g1y8).
    """
    source = candidate["source"]
    modified = source.source_modified_at
    return (
        _disagreement_tier(candidate),
        candidate["prohibited"],
        -candidate["engagement"],
        -candidate["agreements"],
        -candidate["references"],
        modified is None,
        -modified.timestamp() if modified is not None else 0.0,
        source.ref,
    )


def _served(candidate: dict) -> dict:
    source = candidate["source"]
    return {
        "rank": candidate["rank"],
        # One ref, and it is the observed asset's own source-native identity.
        # There is no field here that can hold a declared ref, which is what
        # keeps a candidate from being substitutable for a resolved link.
        "ref": source.ref,
        "connector": source.connector,
        "asset_type": source.asset_type,
        "external_id": source.external_id,
        "asset_id": source.asset_id,
        "connection_id": source.connection_id,
        "observed_version_id": source.observed_version_id,
        "governance": OBSERVED,
        "signals": candidate["signals"],
        "disagrees_with_git": candidate["disagrees_with_git"],
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
