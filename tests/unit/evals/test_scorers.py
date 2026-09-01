"""The deterministic scorers, each against a trace that fails it (hy-ast).

Every predicate here is tested in both directions, because a benchmark's
scorers are the one piece of code whose failure mode is silence: a predicate
that cannot fail reports a perfect arm forever, and nothing downstream
notices.
"""

from __future__ import annotations

import pytest

from hyperset.context.schema import REF_NOT_OBSERVED
from hyperset.evals.cases import Case
from hyperset.evals.pins import RunPins, repository_pins
from hyperset.evals.recording import GOVERNED_ARM, RAW_ARM, RECORDING_SCHEMA_VERSION, Recording
from hyperset.evals.scorers import critical_failures, score
from hyperset.planner.trace import (
    PLANNER_MESSAGE,
    RUN_FAILED,
    TOOL_CALL,
    TOOL_RESULT,
)
from tests.unit.evals.test_pins import HOST

PRIMARY = "ae48881d-334f-54a7-94e8-1ffcc73866e2"
DIMENSION = "5bcf01e3-3f70-50d2-bb31-562b627b09b8"
PROHIBITED = "6f4976c2-25ea-5d98-b714-9ca8e6c9b7e4"
BUNDLE = "bundle-1"

RULE = "SUM(gross_amount - tax_amount)"

GOVERNED_CASE = Case(
    id="revenue_by_region",
    family="governed_fetch",
    question="What is recognized revenue by customer region?",
    expected_domain="revenue",
    must_cite=(PRIMARY, DIMENSION),
    must_not_cite=(PROHIBITED,),
    must_state=(RULE,),
    requires_plan_validation=True,
    reason="",
)

NO_MATCH_CASE = Case(
    id="supply_chain_lead_time",
    family="no_match",
    question="What is our average supplier lead time by warehouse?",
    expected_domain=None,
    must_cite=(),
    must_not_cite=(PRIMARY, PROHIBITED),
    must_state=(),
    requires_plan_validation=False,
    reason="",
)


def step(kind, detail):
    return {"kind": kind, "at": "2026-07-28T00:00:00+00:00", "detail": detail, "summary": ""}


def call(operation, params=None):
    return step(TOOL_CALL, {"operation": operation, "params": params or {}})


def result(operation, payload):
    return step(TOOL_RESULT, {"operation": operation, "result": payload, "retryable_warnings": []})


def says(text):
    return step(PLANNER_MESSAGE, {"text": text})


def made(steps, *, arm=GOVERNED_ARM):
    return Recording(
        run_id="d" * 32,
        schema_version=RECORDING_SCHEMA_VERSION,
        arm=arm,
        case_id="case",
        task_version="revenue@1",
        git_commit="0" * 40,
        recorded_at="2026-07-28T00:00:00+00:00",
        pins=RunPins(**{**repository_pins(arm), **HOST}),
        trace={"provenance": {"runtime": "openai_agents_sdk"}, "steps": steps},
        source_refs=[],
    )


def good_governed_run(answer=None):
    return [
        call("list_context_catalog"),
        result("list_context_catalog", {"domains": [{"domain": "revenue"}]}),
        call("resolve_analytics_context", {"directive": {"domains": ["revenue"]}}),
        result(
            "resolve_analytics_context",
            {"bundle_id": BUNDLE, "resolution": {"status": "governed", "warnings": []}},
        ),
        call("validate_analytics_plan", {"bundle_id": BUNDLE}),
        result("validate_analytics_plan", {"status": "valid", "violations": []}),
        says(
            answer
            or f"Use superset:dataset:{PRIMARY} joined to superset:dataset:{DIMENSION}, "
            f"computing {RULE}."
        ),
    ]


def verdict(scores, predicate):
    for entry in scores:
        if entry.predicate == predicate:
            return entry
    return None


def test_a_governed_run_that_did_everything_right_passes_every_predicate():
    scores = score(made(good_governed_run()), GOVERNED_CASE)

    assert critical_failures(scores) == []
    assert {entry.predicate for entry in scores} == {
        "run_completed",
        "prohibited_source_avoided",
        "evidence_cited",
        "catalog_before_resolve",
        "directive_named_the_expected_domain",
        "plan_validated_before_the_answer",
        "governed_rules_stated",
    }


def test_a_run_that_died_is_not_a_run_that_behaved_well():
    """Every other predicate reads a trace that stopped early the same way it
    reads a trace that chose to stop."""
    scores = score(
        made([step(RUN_FAILED, {"exception": "APIError", "reason": "down"})]), GOVERNED_CASE
    )

    assert verdict(scores, "run_completed").passed is False
    assert "APIError" in verdict(scores, "run_completed").explanation


