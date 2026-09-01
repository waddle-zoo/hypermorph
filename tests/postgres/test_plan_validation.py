"""Walking-skeleton step 10 against real evidence (hy-gh-31).

Plans validated against a bundle resolved from the pinned Git commit and the
observations the earlier steps persisted -- the same canonical revenue slice
the processor (hy-gh-38) and the bundle itself are proven against, not a
similar one. Hyperset runs no SQL here and never will in v0: the correct plan
and every wrong plan are separated by contradiction with governed context.
"""

from __future__ import annotations

import pytest

from hyperset.bundle import (
    AnalyticsPlan,
    ContextDirective,
    resolve_analytics_context,
    validate_analytics_plan,
)
from hyperset.processor import run_sync_processing
from tests.postgres.conftest import (
    APPROVED_DATASET,
    DRIFTED_EXPRESSION,
    GIT_EXPRESSION,
    sync_superset,
)

QUESTION = "Which source and rules should an analyst use for recognized revenue by region?"
APPROVED_REF = f"superset:dataset:{APPROVED_DATASET}"
DIMENSION_REF = "superset:dataset:5bcf01e3-3f70-50d2-bb31-562b627b09b8"
PROHIBITED_REF = "superset:dataset:6f4976c2-25ea-5d98-b714-9ca8e6c9b7e4"


def _bundle(session_factory, **directive):
    directive.setdefault("domains", ["revenue"])
    if directive["domains"]:
        directive.setdefault("concepts", ["recognized_revenue"])
    return resolve_analytics_context(
        query=QUESTION,
        directive=ContextDirective(**directive),
        session_factory=session_factory,
    )


def _plan(**overrides) -> AnalyticsPlan:
    """The plan the checked-in revenue context describes, written the way an
    agent that read the bundle would write it."""
    payload = {
        "source_refs": [APPROVED_REF, DIMENSION_REF],
        "fields": ["recognized_revenue", "region"],
        "joins": [
            {
                "from": "finance_orders_daily.customer_id",
                "to": "customer_dim.customer_id",
                "type": "inner",
            }
        ],
        "filters": ["finance_orders_daily.status = 'completed'", "customer_dim.is_test = false"],
        "grain": "order_date by customer_dim.region",
        "checks": [
            "recognized_revenue is non-negative",
            "monthly totals reconcile within 1% of the fixture close value",
        ],
    }
    payload.update(overrides)
    return AnalyticsPlan(**payload)


def _validate(session_factory, **overrides):
    return validate_analytics_plan(bundle=_bundle(session_factory), plan=_plan(**overrides))


@pytest.mark.postgres
def test_the_plan_the_governed_context_describes_is_valid(session_factory, revenue_slice):
    bundle = _bundle(session_factory)

    result = validate_analytics_plan(bundle=bundle, plan=_plan(bundle_id=bundle.bundle_id))

    assert result.status == "valid", [item.message for item in result.violations]
    assert result.violations == []
    # The answer names the exact commit it was checked against.
    assert result.checked_against["commit_sha"] == revenue_slice["context"].commit_sha
    assert result.checked_against["context_snapshot_id"] == revenue_slice["context"].snapshot_id
    assert result.to_dict()["execution"] == {
        "performed_by_hyperset": False,
        "result_validated_by_hyperset": False,
    }


@pytest.mark.postgres
def test_the_source_the_context_forbids_is_rejected_with_the_reason(session_factory, revenue_slice):
    result = _validate(session_factory, source_refs=[APPROVED_REF, DIMENSION_REF, PROHIBITED_REF])

    assert result.status == "invalid"
    (violation,) = [item for item in result.violations if item.subject == PROHIBITED_REF]
    assert violation.code == "prohibited_source"
    assert violation.section == "instructions.prohibited_sources"
    assert "Double-counts partial captures" in violation.message


@pytest.mark.postgres
def test_a_field_computed_against_the_context_is_rejected(session_factory, revenue_slice):
    """The exact mistake the drift finding describes, proposed up front."""
    result = _validate(
        session_factory,
        fields=[{"name": "recognized_revenue", "expression": DRIFTED_EXPRESSION}, "region"],
    )

    (violation,) = [item for item in result.violations if item.code == "field_expression_mismatch"]
    assert violation.subject == "recognized_revenue"
    assert GIT_EXPRESSION in violation.message
    assert DRIFTED_EXPRESSION in violation.message


