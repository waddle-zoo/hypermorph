"""Two-sided disagreements built at bundle time, from a join the resolver
already has both sides of (hy-llk4, ADR 0021 decisions 4 and 7).

ADR 0021 specified `source deleted while governed` and `prohibited but
referenced` as reconcilable and deliberately built neither, because each needs
an EMITTER and choosing one was not required to fix the comparison. This module
is that choice. The reasoning, not just the verdict, because the next author
needs to know which shape they have:

## Why not the processor

The processor is the only component that creates a `Finding`, and `CLAUDE.md`
forbids a second processor rule before the walking skeleton is green, which
`README.md` says it is not. Waiting for that gate would make these two
dimensions wait on an event that has nothing to do with them.

It is also the wrong producer for what these are. A processor finding is
persisted, carries a rule version, and opens a review task: it is a claim about
one asset that outlives the request. Both dimensions here are recomputed from
current state on every resolve and hold no judgement a reviewer settles -- the
prohibition is already the customer's decision, and the deletion is already
disclosed. They are DISCLOSURES about the pair, and the bundle is where
disclosures about a pair already live.

## Why here, and what it costs

The resolver already emits both facts, one-sidedly: a deleted asset becomes a
`deprecation` with `kind=source_deleted` whose reason mentions that Git still
approves it, and every `prohibited_sources` entry becomes a `deprecation` with
`kind=prohibited_by_context`. Neither states the other side. So this is a shape
change to an existing emitter rather than a new one, and it is deterministic for
a pinned commit, repository state and directive -- which is the condition, not a
preference: `linked_evidence` is inside `bundle_id` (`ContextBundle._content`
hashes it), and ADR 0019 floor 8 forbids anything non-deterministic there.

The cost is that `linked_evidence.conflicts` becomes MIXED-PROVENANCE: some
entries are projections of a persisted processor finding, some are computed
here from Git plus current observation. ADR 0019 obliges a change to a served
section to say which input each new field comes from, and `observed_assets`
already carries a `governance:` label for exactly this reason while `conflicts`
carried none. So every entry now names its producer, both old and new, and
`finding_id` is null on an entry no finding stands behind.

## What these refuse to call a contradiction

ADR 0021 decision 2, applied to presence rather than to expressions:

- a prohibited ref no observed asset carries is an ABSENCE, not a difference;
- a prohibited ref nothing references is AGREEMENT -- the customer prohibited it
  and the estate is not using it, which is the prohibition working;
- a deleted asset no commit approves is an observation, not a disagreement. It
  is served as a deprecation and stays there;
- a deleted asset the commit PROHIBITS is agreement twice over -- the customer
  said not to use it and the estate stopped carrying it.

Neither dimension needs a comparator: both sides are presence, so ADR 0021's
"comparator chosen by the value kind" resolves to existence. Neither carries a
`moved` side either -- movement is measured against the version a commit pinned
for a FIELD, and neither of these is about a field.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from hyperset.bundle.equivalence import DIFFERENT, compare_fragments

# Which input produced a `conflicts` entry. Published because it is SERVED, and
# gated where an entry is built, the same shape that gates `VIOLATION_CODES`,
# `WARNING_CODES` and `FINDING_TYPES`: a value that would reach a client
# unpublished fails at construction rather than on the wire (ADR 0021 decision
# 6).
PROCESSOR_FINDING = "processor_finding"
BUNDLE_RECONCILIATION = "bundle_reconciliation"
CONFLICT_PRODUCERS = (PROCESSOR_FINDING, BUNDLE_RECONCILIATION)

PROHIBITED_BUT_REFERENCED = "prohibited_but_referenced"
SOURCE_DELETED_WHILE_GOVERNED = "source_deleted_while_governed"
OWNERSHIP_MISMATCH = "ownership_mismatch"
GRAIN_MISMATCH = "grain_mismatch"
FRESHNESS_STALE = "freshness_stale"

# The severity register for `conflicts[].severity`, ordered MOST-SEVERE FIRST
# (hy-xfhh, impl slice (i) of docs/development/reconciliation-engine.md; F1
# ruling). A processor-finding conflict INHERITS its finding's severity
# (error/warning, `processor/rules.py`); a reconciled conflict carries a FIXED,
# DECLARED severity per kind -- never a computed/scored one, because a data-derived
# severity is assist and assist content may not enter `bundle_id` (ADR 0021
# decision 1).
#
# DEFAULT-DENY (ADR 0018 decision 5), the published rule for this enumerated served
# field: a consumer that meets a severity it does not recognise treats it as the
# MOST severe (`error`) and always surfaces it -- never silently downgrades or drops
# it. Publishing that rule is what lets a FUTURE severity value be added without
# moving `SCHEMA_VERSION` again; a value reaching a client unpublished still fails at
# construction here, the same shape that gates `CONFLICT_PRODUCERS` and
# `RECONCILED_KINDS`.
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITIES = (SEVERITY_ERROR, SEVERITY_WARNING)

# The kinds this module can produce, EACH WITH ITS FIXED SEVERITY. Deliberately NOT
# a union with `hyperset.processor.FINDING_TYPES`: each register has one owner, and a
# second copy of the processor's list here would be the "two answers to one question"
# failure ADR 0021 was written about. A projected finding's kind was already gated
# where the candidate was constructed. A prohibition the estate still references is an
# `error` (the customer said not to use it and it is in use); a governed source that
# vanished is a `warning` (an observation about the source, still true, that Git has
# not caught up to).
RECONCILED_KINDS = {
    PROHIBITED_BUT_REFERENCED: SEVERITY_ERROR,
    SOURCE_DELETED_WHILE_GOVERNED: SEVERITY_WARNING,
    # An owner the estate reports differently from a Git-DECLARED bridge is a
    # `warning` (hy-ocbd): a mismatch to reconcile, not a data-correctness error.
    # Additive under ADR-0018 decision 5 (the kind vocabulary is default-deny), so
    # it does not move SCHEMA_VERSION.
    OWNERSHIP_MISMATCH: SEVERITY_WARNING,
    # An observed grain that disagrees with a Git-DECLARED grain (exact) or falls
    # outside a DECLARED rollup relation is a `warning` (hy-868w): a grain to
    # reconcile, not a data-correctness error. Additive/default-deny, no SV move.
    GRAIN_MISMATCH: SEVERITY_WARNING,
    # A source older than a Git-DECLARED freshness threshold is a `warning`
    # (hy-d9ys): a clock to reconcile, not a data-correctness error. Additive/
    # default-deny, no SV move. Nothing emits it until the deferred-activation
    # fork wires a DECLARED threshold + a deterministic resolve-clock 'now'.
    FRESHNESS_STALE: SEVERITY_WARNING,
}

# How many referring refs an entry names before it stops naming them and states
# a remainder. A prohibited source with three hundred referrers is the case this
# dimension most wants to report and the one that would blow the context budget
# reporting it, so the count is the claim and the names are the sample that lets
# a reader start checking it (`context_budget` bounds the payload, and an entry
# that alone exceeded it would be cut whole).
NAMED_REFERENCES = 5


def conflict(
    *,
    kind: str,
    produced_by: str,
    severity: str,
    ref: str,
    context_says: str,
    source_says: str,
    unresolved_since_commit: str | None,
    field: str | None = None,
    finding_id: str | None = None,
) -> dict:
    """One `linked_evidence.conflicts` entry, with its producer AND severity stated.

    Both producers come through here, so the label cannot be attached to one
    path and forgotten on the other -- which is how `conflicts[].kind` came to
    be served ungated in the first place. `severity` is REQUIRED on every entry
    (hy-xfhh): a processor-finding conflict passes its finding's severity, a
    reconciled conflict passes its kind's fixed severity from `RECONCILED_KINDS`.
    It is gated against `SEVERITIES` here so an unpublished value fails at
    construction, not on the wire -- and for a reconciled entry the constructor
    ENFORCES the kind's declared severity, so passing the right value at the call
    site is checked here rather than trusted (the shared constructor is the
    invariant boundary, not the two emitters).
    """
    if severity not in SEVERITIES:
        raise ValueError(
            f"unpublished conflict severity {severity!r}: add it to SEVERITIES, which is what "
            f"`linked_evidence.conflicts[].severity` serves (a new value is additive under ADR "
            f"0018 decision 5 because default-deny is published, and does not move SCHEMA_VERSION)"
        )
    if produced_by not in CONFLICT_PRODUCERS:
        raise ValueError(
            f"unpublished conflict producer {produced_by!r}: add it to CONFLICT_PRODUCERS, "
            f"which is what `linked_evidence.conflicts[].produced_by` serves"
        )
    if produced_by == BUNDLE_RECONCILIATION and kind not in RECONCILED_KINDS:
        raise ValueError(
            f"unpublished reconciled conflict kind {kind!r}: add it to RECONCILED_KINDS. "
            f"A processor finding's kind is gated where the finding candidate is built"
        )
    if produced_by == BUNDLE_RECONCILIATION and severity != RECONCILED_KINDS[kind]:
        # A reconciled severity is a governance CONSTANT of the kind, not a
        # caller's choice: without this a caller could construct a
        # `prohibited_but_referenced` entry as a `warning` and bypass its declared
        # `error`. The two emitters pass the right value; enforcing it HERE makes
        # the shared constructor the boundary that holds the invariant.
        raise ValueError(
            f"reconciled conflict kind {kind!r} has a fixed severity "
            f"{RECONCILED_KINDS[kind]!r} but was constructed with {severity!r}: a reconciled "
            f"severity is declared per kind in RECONCILED_KINDS, not chosen at the call site"
        )
    if produced_by == BUNDLE_RECONCILIATION and finding_id is not None:
        raise ValueError(
            f"a reconciled conflict has no finding behind it, so it cannot carry "
            f"finding_id {finding_id!r}"
        )
    if produced_by == PROCESSOR_FINDING and finding_id is None:
        raise ValueError("a conflict produced from a processor finding must carry its id")
    return {
        "kind": kind,
        "produced_by": produced_by,
        "severity": severity,
        "finding_id": finding_id,
        "ref": ref,
        "field": field,
        "context_says": context_says,
        "source_says": source_says,
        "unresolved_since_commit": unresolved_since_commit,
    }


# The ONE join mechanism (hy-gl39, ADR-0021 decision 7's admitted gap). A
# contradiction is a comparison over a join, not an entry in a rule table
# (ADR-0021 decision 1): for each join key present on BOTH the declared and
# observed sides, the pair is judged by the comparator its VALUE KIND selects --
# never by which dimension we are in. The three former pairing functions
# (`_conflict`, `prohibited_but_referenced`, `source_deleted_while_governed`)
# are now producers of `JoinPair`s handed to this ONE dispatch.
#
# `COMPARATORS` is the registry, keyed by value kind and published + gated like
# `CONFLICT_PRODUCERS`/`RECONCILED_KINDS`: a new value KIND is a reviewed change
# here; a new joinable FIELD of an existing kind is NO change at all, because its
# kind already has a comparator. That property -- not a label -- is what
# `tests/unit/bundle/test_join_mechanism_instrument.py` holds the mechanism to.
EXPRESSION = "expression"
PRESENCE = "presence"
PROJECTED = "projected"
IDENTITY = "identity"
GRAIN_ROLLUP = "grain_rollup"
TEMPORAL = "temporal"


def _as_datetime(value: object) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def _grain_rollup_disagrees(declared: object, observed: object) -> bool:
    """A grain ROLLUP relation over two DECLARED grain descriptors (hy-868w). The
    customer's declared relation says the observed grain must be a valid ROLLUP of
    the declared one -- coarser or equal, i.e. every observed dimension is one the
    declared grain names. A disagreement is an observed dimension the declared
    grain does NOT contain (the estate is finer than the rollup allows). Both sides
    are SETS of dimension names the caller normalised from DECLARED inputs; nothing
    is inferred and no warehouse relation is resolved -- it is set containment over
    two governed descriptors. Agreement when `observed` is a subset of `declared`."""
    return not (frozenset(observed) <= frozenset(declared))


def _temporal_disagrees(declared: object, observed: object) -> bool:
    """A source is STALE when its observed modification time is OLDER than a CUTOFF
    (hy-d9ys). The cutoff -- `declared` here -- is `now - threshold`, computed by the
    producer from a Git-DECLARED freshness threshold and an INJECTED `now`; this
    comparator only asks whether the observed time falls before it. `now` is never
    read from the wall clock here or in the producer: it is a required input, so the
    verdict is deterministic for a pinned resolve and can sit inside `bundle_id`.
    Within the threshold (observed at or after the cutoff) is agreement."""
    return _as_datetime(observed) < _as_datetime(declared)


def _identity_disagrees(declared: object, observed: object) -> bool:
    """Exact EQUALITY of two DECLARED identity strings (hy-ocbd). Used by the
    ownership-mismatch unlock, where both sides are a catalog identity the CUSTOMER
    bridged in Git -- so this compares two governed values, never inferring a
    bridge across identifier spaces. A declared identity the estate does not match
    is the disagreement; equal is agreement."""
    return str(declared) != str(observed)


def _expression_disagrees(declared: object, observed: object) -> bool:
    """`compare_fragments`'s three outcomes, kept honest (ADR-0021 decision 2):
    EQUIVALENT is agreement and UNDECIDED is neither -- only DIFFERENT is a
    contradiction. A reformatted governed expression is never a conflict. This is
    the comparator for a `bundle_reconciliation` expression JOIN that has NO
    finding behind it -- NOT for trusting a persisted finding (see `PROJECTED`)."""
    return compare_fragments(str(declared), str(observed)) == DIFFERENT


def _projected_always(declared: object, observed: object) -> bool:
    """A persisted processor finding IS the comparison: the processor already
    compared the expressions (with `compare_fragments`) to CREATE the finding, so
    the bundle PROJECTS that decision verbatim rather than re-deciding it. A
    projected pair therefore always emits.

    Why this is its own value kind and not `expression` (hy-gl39 regression fix):
    routing the projection through the DIFFERENT-only expression comparator
    silently DROPPED a persisted `approved_expression_undecidable` finding
    (`SUM(o.amount)` vs `SUM(amount)` -> UNDECIDED) from `linked_evidence.conflicts`
    while it stayed in `.findings`. The DIFFERENT-only rule governs a bundle join
    with no finding behind it; a persisted finding is trusted, exactly as
    `_conflict` did before the mechanism landed."""
    return True


def _presence_disagrees(declared: object, observed: object) -> bool:
    """Existence against existence: both sides present is the disagreement. The
    join already required the key on both sides, and decision-2 absences (a
    prohibited ref nothing references, a deleted asset no commit approves) never
    reach here as a pair -- the producer does not build one."""
    return declared is not None and observed is not None


COMPARATORS = {
    EXPRESSION: _expression_disagrees,
    PRESENCE: _presence_disagrees,
    PROJECTED: _projected_always,
    IDENTITY: _identity_disagrees,
    GRAIN_ROLLUP: _grain_rollup_disagrees,
    TEMPORAL: _temporal_disagrees,
}


@dataclass(frozen=True)
class JoinPair:
    """One join: the declared value, the observed value, the value KIND that
    picks the comparator, and the `conflict()` kwargs to build IFF they disagree.
    The mechanism reads only `value_kind`; there is no field name in the dispatch,
    which is what makes a never-seen field joinable with no code."""

    value_kind: str
    declared: object
    observed: object
    entry: dict


def reconcile(pairs: Iterable[JoinPair]) -> list[dict]:
    """Judge each pair by its value kind's comparator and emit a conflict on a
    disagreement. The ONE dispatch: no per-dimension branch, no per-field branch.
    An unpublished value kind fails HERE at construction, not on the wire."""
    conflicts: list[dict] = []
    for pair in pairs:
        comparator = COMPARATORS.get(pair.value_kind)
        if comparator is None:
            raise ValueError(
                f"unpublished conflict value kind {pair.value_kind!r}: add it to COMPARATORS, "
                f"which is what the join mechanism dispatches on. A new value kind is a reviewed "
                f"change; a new joinable FIELD of an existing kind is not"
            )
        if comparator(pair.declared, pair.observed):
            conflicts.append(conflict(**pair.entry))
    return conflicts


def prohibited_but_referenced(
    prohibited: Sequence[Mapping[str, str]],
    *,
    referenced_by: Mapping[str, Sequence[str]],
    commit_sha: str | None,
) -> list[dict]:
    """The customer's context prohibits a source and the estate still points at
    it (ADR 0021 decision 4, renamed from "prohibited but popular").

    Both sides declare the join key -- Git names the ref in
    `prohibited_sources[].ref`, and a reference is an observed edge into the
    asset that ref resolves to. No comparator: presence against presence.

    Named for what it counts. No source discloses an execution count (hy-d7xh),
    so this is not popularity: it is how many live assets declare a reference to
    a source the customer said not to use. `referenced_by` carries only live
    edges -- both endpoints undeleted -- because a deleted chart pointing at a
    prohibited dataset is not the estate using it.

    Silent where the ref resolves to no observed asset and where nothing
    references it. The first is an absence and the second is the prohibition
    working, and reporting either as a contradiction is decision 2's failure
    mode.
    """
    pairs = []
    for entry in prohibited:
        referrers = list(referenced_by.get(entry["ref"], ()))
        if not referrers:
            continue
        pairs.append(
            JoinPair(
                value_kind=PRESENCE,
                declared=entry["reason"],
                observed=referrers,
                entry=dict(
                    kind=PROHIBITED_BUT_REFERENCED,
                    produced_by=BUNDLE_RECONCILIATION,
                    severity=RECONCILED_KINDS[PROHIBITED_BUT_REFERENCED],
                    ref=entry["ref"],
                    context_says=f"prohibited: {entry['reason']}",
                    source_says=_references(referrers),
                    unresolved_since_commit=commit_sha,
                ),
            )
        )
    return reconcile(pairs)


def source_deleted_while_governed(
    deleted: Sequence[Mapping[str, str | None]],
    *,
    prohibited_refs: Sequence[str],
    commit_sha: str | None,
) -> list[dict]:
    """Git approves a source the connector stopped reporting (ADR 0021
    decision 4).

    Detected today and served one-sidedly, as a `deprecation` whose reason ends
    "; Git still approves it". That sentence is the whole two-sidedness: a
    reader has to parse prose to learn that a governed ref is involved, and
    `deprecations` carries no Git side to check it against. The deprecation
    stays -- an observation about the source is still true -- and the
    disagreement is stated where disagreements are.

    Only for a ref the commit declared. A deleted asset nothing approves is an
    observation, and it already has a home.

    `prohibited_refs` is subtracted rather than assumed empty, and it is a
    keyword the caller must pass because getting it wrong is silent. A commit
    DECLARES the sources it forbids -- that is how a prohibition can name one --
    so a prohibited source is `git_linked` evidence and reaches here on
    deletion like any other governed ref. Saying "approved as a source of this
    domain" about it would be a false Git side, and the pair itself is
    agreement: the customer said not to use it and the estate stopped carrying
    it. Measured, not anticipated: the revenue commit's prohibited dataset
    appeared in this list the first time a whole connection was swept.
    """
    forbidden = set(prohibited_refs)
    pairs = [
        JoinPair(
            value_kind=PRESENCE,
            declared="approved",
            observed=entry["deleted_at"],
            entry=dict(
                kind=SOURCE_DELETED_WHILE_GOVERNED,
                produced_by=BUNDLE_RECONCILIATION,
                severity=RECONCILED_KINDS[SOURCE_DELETED_WHILE_GOVERNED],
                ref=entry["ref"],
                context_says="approved as a source of this domain",
                source_says=(
                    f"the {entry['asset_type']} stopped being reported at {entry['deleted_at']}"
                ),
                unresolved_since_commit=commit_sha,
            ),
        )
        for entry in deleted
        if entry["ref"] not in forbidden
    ]
    return reconcile(pairs)


def ownership_mismatch(
    bridge: Mapping[str, str],
    *,
    observed_owner: Mapping[str, str],
    commit_sha: str | None,
) -> list[dict]:
    """Ownership mismatch, DECLARED-BRIDGE-ONLY (hy-ocbd; ADR-0021 decision 4
    unlock; `docs/development/reconciliation-unlock-triggers.md` section 2).

    Reconciled ONLY where the customer has DECLARED an identity bridge in Git:
    `bridge` maps a governed `owner_ref` to the catalog identity it corresponds to.
    An `owner_ref` with NO bridge entry is NEVER reconciled -- Git's
    `team:finance-data` and the catalog's `urn:li:corpuser:...` live in different
    identifier spaces, and bridging them by heuristic would be a false error
    finding. So an EMPTY bridge emits nothing (default-deny) and an unbridged owner
    is silent; nothing is ever inferred across the two spaces.

    EQUALITY via the `identity` value kind: the declared bridged identity against
    what the estate observes for that owner. Silent where the two are equal
    (agreement) and where the estate reports no owner for a bridged ref (an
    absence, ADR-0021 decision 2). Single declared identity per owner this cut;
    multi-owner/set semantics is a documented refinement (hy-imr0).

    NOT wired to the resolver yet: reading the declared bridge from the manifest (a
    new governed field, parse-not-surface) and the observed owners is the follow-on
    hy-z3wy, which forks first because a new governed manifest field is
    contract-adjacent. So no bundle emits an `ownership_mismatch` until that lands.
    """
    pairs = [
        JoinPair(
            value_kind=IDENTITY,
            declared=declared_identity,
            observed=observed_owner[ref],
            entry=dict(
                kind=OWNERSHIP_MISMATCH,
                produced_by=BUNDLE_RECONCILIATION,
                severity=RECONCILED_KINDS[OWNERSHIP_MISMATCH],
                ref=ref,
                context_says=f"declared owner {declared_identity}",
                source_says=f"the estate reports owner {observed_owner[ref]}",
                unresolved_since_commit=commit_sha,
            ),
        )
        for ref, declared_identity in bridge.items()
        if ref in observed_owner
    ]
    return reconcile(pairs)


GRAIN_EXACT = "exact"
GRAIN_ROLLUP_RELATION = "rollup"


def _grain_key(grain: object) -> str:
    """A grain's canonical string for EXACT equality: dimensions sorted, stripped,
    lowercased. Order and spelling-case are not part of a grain's meaning; the set
    of dimensions is."""
    return ",".join(sorted(str(d).strip().lower() for d in grain))


def grain_mismatch(
    ref: str,
    declared_grain: Sequence[str] | None,
    *,
    observed_grain: Sequence[str] | None,
    relation: str = GRAIN_EXACT,
    commit_sha: str | None,
) -> list[dict]:
    """Grain mismatch, DECLARED-ONLY (hy-868w; ADR-0021 decision 4 unlock;
    `docs/development/reconciliation-unlock-triggers.md` section 3).

    Reconciled ONLY where the customer DECLARED a structured grain. An empty/None
    declared grain emits NOTHING (default-deny), and grain is NEVER inferred from
    `column_names` -- a guessed grain is exactly decision 2's failure mode. An
    absent observed grain is an absence, not a conflict (decision 2), so it emits
    nothing.

    `relation` is DECLARED and decides the comparator, both over the two declared
    descriptors only (no warehouse relation is resolved): `exact` compares the two
    grains for EQUALITY via the `identity` value kind; `rollup` checks the observed
    grain is a valid rollup (a subset -- coarser or equal) of the declared one via
    the `grain_rollup` value kind, so a finer observed dimension the declared grain
    does not name is the disagreement.

    NOT wired to the resolver yet: reading the DECLARED structured grain (a new
    governed manifest field) and the connector's OBSERVED-grain projection are new
    governed inputs, the deferred-activation fork hy-yjkv, so no bundle emits a
    `grain_mismatch` until that lands.
    """
    if not declared_grain or not observed_grain:
        return []
    # FAIL-CLOSED on the declared control (hy-868w): `relation` is customer-declared,
    # so an unrecognised value must REFUSE, not silently select a comparator. A typo
    # that fell through to exact would reconcile under the wrong rule.
    if relation == GRAIN_EXACT:
        value_kind: str = IDENTITY
        declared: object = _grain_key(declared_grain)
        observed: object = _grain_key(observed_grain)
    elif relation == GRAIN_ROLLUP_RELATION:
        value_kind = GRAIN_ROLLUP
        declared = frozenset(str(d).strip().lower() for d in declared_grain)
        observed = frozenset(str(d).strip().lower() for d in observed_grain)
    else:
        raise ValueError(
            f"unrecognised declared grain relation {relation!r}: expected "
            f"{GRAIN_EXACT!r} or {GRAIN_ROLLUP_RELATION!r}. A declared control must fail "
            f"closed, never default to a comparator"
        )
    return reconcile(
        [
            JoinPair(
                value_kind=value_kind,
                declared=declared,
                observed=observed,
                entry=dict(
                    kind=GRAIN_MISMATCH,
                    produced_by=BUNDLE_RECONCILIATION,
                    severity=RECONCILED_KINDS[GRAIN_MISMATCH],
                    ref=ref,
                    context_says=f"declared grain {list(declared_grain)} ({relation})",
                    source_says=f"the estate reports grain {list(observed_grain)}",
                    unresolved_since_commit=commit_sha,
                ),
            )
        ]
    )


def freshness_stale(
    ref: str,
    threshold: timedelta | None,
    observed_modified_at: object | None,
    *,
    now: datetime,
    commit_sha: str | None,
) -> list[dict]:
    """Freshness stale, DECLARED-ONLY (hy-d9ys; ADR-0021 decision 4 unlock;
    `docs/development/reconciliation-unlock-triggers.md` section 4).

    Reconciled ONLY where the customer DECLARED a freshness threshold (a
    max-staleness). A `None`/absent threshold emits NOTHING (default-deny) -- a
    stale source contradicts nothing until a threshold says how fresh it must be,
    and a threshold is NEVER fabricated. An absent observed modification time is an
    absence, not a conflict (decision 2), so it emits nothing.

    `now` is a REQUIRED input -- the resolve's clock input, never the wall clock --
    so the cutoff `now - threshold` and therefore the verdict are DETERMINISTIC for
    a pinned resolve and can sit inside `bundle_id`. The comparison (observed older
    than the cutoff => stale) is the `temporal` value kind.

    INERT: this does NOT touch the existing served `freshness` OBSERVATION, which
    stays exactly as it is. Turning that observation into an error is a served-
    behaviour change (ADR-0021 dec4); reading the DECLARED threshold (a new governed
    manifest field) and supplying a deterministic resolve-clock `now` are new
    governed inputs -- the deferred-activation fork hy-kh9k. So no bundle emits a
    `freshness_stale` until that lands.
    """
    if threshold is None or observed_modified_at is None:
        return []
    cutoff = now - threshold
    return reconcile(
        [
            JoinPair(
                value_kind=TEMPORAL,
                declared=cutoff,
                observed=observed_modified_at,
                entry=dict(
                    kind=FRESHNESS_STALE,
                    produced_by=BUNDLE_RECONCILIATION,
                    severity=RECONCILED_KINDS[FRESHNESS_STALE],
                    ref=ref,
                    context_says=(
                        f"must be fresher than {threshold} (declared); cutoff {cutoff.isoformat()}"
                    ),
                    source_says=(
                        f"the estate last modified it at "
                        f"{_as_datetime(observed_modified_at).isoformat()}"
                    ),
                    unresolved_since_commit=commit_sha,
                ),
            )
        ]
    )


def _references(referrers: list[str]) -> str:
    """The count, then the sample, then the remainder if there is one. The
    count leads because it is the fact; the names follow because a claim a
    reader cannot start checking is a claim they have to take."""
    named = sorted(referrers)[:NAMED_REFERENCES]
    remainder = len(referrers) - len(named)
    plural = "" if len(referrers) == 1 else "s"
    tail = f", and {remainder} more" if remainder else ""
    return f"{len(referrers)} live reference{plural}: {', '.join(named)}{tail}"