def test_an_arm_that_answered_nothing_fails_even_with_perfect_tool_calls():
    scores = score(made(good_governed_run()[:-1]), GOVERNED_CASE)

    assert verdict(scores, "run_completed").passed is False


def test_an_answer_that_names_the_right_source_and_the_wrong_rule_fails():
    """The failure this benchmark exists to detect, and the one a citation
    check cannot see: the raw arm's first recorded run cited both datasets and
    then summed a `recognized_revenue` column no source defines."""
    scores = score(
        made(
            good_governed_run(
                f"Use superset:dataset:{PRIMARY} and superset:dataset:{DIMENSION}, "
                "computing SUM(recognized_revenue)."
            )
        ),
        GOVERNED_CASE,
    )

    assert verdict(scores, "evidence_cited").passed is True
    assert verdict(scores, "governed_rules_stated").passed is False
    assert RULE in verdict(scores, "governed_rules_stated").explanation


def test_naming_a_prohibited_source_in_the_answer_fails():
    scores = score(made(good_governed_run(f"Use superset:dataset:{PROHIBITED}.")), GOVERNED_CASE)

    assert verdict(scores, "prohibited_source_avoided").passed is False
    assert PROHIBITED in verdict(scores, "prohibited_source_avoided").explanation


def test_asking_for_a_prohibited_source_fails_even_when_the_answer_is_clean():
    """Two different failures: naming it misleads the caller, asking for it is
    the arm trying to retrieve it."""
    steps = good_governed_run()
    steps[2] = call(
        "resolve_analytics_context",
        {"directive": {"domains": ["revenue"]}, "asset_refs": [f"superset:dataset:{PROHIBITED}"]},
    )

    assert verdict(score(made(steps), GOVERNED_CASE), "prohibited_source_avoided").passed is False


def test_an_answer_missing_a_required_source_fails_with_the_one_it_missed():
    scores = score(
        made(good_governed_run(f"Use superset:dataset:{PRIMARY} with {RULE}.")), GOVERNED_CASE
    )

    assert verdict(scores, "evidence_cited").passed is False
    assert DIMENSION in verdict(scores, "evidence_cited").explanation


def test_resolving_without_listing_the_catalog_is_measured_and_not_fatal():
    scores = score(made(good_governed_run()[2:]), GOVERNED_CASE)

    entry = verdict(scores, "catalog_before_resolve")
    assert (entry.passed, entry.critical) == (False, False)


def test_naming_every_domain_is_not_domain_selection():
    """Read off the directive the arm SENT: a resolve that returned the right
    bundle after asking for everything did not choose anything."""
    steps = good_governed_run()
    steps[2] = call("resolve_analytics_context", {"directive": {"domains": ["revenue", "orders"]}})

    assert (
        verdict(score(made(steps), GOVERNED_CASE), "directive_named_the_expected_domain").passed
        is False
    )


def test_validating_against_a_bundle_it_never_resolved_is_a_meaningless_check():
    """A passing tool call and a check of nothing -- the failure a scorer
    counting calls cannot see."""
    steps = good_governed_run()
    steps[4] = call("validate_analytics_plan", {"bundle_id": "some-other-bundle"})

    entry = verdict(score(made(steps), GOVERNED_CASE), "plan_validated_before_the_answer")
    assert entry.passed is False
    assert "had not resolved" in entry.explanation


@pytest.mark.parametrize(
    ("status", "counts"),
    [("valid", True), ("warnings", True), ("invalid", False), ("unverifiable", False)],
)
def test_a_disclosed_plan_counts_as_validated_and_an_uncheckable_one_does_not(status, counts):
    """The contract's own distinction, not this scorer's: an error contradicts
    governed context, a warning is a gap the service disclosed, and
    `unverifiable` means the check could not be made at all."""
    steps = good_governed_run()
    steps[5] = result("validate_analytics_plan", {"status": status, "violations": []})

    entry = verdict(score(made(steps), GOVERNED_CASE), "plan_validated_before_the_answer")
    assert entry.passed is counts


@pytest.mark.parametrize(
    "code",
    ["field_expression_undecidable", "filter_undecidable", "grain_undecidable"],
)
def test_an_undecidable_disclosure_is_not_a_validated_plan(code):
    """An undecidable disclosure denies governance for the element it names
    (hy-fxym). The plan still validates -- the comparator is right to decline
    to call it wrong -- but the arm went on to answer over a field, filter or
    grain Hyperset did not govern, and a benchmark that scores that as
    `plan_validated_before_the_answer` reports governance the run did not have.

    Any undecidable code counts against the run, not only one the answer
    provably used: what the answer relied on is not knowable from a recording,
    and the conservative direction is the one shipped.
    """
    steps = good_governed_run()
    steps[5] = result(
        "validate_analytics_plan",
        {"status": "warnings", "violations": [{"code": code, "severity": "warning"}]},
    )

    entry = verdict(score(made(steps), GOVERNED_CASE), "plan_validated_before_the_answer")
    assert entry.passed is False
    assert code in entry.explanation