@pytest.mark.postgres
def test_the_governed_expression_reformatted_is_still_the_governed_expression(
    session_factory, revenue_slice
):
    """Against the real commit: the same computation, spelled the way a
    warehouse or a formatter prints it. Under string-equality this was
    `field_expression_mismatch` and the plan was `invalid`, so the checked-in
    manifest had to be resynced to the exact characters (hy-gh-128)."""
    result = _validate(
        session_factory,
        fields=[
            {
                "name": "recognized_revenue",
                "expression": "sum( GROSS_AMOUNT - tax_amount ) AS recognized_revenue",
            },
            "region",
        ],
    )

    assert result.status == "valid", [item.message for item in result.violations]


@pytest.mark.postgres
def test_a_qualified_expression_against_the_real_commit_is_disclosed_not_rejected(
    session_factory, revenue_slice
):
    """Qualifying the columns with the approved dataset's table may name the
    same columns or may not. Hyperset reads no warehouse schema and runs no
    query, so it states both forms instead of calling the plan wrong."""
    result = _validate(
        session_factory,
        fields=[
            {
                "name": "recognized_revenue",
                "expression": "SUM(finance_orders_daily.gross_amount - "
                "finance_orders_daily.tax_amount)",
            },
            "region",
        ],
    )

    (violation,) = [
        item for item in result.violations if item.code == "field_expression_undecidable"
    ]
    assert violation.severity == "warning"
    assert GIT_EXPRESSION in violation.message
    assert result.status == "warnings"


@pytest.mark.postgres
def test_an_undeclared_join_is_rejected(session_factory, revenue_slice):
    result = _validate(
        session_factory,
        joins=[
            {
                "from": "finance_orders_daily.customer_id",
                "to": "customer_dim.customer_id",
                "type": "left",
            }
        ],
    )

    (violation,) = [item for item in result.violations if item.code == "join_type_mismatch"]
    assert violation.subject == "finance_orders_daily.customer_id->customer_dim.customer_id"
    assert "inner" in violation.message


@pytest.mark.postgres
def test_dropping_the_completed_orders_filter_is_rejected(session_factory, revenue_slice):
    result = _validate(session_factory, filters=["customer_dim.is_test = false"])

    (violation,) = [item for item in result.violations if item.code == "missing_required_filter"]
    assert violation.subject == "finance_orders_daily.status = 'completed'"
    assert violation.severity == "error"


@pytest.mark.postgres
def test_the_wrong_grain_is_rejected(session_factory, revenue_slice):
    result = _validate(session_factory, grain="order_date")

    (violation,) = [item for item in result.violations if item.code == "grain_mismatch"]
    assert violation.section == "instructions.grain"
    assert "order_date by customer_dim.region" in violation.message


@pytest.mark.postgres
def test_the_same_bundle_and_plan_validate_identically(session_factory, revenue_slice):
    first = _validate(session_factory, grain="order_date")
    second = _validate(session_factory, grain="order_date")

    assert first.to_dict() == second.to_dict()


@pytest.mark.postgres
def test_a_plan_validated_against_a_bundle_the_sources_have_outdated_cannot_be_judged(
    session_factory, revenue_slice
):
    """Steps 7 -> 8 -> 9 -> 10: the source drifts, the bundle changes, and a
    plan still quoting the old bundle id is told to resolve again instead of
    being approved against an answer that no longer holds."""
    stale = _bundle(session_factory).bundle_id
    drift = sync_superset(revenue_slice["connection_id"], session_factory, "drift")
    run_sync_processing(sync_run_id=drift.sync_run_id, session_factory=session_factory)

    result = _validate(session_factory, bundle_id=stale)

    assert result.status == "unverifiable"
    (violation,) = result.violations
    assert violation.code == "stale_bundle"
    assert stale in violation.message


