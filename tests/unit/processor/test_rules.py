"""The one v0 rule, in isolation (hy-gh-38).

Supplemental to `tests/postgres/test_processor_engine.py`, which runs the
same rule over real pinned-Superset payloads and a real Git commit. ADR 0009
makes the real path the evidence; these cover the branches that are awkward
to stage end to end.
"""

from __future__ import annotations

import pytest

from hyperset.processor.rules import (
    FINDING_TYPES,
    MOVED_SIDES,
    RULE_ID,
    UNDECIDABLE_ID,
    FindingCandidate,
    GitContext,
    ObservedSource,
    approved_expression_drift,
)

_APPROVED_REF = "superset:dataset:ae48881d-334f-54a7-94e8-1ffcc73866e2"
_GIT_EXPRESSION = "SUM(gross_amount - tax_amount)"
_DRIFTED_EXPRESSION = "SUM(gross_amount)"


def _source(expressions, **overrides) -> ObservedSource:
    return ObservedSource(
        asset_id=overrides.pop("asset_id", "oa-1"),
        external_id="ae48881d-334f-54a7-94e8-1ffcc73866e2",
        asset_type="dataset",
        version_id="oav-2",
        expressions=expressions,
        **overrides,
    )


def _context(source: ObservedSource | None, *, fields=None) -> GitContext:
    return GitContext(
        snapshot_id="ctxsnap-1",
        commit_sha="a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
        repository="https://git.test/acme/analytics.git",
        ref="main",
        path="domains/revenue",
        domain="revenue",
        fields=fields
        or [
            {
                "name": "recognized_revenue",
                "source_ref": _APPROVED_REF,
                "expression": _GIT_EXPRESSION,
            }
        ],
        sources_by_ref={} if source is None else {_APPROVED_REF: source},
        owner_refs=["team:finance-data"],
    )


def test_drifted_expression_is_one_explainable_finding():
    context = _context(_source({"recognized_revenue": _DRIFTED_EXPRESSION}))

    findings = approved_expression_drift(context)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.finding_type == RULE_ID
    assert finding.severity == "error"
    assert finding.asset_id == "oa-1"
    assert finding.context_snapshot_id == "ctxsnap-1"
    # Both sides of the disagreement are in the sentence a human reads.
    assert _GIT_EXPRESSION in finding.explanation
    assert _DRIFTED_EXPRESSION in finding.explanation
    # Provenance is exact on both sides.
    assert finding.evidence["git"]["commit_sha"] == context.commit_sha
    assert finding.evidence["git"]["expression"] == _GIT_EXPRESSION
    assert finding.evidence["observed"]["observed_version_id"] == "oav-2"
    assert finding.evidence["observed"]["expression"] == _DRIFTED_EXPRESSION
    assert finding.proposed_reviewer == "team:finance-data"


def test_the_proposal_is_a_git_review_hyperset_cannot_apply():
    finding = approved_expression_drift(
        _context(_source({"recognized_revenue": _DRIFTED_EXPRESSION}))
    )[0]

    proposal = finding.proposed_action
    assert proposal["kind"] == "review_git_context"
    assert proposal["applied_by_hyperset"] is False
    assert proposal["requires_human_git_commit"] is True
    assert proposal["generated_by"].startswith("hyperset-processor/")
    assert proposal["repository"] == "https://git.test/acme/analytics.git"
    assert len(proposal["options"]) == 3


def test_a_candidate_cannot_express_an_approval():
    finding = approved_expression_drift(
        _context(_source({"recognized_revenue": _DRIFTED_EXPRESSION}))
    )[0]

    # Structural, not a policy check: there is no field to set.
    forbidden = {"approved", "state", "decision", "governed_version_id", "approve"}
    assert forbidden.isdisjoint(vars(finding))


def test_matching_expression_finds_nothing():
    context = _context(_source({"recognized_revenue": _GIT_EXPRESSION}))

    assert approved_expression_drift(context) == []


def test_a_field_the_source_declares_no_expression_for_is_not_a_finding():
    """`region` is a plain column reference; the dataset declares no metric
    of that name. Absence is not disagreement."""
    context = _context(
        _source({"recognized_revenue": _GIT_EXPRESSION}),
        fields=[
            {"name": "region", "source_ref": _APPROVED_REF, "expression": "customer_dim.region"}
        ],
    )

    assert approved_expression_drift(context) == []