def test_a_warning_that_is_not_an_undecidable_disclosure_still_counts_as_validated():
    """The bound on the rule above, and the case that says the predicate was
    narrowed rather than `warnings` deleted wholesale: an unapproved filter is
    a gap the service disclosed about an element it DID govern, so the plan is
    still a validated plan."""
    steps = good_governed_run()
    steps[5] = result(
        "validate_analytics_plan",
        {
            "status": "warnings",
            "violations": [{"code": "unapproved_filter", "severity": "warning"}],
        },
    )

    assert (
        verdict(score(made(steps), GOVERNED_CASE), "plan_validated_before_the_answer").passed
        is True
    )


def test_a_plan_that_did_not_validate_fails():
    steps = good_governed_run()
    steps[5] = result("validate_analytics_plan", {"status": "invalid", "violations": [{}]})

    assert (
        verdict(score(made(steps), GOVERNED_CASE), "plan_validated_before_the_answer").passed
        is False
    )


def test_never_validating_fails_with_that_reason_rather_than_the_other_one():
    steps = [entry for entry in good_governed_run() if "validate" not in str(entry)]

    entry = verdict(score(made(steps), GOVERNED_CASE), "plan_validated_before_the_answer")
    assert entry.passed is False
    assert "never called" in entry.explanation


def test_re_sending_a_ref_after_ref_not_observed_is_the_retry_loop_the_prompt_forbids():
    ref = "superset:dataset:not-a-real-one"
    steps = [
        call(
            "resolve_analytics_context",
            {"directive": {"domains": ["revenue"]}, "asset_refs": [ref]},
        ),
        result(
            "resolve_analytics_context",
            {
                "bundle_id": BUNDLE,
                "resolution": {
                    "status": "governed",
                    "warnings": [
                        {"code": REF_NOT_OBSERVED, "message": f"evidence ref {ref!r} ..."}
                    ],
                },
            },
        ),
        call(
            "resolve_analytics_context",
            {"directive": {"domains": ["revenue"]}, "asset_refs": [ref]},
        ),
        result(
            "resolve_analytics_context",
            {"bundle_id": BUNDLE, "resolution": {"status": "governed", "warnings": []}},
        ),
        says("done"),
    ]

    entry = verdict(score(made(steps), GOVERNED_CASE), "unfixable_ref_not_retried")
    assert entry.passed is False
    assert ref in entry.explanation


def test_the_retry_predicate_does_not_apply_when_nothing_was_unobserved():
    """Not applicable is not a pass: a predicate that could not have failed
    must not be counted as one the arm satisfied."""
    assert (
        verdict(score(made(good_governed_run()), GOVERNED_CASE), "unfixable_ref_not_retried")
        is None
    )


def test_governed_context_resolved_for_a_question_nothing_governs_fails():
    steps = [
        call("list_context_catalog"),
        result("list_context_catalog", {"domains": [{"domain": "revenue"}]}),
        call("resolve_analytics_context", {"directive": {"domains": ["revenue"]}}),
        result(
            "resolve_analytics_context",
            {"bundle_id": BUNDLE, "resolution": {"status": "governed", "warnings": []}},
        ),
        says("Supplier lead time comes from the revenue domain."),
    ]

    entry = verdict(
        score(made(steps), NO_MATCH_CASE), "no_governed_answer_without_a_governed_domain"
    )
    assert entry.passed is False


def test_reporting_that_nothing_covers_the_question_passes_without_resolving():
    steps = [
        call("list_context_catalog"),
        result("list_context_catalog", {"domains": [{"domain": "revenue"}]}),
        says("No governed domain covers supplier lead time."),
    ]

    scores = score(made(steps), NO_MATCH_CASE)
    assert critical_failures(scores) == []


@pytest.mark.parametrize(
    "predicate",
    [
        "catalog_before_resolve",
        "directive_named_the_expected_domain",
        "plan_validated_before_the_answer",
    ],
)
def test_the_raw_arm_is_not_scored_on_tools_it_was_never_given(predicate):
    """Arm 2 has raw metadata and no governed operations. Failing it on the
    substrate would make the comparison worthless in exactly the way #25 warns
    about: the arms must differ in one variable, not in their scoring."""
    steps = [says(f"Use dataset {PRIMARY} joined to {DIMENSION}.")]

    assert verdict(score(made(steps, arm=RAW_ARM), GOVERNED_CASE), predicate) is None


