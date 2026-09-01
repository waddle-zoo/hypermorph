"""The comparator's own rules (hy-gh-31).

Supplemental to `tests/postgres/test_plan_validation.py`, which validates
plans against a bundle resolved from a real Git commit and real pinned-source
evidence. Here the instructions are stated inline so each rule is readable on
its own.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

import hyperset.bundle.plan
from hyperset.bundle import (
    VIOLATION_CODES,
    AnalyticsPlan,
    ContextBundle,
    PlanViolation,
    validate_analytics_plan,
)
from hyperset.bundle.plan import VIOLATION_RECOVERY

PRIMARY = "superset:dataset:orders"
UNGOVERNED = "superset:dataset:marketing_spend"
DIMENSION = "superset:dataset:customers"
BANNED = "superset:dataset:partial-captures"

INSTRUCTIONS = {
    "definitions": [],
    "approved_sources": [
        {"ref": PRIMARY, "role": "primary", "reason": "Completed orders."},
        {"ref": DIMENSION, "role": "required_dimension", "reason": "Region."},
    ],
    "prohibited_sources": [{"ref": BANNED, "reason": "Double-counts partial captures."}],
    "fields": [
        {
            "name": "recognized_revenue",
            "source_ref": PRIMARY,
            "expression": "SUM(gross_amount - tax_amount)",
        },
        {"name": "region", "source_ref": DIMENSION, "expression": "customer_dim.region"},
    ],
    "joins": [
        {
            "from": "finance_orders_daily.customer_id",
            "to": "customer_dim.customer_id",
            "type": "inner",
        }
    ],
    "filters": ["finance_orders_daily.status = 'completed'", "customer_dim.is_test = false"],
    "grain": "order_date by customer_dim.region",
    "caveats": [],
    "validations": ["recognized_revenue is non-negative"],
    "context_doc": "...",
}


def _bundle(**overrides) -> ContextBundle:
    payload = {
        "request": {"query": "recognized revenue by region"},
        "resolution": {"status": "governed", "summary": "", "warnings": []},
        "context_authority": {
            "type": "git",
            "commit_sha": "abc123",
            "context_snapshot_id": "ctxsnap-1",
        },
        "instructions": INSTRUCTIONS,
        "linked_evidence": {"observed_assets": [], "findings": [], "conflicts": []},
        "domain_graph": {"nodes": [], "edges": []},
        "provenance_refs": ["git_context:ctxsnap-1@abc123"],
        "resolved_at": datetime(2026, 7, 28, tzinfo=UTC),
    }
    payload.update(overrides)
    return ContextBundle(**payload)


def _mixed_bundle() -> ContextBundle:
    """Governed revenue context plus one ref only the directive asked for."""
    return _bundle(
        resolution={"status": "mixed", "summary": "", "warnings": []},
        linked_evidence={
            "observed_assets": [
                {"ref": PRIMARY, "governance": "git_linked"},
                {"ref": UNGOVERNED, "governance": "observed_only"},
            ],
            "findings": [],
            "conflicts": [],
        },
    )


def _plan(**overrides) -> AnalyticsPlan:
    payload = {
        "source_refs": [PRIMARY, DIMENSION],
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
        "checks": ["recognized_revenue is non-negative"],
    }
    payload.update(overrides)
    return AnalyticsPlan(**payload)


def _validate(bundle=None, **plan_overrides):
    return validate_analytics_plan(bundle=bundle or _bundle(), plan=_plan(**plan_overrides))


def _codes(result) -> list[str]:
    return [violation.code for violation in result.violations]


def test_a_plan_that_follows_the_governed_context_is_valid():
    result = _validate()

    assert result.status == "valid"
    assert result.violations == []
    assert result.bundle_id == _bundle().bundle_id
    assert result.checked_against["commit_sha"] == "abc123"


def test_an_explicit_bi_override_is_an_approved_address_for_its_table():
    instructions = deepcopy(INSTRUCTIONS)
    table_ref = "table:postgres:analytics.public.finance_orders_daily"
    override_ref = "superset:dataset:orders"
    instructions["approved_sources"][0] = {
        "ref": table_ref,
        "role": "primary",
        "bi_override": {"ref": override_ref, "reason": "Governed published dataset."},
    }
    instructions["fields"][0]["source_ref"] = table_ref

    result = _validate(
        bundle=_bundle(instructions=instructions),
        source_refs=[override_ref, DIMENSION],
        fields=[
            {"name": "recognized_revenue", "source_ref": override_ref},
            "region",
        ],
    )

    assert result.status == "valid"
    assert result.violations == []


def test_the_result_says_which_bundle_the_plan_was_judged_against():
    """Whether staleness was checked is part of the answer: a reader who
    cannot see the plan's own bundle id cannot tell that nothing compared
    the plan with the answer it came from."""
    checked = _validate(bundle=_bundle(), **{}).checked_against
    assert checked["planned_bundle_id"] is None
    assert checked["bundle_id"] == _bundle().bundle_id

    checked = validate_analytics_plan(
        bundle=_bundle(), plan=_plan(bundle_id=_bundle().bundle_id)
    ).checked_against
    assert checked["planned_bundle_id"] == _bundle().bundle_id


def test_hyperset_never_claims_to_have_run_the_query():
    assert _validate().to_dict()["execution"] == {
        "performed_by_hyperset": False,
        "result_validated_by_hyperset": False,
    }


def test_a_prohibited_source_names_the_source_and_the_reason_git_gave():
    result = _validate(source_refs=[PRIMARY, DIMENSION, BANNED])

    (violation,) = [item for item in result.violations if item.subject == BANNED]
    assert violation.code == "prohibited_source"
    assert violation.section == "instructions.prohibited_sources"
    assert "Double-counts partial captures" in violation.message
    assert result.status == "invalid"


def test_a_source_git_never_approved_is_a_violation_that_lists_the_approved_ones():
    result = _validate(source_refs=[PRIMARY, DIMENSION, "superset:dataset:unknown"])

    (violation,) = [item for item in result.violations if item.code == "unapproved_source"]
    assert violation.subject == "superset:dataset:unknown"
    assert PRIMARY in violation.message


def test_a_field_the_context_does_not_define_is_a_violation():
    result = _validate(fields=["recognized_revenue", "gross_margin"])

    (violation,) = [item for item in result.violations if item.code == "unapproved_field"]
    assert violation.subject == "gross_margin"
    assert violation.section == "instructions.fields"
    assert "recognized_revenue" in violation.message


def test_a_field_computed_differently_than_git_defines_it_is_a_violation():
    result = _validate(
        fields=[{"name": "recognized_revenue", "expression": "SUM(gross_amount)"}, "region"]
    )

    (violation,) = [item for item in result.violations if item.code == "field_expression_mismatch"]
    assert "SUM(gross_amount - tax_amount)" in violation.message
    assert "SUM(gross_amount)" in violation.message


def test_the_same_computation_spelled_differently_is_not_a_violation():
    """The governed expression is reformatted, aliased, and re-cased. Nothing
    about the computation moved, so nothing about the plan is wrong (hy-gh-128).
    String-equality made this the commonest false `invalid` on a real
    warehouse, and the manifest had to be hand-resynced to the exact
    characters to clear it."""
    result = _validate(
        fields=[
            {
                "name": "recognized_revenue",
                "expression": "sum( Gross_Amount - tax_amount ) AS recognized_revenue",
            },
            "region",
        ]
    )

    assert result.status == "valid"


def test_a_qualifier_the_comparator_cannot_settle_is_disclosed_not_rejected():
    """`SUM(gross_amount)` and `SUM(orders.gross_amount)` are the same column
    or are not, and only the warehouse knows. Hyperset does not run the query,
    so it states both forms."""
    result = _validate(
        fields=[
            {
                "name": "recognized_revenue",
                "expression": "SUM(orders.gross_amount - orders.tax_amount)",
            },
            "region",
        ]
    )

    (violation,) = [item for item in result.violations if item.section == "instructions.fields"]
    assert violation.code == "field_expression_undecidable"
    assert violation.severity == "warning"
    assert "SUM(gross_amount - tax_amount)" in violation.message
    assert "SUM(orders.gross_amount - orders.tax_amount)" in violation.message
    assert result.status == "warnings"


def test_the_undecidable_band_never_produces_a_valid_plan():
    """The bound on the loosening, stated adversarially: qualifying the
    columns with a DIFFERENT table is a different computation, and the
    comparator cannot prove it -- `refunds` could be an alias of the approved
    dataset, and Hyperset holds no warehouse schema that says otherwise.

    So it is disclosed, and disclosure is the ceiling: the plan comes back
    `warnings` with a violation naming the field and both forms. `valid` is
    reachable only through folds that cannot change a value. A client whose
    policy is stricter than the governed verdict refuses on the violation,
    which is the same shape as the default-deny rule section 7 states for a
    code a client does not recognise."""
    result = _validate(
        fields=[
            {"name": "recognized_revenue", "expression": "SUM(refunds.gross_amount - tax_amount)"},
            "region",
        ]
    )

    assert result.status != "valid"
    assert _codes(result) == ["field_expression_undecidable"]
    assert "SUM(refunds.gross_amount - tax_amount)" in result.violations[0].message


def test_the_same_names_in_a_different_order_are_still_a_contradiction():
    """The relaxations fold spellings, never structure: `a - b` and `b - a`
    read the same columns and are not the same number."""
    result = _validate(
        fields=[
            {"name": "recognized_revenue", "expression": "SUM(tax_amount - gross_amount)"},
            "region",
        ]
    )

    assert "field_expression_mismatch" in _codes(result)
    assert result.status == "invalid"


@pytest.mark.parametrize(
    ("governed", "reversed_operands"),
    [
        # Qualifiers: `gross.amount` and `refunds.amount` collapse to `amount`.
        ("SUM(gross.amount) - SUM(refunds.amount)", "SUM(refunds.amount) - SUM(gross.amount)"),
        # Casts do it too, and to operands that are unqualified and whose
        # qualifiers therefore agree -- which is why the sentence in section 7
        # is bounded by the band rather than by the qualifier rule (hy-70fk).
        ("SUM(amount::numeric) - SUM(amount)", "SUM(amount) - SUM(amount::numeric)"),
    ],
)
def test_reordering_operands_the_relaxations_collapse_is_disclosed_not_refused(
    governed, reversed_operands
):
    """The bound on the sentence above, pinned rather than left incidental.

    Operand order errors while the operands stay distinguishable. When the two
    relaxations `_relaxed` applies -- qualifiers and casts -- collapse both
    operands to the same tokens, the reversal lands in the undecidable band and
    the sign flip is disclosed as a `warning` naming both forms. Tightening it
    is not free: refusing these means refusing `SUM(o.amount)` against
    `SUM(orders.amount)` and `posting_date` against `posting_date::date`, the
    cases the relaxations exist to serve."""
    bundle = _bundle(
        instructions={
            **INSTRUCTIONS,
            "fields": [
                {"name": "recognized_revenue", "source_ref": PRIMARY, "expression": governed},
                INSTRUCTIONS["fields"][1],
            ],
        }
    )

    result = validate_analytics_plan(
        bundle=bundle,
        plan=_plan(
            fields=[
                {"name": "recognized_revenue", "expression": reversed_operands},
                "region",
            ]
        ),
    )

    assert _codes(result) == ["field_expression_undecidable"]
    assert result.status == "warnings"
    assert governed in result.violations[0].message
    assert reversed_operands in result.violations[0].message


def test_a_field_read_from_the_wrong_source_is_a_violation():
    result = _validate(
        fields=[{"name": "region", "source_ref": PRIMARY}], source_refs=[PRIMARY, DIMENSION]
    )

    (violation,) = [item for item in result.violations if item.code == "field_source_mismatch"]
    assert violation.subject == "region"
    assert DIMENSION in violation.message


def test_a_field_whose_source_the_plan_forgot_to_declare_is_a_violation():
    result = _validate(source_refs=[PRIMARY])

    (violation,) = [item for item in result.violations if item.code == "undeclared_field_source"]
    assert violation.subject == "region"
    assert DIMENSION in violation.message


def test_a_join_the_context_never_declared_is_a_violation():
    result = _validate(joins=["finance_orders_daily.id->payments.order_id"])

    (violation,) = [item for item in result.violations if item.code == "unapproved_join"]
    assert violation.subject == "finance_orders_daily.id->payments.order_id"
    assert "customer_dim.customer_id" in violation.message


def test_the_declared_join_used_with_the_wrong_type_is_a_violation():
    result = _validate(
        joins=[
            {
                "from": "finance_orders_daily.customer_id",
                "to": "customer_dim.customer_id",
                "type": "left",
            }
        ]
    )

    (violation,) = [item for item in result.violations if item.code == "join_type_mismatch"]
    assert "inner" in violation.message
    assert "left" in violation.message


def test_a_declared_join_written_with_an_equals_delimiter_is_valid():
    """`=` is the SQL-ish delimiter a caller reaches for, and the one the shipped
    revenue eval bank itself writes. A `from = to` join must be compared on its
    members, not parsed to a `from`-only string that reads as an `unapproved_join`
    the governed context "declares no" -- a governed-looking false negative (#281).
    """
    result = _validate(joins=["finance_orders_daily.customer_id = customer_dim.customer_id"])

    assert result.status == "valid", [item.to_dict() for item in result.violations]
    assert "unapproved_join" not in _codes(result)


def test_a_join_string_that_is_genuinely_undeclared_still_violates_under_either_delimiter():
    """The `=` acceptance must not swallow a real finding: a join the context never
    declared is still named, whichever delimiter encodes it."""
    arrow = _validate(joins=["finance_orders_daily.id->payments.order_id"])
    equals = _validate(joins=["finance_orders_daily.id = payments.order_id"])

    assert "unapproved_join" in _codes(arrow)
    assert "unapproved_join" in _codes(equals)


def test_object_shaped_filters_are_validated_by_their_expression_not_a_500():
    """`filters` is typed `["string", "object"]`, so a caller may echo the bundle's
    entries back as mappings. The object form must be projected to its SQL text, not
    carried as a dict into `PlanViolation.subject` -- where `_result`'s sort raised
    `TypeError: '<' not supported between instances of 'dict' and 'dict'`, a 500 on
    schema-valid input (#281).
    """
    result = _validate(
        filters=[
            {"expression": "finance_orders_daily.status = 'completed'"},
            {"expression": "customer_dim.is_test = false"},
        ]
    )

    assert result.status == "valid", [item.to_dict() for item in result.violations]
    assert all(isinstance(item.subject, str) for item in result.violations)


def test_an_object_shaped_filter_that_is_missing_is_still_reported_as_a_string():
    """Projection must not lose the finding: an object filter the plan omits is still
    a `missing_required_filter`, and its subject is the SQL text, not a dict repr."""
    result = _validate(filters=[{"expression": "customer_dim.is_test = false"}])

    missing = [item for item in result.violations if item.code == "missing_required_filter"]
    assert [item.subject for item in missing] == ["finance_orders_daily.status = 'completed'"]


def test_an_object_shaped_field_missing_its_name_is_judged_not_a_500():
    """`fields`/`joins`/`checks` share the `["string","object"]` typing that made object
    filters crash. A field object that omits `name` must be validated, not raise KeyError
    -- a 500 on schema-valid input, the same class (#281). It matches no governed field
    name, so it is an unapproved field with a string subject."""
    result = _validate(fields=[{"expression": "SUM(x)", "source_ref": PRIMARY}, "region"])

    assert all(isinstance(item.subject, str) for item in result.violations)
    assert "unapproved_field" in _codes(result)


def test_an_object_shaped_join_missing_a_side_is_judged_not_a_500():
    """A join object that omits `from` must not KeyError (#281). A missing side matches no
    governed join, so it is an unapproved_join with a string subject."""
    result = _validate(joins=[{"to": "customer_dim.customer_id", "type": "inner"}])

    assert all(isinstance(item.subject, str) for item in result.violations)
    assert "unapproved_join" in _codes(result)


def test_an_object_shaped_check_echoed_from_the_bundle_is_not_a_false_missing():
    """`checks` is typed like `filters`; the object form must be compared by its text, not
    its Python repr (which produced a spurious `missing_required_check`). Echoing the
    governed check back as a mapping is a satisfied check (#281)."""
    result = _validate(checks=[{"expression": "recognized_revenue is non-negative"}])

    assert "missing_required_check" not in _codes(result)


def test_dropping_a_required_filter_is_an_error_and_adding_one_is_disclosed():
    result = _validate(filters=["customer_dim.is_test = false", "customer_dim.region = 'EMEA'"])

    missing = [item for item in result.violations if item.code == "missing_required_filter"]
    extra = [item for item in result.violations if item.code == "unapproved_filter"]
    assert [item.subject for item in missing] == ["finance_orders_daily.status = 'completed'"]
    assert [item.subject for item in extra] == ["customer_dim.region = 'EMEA'"]
    assert missing[0].severity == "error"
    # Narrower than the governed definition, but not a contradiction.
    assert extra[0].severity == "warning"


def test_a_filter_matching_only_by_case_is_still_missing():
    """Whitespace is noise; case is meaning. `'Completed'` and `'completed'`
    are different rows."""
    result = _validate(
        filters=["finance_orders_daily.status =  'Completed'", "customer_dim.is_test = false"]
    )

    assert "missing_required_filter" in _codes(result)


def test_a_dollar_quoted_literal_matching_only_by_case_is_still_missing():
    """The same rule as the test above, through the delimiter Postgres uses
    when the value contains a quote. It is here and not only in
    `test_equivalence.py` because the failure it guards was not a lexer
    curiosity: at 01fa4b0 this plan came back `valid` with zero violations
    while filtering on rows the governed context does not approve (hy-fae7).
    A false pass is a governance failure, so the governance surface pins it."""
    governed = dict(INSTRUCTIONS, filters=["status = $$completed$$"])

    result = validate_analytics_plan(
        bundle=_bundle(instructions=governed), plan=_plan(filters=["status = $$Completed$$"])
    )

    assert result.status == "invalid"
    assert "missing_required_filter" in _codes(result)


def test_reformatted_whitespace_is_the_same_filter():
    result = _validate(
        filters=["finance_orders_daily.status\n  = 'completed'", "customer_dim.is_test = false"]
    )

    assert result.status == "valid"


def test_a_required_filter_written_as_a_one_element_in_list_is_present():
    result = _validate(
        filters=["finance_orders_daily.status IN ('completed')", "customer_dim.is_test = false"]
    )

    assert result.status == "valid"


def test_a_filter_that_may_be_the_required_one_is_disclosed_once_not_twice():
    """Unqualified, the filter is the required one or is another source's
    column. The old comparator called that two separate things -- a required
    filter omitted and an unapproved filter added -- and the first of them was
    an error the plan could not fix without guessing which."""
    result = _validate(filters=["status = 'completed'", "customer_dim.is_test = false"])

    (violation,) = [item for item in result.violations if item.section == "instructions.filters"]
    assert violation.code == "filter_undecidable"
    assert violation.severity == "warning"
    assert violation.subject == "finance_orders_daily.status = 'completed'"
    assert "status = 'completed'" in violation.message
    assert result.status == "warnings"


def test_an_exact_filter_is_never_consumed_by_a_near_match():
    """Both passes see the whole list: the relaxed comparison only gets the
    filters no exact statement claimed."""
    result = _validate(
        filters=[
            "status = 'completed'",
            "finance_orders_daily.status = 'completed'",
            "customer_dim.is_test = false",
        ]
    )

    codes = _codes(result)
    assert "missing_required_filter" not in codes
    assert "filter_undecidable" not in codes
    # The qualifier-less one is left over, and an extra filter is disclosed.
    (extra,) = [item for item in result.violations if item.code == "unapproved_filter"]
    assert extra.subject == "status = 'completed'"


def test_the_wrong_grain_is_a_violation_that_states_both():
    result = _validate(grain="order_date")

    (violation,) = [item for item in result.violations if item.code == "grain_mismatch"]
    assert violation.section == "instructions.grain"
    assert "order_date by customer_dim.region" in violation.message


def test_a_grain_that_only_casts_the_governed_one_is_disclosed():
    """`order_date::date` truncates a timestamp and changes nothing on a date.
    The plan is not contradicting the governed grain, and it is not provably
    stating it either."""
    result = _validate(grain="order_date::date by customer_dim.region")

    (violation,) = [item for item in result.violations if item.section == "instructions.grain"]
    assert violation.code == "grain_undecidable"
    assert violation.severity == "warning"
    assert "order_date by customer_dim.region" in violation.message
    assert "order_date::date by customer_dim.region" in violation.message
    assert result.status == "warnings"


def test_a_reformatted_grain_is_the_governed_grain():
    result = _validate(grain="ORDER_DATE  by\tcustomer_dim.region")

    assert result.status == "valid"


def test_a_plan_with_no_grain_at_all_still_contradicts_the_governed_grain():
    result = _validate(grain=None)

    assert "grain_mismatch" in _codes(result)


def test_an_omitted_check_is_disclosed_because_hyperset_will_not_run_it():
    result = _validate(checks=[])

    (violation,) = result.violations
    assert violation.code == "missing_required_check"
    assert violation.section == "instructions.validations"
    assert violation.severity == "warning"
    assert result.status == "warnings"


def test_a_plan_built_against_a_different_bundle_cannot_be_judged():
    result = validate_analytics_plan(bundle=_bundle(), plan=_plan(bundle_id="cb-0000000000000000"))

    assert result.status == "unverifiable"
    (violation,) = result.violations
    assert violation.code == "stale_bundle"
    # The id covers the request as well as the answer, so the message names
    # every cause it cannot tell apart rather than asserting one (hy-dvn).
    assert "the 'query', the 'directive', or the underlying context" in violation.message
    assert "resolved again" in violation.message


def test_a_plan_naming_this_bundle_is_judged_normally():
    bundle = _bundle()

    result = validate_analytics_plan(bundle=bundle, plan=_plan(bundle_id=bundle.bundle_id))

    assert result.status == "valid"


def test_a_bundle_with_no_governed_context_approves_nothing():
    bundle = _bundle(
        resolution={"status": "no_match", "summary": "", "warnings": []},
        context_authority=None,
    )

    result = validate_analytics_plan(bundle=bundle, plan=_plan())

    assert result.status == "unverifiable"
    assert _codes(result) == ["no_governed_context"]
    assert result.checked_against is None


def test_an_observed_only_bundle_approves_nothing_either():
    """Raw observation is not a weaker kind of governed context: there is
    nothing here that anyone approved, so there is nothing to check against
    (hy-5c2)."""
    bundle = _bundle(
        resolution={"status": "observed_only", "summary": "", "warnings": []},
        context_authority=None,
    )

    result = validate_analytics_plan(bundle=bundle, plan=_plan())

    assert result.status == "unverifiable"
    assert _codes(result) == ["no_governed_context"]


def test_a_plan_declaring_no_sources_is_refused_as_the_omission_it_is():
    """hy-pvbu, defect 1, measured on a real agent run.

    Every governed field requires a source a plan with no `source_refs` does not
    list, so the answer used to be one `undeclared_field_source` per field: true
    of each field, and silent about the single omission behind all of them. The
    count tracked the plan's fields rather than the mistake.
    """
    result = _validate(source_refs=[])

    assert _codes(result) == ["no_declared_sources"]
    (violation,) = result.violations
    assert violation.section == "source_refs"
    # Nothing was declared, so there is no element to name.
    assert violation.subject == ""
    # And the caller is pointed at the list it should have declared from.
    assert PRIMARY in violation.message
    assert DIMENSION in violation.message


def test_a_plan_declaring_no_sources_is_unverifiable_and_not_invalid():
    """`invalid` is a verdict about a plan that WAS compared with governed
    context and contradicts it. This one contradicts nothing: one side of the
    comparison is missing, as it is for `stale_bundle` and
    `no_governed_context`."""
    assert _validate(source_refs=[]).status == "unverifiable"


def test_the_staleness_of_a_plan_is_reported_before_its_emptiness():
    """Order, and the reason for it: `no_declared_sources` sends the caller to
    read `instructions.approved_sources`, which is worth reading only off a
    bundle this plan was actually built against."""
    result = validate_analytics_plan(
        bundle=_bundle(), plan=_plan(bundle_id="cb-0000000000000000", source_refs=[])
    )

    assert _codes(result) == ["stale_bundle"]


def test_an_ungoverned_bundle_is_reported_before_emptiness_because_it_approves_nothing():
    """The other half of the same order, and the sharper case (hy-lyfn).

    `no_declared_sources` sends the caller to `instructions.approved_sources`,
    and an ungoverned bundle has nothing in that list: its instructions come
    from `resolver.git_instructions({})`, every section of which defaults to
    empty. Reporting the emptiness first would answer such a plan with a
    pointer to nothing -- and `checked_against` is null here too, so there is
    no other list to fall back to. `no_governed_context` is the reason the
    caller can act on: resolve a domain that governs the question.

    The empty list is stated inline rather than imported from the resolver,
    the way this module states every other instruction section.
    """
    bundle = _bundle(
        resolution={"status": "observed_only", "summary": "", "warnings": []},
        context_authority=None,
        instructions={**INSTRUCTIONS, "approved_sources": []},
    )

    result = validate_analytics_plan(bundle=bundle, plan=_plan(source_refs=[]))

    assert _codes(result) == ["no_governed_context"]
    assert result.checked_against is None
    assert bundle.instructions["approved_sources"] == []


def test_a_mixed_bundle_is_judged_against_its_governed_part():
    """A directive that also named an ungoverned ref does not cost the plan
    the governed context it did resolve (hy-5c2)."""
    bundle = _mixed_bundle()

    result = validate_analytics_plan(bundle=bundle, plan=_plan())

    assert result.status == "valid"
    assert result.checked_against["bundle_status"] == "mixed"


def test_planning_on_an_observed_only_source_is_a_violation_of_its_own():
    """The ref is real, and nothing governs it. That is a different mistake
    from naming a source the context never heard of, and the plan needs to
    be told which one it made."""
    bundle = _mixed_bundle()

    result = validate_analytics_plan(
        bundle=bundle, plan=_plan(source_refs=[PRIMARY, DIMENSION, UNGOVERNED])
    )

    assert result.status == "invalid"
    assert _codes(result) == ["observed_only_source"]
    violation = result.violations[0]
    assert violation.subject == UNGOVERNED
    assert "never approved meaning" in violation.message


def _a_plan_on_an_observed_only_ref():
    return validate_analytics_plan(
        bundle=_mixed_bundle(), plan=_plan(source_refs=[PRIMARY, DIMENSION, UNGOVERNED])
    )


def _a_disputed_field_beside_a_prohibited_source():
    bundle = _bundle(
        linked_evidence={
            "observed_assets": [],
            "findings": [],
            "conflicts": [
                {
                    "kind": "context_source_conflict",
                    "finding_id": "fnd-1",
                    "ref": PRIMARY,
                    "field": "recognized_revenue",
                    "context_says": "SUM(gross_amount - tax_amount)",
                    "source_says": "SUM(gross_amount)",
                }
            ],
        }
    )
    return validate_analytics_plan(
        bundle=bundle, plan=_plan(source_refs=[PRIMARY, DIMENSION, BANNED])
    )


@pytest.mark.parametrize(
    "served", [_a_plan_on_an_observed_only_ref, _a_disputed_field_beside_a_prohibited_source]
)
def test_no_remedy_served_beside_an_invalid_verdict_offers_to_leave_the_plan_alone(served):
    """hy-1a6j. `status: invalid` has one published meaning -- the plan was
    compared with governed context and contradicts it -- and no reading under
    which the caller sends the same plan again. Two remedies said otherwise.

    `observed_only_source` is ERROR and offered "keep the ref and present
    whatever it yields as ungoverned", which is served instruction for getting
    past the boundary ADR 0019 rests on this verdict ("Both are ERROR, so the
    verdict is `invalid` either way ... It holds by a value check"). Its two
    ERROR siblings hold the line, so the remedy moved rather than the severity.
    `disputed_field` is WARNING and said "the plan may proceed", which is true
    of the code alone and false the moment any ERROR co-occurs: a per-violation
    field cannot speak for the verdict, and `status` is where the verdict is.
    """
    result = served()

    assert result.status == "invalid"
    for violation in result.violations:
        assert "may proceed" not in violation.recovery
        assert "keep the ref" not in violation.recovery


def test_a_disputed_field_the_plan_uses_travels_with_the_result():
    """The plan agrees with Git and the source does not: not wrong, but not
    something to build on silently."""
    bundle = _bundle(
        linked_evidence={
            "observed_assets": [],
            "findings": [],
            "conflicts": [
                {
                    "kind": "context_source_conflict",
                    "finding_id": "fnd-1",
                    "ref": PRIMARY,
                    "field": "recognized_revenue",
                    "context_says": "SUM(gross_amount - tax_amount)",
                    "source_says": "SUM(gross_amount)",
                }
            ],
        }
    )

    result = validate_analytics_plan(bundle=bundle, plan=_plan())

    (violation,) = result.violations
    assert violation.code == "disputed_field"
    assert violation.severity == "warning"
    assert "fnd-1" in violation.message
    assert result.status == "warnings"


def test_a_conflict_about_a_field_the_plan_never_uses_is_not_reported():
    bundle = _bundle(
        linked_evidence={
            "observed_assets": [],
            "findings": [],
            "conflicts": [
                {
                    "kind": "context_source_conflict",
                    "finding_id": "fnd-1",
                    "ref": PRIMARY,
                    "field": "region",
                    "context_says": "a",
                    "source_says": "b",
                }
            ],
        }
    )

    result = validate_analytics_plan(bundle=bundle, plan=_plan(fields=["recognized_revenue"]))

    assert "disputed_field" not in _codes(result)


def test_the_same_bundle_and_plan_produce_the_same_result_whatever_the_order():
    forward = _validate(
        source_refs=[BANNED, "superset:dataset:unknown", PRIMARY, DIMENSION],
        fields=["region", "gross_margin", "recognized_revenue"],
    )
    reversed_plan = _validate(
        source_refs=["superset:dataset:unknown", BANNED, DIMENSION, PRIMARY],
        fields=["gross_margin", "recognized_revenue", "region"],
    )

    # Same violations, same order, same bytes: the order the caller happened
    # to write its plan in is not part of the answer.
    assert forward.to_dict() == reversed_plan.to_dict()
    keys = [(item.section, item.code, item.subject) for item in forward.violations]
    assert keys == sorted(keys)


def test_the_same_source_named_twice_is_reported_once():
    result = _validate(source_refs=[PRIMARY, DIMENSION, BANNED, BANNED])

    assert _codes(result).count("prohibited_source") == 1


@pytest.mark.parametrize("section", ["bundle_id", "status", "summary", "violations", "execution"])
def test_every_result_section_is_serialized(section):
    assert section in _validate().to_dict()


def test_the_served_plan_validation_states_the_version_it_was_built_under():
    """The fourth served surface, and one of the two nothing checked (hy-q4ln).

    `schema_version` crosses the wire on four surfaces -- health, the bundle,
    the catalog and this one -- and the value was asserted on two. A validation
    response is where an agent learns its plan was refused, so the number that
    tells it how to read the refusal belongs to the answer.

    Typed by hand rather than compared to `SCHEMA_VERSION`, which is the whole
    point of the line: the served value IS that constant, so a derived
    comparison holds at 4, at 5 and at 41 alike and cannot fail on a wrong
    number (hy-ndzz). The literal is the constant's value plus a human
    keystroke, and the keystroke is what a deliberate bump has to spend.

    The parametrized test above does not cover this: it asks whether a section
    is present, which is true at every number.
    """
    assert _validate().to_dict()["schema_version"] == 26


def test_every_served_violation_carries_the_move_that_answers_it():
    """hy-pvbu, defect 2, generalised past the one code that measured it.

    Section 7's tool-design requirements have said "errors explain recovery"
    since v0, and every violation was breaking it: an agent told what is wrong
    with its plan was never told what to send instead. Asserted on the wire
    shape rather than on the attribute, because the response is what a caller
    gets.
    """
    served = _validate(source_refs=[PRIMARY, DIMENSION, BANNED, UNGOVERNED], checks=[]).to_dict()

    assert len(served["violations"]) > 1
    for violation in served["violations"]:
        assert set(violation) == {"code", "severity", "section", "subject", "message", "recovery"}
        assert violation["recovery"].strip()


def test_a_remedy_is_a_property_of_the_code_rather_than_of_the_call_site():
    """The register is the vocabulary, so the two cannot disagree: a code with
    no remedy is not declarable, and two call sites cannot answer one code
    differently because neither of them writes the text.

    The derivation is what carries that, so the derivation is what is checked
    (hy-yl6a): `set(VIOLATION_RECOVERY) == set(VIOLATION_CODES)` cannot fail
    while one is built from the other, and it would stop being true silently
    the day somebody writes the tuple out by hand instead.
    """
    source = (Path(hyperset.bundle.plan.__file__)).read_text()
    assert "VIOLATION_CODES = tuple(VIOLATION_RECOVERY)" in source
    assert all(remedy.strip() for remedy in VIOLATION_RECOVERY.values())

    prohibited = _validate(source_refs=[PRIMARY, DIMENSION, BANNED]).violations[0]
    assert prohibited.recovery == VIOLATION_RECOVERY["prohibited_source"]


def test_a_violation_code_outside_the_vocabulary_is_refused_where_it_is_built():
    """The gate that makes `VIOLATION_CODES` a vocabulary rather than a list.

    Fifteen codes reached clients unpublished because nothing between a call
    site and the wire knew what the published set was (hy-ruui). Checked here
    the way `warning()` checks a disclosure code, and refused at construction:
    a code no document names is a code no client can branch on, and section
    7's default-deny rule turns it into a refused plan rather than a silent
    pass.
    """
    with pytest.raises(ValueError) as refused:
        PlanViolation(
            code="teapot_source",
            severity="error",
            section="instructions.approved_sources",
            subject=PRIMARY,
            message="",
        )

    assert "teapot_source" in str(refused.value)
    # The register, because that is where a new code has to be added: it maps
    # code to remedy, so declaring one without a remedy is unsayable.
    assert "VIOLATION_RECOVERY" in str(refused.value)


def test_every_published_violation_code_is_one_the_validator_actually_emits():
    """The other direction of the same binding, and the one the gate cannot
    check: the gate refuses a code that is served and unpublished, while this
    refuses a code that is published and unserved -- a promise to a client that
    no plan will ever keep.

    Read off the source rather than off a run, because reaching all twenty
    through `validate_analytics_plan` would take twenty fixtures and would
    still pin the fixtures rather than the vocabulary.
    """
    source = (Path(hyperset.bundle.plan.__file__)).read_text()
    emitted = set(re.findall(r'code="([a-z_]+)"', source))

    assert emitted == set(VIOLATION_CODES)


# --- Not-checkable disclosure: a sparse domain is not a false green (#285) ---


def _sparse_instructions(**empty):
    """The governed context with one or more requirement sections emptied. A
    sparse domain is legitimate -- it just cannot state the requirements a fully
    specified one does."""
    sparse = deepcopy(INSTRUCTIONS)
    sparse["filters"] = []
    sparse["joins"] = []
    sparse["grain"] = ""
    sparse["validations"] = []
    sparse.update(empty)
    return sparse


def _bare_plan(**overrides) -> AnalyticsPlan:
    """A plan that reads the approved sources and the governed fields and omits
    every requirement -- what an agent submits against a stripped domain."""
    payload = {
        "source_refs": [PRIMARY, DIMENSION],
        "fields": ["recognized_revenue", "region"],
        "joins": [],
        "filters": [],
        "grain": None,
        "checks": [],
    }
    payload.update(overrides)
    return AnalyticsPlan(**payload)


def test_a_sparse_domain_reports_gaps_rather_than_a_false_green():
    """The bug: a plan that omits every requirement of a domain that declares
    none comes back `valid`, indistinguishable from a checked pass. It is now
    `valid_with_gaps`, naming each section that could not be checked (#285)."""
    result = validate_analytics_plan(
        bundle=_bundle(instructions=_sparse_instructions()), plan=_bare_plan()
    )

    # It is not FAILED -- a sparse domain is legitimate, so this is a disclosure.
    assert result.violations == []
    assert result.status == "valid_with_gaps"
    # Each empty requirement section is disclosed, in a fixed order, with the
    # "declared nothing" reason (the only one available without adapters).
    assert result.sections_not_checkable == [
        {"section": "instructions.filters", "reason": "the governed context declares no filters"},
        {"section": "instructions.joins", "reason": "the governed context declares no joins"},
        {"section": "instructions.grain", "reason": "the governed context declares no grain"},
        {
            "section": "instructions.validations",
            "reason": "the governed context declares no checks",
        },
    ]
    # A caller can tell the two apart on the WIRE without diffing the bundle.
    served = result.to_dict()
    assert served["status"] == "valid_with_gaps"
    assert served["sections_not_checkable"] == result.sections_not_checkable


def test_the_valid_with_gaps_summary_reports_success_and_the_gap_in_one_sentence():
    """Acceptance (c): the summary distinguishes 'checked and clean' from
    'clean, and N sections could not be checked', in the same sentence, so a
    reader cannot take the success without the caveat."""
    gapped = validate_analytics_plan(
        bundle=_bundle(instructions=_sparse_instructions()), plan=_bare_plan()
    ).summary
    clean = _validate().summary

    assert "could not be checked" in gapped
    assert "4 section(s)" in gapped
    assert "not the same as a checked one" in gapped
    # The genuine pass says nothing about gaps.
    assert "could not be checked" not in clean


def test_a_fully_specified_valid_plan_is_byte_unchanged():
    """Acceptance (a): the disclosure appears ONLY when a section is empty. A
    fully specified domain's valid result stays `valid`, carries no
    `sections_not_checkable` key, and is byte-identical to the pre-#285 shape."""
    served = _validate().to_dict()

    assert served["status"] == "valid"
    assert "sections_not_checkable" not in served  # key absent, not empty
    # The exact wire shape, so a new key anywhere reds this.
    assert set(served) == {
        "schema_version",
        "bundle_id",
        "status",
        "summary",
        "checked_against",
        "violations",
        "execution",
    }


def test_an_empty_section_on_a_failing_plan_adds_no_disclosure():
    """The safety property: the disclosure is computed ONLY for an otherwise
    `valid` result, so an `invalid` verdict is never altered. A plan that
    contradicts the governed context against a sparse domain stays `invalid`
    with no `sections_not_checkable` -- the gap is moot once there is a verdict
    from a section that WAS checkable (#285)."""
    # fields is still governed, so an unapproved field is a real contradiction;
    # filters/joins/grain/validations are empty.
    result = validate_analytics_plan(
        bundle=_bundle(instructions=_sparse_instructions()),
        plan=_bare_plan(fields=["recognized_revenue", "not_a_governed_field"]),
    )

    assert result.status == "invalid"
    assert "unapproved_field" in _codes(result)
    assert result.sections_not_checkable == []
    assert "sections_not_checkable" not in result.to_dict()


def test_a_warnings_plan_against_a_sparse_domain_still_discloses_its_gaps():
    """A `warnings` result keeps its status but STILL carries the disclosure
    (panel MINOR-1): the `unapproved_filter` warning below exists precisely
    because the governed filters are empty, so adding one narrowing filter to a
    sparse domain must not suppress what could not be checked (#285)."""
    # An extra filter against an empty governed-filters section is a disclosure
    # (WARNING) -> status `warnings`; the sparse sections are disclosed alongside.
    result = validate_analytics_plan(
        bundle=_bundle(instructions=_sparse_instructions()),
        plan=_bare_plan(filters=["customer_dim.region = 'EMEA'"]),
    )

    assert result.status == "warnings"  # NOT upgraded to valid_with_gaps
    assert "unapproved_filter" in _codes(result)
    # The gaps are disclosed, and the empty `filters` section is among them even
    # though the plan's extra filter is what produced the warning.
    sections = [item["section"] for item in result.sections_not_checkable]
    assert sections == [
        "instructions.filters",
        "instructions.joins",
        "instructions.grain",
        "instructions.validations",
    ]
    served = result.to_dict()
    assert served["status"] == "warnings"
    assert served["sections_not_checkable"] == result.sections_not_checkable
    assert "could not be checked" in served["summary"]


def test_a_fully_specified_warnings_result_is_byte_unchanged():
    """The additive property extends to `warnings`: a fully-specified domain
    whose plan earns a disclosure carries NO `sections_not_checkable` key and is
    byte-identical to before this field existed (panel MINOR-1)."""
    # An extra filter the fully-specified governed context does not declare ->
    # `unapproved_filter` WARNING, but every section is populated, so no gaps.
    result = _validate(filters=[*INSTRUCTIONS["filters"], "customer_dim.region = 'EMEA'"])

    assert result.status == "warnings"
    assert result.sections_not_checkable == []
    assert "sections_not_checkable" not in result.to_dict()


def test_a_whitespace_only_governed_grain_is_treated_as_empty_not_a_false_green():
    """The #285 class surviving in one field (panel MINOR-2): a governed grain of
    whitespace is vacuous. `_grain_violations` and `_not_checkable` must AGREE it
    is empty -- otherwise the grain is compared as equivalent to the plan's (no
    violation) and never disclosed (no gap), a plain `valid` false green."""
    result = validate_analytics_plan(
        bundle=_bundle(instructions=_sparse_instructions(grain="   ")),
        plan=_bare_plan(grain=None),
    )

    assert result.violations == []
    assert result.status == "valid_with_gaps"
    assert {
        "section": "instructions.grain",
        "reason": "the governed context declares no grain",
    } in (result.sections_not_checkable)


def test_the_not_checkable_disclosure_does_not_move_the_tools_hash():
    """The change is OUTPUT-only: it adds a response field and a status value,
    and touches neither the VALIDATE tool description nor its input schema. So it
    did not move the resolve-path planner tools hash a committed benchmark
    recording is pinned to (hy-gh-285 fork ruling). The pinned value is fe930a003b731211
    since hy-gh-281 item 3 added VALIDATE's input-schema field descriptions --
    which does touch the input schema and did move it; this one does not."""
    from hyperset.planner.loop import tools_hash

    assert tools_hash() == "sha256:fe930a003b731211"


# --- 284-4 (hy-bz5f): per-source grain fan-out, REFINE semantics ---

FX = "table:postgres:analytics.public.fx_rates_daily"


def _fx_instructions(grain="fx_rate_date"):
    """Revenue instructions plus a supporting source that declares a per-source
    grain (284-3 `facets.grain`) and an aggregate field reading it."""
    instructions = deepcopy(INSTRUCTIONS)
    instructions["approved_sources"].append(
        {"ref": FX, "role": "supporting", "reason": "Daily FX rates.", "facets": {"grain": grain}}
    )
    instructions["fields"].append(
        {"name": "usd_rate", "source_ref": FX, "expression": "SUM(fx_rates_daily.usd_rate)"}
    )
    return instructions


def test_a_source_read_at_a_disagreeing_grain_without_aggregation_fans_out():
    # fx_rates_daily is governed at fx_rate_date; the plan reads it (source_refs)
    # at order grain and selects no aggregate over it -> its daily rows fan out.
    bundle = _bundle(instructions=_fx_instructions())
    plan = _plan(
        source_refs=[PRIMARY, DIMENSION, FX],
        fields=["recognized_revenue", "region"],  # FX read as a source, not aggregated
        grain="order_date by customer_dim.region",
    )
    result = validate_analytics_plan(bundle=bundle, plan=plan)
    (violation,) = [v for v in result.violations if v.code == "grain_fanout"]
    assert violation.severity == "error"
    assert violation.subject == FX
    assert "fan out" in violation.message
    assert result.status == "invalid"


def test_aggregating_the_source_to_the_plan_grain_does_not_fan_out():
    # The same source, but the plan aggregates it (SUM) to the plan grain -> no
    # fan-out. REFINE's second way out.
    bundle = _bundle(instructions=_fx_instructions())
    plan = _plan(
        source_refs=[PRIMARY, DIMENSION, FX],
        fields=[
            "recognized_revenue",
            "region",
            {"name": "usd_rate", "source_ref": FX, "expression": "SUM(fx_rates_daily.usd_rate)"},
        ],
        grain="order_date by customer_dim.region",
    )
    result = validate_analytics_plan(bundle=bundle, plan=plan)
    assert "grain_fanout" not in _codes(result)


def test_a_plan_stated_at_the_sources_own_grain_does_not_fan_out():
    # grain-matched: the plan states the source's grain, so it is used at its own
    # grain and nothing fans out (a grain_mismatch vs the DOMAIN grain is a
    # separate concern; this asserts only the fan-out arm is silent).
    bundle = _bundle(instructions=_fx_instructions())
    plan = _plan(source_refs=[PRIMARY, DIMENSION, FX], grain="fx_rate_date")
    result = validate_analytics_plan(bundle=bundle, plan=plan)
    assert "grain_fanout" not in _codes(result)


def test_a_source_that_declares_no_per_source_grain_never_fans_out():
    # Opt-in / back-compat: a source without facets.grain (e.g. finance_orders_daily
    # and the other dimensions in the shipped revenue manifest) is not checked, even
    # read unaggregated at any grain.
    instructions = deepcopy(INSTRUCTIONS)
    instructions["approved_sources"].append(
        {"ref": FX, "role": "supporting", "reason": "Daily FX rates."}  # no facets
    )
    bundle = _bundle(instructions=instructions)
    plan = _plan(source_refs=[PRIMARY, DIMENSION, FX], grain="order_date by customer_dim.region")
    result = validate_analytics_plan(bundle=bundle, plan=plan)
    assert "grain_fanout" not in _codes(result)


def test_a_declared_source_the_plan_does_not_read_does_not_fan_out():
    # The facet is declared but the plan never reads the source -> nothing fans out.
    bundle = _bundle(instructions=_fx_instructions())
    plan = _plan(grain="order_date by customer_dim.region")  # source_refs omit FX
    assert "grain_fanout" not in _codes(validate_analytics_plan(bundle=bundle, plan=plan))


def test_a_plan_with_no_stated_grain_is_left_to_grain_mismatch_not_fanout():
    # With no plan grain there is no disagreement to measure here; the plan-vs-
    # domain grain check owns the missing-grain case.
    bundle = _bundle(instructions=_fx_instructions())
    plan = _plan(source_refs=[PRIMARY, DIMENSION, FX], grain=None)
    assert "grain_fanout" not in _codes(validate_analytics_plan(bundle=bundle, plan=plan))


def test_aggregation_named_by_short_name_not_source_ref_still_counts():
    # A bare-string aggregate field that names the source by short name (no
    # source_ref attribute) is recognised as aggregating it.
    bundle = _bundle(instructions=_fx_instructions())
    plan = _plan(
        source_refs=[PRIMARY, DIMENSION, FX],
        fields=["recognized_revenue", "region", "SUM(fx_rates_daily.usd_rate)"],
        grain="order_date by customer_dim.region",
    )
    assert "grain_fanout" not in _codes(validate_analytics_plan(bundle=bundle, plan=plan))


def test_a_plan_grain_that_only_qualifies_the_sources_grain_is_not_a_fanout():
    # UNDECIDED (a qualifier/cast-only difference) is not a provable disagreement,
    # so it is NOT a fan-out -- consistent with `grain_undecidable` being a WARNING
    # not a hard ERROR, and so the "state the plan grain as the source's own"
    # remedy is reachable even spelled table-qualified.
    bundle = _bundle(instructions=_fx_instructions())
    plan = _plan(source_refs=[PRIMARY, DIMENSION, FX], grain="fx.fx_rate_date")
    result = validate_analytics_plan(bundle=bundle, plan=plan)
    assert "grain_fanout" not in _codes(result)


# --- 283-7 (hy-p5hf): an adapter-projected empty section CANNOT declare ---


def _adapter_bundle(instructions):
    """A governed bundle whose domain came through a context adapter: it carries
    `resolution.projection` (283-5), the metadata `_not_checkable` reads."""
    return _bundle(
        instructions=instructions,
        resolution={
            "status": "governed",
            "summary": "",
            "warnings": [],
            "projection": {
                "adapter": "acme-pipeline-docs-v2",
                "adapter_version": 1,
                "fields_unmapped": [],
                "fields_lossy": [],
                "fields_derived": [],
            },
        },
    )


def test_an_adapter_projected_domains_empty_sections_report_shape_cannot_declare():
    result = validate_analytics_plan(
        bundle=_adapter_bundle(_sparse_instructions()), plan=_bare_plan()
    )
    assert result.violations == []
    assert result.status == "valid_with_gaps"
    assert result.sections_not_checkable == [
        {
            "section": "instructions.filters",
            "reason": "the adapter's projected shape cannot declare filters",
        },
        {
            "section": "instructions.joins",
            "reason": "the adapter's projected shape cannot declare joins",
        },
        {
            "section": "instructions.grain",
            "reason": "the adapter's projected shape cannot declare grain",
        },
        {
            "section": "instructions.validations",
            "reason": "the adapter's projected shape cannot declare checks",
        },
    ]
    # And it survives serialization -- the caller reads it on the wire.
    assert result.to_dict()["sections_not_checkable"] == result.sections_not_checkable


def test_an_adapter_domain_with_no_fields_reports_cannot_declare_fields():
    result = validate_analytics_plan(
        bundle=_adapter_bundle(_sparse_instructions(fields=[])), plan=_bare_plan(fields=[])
    )
    reasons = {item["section"]: item["reason"] for item in result.sections_not_checkable}
    assert reasons["instructions.fields"] == "the adapter's projected shape cannot declare fields"


def test_the_two_reasons_distinguish_an_adapter_shape_from_an_authors_silence():
    # Non-vacuous: the SAME empty sections yield DIFFERENT reasons depending only on
    # whether the domain came through an adapter -- a hand-written silence ("declares
    # no ...") versus a projection that cannot express the section ("cannot declare").
    sparse = _sparse_instructions()
    hand = validate_analytics_plan(bundle=_bundle(instructions=sparse), plan=_bare_plan())
    adapter = validate_analytics_plan(bundle=_adapter_bundle(sparse), plan=_bare_plan())

    assert [s["section"] for s in hand.sections_not_checkable] == [
        s["section"] for s in adapter.sections_not_checkable
    ]
    assert all("declares no" in s["reason"] for s in hand.sections_not_checkable)
    assert all("cannot declare" in s["reason"] for s in adapter.sections_not_checkable)
    assert hand.sections_not_checkable != adapter.sections_not_checkable


# --- hy-eif4 (#230 enforcement, ex-284-9): a restricted/pii source exposed
# --- without a governed handling caveat is a plan violation. STRUCTURAL only.

SENSITIVE = "table:postgres:analytics.public.customer_pii"


def _classified_instructions(classification="restricted", caveats=None):
    """Revenue instructions plus a supporting source carrying a classification
    facet (#319). `caveats` defaults to none declared."""
    instructions = deepcopy(INSTRUCTIONS)
    instructions["approved_sources"].append(
        {
            "ref": SENSITIVE,
            "role": "supporting",
            "reason": "Customer detail.",
            "facets": {"classification": classification},
        }
    )
    if caveats is not None:
        instructions["caveats"] = caveats
    return instructions


def test_a_restricted_source_read_without_a_handling_caveat_is_a_violation():
    bundle = _bundle(instructions=_classified_instructions("restricted"))
    plan = _plan(source_refs=[PRIMARY, DIMENSION, SENSITIVE])
    result = validate_analytics_plan(bundle=bundle, plan=plan)
    (violation,) = [v for v in result.violations if v.code == "classification_undisclosed"]
    assert violation.severity == "error"
    assert violation.subject == SENSITIVE
    assert "no governed caveat declares its required handling" in violation.message
    assert result.status == "invalid"


def test_a_pii_source_is_flagged_the_same_way():
    bundle = _bundle(instructions=_classified_instructions("pii"))
    plan = _plan(source_refs=[PRIMARY, DIMENSION, SENSITIVE])
    assert "classification_undisclosed" in _codes(validate_analytics_plan(bundle=bundle, plan=plan))


def test_a_caveat_naming_the_ref_declares_handling_and_clears_it():
    caveat = f"{SENSITIVE} holds PII; mask email and redact before export."
    bundle = _bundle(instructions=_classified_instructions("restricted", caveats=[caveat]))
    plan = _plan(source_refs=[PRIMARY, DIMENSION, SENSITIVE])
    assert "classification_undisclosed" not in _codes(
        validate_analytics_plan(bundle=bundle, plan=plan)
    )


def test_a_caveat_naming_the_ref_in_parentheses_still_clears_it():
    # Word-bounded, punctuation-tolerant: the ref inside `(...)` still counts.
    caveat = f"Sensitive customer data ({SENSITIVE}) is masked at query time."
    bundle = _bundle(instructions=_classified_instructions("restricted", caveats=[caveat]))
    plan = _plan(source_refs=[PRIMARY, DIMENSION, SENSITIVE])
    assert "classification_undisclosed" not in _codes(
        validate_analytics_plan(bundle=bundle, plan=plan)
    )


def test_a_caveat_naming_a_longer_ref_does_not_clear_the_shorter_one():
    # The hy-c89s prefix-alias class, one layer over: a caveat about a DIFFERENT,
    # longer ref (SENSITIVE + '_eu') must NOT disclose SENSITIVE.
    caveat = f"{SENSITIVE}_eu is handled by the EU pipeline."
    bundle = _bundle(instructions=_classified_instructions("restricted", caveats=[caveat]))
    plan = _plan(source_refs=[PRIMARY, DIMENSION, SENSITIVE])
    assert "classification_undisclosed" in _codes(validate_analytics_plan(bundle=bundle, plan=plan))


def test_internal_and_public_sources_are_never_flagged():
    for classification in ("internal", "public"):
        bundle = _bundle(instructions=_classified_instructions(classification))
        plan = _plan(source_refs=[PRIMARY, DIMENSION, SENSITIVE])
        assert "classification_undisclosed" not in _codes(
            validate_analytics_plan(bundle=bundle, plan=plan)
        ), classification


def test_a_restricted_source_the_plan_does_not_read_is_not_flagged():
    bundle = _bundle(instructions=_classified_instructions("restricted"))
    plan = _plan(source_refs=[PRIMARY, DIMENSION])  # SENSITIVE not read
    assert "classification_undisclosed" not in _codes(
        validate_analytics_plan(bundle=bundle, plan=plan)
    )


def test_a_source_with_no_classification_is_never_flagged():
    # Opt-in / byte-identical: the shipped revenue instructions declare no
    # classification, so this enforcement never fires on them.
    result = validate_analytics_plan(bundle=_bundle(), plan=_plan())
    assert "classification_undisclosed" not in _codes(result)


# --- Cross-domain plan validation against a composed bundle (#230 slice 6, hy-i2us) ---
#
# A composed bundle (`domains[]` present, slice 5) carries each domain's governed
# content in its own entry; a plan is validated cross-domain by routing every source
# to its OWNING component and reusing the single-domain validators over the union.
# A join between two components is UNVERIFIABLE this slice -- a WARNING, never upgraded
# to verified (the governed `joinable_on` edge that would verify it is slice 2b).

ORDERS = "superset:dataset:orders"
LINE_ITEMS = "superset:dataset:line_items"
PRICES = "superset:dataset:prices"


def _instr(**overrides) -> dict:
    base = {
        "definitions": [],
        "approved_sources": [],
        "fields": [],
        "joins": [],
        "filters": [],
        "grain": None,
        "caveats": [],
        "validations": [],
        "prohibited_sources": [],
        "context_doc": None,
    }
    base.update(overrides)
    return base


def _component(instructions: dict) -> dict:
    return {
        "instructions": instructions,
        "linked_evidence": {"observed_assets": [], "findings": [], "conflicts": []},
    }


def _composed(*components: dict) -> ContextBundle:
    """A multi-domain (composed) bundle: authority per-domain in `domains[]`, all flat
    governed fields empty (the null-envelope guardrail, slice 5)."""
    return ContextBundle(
        request={"query": "cross-domain question"},
        resolution={"status": "governed", "summary": "", "warnings": []},
        context_authority=None,
        instructions={},
        linked_evidence={"observed_assets": [], "findings": [], "conflicts": []},
        domain_graph={"nodes": [], "edges": []},
        provenance_refs=[],
        resolved_at=datetime(2026, 7, 28, tzinfo=UTC),
        domains=list(components),
    )


def test_a_source_approved_by_its_own_component_validates():
    bundle = _composed(
        _component(_instr(approved_sources=[{"ref": ORDERS, "role": "primary"}])),
        _component(_instr(approved_sources=[{"ref": PRICES, "role": "primary"}])),
    )
    result = validate_analytics_plan(
        bundle=bundle, plan=AnalyticsPlan(source_refs=[ORDERS, PRICES])
    )

    assert _codes(result) == []
    assert result.status in ("valid", "valid_with_gaps")
    # A composed answer has no single authority -- provenance stays per-domain in `domains[]`.
    assert result.checked_against is None
    assert result.bundle_id == bundle.bundle_id


def test_a_source_no_component_approves_is_unapproved():
    bundle = _composed(
        _component(_instr(approved_sources=[{"ref": ORDERS, "role": "primary"}])),
        _component(_instr(approved_sources=[{"ref": PRICES, "role": "primary"}])),
    )
    result = validate_analytics_plan(
        bundle=bundle, plan=AnalyticsPlan(source_refs=[ORDERS, "superset:dataset:ghost"])
    )

    (violation,) = [item for item in result.violations if item.code == "unapproved_source"]
    assert violation.subject == "superset:dataset:ghost"
    assert result.status == "invalid"


def test_a_source_approved_by_more_than_one_component_is_a_disclosed_ambiguity():
    # F1 guardrail: refs are the routing key, and a ref two components both approve
    # has no unique owner -- disclosed `ambiguous_source_component`, never mis-routed
    # or dropped. (Both approve it, so it is NOT `unapproved_source`.)
    bundle = _composed(
        _component(_instr(approved_sources=[{"ref": ORDERS, "role": "primary"}])),
        _component(_instr(approved_sources=[{"ref": ORDERS, "role": "primary"}])),
    )
    result = validate_analytics_plan(bundle=bundle, plan=AnalyticsPlan(source_refs=[ORDERS]))

    (violation,) = [item for item in result.violations if item.code == "ambiguous_source_component"]
    assert violation.subject == ORDERS
    assert violation.severity == "warning"
    assert "unapproved_source" not in _codes(result)
    assert result.status == "warnings"


def test_a_join_across_two_components_is_unverifiable_never_verified():
    # req 4 / F2: a cross-domain join is disclosed UNVERIFIABLE and never upgraded --
    # it is a WARNING, not an ERROR, and specifically NOT `unapproved_join` (which
    # would read as "the governed context forbids it"). The governed `joinable_on`
    # edge that would verify it is slice 2b.
    bundle = _composed(
        _component(_instr(approved_sources=[{"ref": ORDERS, "role": "primary"}])),
        _component(_instr(approved_sources=[{"ref": PRICES, "role": "primary"}])),
    )
    plan = AnalyticsPlan(
        source_refs=[ORDERS, PRICES],
        joins=[{"from": "orders.customer_id", "to": "prices.customer_id", "type": "inner"}],
    )
    result = validate_analytics_plan(bundle=bundle, plan=plan)

    (violation,) = [
        item for item in result.violations if item.code == "cross_domain_join_unverifiable"
    ]
    assert violation.severity == "warning"
    assert violation.subject == "orders.customer_id->prices.customer_id"
    assert "unapproved_join" not in _codes(result)
    # A WARNING, so the plan is not `valid` and not `invalid`: it is not verified.
    assert result.status == "warnings"


def test_a_join_within_one_component_is_validated_normally_not_cross_domain():
    # Both sides live in the same component and the component declares the join --
    # it is a within-domain join, validated by the reused single-domain rule, and is
    # NOT disclosed cross-domain-unverifiable.
    within = {"from": "orders.id", "to": "line_items.order_id", "type": "inner"}
    bundle = _composed(
        _component(
            _instr(
                approved_sources=[
                    {"ref": ORDERS, "role": "primary"},
                    {"ref": LINE_ITEMS, "role": "primary"},
                ],
                joins=[within],
            )
        ),
        _component(_instr(approved_sources=[{"ref": PRICES, "role": "primary"}])),
    )
    plan = AnalyticsPlan(source_refs=[ORDERS, LINE_ITEMS], joins=[within])
    result = validate_analytics_plan(bundle=bundle, plan=plan)

    assert "cross_domain_join_unverifiable" not in _codes(result)
    assert "unapproved_join" not in _codes(result)


def test_a_single_domain_bundle_still_takes_the_solo_path():
    # The composed path is entered ONLY when `domains[]` is present; an ordinary
    # bundle is unchanged and still reports its single git authority.
    result = validate_analytics_plan(bundle=_bundle(), plan=_plan())
    assert result.status == "valid"
    assert result.checked_against["commit_sha"] == "abc123"


# --- #367 review bounce: the per-component routing was flat, not scoped ---


def test_a_component_listing_a_ref_twice_is_one_owner_not_ambiguous():
    # CRITIC bounce: a single component may approve the same ref twice (role primary +
    # secondary, a legit pattern the schema does not forbid). Ownership is keyed by DISTINCT
    # component index, so one component that lists a ref twice is ONE owner, not two -- it
    # must NOT trip the >1-owner ambiguity.
    bundle = _composed(
        _component(
            _instr(
                approved_sources=[
                    {"ref": ORDERS, "role": "primary"},
                    {"ref": ORDERS, "role": "secondary"},
                ]
            )
        ),
        _component(_instr(approved_sources=[{"ref": PRICES, "role": "primary"}])),
    )
    result = validate_analytics_plan(bundle=bundle, plan=AnalyticsPlan(source_refs=[ORDERS]))

    assert "ambiguous_source_component" not in _codes(result)
    assert "unapproved_source" not in _codes(result)


def test_a_one_component_plan_is_unaffected_by_another_components_required_filter_and_check():
    # ADVERSARY bounce: a flat union validated a plan against EVERY component. A plan
    # reading only domain A's `orders` was then flagged for omitting domain B's required
    # `prices` filter/check. The composite must be built from ENGAGED components only, so an
    # unengaged domain's required filter/check never bleeds onto this plan.
    a = _instr(
        approved_sources=[{"ref": ORDERS, "role": "primary"}],
        filters=["orders.status = 'completed'"],
        validations=["orders total is non-negative"],
    )
    b = _instr(
        approved_sources=[{"ref": PRICES, "role": "primary"}],
        filters=["prices.active = true"],
        validations=["prices are positive"],
    )
    bundle = _composed(_component(a), _component(b))
    plan = AnalyticsPlan(
        source_refs=[ORDERS],
        filters=["orders.status = 'completed'"],
        checks=["orders total is non-negative"],
    )
    result = validate_analytics_plan(bundle=bundle, plan=plan)
    codes = _codes(result)

    # B is unengaged: its required prices filter/check must NOT fall on this plan.
    assert "missing_required_filter" not in codes
    assert "missing_required_check" not in codes
    # A's own required filter/check, which the plan DID carry, are satisfied.
    assert result.status in ("valid", "valid_with_gaps")


def test_a_field_defined_by_two_engaged_components_is_ambiguous_not_last_wins():
    # ADVERSARY bounce: duplicate field names became last-wins across the flat union. When
    # two engaged components both define `revenue`, the field has no single governing
    # definition: disclose it, and do NOT judge the plan's `revenue` against whichever
    # component won the union (which would emit a spurious mismatch).
    a = _instr(
        approved_sources=[{"ref": ORDERS, "role": "primary"}],
        fields=[{"name": "revenue", "source_ref": ORDERS, "expression": "SUM(orders.amount)"}],
    )
    b = _instr(
        approved_sources=[{"ref": PRICES, "role": "primary"}],
        fields=[{"name": "revenue", "source_ref": PRICES, "expression": "SUM(prices.value)"}],
    )
    bundle = _composed(_component(a), _component(b))
    plan = AnalyticsPlan(
        source_refs=[ORDERS, PRICES],
        fields=[{"name": "revenue", "source_ref": ORDERS, "expression": "SUM(orders.amount)"}],
    )
    result = validate_analytics_plan(bundle=bundle, plan=plan)
    codes = _codes(result)

    (violation,) = [item for item in result.violations if item.code == "ambiguous_field_component"]
    assert violation.subject == "revenue"
    assert violation.severity == "warning"
    assert "field_expression_mismatch" not in codes
    assert result.status == "warnings"
