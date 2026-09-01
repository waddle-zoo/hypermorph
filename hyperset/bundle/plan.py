"""Deterministic plan validation against one served `ContextBundle`
(hy-gh-31), `docs/v0-foundation.md` section 7 operation 2.

The validator compares a proposed analytics fetch with the instruction
sections the bundle already carries, and nothing else. It reads no database,
writes nothing, and never runs or checks the customer's SQL: `execution`
stays false on every result (`docs/v0-foundation.md` invariant 6). A wrong
plan is therefore caught by contradiction with governed context, not by
running it.

The documented parameters (`bundle_id, source_refs, fields, joins, filters,
grain, checks`) travel as one `AnalyticsPlan` so an HTTP or MCP adapter can
hand a decoded request straight through without re-typing the contract. The
bundle itself is passed in rather than looked up by id: bundles are
content-derived and unstored, so `bundle_id` is what the agent claims it
planned against, and a mismatch is a finding of its own -- the plan was built
against an answer that no longer describes the sources. `checked_against`
reports both ids, so a caller that omitted its own can see that nothing
compared the plan with the answer it came from; the served operation requires
it for that reason.

Violations name the offending ref, field, join, filter, or grain and the
instruction section it contradicts, because "invalid" alone gives an agent
nothing to change. Each one also carries `recovery`: the move that answers that
code, on the response rather than in a document the caller would have to be
reading already (hy-pvbu). A plan that declares no sources at all is refused as
`no_declared_sources` rather than compared field by field, for the same reason
-- one true statement per field does not name the single omission that produced
all of them.

SQL fragments -- expressions, filters, grain -- are compared as computations
rather than as characters (`hyperset/bundle/equivalence.py`, hy-gh-128). A
reformatted expression is not a contradiction, and a difference the comparator
cannot decide without the warehouse is disclosed with both forms instead of
being called wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from hyperset.bundle.directive import CATALOG_OPERATION
from hyperset.bundle.equivalence import (
    DIFFERENT,
    EQUIVALENT,
    UNDECIDED,
    canonical_key,
    compare_fragments,
)
from hyperset.bundle.schema import OBSERVED_ONLY, SCHEMA_VERSION, ContextBundle

PLAN_STATUSES = ("valid", "valid_with_gaps", "warnings", "invalid", "unverifiable")

# The governed requirement/definition sections a plan is validated AGAINST. When
# one is empty there is nothing for the plan to contradict or omit, so a plan can
# come back `valid` for want of anything to check rather than because it was
# checked and passed -- a false green indistinguishable from a genuine pass
# (#285). Each empty section is DISCLOSED (never a violation): the governed
# context is silent there, which is a legitimate state, so the plan is not failed
# -- the caller is told which sections could not be checked, the same posture as
# `resolution.warnings` on the resolve path.
#
# `reason` distinguishes the two ways a section can be empty (283-7, hy-p5hf):
#
#   * "declares no ..." -- a HAND-WRITTEN v0 domain is silent there. The author
#     could have declared it and chose not to; the section is legitimately empty.
#   * "the adapter's projected shape cannot declare ..." -- the domain came through
#     a context adapter (283-5), whose shape maps only identity/title/owners/
#     definitions and has NO expression for a requirement section, so the section
#     is absent by the projection, not left empty by an author. That is a stronger,
#     different fact: no restatement of the manifest could fill it while the domain
#     is adapter-projected.
#
# Both are DISCLOSURES, never violations -- a plan is not failed for either -- and
# the fourth tuple element is the adapter reason, chosen when the bundle carries a
# `resolution.projection` (283-5), the metadata this seam was reserved for.
_CHECKABLE_SECTIONS = (
    (
        "instructions.filters",
        "filters",
        "the governed context declares no filters",
        "the adapter's projected shape cannot declare filters",
    ),
    (
        "instructions.joins",
        "joins",
        "the governed context declares no joins",
        "the adapter's projected shape cannot declare joins",
    ),
    (
        "instructions.fields",
        "fields",
        "the governed context declares no fields",
        "the adapter's projected shape cannot declare fields",
    ),
    (
        "instructions.grain",
        "grain",
        "the governed context declares no grain",
        "the adapter's projected shape cannot declare grain",
    ),
    (
        "instructions.validations",
        "validations",
        "the governed context declares no checks",
        "the adapter's projected shape cannot declare checks",
    ),
)

# Errors contradict governed context. Warnings are things the governed
# context does not cover or no longer agrees with its own sources -- real
# enough to disclose, not enough to call the plan wrong.
ERROR = "error"
WARNING = "warning"

# Every value `violations[].code` can carry, and the move that answers it, for
# the reason `WARNING_CODES` exists one module over: a code is what a client
# branches on, so a served value nobody published is a branch nobody can write,
# and section 7's default-deny rule turns it into a refusal rather than a silent
# pass. Nineteen of them were served and four were named anywhere in the
# contract (hy-ruui).
#
# ONE REGISTER RATHER THAN A TUPLE PLUS A TABLE, so a code without a remedy is
# unrepresentable rather than merely tested for. Section 7's tool-design
# requirements have said "errors explain recovery" since v0, and every violation
# was breaking it: an agent told `undeclared_field_source` was told what is
# wrong with its plan and never what to send instead, which is the whole of
# hy-pvbu's second half. `OperationError` carries `recovery` for exactly this
# reason and a violation is the same obligation on the other response.
#
# The remedy is a property of the CODE and not of the instance: what a caller
# does about a prohibited source does not vary with which source it was, and the
# specifics -- which ref, which expression, which two bundle ids -- are already
# in `message`, `subject` and `checked_against`. So the text says which field to
# read for them rather than restating them.
#
# Gated in `PlanViolation` rather than pinned by a test alone, because a
# vocabulary a call site can bypass is not one: the failure being fixed here is
# exactly a code that reached a client without passing through anything that
# knew the list. Grouped by what a caller has to do about them.
#
# A remedy answers ONE code and never the verdict, because `status` is the
# verdict and a per-violation field cannot see what else is in the list
# (hy-1a6j, measured: `disputed_field` beside one `prohibited_source` served
# "the plan may proceed" next to `status: invalid`). For an ERROR the rule is
# sharper: an ERROR alone is already `invalid`, `invalid` has one published
# meaning -- compared with governed context and contradicting it -- and there
# is no reading of it under which the caller sends the same plan again. So a
# remedy that leaves the plan as sent is served instruction for getting past a
# refusal, which `observed_only_source` was: ADR 0019 rests the assist boundary
# on exactly this verdict ("Both are ERROR, so the verdict is `invalid` either
# way ... It holds by a value check"), and its two ERROR siblings say a caller
# cannot override the refusal. The remedy moved; the severity did not.
_UNDECIDABLE_RECOVERY = (
    "send the governed form verbatim to remove the difference, or settle it against the "
    "warehouse schema yourself and say which form you used; Hyperset reads no schema and runs "
    "no query, so it will not decide this and nothing here is approved"
)

VIOLATION_RECOVERY = {
    # One side of the comparison is missing, so nothing was compared.
    "no_governed_context": (
        "resolve a domain whose Git context covers this question and validate the plan "
        f"against that bundle; nothing in this one is approved. Call {CATALOG_OPERATION} "
        "for the configured domains"
    ),
    # BOTH branches, request-differs FIRST, because that one is reachable by
    # the caller and cheap, and it was the measured case (hy-t3am) while the
    # earlier text named only the moved-context branch. An agent that compared
    # the two requests, found a difference, and read a recovery beginning "if
    # they match" was given no next move for the case it was actually in --
    # and spent its final message reporting the mismatch instead of answering.
    "stale_bundle": (
        "compare this request with the one in the bundle's 'request'. If they DIFFER, this "
        "call re-resolved to a different bundle than the plan was built against: re-send "
        "this same call with 'query' and 'directive' copied verbatim from the bundle's "
        "'request', and do NOT rebuild the plan -- the request moved, not the plan. Refs "
        "your plan reads belong in 'source_refs' and are never added to 'directive'. If "
        "they MATCH, the context or its sources moved instead: resolve again and rebuild "
        "the plan against the id that resolve returns. 'checked_against.bundle_id' is what "
        "this request resolved to"
    ),
    "no_declared_sources": (
        "list the source refs the query will read in 'source_refs', taking them from this "
        "bundle's 'instructions.approved_sources', and validate again"
    ),
    # A source the governed context refuses or does not vouch for.
    "prohibited_source": (
        "remove the ref from 'source_refs' and read the same measure from an approved "
        "source; a prohibition is not overridable by a caller, and lifting one is a Git "
        "context change under human review"
    ),
    "unapproved_source": (
        "drop the ref, or replace it with one of 'instructions.approved_sources'. Approving "
        "it is a Git context change under human review, not a retry"
    ),
    "observed_only_source": (
        "remove the ref from 'source_refs' and read the measure from one of "
        "'instructions.approved_sources', or resolve a domain that governs this ref and "
        "plan against that bundle. The observation itself stays disclosed in "
        "'linked_evidence.observed_assets'; observation is never approval, so no plan "
        "that names this ref validates"
    ),
    "disputed_field": (
        "keep the field and surface the named finding wherever its value is shown, "
        "because the governed definition and the source disagree and only a human review "
        "settles that. This finding alone asks for no edit; 'status' carries the verdict"
    ),
    # The plan states something the governed context does not.
    "unapproved_field": (
        "use a field 'instructions.fields' defines, or drop this one from the plan"
    ),
    "unapproved_filter": (
        "keep it if the question really is narrower than the governed definition, and "
        "disclose that narrowing with the answer; otherwise remove it"
    ),
    "unapproved_join": "join through one of 'instructions.joins', or drop the join",
    "undeclared_field_source": (
        "add the source 'instructions.fields' names for this field to 'source_refs', or "
        "drop the field from the plan"
    ),
    "missing_required_filter": (
        "add the filter to 'filters', stated as 'instructions.filters' states it"
    ),
    "missing_required_check": (
        "run the check yourself after the query and report its outcome; Hyperset does not "
        "execute or check anything"
    ),
    # The plan and the context both state it, differently.
    "field_expression_mismatch": (
        "compute the field as 'instructions.fields' defines it, or drop the field"
    ),
    "field_source_mismatch": "read the field from the source 'instructions.fields' names for it",
    "grain_mismatch": "state the governed grain in 'grain', as 'instructions.grain' states it",
    # A source declares a per-source grain (284-3) and the plan reads it at a
    # different, unaggregated grain, so its rows fan out (284-4, Brandon's fork-2
    # REFINE ruling): the finer/more-specific per-source grain wins.
    "grain_fanout": (
        "the source is governed at a more specific per-source grain than the plan reads it "
        "at, so its rows fan out. Aggregate the source to the plan's grain -- wrap its "
        "measure in an aggregate like SUM/AVG -- or state the plan's 'grain' as the source's "
        "own; a finer per-source grain wins on disagreement and is not overridable by reading "
        "it coarser"
    ),
    "join_type_mismatch": "use the join type 'instructions.joins' declares for this join",
    # Neither the same computation nor provably a different one: disclosed
    # with both forms, and the element named is not governed.
    "field_expression_undecidable": _UNDECIDABLE_RECOVERY,
    "filter_undecidable": _UNDECIDABLE_RECOVERY,
    "grain_undecidable": _UNDECIDABLE_RECOVERY,
    # A plan reads a source the governed context classifies `restricted`/`pii`
    # (the #319 classification facet) and no governed caveat declares its handling
    # (hy-eif4, the first #230 enforcement slice). STRUCTURAL manifest governance:
    # it checks that a governed handling caveat EXISTS, never whether the handling
    # is adequate, and makes no access or identity decision -- that is the deferred
    # identity-gated access model, ADR-0030.
    "classification_undisclosed": (
        "the plan reads a source the governed context classifies restricted or pii, and no "
        "governed caveat declares how it must be handled. Add a caveat that names the source's "
        "ref and states its required handling or disclosure, or drop the source from the plan. "
        "Hyperset checks only that a governed handling caveat EXISTS -- not that the handling is "
        "correct -- and makes no access decision from the label"
    ),
    # Cross-domain plan validation (#230 slice 6, hy-i2us). A join between two composed
    # domains cannot be verified until the governed `joinable_on` relationship is emitted
    # (slice 2b, hy-g5u3): a WARNING, never an error and never upgraded to verified.
    "cross_domain_join_unverifiable": (
        "the plan joins two DIFFERENT governed domains, and the governed relationship that "
        "would verify the join -- its key, direction, grain, and cardinality -- is not yet "
        "emitted (the 'joinable_on' edge, slice 2b). Hyperset cannot verify this cross-domain "
        "join and does NOT upgrade it to verified because a plan proposed it. Check the "
        "bundle's 'composition.graph' for a governed relationship between the two domains, or "
        "wait for 'joinable_on' to land before relying on this join"
    ),
    # The routing key -- a source ref -- is not unique across the composed domains.
    "ambiguous_source_component": (
        "this source ref is an approved source in MORE THAN ONE of the composed domains, so "
        "which component governs it here is ambiguous. Resolve the domains separately, or "
        "qualify the plan to one domain, so a single governed component owns the source"
    ),
    # The field NAME is defined by more than one of the composed domains, so no single
    # component's definition governs it here.
    "ambiguous_field_component": (
        "this field is defined by MORE THAN ONE of the composed domains, so which "
        "component's definition governs it here is ambiguous. Resolve the domains "
        "separately, or qualify the plan to one domain, so a single governed component "
        "defines the field"
    ),
}

VIOLATION_CODES = tuple(VIOLATION_RECOVERY)


@dataclass(frozen=True)
class AnalyticsPlan:
    """What an agent proposes to fetch, in the caller's own words.

    `fields` and `joins` accept either a bare string (`"recognized_revenue"`,
    `"finance_orders_daily.customer_id->customer_dim.customer_id"`) or the
    mapping the bundle uses, so a caller that echoes the bundle's own
    instructions back is compared on every attribute it echoed.
    """

    bundle_id: str | None = None
    source_refs: list = field(default_factory=list)
    fields: list = field(default_factory=list)
    joins: list = field(default_factory=list)
    filters: list = field(default_factory=list)
    grain: str | None = None
    checks: list = field(default_factory=list)


@dataclass(frozen=True)
class PlanViolation:
    code: str
    severity: str
    section: str
    subject: str
    message: str

    def __post_init__(self) -> None:
        """The code is checked against the vocabulary rather than accepted as
        given, exactly as `warning()` checks a disclosure code: a violation
        invented at a call site is one no client can branch on, and it is how
        fifteen values reached the wire unpublished.

        A real check rather than an `assert`, because `-O` strips asserts and
        this is the only thing standing between a new call site and an
        unpublished served value.
        """
        if self.code not in VIOLATION_CODES:
            raise ValueError(
                f"unknown violation code {self.code!r}; add it to VIOLATION_RECOVERY "
                "and to docs/v0-foundation.md section 7 first"
            )

    @property
    def recovery(self) -> str:
        """What to send instead, read off the register rather than stored.

        A property and not a field: there is one remedy per code, so a
        constructor argument would be a chance for two call sites to answer the
        same code differently, and `VIOLATION_CODES` is the register's own keys
        -- a code that reaches here has a remedy by construction.
        """
        return VIOLATION_RECOVERY[self.code]

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.section, self.code, self.subject)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "section": self.section,
            "subject": self.subject,
            "message": self.message,
            # The remedy travels with the finding, on the response rather than
            # in a document the caller would have to already be reading
            # (hy-pvbu). Prose for the caller's next call, like an error's
            # `recovery`; `code` stays the thing to branch on.
            "recovery": self.recovery,
        }


@dataclass(frozen=True)
class PlanValidation:
    """The second public v0 response shape, and the last one in v0."""

    bundle_id: str
    status: str
    summary: str
    violations: list[PlanViolation]
    checked_against: dict | None
    # The governed sections that could not be checked because they declare
    # nothing (#285). Empty for every result that was fully checkable, and the
    # key is then ABSENT from the wire (see `to_dict`) so a fully-specified
    # `valid` result is byte-identical to before this field existed.
    sections_not_checkable: list[dict] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        payload = {
            "schema_version": self.schema_version,
            "bundle_id": self.bundle_id,
            "status": self.status,
            "summary": self.summary,
            "checked_against": self.checked_against,
            "violations": [violation.to_dict() for violation in self.violations],
            # Stated on every response, exactly as the bundle states it.
            "execution": {
                "performed_by_hyperset": False,
                "result_validated_by_hyperset": False,
            },
        }
        # Additive and CONDITIONAL: present only when a section could not be
        # checked, so a result with nothing to disclose is unchanged byte for
        # byte. A caller can then tell "checked and clean" (`valid`, key absent)
        # from "clean, and N sections had nothing to check" (`valid_with_gaps`,
        # key present) without diffing the bundle itself.
        if self.sections_not_checkable:
            payload["sections_not_checkable"] = list(self.sections_not_checkable)
        return payload


def validate_analytics_plan(*, bundle: ContextBundle, plan: AnalyticsPlan) -> PlanValidation:
    """Compare one proposed fetch with one bundle's instructions.

    Deterministic by construction: the same bundle and the same plan produce
    the same violations in the same order. A COMPOSED bundle (`domains[]` present,
    #230 slice 5) is validated cross-domain (`_validate_composed`, slice 6).
    """
    if bundle.domains is not None:
        return _validate_composed(bundle, plan)

    blocking = _blocking(bundle, plan)
    if blocking:
        return _result(bundle, plan, blocking)

    instructions = bundle.instructions
    violations = [
        *_source_violations(bundle, plan),
        *_field_violations(instructions, plan),
        *_join_violations(instructions, plan),
        *_filter_violations(instructions, plan),
        *_grain_violations(instructions, plan),
        *_fanout_violations(instructions, plan),
        *_classification_violations(instructions, plan),
        *_check_violations(instructions, plan),
        *_dispute_violations(bundle, plan),
    ]
    return _result(bundle, plan, violations)


# --- Cross-domain plan validation against a composed bundle (#230 slice 6, hy-i2us) ---
#
# WHAT LANDS: each plan source/field/filter/check is validated against its OWNING
# component (a `domains[]` entry) by reusing the single-domain validators over a composite
# built from ONLY the ENGAGED components -- the ones the plan reads a uniquely-owned ref
# from. An unengaged component contributes nothing, so its required filter/check never
# falls on a plan that does not touch that domain (#367). An undeclared source is
# `unapproved_source`; a source approved by MORE THAN ONE component is disclosed
# `ambiguous_source_component` and a field NAME defined by more than one engaged component
# is disclosed `ambiguous_field_component` (the routing key is not unique -- the F1
# guardrail; both are excluded from validation rather than silently routed/last-won). A
# JOIN between two different components is `cross_domain_join_unverifiable`: a WARNING,
# never an error and never upgraded to verified.
#
# WHAT IS DEFERRED TO SLICE 2b (hy-g5u3): verifying a cross-domain join's key, direction,
# grain compatibility, cardinality, and required filters -- all need the governed
# `joinable_on` edge, which 2b emits. Until it lands, a cross-domain join stays unverifiable
# (req 4: a missing governed relationship is never upgraded because a model proposed it),
# and cross-domain grain/fanout are NOT checked here (single-domain grain would misfire
# across a join). The follow-on bead upgrades this disclosure to real verification.


def _validate_composed(bundle: ContextBundle, plan: AnalyticsPlan) -> PlanValidation:
    blocking = _blocking(bundle, plan)
    if blocking:
        return _composed_result(bundle, plan, blocking, None)

    components = [entry["instructions"] for entry in bundle.domains]
    owners = _owning_components(components)

    # ENGAGED components are the ones the plan actually reads a UNIQUELY-owned ref from.
    # The composite is built from those only, NOT from every component (adversary bounce,
    # #367): an unengaged component must not contribute its required filter/check/grain, or
    # a plan reading only domain A's `orders` is falsely flagged for omitting domain B's
    # required `prices` filter. A ref owned by >1 component (ambiguous) or by none
    # (unapproved) does not engage a component -- it is DISCLOSED, never silently routed.
    engaged = _engaged_components(owners, plan)
    ambiguous_refs = _ambiguous_refs(owners, plan)
    # A field NAME defined by more than one ENGAGED component has no single governing
    # definition; validating it against a union would silently let the last component win
    # (adversary bounce, #367). It is disclosed and excluded from validation, exactly as an
    # ambiguous source ref is.
    ambiguous_fields = _ambiguous_field_names(components, engaged)

    disclosures = [
        *_ambiguous_source_violations(owners, plan),
        *_ambiguous_field_violations(ambiguous_fields, plan),
        *_cross_domain_join_violations(components, plan),
    ]
    # Reuse the single-domain validators over a composite of the ENGAGED components'
    # governed content; cross-domain joins, ambiguous refs and ambiguous fields are removed
    # from the inner plan so they disclose (above) rather than flag unapproved/mismatch.
    # Grain/fanout are omitted (deferred to slice 2b).
    composite = _composite_bundle(bundle, engaged)
    inner = _inner_plan(plan, components, ambiguous_refs, ambiguous_fields)
    violations = [
        *_scoped_source_violations(composite, inner, plan),
        *_field_violations(composite.instructions, inner),
        *_join_violations(composite.instructions, inner),
        *_filter_violations(composite.instructions, inner),
        *_classification_violations(composite.instructions, inner),
        *_check_violations(composite.instructions, inner),
        *_dispute_violations(composite, inner),
        *disclosures,
    ]
    return _composed_result(bundle, plan, violations, composite)


def _engaged_components(owners: dict[str, list[int]], plan: AnalyticsPlan) -> set[int]:
    """The components the plan reads a UNIQUELY-owned source ref from. Only these
    contribute their governed content to the composite -- an unengaged component's required
    filter/check must never fall on a plan that does not touch that domain (#367)."""
    return {owners[ref][0] for ref in _unique(plan.source_refs) if len(owners.get(ref, [])) == 1}


def _ambiguous_refs(owners: dict[str, list[int]], plan: AnalyticsPlan) -> set[str]:
    return {ref for ref in _unique(plan.source_refs) if len(owners.get(ref, [])) > 1}


def _ambiguous_field_names(components: list[dict], engaged: set[int]) -> set[str]:
    """Field names defined by MORE THAN ONE engaged component -- no single component
    governs them, so they are disclosed rather than resolved by union last-wins."""
    counts: dict[str, int] = {}
    for index in engaged:
        for name in {item["name"] for item in components[index]["fields"]}:
            counts[name] = counts.get(name, 0) + 1
    return {name for name, count in counts.items() if count > 1}


def _ambiguous_field_violations(names: set[str], plan: AnalyticsPlan) -> list[PlanViolation]:
    return [
        PlanViolation(
            code="ambiguous_field_component",
            severity=WARNING,
            section="instructions.fields",
            subject=name,
            message=(
                f"{name} is defined by more than one of the composed domains, so which "
                f"component's definition governs it here is ambiguous"
            ),
        )
        for name in _unique([name for name, _expression, _source in _field_entries(plan.fields)])
        if name in names
    ]


def _scoped_source_violations(
    composite: ContextBundle, inner: AnalyticsPlan, plan: AnalyticsPlan
) -> list[PlanViolation]:
    """Source validation over the engaged-component union. If every source the plan named
    was ambiguous (and so removed from `inner`), there is nothing left to source-validate:
    its refs are DISCLOSED as ambiguous, not "undeclared", so the empty `inner` must not
    trip `no_declared_sources`."""
    if not _unique(inner.source_refs) and _unique(plan.source_refs):
        return []
    return _source_violations(composite, inner)


def _owning_components(components: list[dict]) -> dict[str, list[int]]:
    """`source ref -> the DISTINCT component indices that approve it`. A ref owned by more
    than one component is the ambiguous routing key the F1 guardrail discloses.

    Keyed by distinct component index, NOT by `approved_sources` entry: one component may
    list the same ref twice (e.g. `role: primary` and `role: secondary`, a legitimate
    pattern the manifest schema does not forbid), and counting the entries would read a
    single owner as two and fire a false `ambiguous_source_component` (critic bounce, #367)."""
    owners: dict[str, list[int]] = {}
    for index, instructions in enumerate(components):
        for source in instructions["approved_sources"]:
            for ref in _source_refs(source):
                bucket = owners.setdefault(ref, [])
                if index not in bucket:
                    bucket.append(index)
    return owners


def _ambiguous_source_violations(
    owners: dict[str, list[int]], plan: AnalyticsPlan
) -> list[PlanViolation]:
    return [
        PlanViolation(
            code="ambiguous_source_component",
            severity=WARNING,
            section="instructions.approved_sources",
            subject=ref,
            message=(
                f"{ref} is an approved source in {len(owners[ref])} of the composed domains, "
                f"so which component governs it here is ambiguous"
            ),
        )
        for ref in _unique(plan.source_refs)
        if len(owners.get(ref, [])) > 1
    ]


def _source_table(ref: str) -> str:
    """The bare table name of a governed source ref, e.g.
    `table:postgres:analytics.public.finance_orders_daily` -> `finance_orders_daily`."""
    return ref.split(":")[-1].split(".")[-1]


def _component_of_table(table: str, components: list[dict]) -> int | None:
    """The component that approves a source whose table matches `table`, or `None`. Matched
    on the exact ref or its bare table name -- the exact `joinable_on` mapping is slice 2b."""
    for index, instructions in enumerate(components):
        for source in instructions["approved_sources"]:
            for ref in _source_refs(source):
                if table == ref or table == _source_table(ref):
                    return index
    return None


def _cross_domain_join_violations(
    components: list[dict], plan: AnalyticsPlan
) -> list[PlanViolation]:
    """A join whose two sides' sources belong to DIFFERENT composed domains -- disclosed
    UNVERIFIABLE, never upgraded to verified (the governed `joinable_on` edge that would
    verify it is slice 2b)."""
    violations = []
    for left, right, _type in _join_entries(plan.joins):
        left_component = _component_of_table(left.split(".")[0].strip(), components)
        right_component = _component_of_table(right.split(".")[0].strip(), components)
        if (
            left_component is not None
            and right_component is not None
            and left_component != right_component
        ):
            violations.append(
                PlanViolation(
                    code="cross_domain_join_unverifiable",
                    severity=WARNING,
                    section="instructions.joins",
                    subject=f"{left}->{right}",
                    message=(
                        f"{left}->{right} joins two different composed domains; the governed "
                        f"'joinable_on' relationship that would verify it is not emitted yet "
                        f"(slice 2b), so Hyperset cannot verify this join and does not upgrade "
                        f"it to verified"
                    ),
                )
            )
    return violations


def _inner_plan(
    plan: AnalyticsPlan,
    components: list[dict],
    ambiguous_refs: set[str],
    ambiguous_field_names: set[str],
) -> AnalyticsPlan:
    """`plan` reduced to the parts the composite validators may judge:

    - cross-domain joins removed, so `_join_violations` does not report them as
      `unapproved_join` -- they are disclosed `cross_domain_join_unverifiable` instead;
    - ambiguous source refs removed, so `_source_violations` does not report a doubly-owned
      ref as `unapproved` on top of its `ambiguous_source_component` disclosure;
    - ambiguous-named fields removed, so `_field_violations` does not judge a field with no
      single governing definition against a union last-wins entry.

    Every other field is untouched."""
    cross_domain = {
        (left, right)
        for left, right, _type in _join_entries(plan.joins)
        if _is_cross_domain(left, right, components)
    }
    kept_joins = [
        join
        for join, (left, right, _type) in zip(plan.joins, _join_entries(plan.joins), strict=True)
        if (left, right) not in cross_domain
    ]
    kept_refs = [ref for ref in plan.source_refs if ref not in ambiguous_refs]
    kept_fields = [
        field
        for field, (name, _expression, _source) in zip(
            plan.fields, _field_entries(plan.fields), strict=True
        )
        if name not in ambiguous_field_names
    ]
    return replace(plan, source_refs=kept_refs, joins=kept_joins, fields=kept_fields)


def _is_cross_domain(left: str, right: str, components: list[dict]) -> bool:
    left_component = _component_of_table(left.split(".")[0].strip(), components)
    right_component = _component_of_table(right.split(".")[0].strip(), components)
    return (
        left_component is not None
        and right_component is not None
        and left_component != right_component
    )


def _composite_bundle(bundle: ContextBundle, engaged: set[int]) -> ContextBundle:
    """A synthetic SINGLE-domain-shaped bundle whose instructions and evidence are the
    UNION of the ENGAGED components' (the ones the plan reads a uniquely-owned ref from),
    so the existing single-domain validators run unchanged. Building from engaged
    components only is what keeps an unengaged domain's required filter/check off a plan
    that never touches it (#367). `grain` is dropped: cross-domain grain compatibility is a
    join property and is deferred to slice 2b. `domains` is None, so this passes the
    composed guardrail; it is used only to drive validation, never served (the served
    bundle keeps its per-domain authority in `domains[]`)."""
    entries = [bundle.domains[index] for index in sorted(engaged)]

    def _flatten(section: str, key: str) -> list:
        return [item for entry in entries for item in entry[section].get(key, [])]

    instructions = {
        "definitions": _flatten("instructions", "definitions"),
        "approved_sources": _flatten("instructions", "approved_sources"),
        "fields": _flatten("instructions", "fields"),
        "joins": _flatten("instructions", "joins"),
        "filters": _flatten("instructions", "filters"),
        "grain": None,
        "caveats": _flatten("instructions", "caveats"),
        "validations": _flatten("instructions", "validations"),
        "prohibited_sources": _flatten("instructions", "prohibited_sources"),
        "context_doc": None,
    }
    linked_evidence = {
        key: _flatten("linked_evidence", key)
        for key in (
            "observed_assets",
            "findings",
            "freshness",
            "conflicts",
            "deprecations",
            "uncorroborated",
        )
    }
    return ContextBundle(
        request=bundle.request,
        resolution=bundle.resolution,
        context_authority=None,
        instructions=instructions,
        linked_evidence=linked_evidence,
        domain_graph={"nodes": [], "edges": []},
        provenance_refs=[],
        resolved_at=bundle.resolved_at,
    )


def _composed_result(
    bundle: ContextBundle,
    plan: AnalyticsPlan,
    violations: list[PlanViolation],
    composite: ContextBundle | None,
) -> PlanValidation:
    """Assemble the `PlanValidation` for a composed answer. `bundle_id` is the COMPOSED
    bundle's, so staleness is checked against the answer the caller planned from.
    `checked_against` is null: a composed answer has no single authority, and each
    component's provenance stays available in the bundle's `domains[]` (req 3)."""
    ordered = sorted(violations, key=lambda violation: violation.sort_key)
    status = _status(ordered)
    not_checkable = (
        _not_checkable(composite.instructions)
        if composite is not None and status in ("valid", "warnings")
        else []
    )
    if not_checkable and status == "valid":
        status = "valid_with_gaps"
    return PlanValidation(
        bundle_id=bundle.bundle_id,
        status=status,
        summary=_summary(status, ordered, not_checkable),
        violations=ordered,
        sections_not_checkable=not_checkable,
        checked_against=None,
    )


def _blocking(bundle: ContextBundle, plan: AnalyticsPlan) -> list[PlanViolation]:
    """Reasons the plan cannot be judged at all. Reported alone: comparing a
    plan against the wrong bundle would produce confident nonsense."""
    if plan.bundle_id is not None and plan.bundle_id != bundle.bundle_id:
        return [
            PlanViolation(
                code="stale_bundle",
                severity=ERROR,
                section="bundle_id",
                subject=plan.bundle_id,
                # Naming one cause would be a guess: the bundle id covers
                # the request as well as the answer, so a different query
                # string or a different directive produces this violation
                # exactly as a moved commit does, and the validator cannot
                # tell which happened (hy-dvn). It says so, and points at
                # the two ids in `checked_against` plus the request this
                # bundle was resolved for.
                message=(
                    f"the plan was built against bundle {plan.bundle_id!r}, and this "
                    f"request resolved to {bundle.bundle_id!r}. One of three things "
                    f"differs: the 'query', the 'directive', or the underlying context "
                    f"and sources. Compare this request with the one you resolved -- it "
                    f"is echoed in the bundle's 'request'. If they differ, the 'query' or "
                    f"'directive' sent here is not the one that was resolved, and this "
                    f"call re-resolved to a different bundle; the plan itself was never "
                    f"judged. If they match, the context or its sources moved and the "
                    f"answer must be resolved again before a plan built on the old one "
                    f"can be checked. The two ids are in 'checked_against': "
                    f"'planned_bundle_id' is what the plan claimed and 'bundle_id' is "
                    f"what this request resolved to"
                ),
            )
        ]
    if bundle.status not in ("governed", "mixed"):
        # `mixed` is not in this list: it has a governed part, and that part
        # is exactly what a plan should be judged against. `observed_only`
        # and `no_match` have none, and judging a plan against raw
        # observation would be an approval nobody gave.
        return [
            PlanViolation(
                code="no_governed_context",
                severity=ERROR,
                section="resolution.status",
                subject=bundle.status,
                message=(
                    f"this bundle has status {bundle.status!r}, so there is no governed "
                    f"context to validate against; nothing about the plan is approved"
                ),
            )
        ]
    if not _unique(plan.source_refs):
        # LAST of the three, and the order is the argument: this violation's
        # remedy names `instructions.approved_sources`, which is a list worth
        # reading only once the bundle is current and governed. A plan that is
        # both stale and empty is told about the staleness first, because a
        # source list read off the wrong bundle is the next defect.
        #
        # Reported alone rather than per field, which is the defect hy-pvbu
        # measured: a plan declaring nothing makes every governed field require
        # a source it does not list, so the answer was one
        # `undeclared_field_source` per field -- true of each field, and silent
        # about the omission that caused all of them. The count tracked the
        # plan's fields rather than the one thing that was wrong, and it sent an
        # agent to look at its fields.
        return [
            PlanViolation(
                code="no_declared_sources",
                severity=ERROR,
                section="source_refs",
                # Nothing was declared, so there is nothing to name: the subject
                # of every other violation is a thing the plan said, and this
                # one is about what it did not say.
                subject="",
                message=(
                    f"the plan declares no sources, so there is nothing to check it "
                    f"against: every governed field would require a source the plan does "
                    f"not list. Approved for this bundle: "
                    f"{', '.join(sorted(_approved(bundle))) or 'none'}"
                ),
            )
        ]
    return []


def _approved(bundle: ContextBundle) -> set[str]:
    return {
        ref for source in bundle.instructions["approved_sources"] for ref in _source_refs(source)
    }


def _source_violations(bundle: ContextBundle, plan: AnalyticsPlan) -> list[PlanViolation]:
    """The governed sections are authoritative even in a `mixed` bundle.

    A ref that is only in the bundle because the directive named it gets its
    own violation rather than the generic unapproved one: the plan is not
    wrong about a name, it is proposing to build on context nobody governs,
    and the two are different things to fix.
    """
    instructions = bundle.instructions
    approved = _approved(bundle)
    prohibited = {
        ref: source["reason"]
        for source in instructions["prohibited_sources"]
        for ref in _source_refs(source)
    }
    observed_only = {
        asset["ref"]
        for asset in bundle.linked_evidence["observed_assets"]
        if asset["governance"] == OBSERVED_ONLY
    }

    violations = []
    for ref in _unique(plan.source_refs):
        if ref in prohibited:
            violations.append(
                PlanViolation(
                    code="prohibited_source",
                    severity=ERROR,
                    section="instructions.prohibited_sources",
                    subject=ref,
                    message=f"{ref} is prohibited by the governed context: {prohibited[ref]}",
                )
            )
        elif ref in observed_only:
            violations.append(
                PlanViolation(
                    code="observed_only_source",
                    severity=ERROR,
                    section="instructions.approved_sources",
                    subject=ref,
                    message=(
                        f"{ref} exists only as an observation: this bundle carries it "
                        f"because the directive asked for it, and no governed context "
                        f"approves it for this domain. Observed-only context is never "
                        f"approved meaning; approved: {', '.join(sorted(approved)) or 'none'}"
                    ),
                )
            )
        elif ref not in approved:
            violations.append(
                PlanViolation(
                    code="unapproved_source",
                    severity=ERROR,
                    section="instructions.approved_sources",
                    subject=ref,
                    message=(
                        f"{ref} is not an approved source for this domain; approved: "
                        f"{', '.join(sorted(approved)) or 'none'}"
                    ),
                )
            )
    return violations


def _field_violations(instructions: dict, plan: AnalyticsPlan) -> list[PlanViolation]:
    governed = {item["name"]: item for item in instructions["fields"]}
    declared = set(_unique(plan.source_refs))
    source_options = {
        source["ref"]: set(_source_refs(source)) for source in instructions["approved_sources"]
    }

    violations = []
    for name, expression, source_ref in _field_entries(plan.fields):
        item = governed.get(name)
        if item is None:
            violations.append(
                PlanViolation(
                    code="unapproved_field",
                    severity=ERROR,
                    section="instructions.fields",
                    subject=name,
                    message=(
                        f"the governed context defines no field {name!r}; defined: "
                        f"{', '.join(sorted(governed)) or 'none'}"
                    ),
                )
            )
            continue
        if expression is not None:
            verdict = compare_fragments(item["expression"], expression)
            if verdict == DIFFERENT:
                violations.append(
                    PlanViolation(
                        code="field_expression_mismatch",
                        severity=ERROR,
                        section="instructions.fields",
                        subject=name,
                        message=(
                            f"{name} is defined as {item['expression']!r} by the governed "
                            f"context, and the plan computes {expression!r}"
                        ),
                    )
                )
            elif verdict != EQUIVALENT:
                violations.append(
                    PlanViolation(
                        code="field_expression_undecidable",
                        severity=WARNING,
                        section="instructions.fields",
                        subject=name,
                        message=_undecidable(
                            f"{name} is defined as {item['expression']!r} by the governed "
                            f"context, and the plan computes {expression!r}"
                        ),
                    )
                )
        accepted_sources = source_options.get(item["source_ref"], {item["source_ref"]})
        if source_ref is not None and source_ref not in accepted_sources:
            violations.append(
                PlanViolation(
                    code="field_source_mismatch",
                    severity=ERROR,
                    section="instructions.fields",
                    subject=name,
                    message=(
                        f"{name} comes from {item['source_ref']} in the governed context, "
                        f"and the plan reads it from {source_ref}"
                    ),
                )
            )
        if declared.isdisjoint(accepted_sources):
            violations.append(
                PlanViolation(
                    code="undeclared_field_source",
                    severity=ERROR,
                    section="instructions.fields",
                    subject=name,
                    message=(
                        f"{name} requires {item['source_ref']}, which the plan does not "
                        f"list in its sources"
                    ),
                )
            )
    return violations


def _source_refs(source: dict) -> tuple[str, ...]:
    override = source.get("bi_override")
    if override is None:
        return (source["ref"],)
    return source["ref"], override["ref"]


def _join_violations(instructions: dict, plan: AnalyticsPlan) -> list[PlanViolation]:
    governed = {(item["from"], item["to"]): item for item in instructions["joins"]}

    violations = []
    for left, right, join_type in _join_entries(plan.joins):
        item = governed.get((left, right))
        subject = f"{left}->{right}"
        if item is None:
            declared = ", ".join(sorted(f"{a}->{b}" for a, b in governed)) or "none"
            violations.append(
                PlanViolation(
                    code="unapproved_join",
                    severity=ERROR,
                    section="instructions.joins",
                    subject=subject,
                    message=(
                        f"the governed context declares no join {subject}; declared: {declared}"
                    ),
                )
            )
        elif join_type is not None and join_type != item["type"]:
            violations.append(
                PlanViolation(
                    code="join_type_mismatch",
                    severity=ERROR,
                    section="instructions.joins",
                    subject=subject,
                    message=(
                        f"{subject} is an {item['type']} join in the governed context, "
                        f"and the plan uses {join_type}"
                    ),
                )
            )
    return violations


def _filter_violations(instructions: dict, plan: AnalyticsPlan) -> list[PlanViolation]:
    """Pair each required filter with the proposed one that states it.

    Two passes rather than one, so an exact statement of a required filter is
    never consumed by a near-match earlier in the list: every equivalence is
    settled first, and only the leftovers are offered the relaxed comparison.
    """
    governed = _unique_fragments(instructions["filters"])
    proposed = _unique_fragments(plan.filters)
    paired = [False] * len(proposed)

    unmatched = []
    for item in governed:
        index = _first_match(item, proposed, paired, EQUIVALENT)
        if index is None:
            unmatched.append(item)
        else:
            paired[index] = True

    violations = []
    for item in unmatched:
        index = _first_match(item, proposed, paired, UNDECIDED)
        if index is None:
            violations.append(
                PlanViolation(
                    code="missing_required_filter",
                    severity=ERROR,
                    section="instructions.filters",
                    subject=item,
                    message=(
                        f"the governed context requires the filter {item!r}, which the plan omits"
                    ),
                )
            )
            continue
        paired[index] = True
        violations.append(
            PlanViolation(
                code="filter_undecidable",
                severity=WARNING,
                section="instructions.filters",
                subject=item,
                message=_undecidable(
                    f"the governed context requires the filter {item!r}, and the plan "
                    f"states {proposed[index]!r}"
                ),
            )
        )
    # An extra filter narrows the answer in a way Git never sanctioned. It is
    # not a contradiction -- a question can be about one region -- so it is
    # disclosed rather than rejected.
    violations.extend(
        PlanViolation(
            code="unapproved_filter",
            severity=WARNING,
            section="instructions.filters",
            subject=item,
            message=(
                f"the plan filters on {item!r}, which the governed context does "
                f"not declare; the result is narrower than the governed definition"
            ),
        )
        for index, item in enumerate(proposed)
        if not paired[index]
    )
    return violations


def _grain_violations(instructions: dict, plan: AnalyticsPlan) -> list[PlanViolation]:
    # Stripped, so a blank-after-strip governed grain is EMPTY here exactly as it
    # is for `_not_checkable` (panel MINOR-2): a whitespace grain declares no
    # grain, and if this read it as present it would compare `'   '` to the plan
    # as equivalent and emit no violation, while `_not_checkable` also skipped it
    # -- the #285 false green surviving in one field. The two must agree.
    governed = (instructions["grain"] or "").strip()
    if not governed:
        return []
    verdict = compare_fragments(governed, plan.grain or "")
    if verdict == EQUIVALENT:
        return []
    stated = f"the governed grain is {governed!r}, and the plan states {plan.grain!r}" + (
        "" if plan.grain else " (none)"
    )
    if verdict == DIFFERENT:
        return [
            PlanViolation(
                code="grain_mismatch",
                severity=ERROR,
                section="instructions.grain",
                subject=plan.grain or "",
                message=stated,
            )
        ]
    return [
        PlanViolation(
            code="grain_undecidable",
            severity=WARNING,
            section="instructions.grain",
            subject=plan.grain or "",
            message=_undecidable(stated),
        )
    ]


# The aggregate functions that ROLL a source's rows up to a coarser grain. A
# plan that reads a finer-grained source at a coarser plan grain does NOT fan out
# if it aggregates that source with one of these; without one, the finer rows
# multiply. Word-boundary + `(` so a column named `summary` or `counted` is not
# read as an aggregate, and case-insensitive because SQL is.
_AGGREGATE = re.compile(
    r"\b(?:sum|avg|count|min|max|median|mode|any_value|"
    r"std\w*|var\w*|stddev\w*|variance|percentile\w*|approx_\w*|"
    r"array_agg|string_agg|group_concat|listagg|bool_and|bool_or|every|bit_and|bit_or|"
    r"grouping|corr|covar\w*|regr_\w*)\s*\(",
    re.IGNORECASE,
)


def _ref_token(ref: str) -> str:
    """The bare table/object name a ref ends in, so an aggregate expression that
    names the source by short name (`SUM(fx_rates_daily.usd_rate)`) is recognised
    as aggregating it even when the field entry carries no `source_ref`.
    `table:postgres:analytics.public.fx_rates_daily` -> `fx_rates_daily`."""
    return ref.split(":")[-1].split(".")[-1].strip()


def _aggregates_source(plan: AnalyticsPlan, refs: tuple[str, ...]) -> bool:
    """Whether the plan AGGREGATES the source named by `refs` (any of its
    addresses). True when a plan field reading that source carries an aggregate
    expression -- matched either by the field's own `source_ref` or by the
    source's short name appearing inside the aggregate call. A source aggregated
    to the plan grain does not fan out."""
    tokens = {token for token in (_ref_token(ref) for ref in refs) if token}
    for name, expression, source_ref in _field_entries(plan.fields):
        # A dict field carries its computation in `expression`; a bare-string
        # field (`"SUM(fx_rates_daily.usd_rate)"`) carries it in the name itself.
        text = expression or name or ""
        # A window aggregate (`SUM(x) OVER (...)`) does NOT collapse rows, so a
        # source cleared by one still fans out -- this reads it as aggregated
        # anyway, a deliberate UNDER-block: the conservative direction for a new
        # ERROR gate is to withhold the accusation when the SQL is aggregate-shaped
        # but Hyperset does not run it. A tighter reading waits for a real case.
        if not _AGGREGATE.search(text):
            continue
        if source_ref in refs:
            return True
        # Word-boundary, not a bare substring: a token `orders` must not be read as
        # aggregated because `orders_backlog` appears in another source's aggregate,
        # which would suppress a real fan-out on the `orders` source.
        if any(re.search(rf"\b{re.escape(token)}\b", text) for token in tokens):
            return True
    return False


def _fanout_violations(instructions: dict, plan: AnalyticsPlan) -> list[PlanViolation]:
    """A per-source grain fan-out (284-4, hy-bz5f), Brandon's fork-2 = REFINE.

    284-3 surfaces a source's `facets.grain` -- the grain the AUTHOR asserts that
    source's rows are at. This is OPT-IN: a source declaring no per-source grain is
    not checked, so every domain that predates the facet (the shipped revenue
    manifest declares none) is byte-for-byte unchanged.

    When a plan READS such a source but states a `grain` that DISAGREES with the
    source's declared grain and does NOT aggregate that source, the source's rows
    fan out into the plan grain -- the fx_rates_daily-at-order-grain bug. REFINE:
    the finer/more-specific per-source grain wins on disagreement, so the plan must
    either state its grain as the source's or aggregate the source up to the plan
    grain. Two ways out, both checked here, so a correctly-aggregated or
    grain-matched plan is never flagged.

    A plan that states NO grain is left to `_grain_violations` (which compares the
    plan against the DOMAIN grain): with no stated plan grain there is no
    disagreement to measure here, and reporting a fan-out against a grain the
    caller never gave would be a guess."""
    plan_grain = (plan.grain or "").strip()
    if not plan_grain:
        return []
    read = set(plan.source_refs)
    violations: list[PlanViolation] = []
    for source in instructions["approved_sources"]:
        source_grain = ((source.get("facets") or {}).get("grain") or "").strip()
        if not source_grain:
            continue
        refs = _source_refs(source)
        if not any(ref in read for ref in refs):
            continue
        # Only a provably DIFFERENT grain fans out. EQUIVALENT means the source is
        # used at its own grain; UNDECIDED means the two differ only in qualifiers
        # or casts -- not a provable disagreement, and calling it a hard fan-out
        # would both contradict the sibling `grain_undecidable` (a WARNING, never
        # an ERROR) and block the very "state the plan grain as the source's own"
        # remedy this violation advises, since a table-qualified restatement reads
        # as UNDECIDED. Hyperset does not run the query, so an undecidable grain
        # relationship is disclosed by silence here, not judged a fan-out.
        if compare_fragments(source_grain, plan_grain) != DIFFERENT:
            continue
        if _aggregates_source(plan, refs):
            continue
        violations.append(
            PlanViolation(
                code="grain_fanout",
                severity=ERROR,
                section="instructions.approved_sources",
                subject=refs[0],
                message=(
                    f"the source {refs[0]!r} is governed at grain {source_grain!r}, and the "
                    f"plan reads it at grain {plan_grain!r} without aggregating it, so its "
                    f"rows fan out and multiply at the plan grain"
                ),
            )
        )
    return violations


# The classifications whose exposure the governed context must declare handling
# for. `internal`/`public` are not sensitive in this sense and are never flagged.
_SENSITIVE_CLASSIFICATIONS = ("restricted", "pii")


def _caveat_names_ref(caveat: str, ref: str) -> bool:
    """Whether a governed caveat NAMES a source by its ref, as a whole token.

    Word-bounded, never a bare substring: a ref is `table:pg:orders` and a caveat
    naming `table:pg:orders_eu` must NOT clear the first (the hy-c89s prefix-alias
    class, one layer over). The ref's inner `:`/`.` are not word characters, so the
    boundary is anchored on the outer word characters (`table` ... `daily`); this
    tolerates surrounding punctuation -- `(table:...:daily)` matches -- while
    `..._eu` does not, because the character after `orders` is `_`, a word char."""
    return re.search(rf"(?<!\w){re.escape(ref)}(?!\w)", caveat) is not None


def _classification_violations(instructions: dict, plan: AnalyticsPlan) -> list[PlanViolation]:
    """A restricted/pii source exposed without a governed handling disclosure
    (hy-eif4, the first #230 enforcement slice; Brandon's fold ruling).

    #319 surfaces a source's `facets.classification` -- a governed sensitivity
    label -- WITHOUT enforcement. This is the first structural enforcement: when a
    plan READS a source the governed context classifies `restricted` or `pii`, the
    governed context must DECLARE how it is handled, or exposing it is a governance
    gap. "Declared handling" is a governed caveat that names the source's ref (the
    only structural home the current shape offers; a dedicated handling field would
    be a served-shape change this slice does not take). OPT-IN like the fanout
    check: a source declaring no classification, or one that is `internal`/`public`,
    is never flagged, so the shipped revenue manifest (which declares none) is
    byte-for-byte unchanged.

    STRUCTURAL / manifest governance ONLY. It reads no caller identity, makes no
    access or deny decision, and does not judge whether the caveat's handling is
    ADEQUATE -- only that a governed, reviewed caveat names the sensitive source.
    Identity-gated access enforcement (deny/filter by caller, PII content handling)
    is the deferred access model, ADR-0030, and is deliberately not built here."""
    read = set(plan.source_refs)
    caveats = instructions.get("caveats") or []
    violations: list[PlanViolation] = []
    for source in instructions["approved_sources"]:
        classification = (source.get("facets") or {}).get("classification") or ""
        if classification not in _SENSITIVE_CLASSIFICATIONS:
            continue
        refs = _source_refs(source)
        if not any(ref in read for ref in refs):
            continue
        if any(_caveat_names_ref(caveat, ref) for ref in refs for caveat in caveats):
            continue
        violations.append(
            PlanViolation(
                code="classification_undisclosed",
                severity=ERROR,
                section="instructions.approved_sources",
                subject=refs[0],
                message=(
                    f"the source {refs[0]!r} is governed as {classification!r} and the plan reads "
                    "it, but no governed caveat declares its required handling"
                ),
            )
        )
    return violations


def _check_violations(instructions: dict, plan: AnalyticsPlan) -> list[PlanViolation]:
    """The bundle's `validations` are the manifest's `checks`, same content.

    Hyperset cannot run them -- it does not execute the query -- so an
    omitted check is disclosed to whoever will.
    """
    # `_fragment_text` first, so an object-shaped check (`{"expression": "..."}`,
    # schema-valid under the same `["string", "object"]` typing as filters) is compared
    # by its text and not by its Python repr -- which never matched a governed string and
    # produced a spurious `missing_required_check` (#281).
    proposed = {_collapse(_fragment_text(item)) for item in plan.checks}
    return [
        PlanViolation(
            code="missing_required_check",
            severity=WARNING,
            section="instructions.validations",
            subject=check,
            message=(
                f"the governed context requires the check {check!r}; the plan does not "
                f"carry it, and Hyperset does not run it"
            ),
        )
        for check in instructions["validations"]
        if _collapse(check) not in proposed
    ]


def _dispute_violations(bundle: ContextBundle, plan: AnalyticsPlan) -> list[PlanViolation]:
    """A field the plan uses whose source has drifted from what Git says.

    The plan agrees with governed context and the source does not, so the
    plan is not wrong -- but building on a disputed field silently is exactly
    the failure the connector and processor exist to surface.
    """
    planned = {name for name, _, _ in _field_entries(plan.fields)}
    return [
        PlanViolation(
            code="disputed_field",
            severity=WARNING,
            section="linked_evidence.conflicts",
            subject=conflict["field"],
            message=(
                f"{conflict['field']} is disputed: the governed context says "
                f"{conflict['context_says']!r} and {conflict['ref']} currently reports "
                f"{conflict['source_says']!r} (finding {conflict['finding_id']})"
            ),
        )
        for conflict in bundle.linked_evidence["conflicts"]
        if conflict.get("field") in planned
    ]


def _result(
    bundle: ContextBundle, plan: AnalyticsPlan, violations: list[PlanViolation]
) -> PlanValidation:
    ordered = sorted(violations, key=lambda violation: violation.sort_key)
    status = _status(ordered)
    # The disclosure is computed ONLY for an otherwise-`valid` result, which is
    # the false green #285 is about, plus `warnings`: an `unapproved_filter`
    # warning exists PRECISELY BECAUSE the governed filters are empty, so a
    # narrowing filter added to a sparse domain is exactly the case that must
    # still disclose its gaps rather than fall silent (panel MINOR-1). `invalid`
    # and `unverifiable` are left untouched -- the plan already has a verdict from
    # a section that WAS checkable, so an empty section is moot and the result
    # stays byte-for-byte as it was. `valid` upgrades to `valid_with_gaps`;
    # `warnings` stays `warnings` and carries the disclosure alongside its codes.
    not_checkable = (
        _not_checkable(bundle.instructions, bundle.resolution.get("projection"))
        if status in ("valid", "warnings")
        else []
    )
    if not_checkable and status == "valid":
        status = "valid_with_gaps"
    authority = bundle.context_authority
    return PlanValidation(
        bundle_id=bundle.bundle_id,
        status=status,
        summary=_summary(status, ordered, not_checkable),
        violations=ordered,
        sections_not_checkable=not_checkable,
        checked_against=(
            None
            if authority is None
            else {
                # Which bundle the plan said it was built from, so a reader
                # can see whether staleness was checked at all: `null` means
                # the caller did not say, and nothing compared the plan with
                # the answer it came from.
                "planned_bundle_id": plan.bundle_id,
                "bundle_id": bundle.bundle_id,
                # Disclosed because 'valid' against a `mixed` bundle means
                # valid against its governed part only.
                "bundle_status": bundle.status,
                "type": authority["type"],
                "commit_sha": authority["commit_sha"],
                "context_snapshot_id": authority["context_snapshot_id"],
                "provenance_refs": list(bundle.provenance_refs),
            }
        ),
    )


def _not_checkable(instructions: dict, projection: dict | None = None) -> list[dict]:
    """The governed sections that declare nothing, in a fixed order (#285).

    A section is not checkable when its governed content is empty: no filters to
    require, no fields to define, no grain to match. Reported as a disclosure, in
    the same deterministic order every time, so a caller can tell a sparse domain
    from a satisfied one without reading the bundle.

    `projection` is the bundle's `resolution.projection` (283-5): present ONLY for
    an adapter-sourced domain. When it is present, an empty requirement section is
    one the adapter's projected shape CANNOT express -- a stronger reason than a
    hand-written domain's silence (283-7). A hand-written domain (no projection)
    keeps the "declares no ..." reason byte-for-byte, so its disclosure is
    unchanged.
    """
    adapter = projection is not None
    return [
        {"section": section, "reason": cannot_declare if adapter else declares_nothing}
        for section, key, declares_nothing, cannot_declare in _CHECKABLE_SECTIONS
        if _section_is_empty(instructions.get(key))
    ]


def _section_is_empty(value) -> bool:
    """Whether a governed section declares nothing, agreeing with the validators
    on what counts as absent (panel MINOR-2 / NIT).

    A string section (`grain`) is empty when it is blank AFTER stripping: a grain
    of whitespace is vacuous, and `_grain_violations` strips it before deciding a
    violation, so the emptiness test here must strip it too or a whitespace grain
    would be checked against by neither -- the exact #285 false green surviving in
    one field. A list section is empty when it has no entries; a missing or null
    section (never produced by a real bundle, which carries every key) is empty."""
    if isinstance(value, str):
        return not value.strip()
    return not value


def _status(violations: list[PlanViolation]) -> str:
    codes = {violation.code for violation in violations}
    # `no_declared_sources` is unverifiable and not invalid, because `invalid`
    # is a verdict about a plan that was compared with governed context and
    # contradicts it. A plan declaring no sources contradicts nothing: one side
    # of the comparison is missing, exactly as it is for the other two, and a
    # client that reads `invalid` as "the governed context says no" would be
    # reading a judgement nobody made.
    if codes & {"stale_bundle", "no_governed_context", "no_declared_sources"}:
        return "unverifiable"
    if any(violation.severity == ERROR for violation in violations):
        return "invalid"
    if violations:
        return "warnings"
    return "valid"


def _summary(status: str, violations: list[PlanViolation], not_checkable: list[dict]) -> str:
    if status == "unverifiable":
        return f"The plan cannot be checked: {violations[0].message}."
    if status == "valid":
        return (
            "The plan contradicts nothing in the governed context. Hyperset did not run "
            "or check the query."
        )
    if status == "valid_with_gaps":
        # The success and the gap in ONE sentence, so a reader cannot take the
        # first without the second (#285): the plan contradicted nothing, but
        # some of that is because there was nothing there to contradict.
        sections = ", ".join(item["section"] for item in not_checkable)
        return (
            f"The plan contradicts nothing in the governed context, but "
            f"{len(not_checkable)} section(s) could not be checked because the governed "
            f"context declares nothing there: {sections}. A clean result here is not the "
            f"same as a checked one. Hyperset did not run or check the query."
        )
    errors = sum(1 for violation in violations if violation.severity == ERROR)
    warnings = len(violations) - errors
    # A `warnings` result may also carry gaps (panel MINOR-1): the disclosures it
    # names can exist precisely because a section is empty, so when they do the
    # summary says so in the same breath rather than leaving it to the structured
    # field alone. `not_checkable` is empty for a fully-specified result, so this
    # clause is absent and that summary is byte-unchanged.
    gaps = (
        f" {len(not_checkable)} section(s) could not be checked because the governed "
        f"context declares nothing there: "
        f"{', '.join(item['section'] for item in not_checkable)}."
        if not_checkable
        else ""
    )
    return (
        f"The plan has {errors} contradiction(s) and {warnings} disclosure(s) against the "
        f"governed context.{gaps} Hyperset did not run or check the query."
    )


def _field_entries(fields) -> list[tuple[str, str | None, str | None]]:
    entries = []
    for item in fields:
        if isinstance(item, dict):
            # `.get`, not `item["name"]`: `fields` is typed `["string", "object"]` with
            # no property-shape check, so a caller may echo an object that omits `name`.
            # Indexing it raised KeyError -> a 500 on schema-valid input, the same class
            # as the filter crash (#281). A nameless object falls back to its string
            # projection, so it is judged (as an unapproved field) rather than crashing.
            name = item.get("name") or _fragment_text(item)
            entries.append((name, item.get("expression"), item.get("source_ref")))
        else:
            entries.append((str(item), None, None))
    return entries


def _join_entries(joins) -> list[tuple[str, str, str | None]]:
    entries = []
    for item in joins:
        if isinstance(item, dict):
            # `.get`, not `item["from"]`/`item["to"]`: same reason as `_field_entries` --
            # a schema-valid object may omit either side, and indexing it 500'd (#281). A
            # missing side becomes empty and matches no governed join, so it is reported as
            # unapproved rather than crashing. (An empty side could only match a governed
            # join whose own side is empty, which the manifest schema does not permit --
            # that would be malformed Git-owned context, not caller-reachable input.)
            entries.append((item.get("from", ""), item.get("to", ""), item.get("type")))
        else:
            # `->` is the documented directional form, but `=` is the SQL-ish form
            # a caller reaches for -- and the one the shipped revenue eval bank
            # itself writes (playground/examples/revenue/evals.yaml). Accept both so
            # a `from = to` join is compared on its members rather than parsed to a
            # `from`-only string that reads as an `unapproved_join` the governed
            # context "declares no" -- a governed-looking false negative on the one
            # operation whose job is catching a planner's mistakes (#281).
            text = str(item)
            delimiter = "->" if "->" in text else "="
            left, _, right = text.partition(delimiter)
            entries.append((left.strip(), right.strip(), None))
    return entries


def _unique(values) -> list[str]:
    """De-duplicated, in the order the caller wrote them: a plan naming the
    same source twice must not produce the same violation twice."""
    return list(dict.fromkeys(values))


def _fragment_text(item) -> str:
    """A plan fragment's string projection, so an object form never reaches a
    `subject` or a `sort_key` as a dict (#281).

    `filters` (like `fields`) is typed `["string", "object"]`, so a caller may
    echo the bundle's own entries back as mappings. The string form is the SQL
    text the comparator already reasons about -- `expression` where a mapping
    names one, the value itself where it is already a string. Without this a
    dict-shaped filter reached `PlanViolation.subject`, and `_result`'s
    `sorted(..., key=sort_key)` raised `TypeError: '<' not supported between
    instances of 'dict' and 'dict'` -- a 500 on schema-valid input.
    """
    if isinstance(item, dict):
        return _collapse(str(item.get("expression", item.get("filter", item))))
    return str(item)


def _unique_fragments(fragments) -> list[str]:
    """One entry per computation, keeping the caller's own words for the one
    that arrived first. Two spellings of one filter are one filter."""
    seen = {}
    for item in fragments:
        text = _fragment_text(item)
        seen.setdefault(canonical_key(text), text)
    return list(seen.values())


def _first_match(fragment: str, candidates: list[str], paired: list[bool], verdict: str):
    for index, candidate in enumerate(candidates):
        if not paired[index] and compare_fragments(fragment, candidate) == verdict:
            return index
    return None


def _undecidable(stated: str) -> str:
    """Why a difference is disclosed instead of judged, in the same words
    everywhere it happens."""
    return (
        f"{stated}. The two differ only in table qualifiers or casts, which are the same "
        f"computation or are not depending on the warehouse schema. Hyperset does not run "
        f"the query, so it states both forms rather than deciding"
    )


def _collapse(text: str) -> str:
    """Whitespace only. Case is not folded: SQL string literals and the
    governed grain mean different things in different cases."""
    return " ".join(str(text).split())