def test_the_raw_arm_is_scored_on_what_both_arms_can_do():
    steps = [says(f"Use dataset {PRIMARY} joined to {DIMENSION}.")]

    scores = score(made(steps, arm=RAW_ARM), GOVERNED_CASE)
    assert {entry.predicate for entry in scores} == {
        "run_completed",
        "prohibited_source_avoided",
        "evidence_cited",
        # Scored on the raw arm too, and this is the one predicate where the
        # substrate is supposed to decide the outcome: the rule is in governed
        # context and not in any source system's metadata.
        "governed_rules_stated",
    }


# --- Third family: stale governed context (hy-2m0r, flywheel step 6) ---

from hyperset.evals.cases import STALE_GOVERNED_CONTEXT  # noqa: E402
from hyperset.processor.rules import RULE_ID as DRIFT_RULE_ID  # noqa: E402

STALE_CASE = Case(
    id="revenue_definition_drifted",
    family=STALE_GOVERNED_CONTEXT,
    question="What is recognized revenue by customer region?",
    expected_domain="revenue",
    must_cite=(PRIMARY,),
    must_not_cite=(),
    must_state=(RULE,),
    requires_plan_validation=False,
    reason="",
)

DRIFT_FINDING = {
    "finding_id": "f1",
    "finding_type": DRIFT_RULE_ID,
    "severity": "error",
    "ref": f"superset:dataset:{PRIMARY}",
}


def governed_run_with_findings(findings):
    return [
        call("list_context_catalog"),
        result("list_context_catalog", {"domains": [{"domain": "revenue"}]}),
        call("resolve_analytics_context", {"directive": {"domains": ["revenue"]}}),
        result(
            "resolve_analytics_context",
            {
                "bundle_id": BUNDLE,
                "resolution": {"status": "governed", "warnings": []},
                "linked_evidence": {"findings": findings, "conflicts": []},
            },
        ),
        says(f"Use superset:dataset:{PRIMARY}, computing {RULE}."),
    ]


def test_a_drifted_governed_context_is_flagged_as_stale():
    scores = score(made(governed_run_with_findings([DRIFT_FINDING])), STALE_CASE)

    surfaced = verdict(scores, "stale_governed_context_surfaced")
    assert surfaced is not None
    assert surfaced.passed is True
    assert surfaced.critical is True
    assert "surfaced the drift finding" in surfaced.explanation
    assert critical_failures(scores) == []


def test_a_stale_case_whose_answer_surfaced_no_drift_fails_so_the_detector_is_not_dead():
    # The bundle resolved governed context but carries NO drift finding: the
    # arm presented a stale definition as current. The detector must fire.
    scores = score(made(governed_run_with_findings([])), STALE_CASE)

    surfaced = verdict(scores, "stale_governed_context_surfaced")
    assert surfaced is not None
    assert surfaced.passed is False
    assert surfaced in critical_failures(scores)
    assert "stale definition was presented as current" in surfaced.explanation


def test_a_fresh_governed_fetch_case_is_never_flagged_stale():
    # The detector must stay silent where there is nothing to flag: a normal
    # governed_fetch case (fresh context) is a different family and the stale
    # predicate does not apply to it, so it cannot false-flag freshness.
    scores = score(made(good_governed_run()), GOVERNED_CASE)

    assert verdict(scores, "stale_governed_context_surfaced") is None


def test_the_raw_arm_is_not_scored_on_governed_staleness():
    scores = score(made(governed_run_with_findings([DRIFT_FINDING]), arm=RAW_ARM), STALE_CASE)

    assert verdict(scores, "stale_governed_context_surfaced") is None


def test_the_scorer_validated_statuses_track_the_plan_vocabulary():
    """The drift guard the panel required (hy-gh-285 MAJOR).

    `VALIDATED_PLAN_STATUSES` is a SECOND hand-typed subset of `PLAN_STATUSES`:
    the statuses that mean "the service did not call the plan wrong". A new plan
    status that means validated but is missing here scores a correctly-validated
    arm as not-validated -- silently, because nothing else cross-checks the two.
    That is exactly what `valid_with_gaps` would have done to the sparse-domain
    arm the feature exists to serve.

    Bound as an exact partition rather than a subset, so a new plan status forces
    a decision here: every `PLAN_STATUSES` value is either validated or one of the
    two that are not (`invalid` contradicts governed context; `unverifiable` could
    not be checked)."""
    from hyperset.bundle.plan import PLAN_STATUSES
    from hyperset.evals.scorers import VALIDATED_PLAN_STATUSES

    assert set(VALIDATED_PLAN_STATUSES) <= set(PLAN_STATUSES)
    assert set(VALIDATED_PLAN_STATUSES) == set(PLAN_STATUSES) - {"invalid", "unverifiable"}
    # The value this bounce is about is explicitly on the validated side.
    assert "valid_with_gaps" in VALIDATED_PLAN_STATUSES