def test_unresolved_ref_is_left_to_another_rule():
    assert approved_expression_drift(_context(None)) == []


def test_soft_deleted_asset_is_left_to_another_rule():
    context = _context(_source({"recognized_revenue": _DRIFTED_EXPRESSION}, deleted=True))

    assert approved_expression_drift(context) == []


def test_many_changes_for_one_asset_in_one_run_are_one_finding_with_all_evidence():
    """`upsert` is a public call and may run twice under one `sync_run_id`,
    so an asset legitimately carries more than one change per run."""
    changes = tuple(
        {"id": f"cc-{n}", "change_type": "updated", "sync_run_id": "sr-1"} for n in (1, 2)
    )
    context = _context(_source({"recognized_revenue": _DRIFTED_EXPRESSION}, changes=changes))

    findings = approved_expression_drift(context)

    assert len(findings) == 1
    assert [c["id"] for c in findings[0].evidence["connector_changes"]] == ["cc-1", "cc-2"]


def test_a_restored_change_without_a_new_version_still_carries_its_evidence():
    """A reappearance with unchanged content writes no version, so its
    change row has no `to_version_id` (hy-y8g.1). The rule reads the asset's
    current version, so the finding is unaffected."""
    context = _context(
        _source(
            {"recognized_revenue": _DRIFTED_EXPRESSION},
            changes=({"id": "cc-9", "change_type": "restored", "sync_run_id": "sr-2"},),
        )
    )

    findings = approved_expression_drift(context)

    assert [c["change_type"] for c in findings[0].evidence["connector_changes"]] == ["restored"]


def test_the_same_inputs_always_produce_the_same_finding():
    context = _context(_source({"recognized_revenue": _DRIFTED_EXPRESSION}))

    first, second = approved_expression_drift(context), approved_expression_drift(context)

    assert [f.dedup_key for f in first] == [f.dedup_key for f in second]
    assert first[0].dedup_key == (RULE_ID, "oa-1", "ctxsnap-1")


# -- the comparison, which used to be `==` (hy-803q, ADR 0021) ----------------
#
# `docs/v0-foundation.md` says "a reformatted governed expression is not a
# contradiction", plan validation has served that rule since hy-gh-128, and
# discovery's ranking counts such a pair as agreement. This rule compared
# characters, so it disagreed with all three -- and the suite passed unchanged
# when the comparison was fixed, which is the measure of how uncovered it was.

_REFORMATTED = "sum( gross_amount-tax_amount )"
"""The same computation, differently typed. Whitespace, case, no parens change."""

_QUALIFIED = "SUM(o.gross_amount - o.tax_amount)"
"""A table qualifier: neither the same computation nor provably a different one."""


def test_a_reformatted_expression_is_not_a_contradiction():
    """The defect, at its smallest (hy-803q).

    An `error` finding here said an agent "would report a number the source does
    not produce", which was false, and it sank the source in discovery's ranking
    while that same candidate reported the expression as agreeing. One pair of
    strings, two answers, because two components used two comparators.
    """
    context = _context(_source({"recognized_revenue": _REFORMATTED}))

    assert approved_expression_drift(context) == []


def test_a_qualifier_only_difference_is_disclosed_and_not_judged():
    """The third outcome the rule lacked (ADR 0021 decision 2).

    Settling `SUM(amount)` against `SUM(o.amount)` needs the warehouse schema
    Hyperset does not read or the query it does not run. Reporting it as an error
    reintroduces hy-803q in a narrower form; reporting nothing hides a real
    drift. So: a `warning`, under its own type, stating both forms.
    """
    context = _context(_source({"recognized_revenue": _QUALIFIED}))

    findings = approved_expression_drift(context)

    assert len(findings) == 1
    assert findings[0].finding_type == UNDECIDABLE_ID
    assert findings[0].severity == "warning"
    assert findings[0].evidence["comparison"] == "undecided"
    # Both forms travel, which is what makes it a disclosure rather than a verdict.
    assert _GIT_EXPRESSION in findings[0].explanation
    assert _QUALIFIED in findings[0].explanation
    assert "cannot be settled here" in findings[0].explanation