@pytest.mark.postgres
def test_a_plan_using_a_drifted_field_is_told_which_finding_disputes_it(
    session_factory, revenue_slice
):
    drift = sync_superset(revenue_slice["connection_id"], session_factory, "drift")
    run_sync_processing(sync_run_id=drift.sync_run_id, session_factory=session_factory)

    result = _validate(session_factory)

    (violation,) = [item for item in result.violations if item.code == "disputed_field"]
    assert violation.subject == "recognized_revenue"
    assert violation.severity == "warning"
    assert GIT_EXPRESSION in violation.message
    assert DRIFTED_EXPRESSION in violation.message
    # Agreeing with Git is still not wrong; the dispute is disclosed, not enforced.
    assert result.status == "warnings"


@pytest.mark.postgres
def test_a_question_no_context_covers_approves_nothing(session_factory, revenue_slice):
    bundle = resolve_analytics_context(
        query="How many support tickets closed last week?",
        directive=ContextDirective(domains=["support"], concepts=["recognized_revenue"]),
        session_factory=session_factory,
    )

    result = validate_analytics_plan(bundle=bundle, plan=_plan())

    assert result.status == "unverifiable"
    (violation,) = result.violations
    assert violation.code == "no_governed_context"
    assert "nothing about the plan is approved" in violation.message


@pytest.mark.postgres
def test_a_plan_on_a_ref_only_the_directive_asked_for_is_rejected(session_factory, revenue_slice):
    """Retrieving an ungoverned asset is allowed and disclosed; planning on
    it as if the context approved it is not (hy-5c2, GitHub #70)."""
    # The Superset database behind the datasets: really observed, and never
    # declared by the revenue context.
    ungoverned = "superset:database:191e8838-4a5c-5f3f-9d53-71f52f56f7f8"
    bundle = _bundle(session_factory, asset_refs=[APPROVED_REF, DIMENSION_REF, ungoverned])
    assert bundle.status == "mixed"

    result = validate_analytics_plan(
        bundle=bundle, plan=_plan(source_refs=[APPROVED_REF, DIMENSION_REF, ungoverned])
    )

    assert result.status == "invalid"
    (violation,) = result.violations
    assert violation.code == "observed_only_source"
    assert violation.subject == ungoverned
    # And the plan that stays inside the governed part is still validated.
    assert validate_analytics_plan(bundle=bundle, plan=_plan()).status == "valid"
    assert (
        validate_analytics_plan(bundle=bundle, plan=_plan()).checked_against["bundle_status"]
        == "mixed"
    )


# hy-pvbu, fixed here, against the real bundle the defects were measured on.
#
# Both defects were measured by PR #103's committed benchmark recording, on the
# `revenue_by_region` case: two `validate_analytics_plan` calls, failing
# differently. PR #114's coverage-claim change moved that arm's route and the
# case now makes none, so hy-xmd7 restored them as contract tests -- each defect
# written twice, once as what the service did and once as an `xfail(strict=True)`
# for the contract it owed.
#
# This pull request pays both, so the two markers and the one characterization
# test that asserted a defect are gone: a characterization test left behind a fix
# is a defect asserted as a requirement. What remains is the contract, stated
# positively.
#
# Defect 2's characterization test SURVIVES, and the reason is worth stating
# because the comment it replaces predicted otherwise: it asserts that a
# `bundle_id` from an earlier resolve is `unverifiable`/`stale_bundle` with both
# ids disclosed, and every word of that is still the contract. Only the missing
# remedy was the defect, and adding one does not falsify the detection.
#
# Neither depends on what a model chose to call, so no re-record can drop them.


@pytest.mark.postgres
def test_a_plan_declaring_no_sources_is_named_as_such(session_factory, revenue_slice):
    """hy-pvbu defect 1: the omission is the finding, not each field.

    The plan declares no sources at all. Every governed field then requires a
    source the plan does not list, so the answer used to be one
    `undeclared_field_source` per field -- true of each field, and silent about
    the one thing that was wrong with the call, with a count that tracked the
    plan's fields rather than the mistake.
    """
    bundle = _bundle(session_factory)

    result = validate_analytics_plan(
        bundle=bundle, plan=_plan(bundle_id=bundle.bundle_id, source_refs=[])
    )

    (violation,) = result.violations
    assert violation.code == "no_declared_sources"
    assert violation.section == "source_refs"
    # Nothing was compared, so this is not a verdict that the governed context
    # says no.
    assert result.status == "unverifiable"
    # And the caller is sent to the list it should have declared from, on this
    # bundle, rather than to its own fields.
    approved = [source["ref"] for source in bundle.instructions["approved_sources"]]
    assert approved
    assert all(ref in violation.message for ref in approved)
    assert "instructions.approved_sources" in violation.recovery


