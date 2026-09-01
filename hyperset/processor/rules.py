"""Deterministic processor rules (hy-gh-38), walking-skeleton step 8.

Exactly one rule ships here. ADR 0009 gate 5 puts breadth after the vertical
slice is green, so a second rule is a later decision, not a later commit --
the pre-pivot ten-rule engine is deliberately not being restored.

Everything in this module is a pure function over already-fetched data. No
session, no repository, no clock: the engine owns I/O, this owns the
judgement, and the judgement is reproducible from its inputs alone.

Nothing here can approve anything. `FindingCandidate` carries no state,
decision, or governed-version field, so "the processor proposed an approval"
is not expressible rather than merely forbidden.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from hyperset.bundle.equivalence import DIFFERENT, EQUIVALENT, compare_fragments

RULE_ID = "approved_expression_drift"
UNDECIDABLE_ID = "approved_expression_undecidable"
RULE_VERSION = 2
RULE_VERSIONS = {RULE_ID: RULE_VERSION, UNDECIDABLE_ID: RULE_VERSION}

# Every finding type this processor can produce, published because it is SERVED:
# `linked_evidence.findings[].finding_type` carries it, and `conflicts[].kind` is
# it passed straight through. It was ungated, which is the failure
# `docs/v0-foundation.md` records having already happened once -- nineteen
# violation codes served while the document named four, with every mechanised
# check green. Gated in `FindingCandidate` the way `PlanViolation` gates
# `VIOLATION_CODES` and `warning()` gates `WARNING_CODES`, so a type that would
# reach a client unpublished fails where it is constructed instead of on the wire
# (ADR 0021 decision 6).
#
# Not self-declaring: no served payload enumerates these, and adding an
# enumeration is a response-shape change ADR 0021 does not make. Publishing and
# gating the register is what replaces that, and hy-803q's follow-up holds
# serving it.
FINDING_TYPES = (RULE_ID, UNDECIDABLE_ID)

# Every value `evidence["moved"]["side"]` can carry, published and gated the way
# `FINDING_TYPES` is, and for one step less reason: the side is persisted on the
# finding and reaches a client today as a SENTENCE inside `explanation`, not yet
# as a field of its own -- no served payload carries `moved`. Gating it now is
# what stops the register and the emitter from being written twice over, which is
# the failure `docs/v0-foundation.md` records happening once already with
# nineteen unpublished violation codes (ADR 0021 decisions 3 and 6).
#
# Movement is measured against the version the commit LINKED, which is the only
# earlier state this system holds: Hyperset reads one commit, not the history of
# one, so "the commit changed" is not observable and is never claimed. What is
# observable is which side differs from that link point.
#
# `neither` is deliberately absent, and is unreachable rather than unbuilt.
# `EQUIVALENT` is equality of the comparator's canonical form, so it is
# transitive: if Git agrees with the linked version and the linked version
# agrees with the current one, Git agrees with the current one, and
# `approved_expression_drift` returns before a candidate is built. ADR 0021
# decision 3 names the case; nothing can produce it while a finding exists.
MOVED_SIDES = ("git", "observed", "both", "undecidable")


@dataclass(frozen=True)
class ObservedSource:
    """One observed asset a Git `source_ref` resolved to, as it stands now.

    `expressions` maps a source-declared field name to the expression the
    source currently computes it with -- the connector's normalized
    projection, so no rule reads a Superset or DataHub payload shape.

    `deleted` comes from `observed_assets.deleted_at`, not from the change
    stream. The stream alone cannot answer "is this asset live?" without
    replaying every event for the asset, and reasoning about a live asset as
    deleted is exactly the wrong finding a human reviewer would receive
    (hy-y8g.1).
    """

    asset_id: str
    external_id: str
    asset_type: str
    version_id: str | None
    expressions: Mapping[str, str]
    deleted: bool = False
    # Every change this sync run recorded for the asset. An asset legitimately
    # carries more than one per run (`upsert` is a public call that may run
    # twice under one `sync_run_id`), so this is evidence attached to a
    # finding -- never the thing the rule iterates.
    changes: tuple[dict, ...] = ()
    # The link point: which version of this asset the commit's evidence ref
    # pinned when the context was synced, and what that version computed. Three
    # states, and they are three different facts rather than one missing value
    # (ADR 0021 decision 3):
    #
    #   linked_version_id None      the commit pinned no version for this ref,
    #                               so there is no earlier state to measure
    #                               against -- the ref was corroborated after
    #                               the commit was read, or never was;
    #   expressions_at_link None    a version was pinned and is no longer
    #                               readable;
    #   both set                    movement is derivable, per field.
    #
    # Expressions and not just the id, because a version is written for the
    # asset and a finding is about one field: an id comparison answers "did this
    # asset change", which would report movement in a field nothing touched.
    linked_version_id: str | None = None
    expressions_at_link: Mapping[str, str] | None = None


@dataclass(frozen=True)
class GitContext:
    """The pinned Git snapshot under evaluation, plus the assets its refs
    resolved to. Authority stays in Git: the rule reads this, compares it to
    observation, and reports -- it never proposes a Hyperset-side edit."""

    snapshot_id: str
    commit_sha: str
    repository: str
    ref: str
    path: str
    domain: str
    fields: Sequence[Mapping[str, str]]
    sources_by_ref: Mapping[str, ObservedSource]
    owner_refs: Sequence[str] = ()


@dataclass(frozen=True)
class FindingCandidate:
    """What a rule found, before persistence assigns it identity."""

    finding_type: str
    rule_version: int
    severity: str
    asset_id: str
    context_snapshot_id: str
    explanation: str
    evidence: dict
    proposed_action: dict
    proposed_reviewer: str | None = None
    # Which (rule, asset, context commit) this candidate is about. Two runs
    # that reach the same conclusion produce the same key, which is what
    # makes a rerun idempotent instead of noisy.
    dedup_key: tuple[str, str, str] = field(init=False)

    def __post_init__(self) -> None:
        if self.finding_type not in FINDING_TYPES:
            raise ValueError(
                f"unpublished finding type {self.finding_type!r}: add it to FINDING_TYPES, "
                "which is what `linked_evidence.findings[].finding_type` and "
                "`conflicts[].kind` serve"
            )
        side = self.evidence.get("moved", {}).get("side")
        if side is not None and side not in MOVED_SIDES:
            raise ValueError(
                f"unpublished moved side {side!r}: add it to MOVED_SIDES, which is what "
                "a finding's persisted `evidence.moved.side` carries"
            )
        object.__setattr__(
            self, "dedup_key", (self.finding_type, self.asset_id, self.context_snapshot_id)
        )


def approved_expression_drift(context: GitContext) -> list[FindingCandidate]:
    """Git approves an expression for a field; the source now computes it
    differently.

    Iterates the Git manifest's fields, not the connector change stream: the
    authoritative question is "does every approved field still match the
    source?", and asking it that way is naturally immune to how many change
    rows one sync happened to write for one asset.

    Compared as COMPUTATIONS, by the comparator plan validation and discovery's
    ranking already use. It used to compare characters with `==`, which made a
    reformatting in the source an `error` finding whose explanation said an agent
    "would report a number the source does not produce" -- while
    `docs/v0-foundation.md` says in as many words that "a reformatted governed
    expression is not a contradiction", and discovery's `expression_agreement`
    signal counted the same pair as agreeing. One candidate said the source both
    agreed and disagreed with Git, from the same two strings (hy-803q).

    Three verdicts, three outcomes, which is the shape `plan.py` already serves
    and this rule lacked (ADR 0021 decision 2):

    - `EQUIVALENT`  -> nothing. Agreement.
    - `DIFFERENT`   -> an `error`. An agent following Git computes a number the
                       source does not produce.
    - `UNDECIDED`   -> a `warning`, and a different finding type. The difference
                       is confined to table qualifiers or casts, and settling it
                       needs the warehouse schema Hyperset does not read or the
                       query it does not run. Reporting it as an error would
                       reintroduce hy-803q in a narrower form; reporting nothing
                       would hide a real drift.

    Still one rule. Three outcomes of one comparison is not the second processor
    rule `CLAUDE.md` defers until the walking skeleton is green.

    Silent about conditions other rules own -- an unresolved ref, a
    soft-deleted asset, a field the source declares no expression for (a
    plain column reference has none). A rule that guessed at those would put
    a wrong finding in front of a human, which is the failure the review
    queue exists to prevent.
    """
    candidates = []
    for approved in context.fields:
        source = context.sources_by_ref.get(approved["source_ref"])
        if source is None or source.deleted:
            continue
        observed = source.expressions.get(approved["name"])
        if observed is None:
            continue
        verdict = compare_fragments(approved["expression"], observed)
        if verdict == EQUIVALENT:
            continue
        candidates.append(_candidate(context, approved, source, observed, verdict))
    return candidates


def _candidate(
    context: GitContext,
    approved: Mapping[str, str],
    source: ObservedSource,
    observed_expression: str,
    verdict: str,
) -> FindingCandidate:
    contradicts = verdict == DIFFERENT
    moved = _moved(approved, source, observed_expression)
    return FindingCandidate(
        finding_type=RULE_ID if contradicts else UNDECIDABLE_ID,
        rule_version=RULE_VERSION,
        # An agent handed the Git expression would compute a number the
        # source no longer produces. That is a wrong answer, not a warning.
        # An UNDECIDED pair is the opposite case: nothing here can tell whether
        # the number moved, so it is disclosed rather than judged.
        severity="error" if contradicts else "warning",
        asset_id=source.asset_id,
        context_snapshot_id=context.snapshot_id,
        explanation=_explanation(
            context, approved, source, observed_expression, contradicts, moved
        ),
        evidence={
            "rule_id": RULE_ID if contradicts else UNDECIDABLE_ID,
            "rule_version": RULE_VERSION,
            # The comparator's own word, so a reader can tell a contradiction
            # from a pair nothing here can settle without re-deriving it.
            "comparison": verdict,
            # Which side moved, and the third expression a reader needs to check
            # it: the two sides alone cannot say which one left the link point.
            "moved": moved,
            "field": approved["name"],
            "git": {
                "repository": context.repository,
                "ref": context.ref,
                "path": context.path,
                "commit_sha": context.commit_sha,
                "context_snapshot_id": context.snapshot_id,
                "domain": context.domain,
                "source_ref": approved["source_ref"],
                "expression": approved["expression"],
            },
            "observed": {
                "asset_id": source.asset_id,
                "asset_type": source.asset_type,
                "external_id": source.external_id,
                "observed_version_id": source.version_id,
                "expression": observed_expression,
            },
            # The connector events that make this run's evidence replayable,
            # however many the sync recorded for this asset.
            "connector_changes": [
                {
                    "id": change["id"],
                    "change_type": change["change_type"],
                    "sync_run_id": change["sync_run_id"],
                }
                for change in source.changes
            ],
        },
        proposed_action=_proposal(context, approved, source, observed_expression),
        proposed_reviewer=context.owner_refs[0] if context.owner_refs else None,
    )


def _moved(
    approved: Mapping[str, str],
    source: ObservedSource,
    observed_expression: str,
) -> dict:
    """Which side left the link point -- the version this commit pinned for the
    ref -- with the expression that version computed, so a reader can check the
    answer instead of taking it (ADR 0021 decision 3).

    Two comparisons, both by the one comparator, because the question is about
    computations and nothing else decides whether two of those agree:

    - Git against the linked version: did the approved expression ever describe
      what this commit pinned?
    - the linked version against the current one: has the source changed since?

    Never a claim that a commit was edited. This system reads one commit, not
    the history of one, so "Git moved" is stated as what is observable -- the
    approved expression differs from the version the commit itself linked, and
    the source has not changed since.

    `basis` is the sentence, built here rather than in `_explanation`, so the
    served fact and the sentence a human reads cannot drift apart.
    """
    field_name = approved["name"]
    fact = {
        "linked_version_id": source.linked_version_id,
        "expression_at_link": None,
    }
    if source.linked_version_id is None:
        return {
            **fact,
            "side": "undecidable",
            "basis": (
                f"Which side moved cannot be determined: this commit pinned no observed "
                f"version for {approved['source_ref']!r}, so there is no earlier state to "
                f"measure against."
            ),
        }
    if source.expressions_at_link is None:
        return {
            **fact,
            "side": "undecidable",
            "basis": (
                f"Which side moved cannot be determined: the version this commit linked "
                f"({source.linked_version_id}) is no longer readable."
            ),
        }
    at_link = source.expressions_at_link.get(field_name)
    if at_link is None:
        return {
            **fact,
            "side": "observed",
            "basis": (
                f"The source moved: the version this commit linked "
                f"({source.linked_version_id}) declared no expression for {field_name!r}, "
                f"and this {source.asset_type} computes one now."
            ),
        }
    fact["expression_at_link"] = at_link
    source_changed = compare_fragments(at_link, observed_expression) != EQUIVALENT
    git_matches_link = compare_fragments(approved["expression"], at_link) == EQUIVALENT
    if source_changed and git_matches_link:
        return {
            **fact,
            "side": "observed",
            "basis": (
                f"The source moved: the version this commit linked "
                f"({source.linked_version_id}) computed {at_link!r}, which is what Git "
                f"approves, and this {source.asset_type} has changed since."
            ),
        }
    if source_changed:
        return {
            **fact,
            "side": "both",
            "basis": (
                f"Both sides moved: the version this commit linked "
                f"({source.linked_version_id}) computed {at_link!r}, which neither the "
                f"approved expression nor the current one matches."
            ),
        }
    # The source still computes what the version this commit linked computed, so
    # the difference is on the Git side. `git_matches_link` cannot be true here:
    # it would mean Git agrees with the link point and the link point agrees with
    # the current expression, which by transitivity of the comparator's canonical
    # form makes the two sides agree, and no finding would have been built --
    # which is why `neither` is not in `MOVED_SIDES`.
    return {
        **fact,
        "side": "git",
        "basis": (
            f"The Git side moved: this {source.asset_type} still computes what the version "
            f"this commit linked ({source.linked_version_id}) computed, {at_link!r}, so what "
            f"differs from that version is the approved expression."
        ),
    }


def _explanation(
    context: GitContext,
    approved: Mapping[str, str],
    source: ObservedSource,
    observed_expression: str,
    contradicts: bool,
    moved: dict,
) -> str:
    """States both sides and which side MOVED. Never which side is wrong.

    A disagreement between Git and an observation is not automatically Git being
    right, and hy-1a6j was closed on a remedy that told a caller to proceed on a
    verdict that refused it. So the sentence names the commit, names the source,
    carries `_moved`'s sentence unchanged, and stops -- the choices live in
    `proposed_action`, in the customer's own repository (ADR 0021 decision 3).

    The movement clause is the same string the finding serves under
    `evidence.moved.basis`, so the fact a machine reads and the sentence a human
    reads cannot come apart. This docstring made the "which side MOVED" claim
    before anything computed it (hy-qfyn).
    """
    preamble = (
        f"Git context {context.repository}@{context.ref}:{context.path} at commit "
        f"{context.commit_sha[:12]} approves {source.asset_type} {source.external_id} "
        f"as the source of {approved['name']!r} with expression "
        f"{approved['expression']!r}, but that {source.asset_type} now computes it as "
        f"{observed_expression!r}. {moved['basis']}"
    )
    if contradicts:
        return (
            f"{preamble} Until a person decides which is right, an agent following this "
            f"context would report a number the source does not produce."
        )
    return (
        f"{preamble} The difference is confined to table qualifiers or casts, so whether the "
        f"two compute the same number cannot be settled here: it needs the warehouse schema, "
        f"which Hyperset does not read, or the query, which Hyperset does not run. Disclosed "
        f"with both forms rather than judged."
    )


def _proposal(
    context: GitContext,
    approved: Mapping[str, str],
    source: ObservedSource,
    observed_expression: str,
) -> dict:
    """The smallest reviewable action, and it happens in the customer's Git
    repository. Hyperset has no way to apply any of these: v0 review is a
    human commit against the authoritative repository (ADR 0012), so the
    proposal names the file to open and the choices, never a change Hyperset
    could make itself."""
    return {
        "kind": "review_git_context",
        # Machine-generated, and marked as such wherever it is rendered.
        "generated_by": f"hyperset-processor/{RULE_ID}@{RULE_VERSION}",
        "applied_by_hyperset": False,
        "requires_human_git_commit": True,
        "repository": context.repository,
        "ref": context.ref,
        "path": context.path,
        "file": "manifest.yaml",
        "field": approved["name"],
        "options": [
            f"accept the source change: set {approved['name']!r} to {observed_expression!r} in Git",
            f"reject the source change: restore {approved['expression']!r} in "
            f"{source.asset_type} {source.external_id}",
            f"approve a different source for {approved['name']!r} in Git",
        ],
    }