def test_a_real_drift_is_still_an_error_and_still_says_which_sides_disagree():
    """The case that already worked, kept working, and kept its severity.

    The existing end-to-end fixture drives this one, which is why it passed
    either way and why the reformatting case above could not have been caught by
    it.
    """
    findings = approved_expression_drift(
        _context(_source({"recognized_revenue": _DRIFTED_EXPRESSION}))
    )

    assert [(f.finding_type, f.severity) for f in findings] == [(RULE_ID, "error")]
    assert findings[0].evidence["comparison"] == "different"


def test_operand_order_stays_a_contradiction():
    """The comparator folds formatting, not meaning: `a - b` and `b - a` are
    different numbers, so relaxing whitespace must not relax subtraction."""
    findings = approved_expression_drift(
        _context(_source({"recognized_revenue": "SUM(tax_amount - gross_amount)"}))
    )

    assert [(f.finding_type, f.severity) for f in findings] == [(RULE_ID, "error")]


def test_no_finding_says_which_side_is_wrong():
    """hy-1a6j was closed on a remedy that told a caller to proceed on a verdict
    that refused it. A disagreement between Git and an observation is not
    automatically Git being right, so both outcomes state both sides and neither
    states a winner -- the choices live in `proposed_action`, in the customer's
    own repository (ADR 0021 decision 3).
    """
    for observed in (_DRIFTED_EXPRESSION, _QUALIFIED):
        finding = approved_expression_drift(_context(_source({"recognized_revenue": observed})))[0]

        assert "wrong" not in finding.explanation
        assert finding.proposed_action["applied_by_hyperset"] is False
        assert finding.proposed_action["requires_human_git_commit"] is True
        # Three choices, and none of them is Hyperset picking one.
        assert len(finding.proposed_action["options"]) == 3


def test_an_unpublished_finding_type_fails_where_it_is_constructed():
    """`conflicts[].kind` is `finding_type` passed straight through, and it was
    ungated -- the failure `docs/v0-foundation.md` records having happened once
    already, when nineteen violation codes were served while the document named
    four and every mechanised check stayed green (ADR 0021 decision 6).
    """
    with pytest.raises(ValueError, match="unpublished finding type"):
        FindingCandidate(
            finding_type="grain_moved",
            rule_version=2,
            severity="error",
            asset_id="oa-1",
            context_snapshot_id="ctxsnap-1",
            explanation="",
            evidence={},
            proposed_action={},
        )


# -- which side moved (hy-qfyn, ADR 0021 decision 3) --------------------------
#
# The decision is written in the present tense -- "It says which side MOVED" --
# and the first instance said neither side's movement nor carried the field that
# would let a reader derive it. Movement is measured against the version the
# commit LINKED, because that is the only earlier state this system holds.

_LINKED_VERSION = "oav-1"


def _linked(expression=_GIT_EXPRESSION, **overrides) -> ObservedSource:
    """A source whose commit pinned `_LINKED_VERSION`, which computed
    `expression`."""
    return _source(
        overrides.pop("expressions", {"recognized_revenue": _DRIFTED_EXPRESSION}),
        linked_version_id=_LINKED_VERSION,
        expressions_at_link=None if expression is None else {"recognized_revenue": expression},
        **overrides,
    )


def test_a_source_that_changed_after_the_commit_linked_it_moved():
    """The end-to-end case: Git approves what the linked version computed, and
    the dataset computes something else now."""
    finding = approved_expression_drift(_context(_linked()))[0]

    moved = finding.evidence["moved"]
    assert moved["side"] == "observed"
    assert moved["linked_version_id"] == _LINKED_VERSION
    # The third expression, which is what makes the answer checkable rather than
    # assertable: the two sides alone cannot say which one left the link point.
    assert moved["expression_at_link"] == _GIT_EXPRESSION
    # One string, both audiences.
    assert moved["basis"] in finding.explanation


def test_the_movement_fact_compares_computations_not_characters():
    """A reformatting at the link point is not the Git side having moved -- the
    same claim hy-803q proved the rule itself had to stop making."""
    finding = approved_expression_drift(_context(_linked(_REFORMATTED)))[0]

    assert finding.evidence["moved"]["side"] == "observed"