@pytest.mark.postgres
def test_a_bundle_id_from_an_earlier_resolve_is_stale_without_anything_drifting(
    session_factory, revenue_slice
):
    """hy-pvbu defect 2, and the cause the existing stale-bundle test does not
    cover.

    `test_a_plan_validated_against_a_bundle_the_sources_have_outdated_cannot_be_judged`
    reaches `stale_bundle` by drifting a source. The agent reached it without
    drifting anything: it resolved twice, then validated against the FIRST
    bundle's id. `bundle_id` covers the request as well as the answer, so a
    different `query` produces a different id on an unchanged corpus -- which
    is why the violation cannot name a cause (hy-dvn) and why the recovery
    path is the defect rather than the detection.
    """
    earlier = resolve_analytics_context(
        query="What is recognized revenue by region?",
        directive=ContextDirective(domains=["revenue"], concepts=["recognized_revenue"]),
        session_factory=session_factory,
    )
    current = _bundle(session_factory)
    assert earlier.bundle_id != current.bundle_id
    # Nothing synced and nothing drifted between the two resolves.
    assert earlier.provenance_refs == current.provenance_refs

    result = validate_analytics_plan(bundle=current, plan=_plan(bundle_id=earlier.bundle_id))

    assert result.status == "unverifiable"
    (violation,) = result.violations
    assert violation.code == "stale_bundle"
    assert result.checked_against["planned_bundle_id"] == earlier.bundle_id
    assert result.checked_against["bundle_id"] == current.bundle_id


@pytest.mark.postgres
def test_a_stale_bundle_answer_carries_the_move_that_fixes_it(session_factory, revenue_slice):
    """hy-pvbu defect 2: the answer says what to do next, in a field.

    The agent that measured this was told `unverifiable`, was given two bundle
    ids, and retried with the same stale one. The detection was right and the
    response carried no move, so the recovery path was the undefended half.
    """
    earlier = resolve_analytics_context(
        query="What is recognized revenue by region?",
        directive=ContextDirective(domains=["revenue"], concepts=["recognized_revenue"]),
        session_factory=session_factory,
    )
    current = _bundle(session_factory)

    result = validate_analytics_plan(bundle=current, plan=_plan(bundle_id=earlier.bundle_id))

    (violation,) = result.violations
    assert violation.recovery
    # The move is named, and so is the field the current bundle is in -- the
    # retry that failed sent the earlier id again.
    assert "resolve again" in violation.recovery
    assert "checked_against.bundle_id" in violation.recovery
    assert result.to_dict()["violations"][0]["recovery"] == violation.recovery
    assert result.to_dict()["checked_against"]["bundle_id"] == current.bundle_id


@pytest.mark.postgres
def test_a_directive_that_gains_the_plans_refs_resolves_to_a_different_bundle(
    session_factory, revenue_slice
):
    """hy-t3am, the measured shape, pinned as a test rather than as prose.

    The governed arm planned correctly, decided to validate -- which is what
    `plan_validated_before_the_answer` asks of it -- and sent a validate
    directive carrying the `asset_refs` its plan reads. The resolve directive
    had not carried them. So the call re-resolved to a different bundle
    (planned cb-0f5046c1de99324b against resolved cb-72b9b503948a2597), came back
    `stale_bundle`, and the arm spent its final message on the mismatch.

    This is the contract, not a defect to be normalized away: `asset_refs`
    narrow `linked_evidence` (resolver.py `in_play`), so a bundle resolved WITH
    them omits the findings, conflicts, deprecations and freshness of every ref
    not named. Two bundles disclosing different caveats must not share one id.
    The directive is therefore copied, never re-chosen, and the refs a plan
    reads go in `source_refs` -- which is what the served schema now says.
    """
    resolved = _bundle(session_factory)

    # The same question, the same domain, plus exactly the refs the plan reads.
    with_plan_refs = _bundle(session_factory, asset_refs=[APPROVED_REF, DIMENSION_REF])

    assert with_plan_refs.bundle_id != resolved.bundle_id, (
        "adding the plan's refs to the directive left the bundle id where it was. "
        "Either the request stopped covering the id -- which is the whole staleness "
        "check -- or the directive is being normalized before it is echoed, which puts "
        "something other than what was asked on the record (ADR 0019). See hy-t3am."
    )

    result = validate_analytics_plan(
        bundle=with_plan_refs, plan=_plan(bundle_id=resolved.bundle_id)
    )

    assert result.status == "unverifiable"
    (violation,) = result.violations
    assert violation.code == "stale_bundle"