def test_an_approved_expression_the_linked_version_never_computed_is_the_git_side():
    """The source still computes what the commit pinned, so what differs from
    that version is Git's expression. Stated as that, never as "the commit was
    edited": this system reads one commit, not the history of one."""
    finding = approved_expression_drift(_context(_linked(_DRIFTED_EXPRESSION)))[0]

    assert finding.evidence["moved"]["side"] == "git"
    assert finding.evidence["moved"]["expression_at_link"] == _DRIFTED_EXPRESSION


def test_both_sides_leaving_the_link_point_is_stated_as_both():
    finding = approved_expression_drift(_context(_linked("SUM(net_amount)")))[0]

    assert finding.evidence["moved"]["side"] == "both"


def test_a_field_the_linked_version_did_not_compute_is_the_source_declaring_one():
    """The version the commit pinned declared no metric of that name; the
    dataset declares one now. That is the source moving, and the link point has
    no expression to serve beside it."""
    source = _source(
        {"recognized_revenue": _DRIFTED_EXPRESSION},
        linked_version_id=_LINKED_VERSION,
        expressions_at_link={},
    )

    moved = approved_expression_drift(_context(source))[0].evidence["moved"]

    assert moved["side"] == "observed"
    assert moved["expression_at_link"] is None


def test_a_commit_that_pinned_no_version_leaves_movement_undecidable():
    """A ref corroborated after the commit was read carries no linked version
    (`linked_version_id` is null on the bundle for exactly this reason). There
    is no earlier state, so nothing is inferred from its absence."""
    finding = approved_expression_drift(
        _context(_source({"recognized_revenue": _DRIFTED_EXPRESSION}))
    )[0]

    moved = finding.evidence["moved"]
    assert moved["side"] == "undecidable"
    assert moved["linked_version_id"] is None
    assert moved["expression_at_link"] is None
    assert _APPROVED_REF in moved["basis"]


def test_a_linked_version_that_can_no_longer_be_read_is_a_different_undecidable():
    finding = approved_expression_drift(_context(_linked(None)))[0]

    moved = finding.evidence["moved"]
    assert moved["side"] == "undecidable"
    assert moved["linked_version_id"] == _LINKED_VERSION
    # The two undecidable cases are different facts and say so.
    assert "no longer readable" in moved["basis"]


def test_nothing_moving_produces_no_finding_to_classify():
    """Why `neither` is not in `MOVED_SIDES`, run rather than argued.

    Git agrees with the version the commit linked, and the source has not
    changed since it. `EQUIVALENT` is equality of the comparator's canonical
    form, so it is transitive: the two sides agree and the rule returns before a
    candidate exists to carry a side.
    """
    context = _context(_linked(expressions={"recognized_revenue": _REFORMATTED}))

    assert approved_expression_drift(context) == []


def test_every_moved_side_the_rule_can_emit_is_published():
    """The register and the emitter cannot drift, in either direction: every
    published side is reachable, and nothing the rule emits is unpublished."""
    emitted = {
        approved_expression_drift(_context(source))[0].evidence["moved"]["side"]
        for source in (
            _linked(),
            _linked(_DRIFTED_EXPRESSION),
            _linked("SUM(net_amount)"),
            _linked(None),
        )
    }

    assert emitted == set(MOVED_SIDES)


def test_an_unpublished_moved_side_fails_where_it_is_constructed():
    with pytest.raises(ValueError, match="unpublished moved side"):
        FindingCandidate(
            finding_type=RULE_ID,
            rule_version=2,
            severity="error",
            asset_id="oa-1",
            context_snapshot_id="ctxsnap-1",
            explanation="",
            evidence={"moved": {"side": "neither"}},
            proposed_action={},
        )


def test_every_type_the_rule_can_emit_is_published():
    """The register and the emitter cannot drift: whatever the rule produces has
    to be in the list a client would read."""
    emitted = set()
    for observed in (_DRIFTED_EXPRESSION, _QUALIFIED):
        emitted |= {
            f.finding_type
            for f in approved_expression_drift(_context(_source({"recognized_revenue": observed})))
        }

    assert emitted == set(FINDING_TYPES)