@pytest.mark.postgres
def test_the_validate_directive_copied_from_the_bundle_round_trips(session_factory, revenue_slice):
    """The move the `stale_bundle` recovery now names, executed.

    The recovery tells an agent whose request DIFFERS to re-send the same call
    with `query` and `directive` copied verbatim from the bundle's own
    `request`, and not to rebuild the plan. This asserts that move actually
    works: reading the echoed request back out and resolving with it must
    return the same bundle id, so a plan built against it validates.

    Without this, the recovery would be prose promising a round trip nothing
    checks -- and `planner.md` already said "the same query and directive you
    resolved with", which the model followed and still got this wrong.
    """
    resolved = _bundle(session_factory)
    echoed = resolved.to_dict()["request"]

    # Exactly what the recovery instructs: copy the request back, change nothing.
    round_tripped = resolve_analytics_context(
        query=echoed["query"],
        directive=ContextDirective(**echoed["directive"]),
        session_factory=session_factory,
    )

    assert round_tripped.bundle_id == resolved.bundle_id, (
        "the bundle's own echoed request did not resolve back to the bundle's own id, "
        "so the move the stale_bundle recovery names does not terminate. See hy-t3am."
    )

    result = validate_analytics_plan(bundle=round_tripped, plan=_plan(bundle_id=resolved.bundle_id))

    assert [violation.code for violation in result.violations] == []
    assert result.status == "valid"


@pytest.mark.postgres
def test_a_stripped_domain_is_valid_with_gaps_not_indistinguishable_from_a_pass(
    session_factory, revenue_slice
):
    """Acceptance (e), against the real resolved revenue bundle (#285).

    A plan that omits every requirement of a stripped-down domain -- the shipped
    revenue context with its filters, joins, grain, and checks emptied -- must
    NOT come back indistinguishable from a genuine pass. It reports each section
    that could not be checked and carries `valid_with_gaps`, where the same plan
    against the full manifest, with every requirement met, is a plain `valid`.
    """
    import dataclasses

    resolved = _bundle(session_factory)

    # A genuine pass: the full manifest, the plan that satisfies every requirement.
    genuine = validate_analytics_plan(bundle=resolved, plan=_plan(bundle_id=resolved.bundle_id))
    assert genuine.status == "valid", [item.message for item in genuine.violations]
    assert genuine.sections_not_checkable == []
    assert "sections_not_checkable" not in genuine.to_dict()

    # The domain stops being able to STATE its requirements: filters, joins,
    # grain, and checks are emptied. `fields` and `approved_sources` stay, so the
    # plan's fields and sources still validate -- nothing contradicts, because
    # there is nothing left to contradict.
    stripped_instructions = {
        **resolved.instructions,
        "filters": [],
        "joins": [],
        "grain": "",
        "validations": [],
    }
    stripped = dataclasses.replace(resolved, instructions=stripped_instructions)

    # The same plan, now omitting every (now-absent) requirement.
    result = validate_analytics_plan(
        bundle=stripped,
        plan=_plan(joins=[], filters=[], grain=None, checks=[]),
    )

    # The plan did not improve; the governed context stopped being able to check
    # it. That must not read as a checked pass.
    assert result.violations == []
    assert result.status == "valid_with_gaps"
    assert [item["section"] for item in result.sections_not_checkable] == [
        "instructions.filters",
        "instructions.joins",
        "instructions.grain",
        "instructions.validations",
    ]

    # The two results are distinguishable on the wire without diffing the bundle:
    # a different status, and a field the genuine pass does not carry.
    assert genuine.to_dict()["status"] != result.to_dict()["status"]
    assert "sections_not_checkable" in result.to_dict()
