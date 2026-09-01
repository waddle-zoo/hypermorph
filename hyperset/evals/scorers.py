"""The deterministic scorers, which are the release gate (ADR 0007, #25).

No LLM judge, and nothing that substring-matches English prose for MEANING.
Two kinds of read appear below and only two: the structure of the trace -- what
was called, in what order, with what parameters, and what came back -- and
whether an exact identifier appears in what the arm said. A dataset UUID is a
token either present or absent; "did it explain the grain well" is not, and is
what #25 leaves to blind human review instead.

Predicates are keyed to identifiers BOTH arms can produce. The governed arm
sees `superset:dataset:<uuid>`, the raw arm sees the same dataset under the
same UUID in raw Superset metadata, so `must_cite` holds the UUID. A predicate
keyed to the governed prefix would score the substrate rather than the answer,
and arm 2 would fail it by construction -- which would make the comparison
worthless in exactly the way #25 warns about.

CRITICAL means the CLI exits nonzero when the GOVERNED arm fails it (#25). It
is a property of the predicate and not of the arm: the raw baseline failing
`plan_validated_before_the_answer` is the measurement, not a build failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from hyperset.bundle.schema import REF_NOT_OBSERVED
from hyperset.evals.cases import GOVERNED_FETCH, NO_MATCH, STALE_GOVERNED_CONTEXT, Case
from hyperset.evals.recording import GOVERNED_ARM, Recording
from hyperset.planner.trace import PLANNER_MESSAGE, RUN_FAILED, TOOL_CALL, TOOL_RESULT
from hyperset.processor.rules import RULE_ID as DRIFT_RULE_ID
from hyperset.processor.rules import UNDECIDABLE_ID as DRIFT_UNDECIDABLE_ID

CATALOG = "list_context_catalog"
RESOLVE = "resolve_analytics_context"
VALIDATE = "validate_analytics_plan"

GOVERNED_STATUSES = ("governed", "mixed")

# The finding types the processor's drift rule persists, as they appear in the
# served bundle's `linked_evidence.findings` -- imported from the rule so the
# scorer reads the exact strings the processor writes rather than a copy that
# can drift from them (hy-2m0r).
DRIFT_FINDING_TYPES = (DRIFT_RULE_ID, DRIFT_UNDECIDABLE_ID)

# The predicates BOTH arms are scored on, and therefore the only ones an
# arm-to-arm number may be computed over. The rest are structural checks of the
# governed path -- did it list the catalog, did it name one domain, did it
# validate against the bundle it resolved -- which arm 2 has no tools to
# attempt. Folding those into one fraction would score the two arms over
# different denominators and call the result a comparison.
#
# Note what stays in the shared set: `governed_rules_stated`. The raw arm CAN
# state the tax split, the completed-order filter and the test-account
# exclusion -- nothing stops a model from reading them out of a payload that
# defines them. That it cannot is the substrate difference the benchmark is
# for, so removing it from the shared set would remove the finding.
SHARED_PREDICATES = (
    "run_completed",
    "prohibited_source_avoided",
    "evidence_cited",
    "governed_rules_stated",
)

# Every predicate name this module can produce. Enumerated for the reason
# `WARNING_CODES` is: something outside this module names them -- the expected
# failure list -- and a name that matches nothing must fail loudly rather than
# quietly excusing nothing.
PREDICATE_NAMES = SHARED_PREDICATES + (
    "catalog_before_resolve",
    "directive_named_the_expected_domain",
    "plan_validated_before_the_answer",
    "unfixable_ref_not_retried",
    "no_governed_answer_without_a_governed_domain",
    "stale_governed_context_surfaced",
)

# A plan the service did not call wrong. `invalid` contradicts governed
# context and `unverifiable` means the check could not be made, so neither is
# a validated plan; `warnings` is a plan whose gaps were disclosed, and
# `valid_with_gaps` is an otherwise-valid plan against a domain that declares
# nothing in some section (#285) -- both validated, exactly as `valid` is. The
# status is necessary and not sufficient -- see `UNDECIDABLE_PLAN_CODES`.
#
# Bound to `PLAN_STATUSES` by `test_the_scorer_validated_statuses_track_the_plan_
# vocabulary`: this is a SECOND hand-typed subset of that enum, and a new plan
# status that means "validated" but is missing here scores a correctly-validated
# arm as not-validated -- silently, since nothing else cross-checks the two. That
# is exactly what `valid_with_gaps` would have done to the sparse-domain arm.
VALIDATED_PLAN_STATUSES = ("valid", "valid_with_gaps", "warnings")

# The disclosures that deny governance FOR THE ELEMENT THEY NAME (hy-fxym).
# The plan still validates and the comparator is right to decline to call it
# wrong, but Hyperset did not govern the field, filter or grain named here, so
# an arm that answered over it answered outside governed context and the run
# did not do what this predicate claims.
#
# Enumerated rather than derived from severity: `warning` also covers gaps in
# elements the context DOES govern, and collapsing the two would delete
# `warnings` from the predicate wholesale. Enumerated rather than filtered by
# what the answer relied on: reliance is not knowable from a recording, and
# the conservative direction is the one shipped.
UNDECIDABLE_PLAN_CODES = (
    "field_expression_undecidable",
    "filter_undecidable",
    "grain_undecidable",
)


# WHICH BRANCH PRODUCED THE VERDICT, as a token a rule can compare (hy-1pqa).
#
# A predicate name says what was checked. It does not say what went wrong, and
# most of these predicates fail in more than one way: a plan that was never
# validated and a plan validated against a bundle the arm never resolved are
# different defects reported under one name. The ratchet matched on the
# predicate name alone, so a declared failure could change mechanism completely
# and no exit code moved -- measured on `revenue_by_region`, where the same
# entry covered a MISSING validate call on one re-roll and a call that happens
# and mis-resolves on the next.
#
# The alternative was pinning the scorer's `explanation`. Rejected on
# measurement rather than taste: most failure branches are f-strings
# interpolating run content, so duplicating one resolve step on the committed
# supply-chain recording changed the string while the defect, the predicate and
# the verdict all stayed put. An exact pin goes red there on a re-roll that
# resolves once more, which is a false red on a required gate.
#
# So a code is a FIXED token chosen per branch, and `Code` below is the whole
# enumeration. AN ENUM RATHER THAN A TABLE OF STRINGS, on the mayor's build
# note: a table is a second place the branch set is written down and can drift
# from the branches themselves, and a mistyped string matches nothing silently
# where a mistyped member is an `AttributeError` at import.
#
# `tests/unit/evals/test_every_branch_names_itself.py` walks each scorer's WHOLE
# BODY for `Code` members and asserts both directions per predicate. Walking the
# function body rather than the `Score(...)` arguments is the difference between
# a check and a stated blind spot: `code` reaches the constructor as a literal
# at three sites, inside a conditional at seven, and at
# `_plan_validated_before_the_answer` -- the five-branch predicate, the largest
# in this file -- as a bare local assigned in an if/elif chain, where an argument
# walk sees a `Name` and passes.
class Code(StrEnum):
    """Every branch either scorer produces, one member each.

    THE GRANULARITY IS THE BRANCH AND NOT FINER, recorded so the fix is not read
    as wider than it is: two defects landing in the SAME branch stay invisible.
    `DID_NOT_VALIDATE` covers a plan whose asset_refs mismatched and an outright
    invalid plan alike. Going finer means comparing run content again, which is
    the brittleness this field was chosen over.

    PASSING BRANCHES CARRY A CODE TOO. Not left blank on the grounds that only
    failures are declared: a conditional field needs a default for the branches
    that opt out, and a default is exactly what lets a new branch ship without
    naming itself -- the defect this bead exists to prevent, rebuilt inside the
    field meant to prevent it.
    """

    ANSWERED = "answered"
    RUN_FAILED = "run_failed"
    SAID_NOTHING = "said_nothing"

    NO_PROHIBITED_SOURCE_NAMED = "no_prohibited_source_named"
    NAMED_A_PROHIBITED_SOURCE = "named_a_prohibited_source"

    CITED_EVERY_REQUIRED_SOURCE = "cited_every_required_source"
    DID_NOT_CITE_A_REQUIRED_SOURCE = "did_not_cite_a_required_source"

    STATED_EVERY_GOVERNED_RULE = "stated_every_governed_rule"
    DID_NOT_STATE_A_GOVERNED_RULE = "did_not_state_a_governed_rule"

    CATALOGUED_BEFORE_RESOLVING = "catalogued_before_resolving"
    NO_RESOLVE_ATTEMPTED = "no_resolve_attempted"
    RESOLVED_WITHOUT_CATALOGUING = "resolved_without_cataloguing"

    NAMED_THE_EXPECTED_DOMAIN = "named_the_expected_domain"
    NEVER_NAMED_THE_EXPECTED_DOMAIN = "never_named_the_expected_domain"

    VALIDATED_AGAINST_THE_RESOLVED_BUNDLE = "validated_against_the_resolved_bundle"
    NEVER_CALLED_VALIDATE = "never_called_validate"
    VALIDATED_AN_UNRESOLVED_BUNDLE = "validated_an_unresolved_bundle"
    UNDECIDABLE_ELEMENT_DISCLOSED = "undecidable_element_disclosed"
    DID_NOT_VALIDATE = "did_not_validate"

    NO_REF_SENT_TWICE = "no_ref_sent_twice"
    RETRIED_AN_UNFIXABLE_REF = "retried_an_unfixable_ref"

    NOTHING_GOVERNED_WAS_RESOLVED = "nothing_governed_was_resolved"
    GOVERNED_CONTEXT_FOR_AN_UNGOVERNED_QUESTION = "governed_context_for_an_ungoverned_question"

    SURFACED_THE_STALE_GOVERNED_CONTEXT = "surfaced_the_stale_governed_context"
    ANSWERED_STALE_GOVERNED_CONTEXT_AS_CURRENT = "answered_stale_governed_context_as_current"


FAILURE_CODES: dict[str, tuple[Code, ...]] = {
    "run_completed": (Code.RUN_FAILED, Code.SAID_NOTHING),
    "prohibited_source_avoided": (Code.NAMED_A_PROHIBITED_SOURCE,),
    "evidence_cited": (Code.DID_NOT_CITE_A_REQUIRED_SOURCE,),
    "governed_rules_stated": (Code.DID_NOT_STATE_A_GOVERNED_RULE,),
    "catalog_before_resolve": (Code.NO_RESOLVE_ATTEMPTED, Code.RESOLVED_WITHOUT_CATALOGUING),
    "directive_named_the_expected_domain": (Code.NEVER_NAMED_THE_EXPECTED_DOMAIN,),
    "plan_validated_before_the_answer": (
        Code.NEVER_CALLED_VALIDATE,
        Code.VALIDATED_AN_UNRESOLVED_BUNDLE,
        Code.UNDECIDABLE_ELEMENT_DISCLOSED,
        Code.DID_NOT_VALIDATE,
    ),
    "unfixable_ref_not_retried": (Code.RETRIED_AN_UNFIXABLE_REF,),
    "no_governed_answer_without_a_governed_domain": (
        Code.GOVERNED_CONTEXT_FOR_AN_UNGOVERNED_QUESTION,
    ),
    "stale_governed_context_surfaced": (Code.ANSWERED_STALE_GOVERNED_CONTEXT_AS_CURRENT,),
}
"""Which of a predicate's branches are FAILURES, which is what a declaration may
pin.

Needed beside the enum rather than derivable from it: `expected_failures.yaml`
declares defects, so a declaration naming a passing branch -- or a real code
belonging to a different predicate -- would be an entry that can never hold. An
entry that matches nothing excuses nothing and sits in the file looking like an
accepted defect, which is the refusal `case_id` and `predicate` already carry.

The passing branch is the remaining member of each predicate's set and is not
tabulated a second time; the guard asserts the partition."""


@dataclass(frozen=True, kw_only=True)
class Score:
    """One predicate's verdict on one recording.

    `explanation` carries what the trace actually showed rather than a restated
    pass/fail, because the first question asked of a red benchmark is always
    "what did it do instead". `code` carries the same thing as a token a rule
    can compare, because prose the harness cannot read is prose the ratchet
    cannot check (hy-1pqa).

    KEYWORD-ONLY, and that is load-bearing rather than style. All twelve
    constructions in this module were positional when `code` was added, so
    inserting a field anywhere but last would have rebound four of them
    silently -- `critical` receiving a string and `explanation` a bool, both
    truthy enough to survive to a wrong verdict rather than crash. `kw_only`
    turns a missed conversion into a `TypeError` at construction, and it retires
    the problem for the next field as well as this one.
    """

    predicate: str
    code: Code
    passed: bool
    critical: bool
    explanation: str

    def to_dict(self) -> dict:
        return {
            "predicate": self.predicate,
            "code": self.code.value,
            "passed": self.passed,
            "critical": self.critical,
            "explanation": self.explanation,
        }


def steps(recording: Recording, kind: str) -> list[dict]:
    return [step for step in recording.trace.get("steps") or [] if step.get("kind") == kind]


def said(recording: Recording) -> str:
    """Everything the arm said, joined. Every message and not only the last:
    an answer split across two turns is still the answer, and scoring only the
    final one would punish a model for where it put a sentence."""
    return "\n".join(
        str(step.get("detail", {}).get("text") or "") for step in steps(recording, PLANNER_MESSAGE)
    )


def calls(recording: Recording, operation: str | None = None) -> list[dict]:
    return [
        step["detail"]
        for step in steps(recording, TOOL_CALL)
        if operation is None or step.get("detail", {}).get("operation") == operation
    ]


def results(recording: Recording, operation: str | None = None) -> list[dict]:
    return [
        step["detail"]
        for step in steps(recording, TOOL_RESULT)
        if operation is None or step.get("detail", {}).get("operation") == operation
    ]


def score(recording: Recording, case: Case) -> list[Score]:
    """Every predicate that applies, in a fixed order.

    A predicate that does not apply returns nothing rather than a pass. A
    not-applicable counted as a pass is how an arm with no tools scores full
    marks on tool behaviour.
    """
    produced = [
        predicate(recording, case)
        for predicate in (
            _run_completed,
            _prohibited_source_avoided,
            _evidence_cited,
            _governed_rules_stated,
            _catalog_before_resolve,
            _directive_named_the_expected_domain,
            _plan_validated_before_the_answer,
            _unfixable_ref_not_retried,
            _no_governed_answer_without_a_governed_domain,
            _stale_governed_context_surfaced,
        )
    ]
    return [entry for entry in produced if entry is not None]


def critical_failures(scores: list[Score]) -> list[Score]:
    return [entry for entry in scores if entry.critical and not entry.passed]


def _run_completed(recording: Recording, case: Case) -> Score:
    """A run that died is not a run that behaved well.

    Scored first and separately because every other predicate reads a trace
    that stops early the same way it reads a trace that chose to stop: a run
    whose endpoint went down calls no prohibited source either.
    """
    failures = steps(recording, RUN_FAILED)
    if failures:
        detail = failures[0].get("detail", {})
        return Score(
            predicate="run_completed",
            code=Code.RUN_FAILED,
            passed=False,
            critical=True,
            explanation=f"the run failed with {detail.get('exception')}: {detail.get('reason')}",
        )
    if not said(recording).strip():
        return Score(
            predicate="run_completed",
            code=Code.SAID_NOTHING,
            passed=False,
            critical=True,
            explanation="the arm said nothing at all",
        )
    return Score(
        predicate="run_completed",
        code=Code.ANSWERED,
        passed=True,
        critical=True,
        explanation="the run finished and the arm answered",
    )


def _prohibited_source_avoided(recording: Recording, case: Case) -> Score | None:
    """A prohibited source stays prohibited -- in the answer AND in the asking.

    Both surfaces, because they are different failures: naming it in an answer
    misleads the caller, and naming it in a directive is the arm trying to
    retrieve it. Either one fails the predicate.
    """
    if not case.must_not_cite:
        return None
    spoken = said(recording)
    asked = str(calls(recording))
    named = sorted({ref for ref in case.must_not_cite if ref in spoken or ref in asked})
    return Score(
        predicate="prohibited_source_avoided",
        code=Code.NO_PROHIBITED_SOURCE_NAMED if not named else Code.NAMED_A_PROHIBITED_SOURCE,
        passed=not named,
        critical=True,
        explanation="no prohibited source was named" if not named else f"named {', '.join(named)}",
    )


def _evidence_cited(recording: Recording, case: Case) -> Score | None:
    """The exact source identifiers the case requires, in what the arm said."""
    if not case.must_cite:
        return None
    spoken = said(recording)
    missing = sorted({ref for ref in case.must_cite if ref not in spoken})
    return Score(
        predicate="evidence_cited",
        code=Code.CITED_EVERY_REQUIRED_SOURCE
        if not missing
        else Code.DID_NOT_CITE_A_REQUIRED_SOURCE,
        passed=not missing,
        critical=True,
        explanation="cited every required source"
        if not missing
        else f"did not cite {', '.join(missing)}",
    )


def _governed_rules_stated(recording: Recording, case: Case) -> Score | None:
    """The rules that make an answer right rather than merely sourced.

    A dataset reference is the easy half: both arms can read a UUID off a
    listing. The tax split, the completed-order filter and the test-account
    exclusion are the half that only exists in governed context, and an answer
    that names the right table and computes the wrong number is the failure
    this benchmark exists to detect. Measured on the raw arm's first recorded
    run, which cited both datasets and then summed a `recognized_revenue`
    column that no source defines.

    Exact strings, taken from the human-owned context file. A paraphrase check
    would need a model to judge a model, which #25 forbids.
    """
    if not case.must_state:
        return None
    spoken = said(recording)
    missing = [rule for rule in case.must_state if rule not in spoken]
    return Score(
        predicate="governed_rules_stated",
        code=Code.STATED_EVERY_GOVERNED_RULE if not missing else Code.DID_NOT_STATE_A_GOVERNED_RULE,
        passed=not missing,
        critical=True,
        explanation="stated every governed rule"
        if not missing
        else f"did not state {'; '.join(missing)}",
    )


def _catalog_before_resolve(recording: Recording, case: Case) -> Score | None:
    """The catalog is what tells an arm which domains exist.

    Governed arm only, and not critical: an arm that resolved the right domain
    without listing first got the answer right by a route the prompt does not
    teach, which is worth measuring and is not worth failing a build over.
    """
    if recording.arm != GOVERNED_ARM:
        return None
    ordered = [call["operation"] for call in calls(recording)]
    if RESOLVE not in ordered:
        return Score(
            predicate="catalog_before_resolve",
            code=Code.NO_RESOLVE_ATTEMPTED,
            passed=False,
            critical=False,
            explanation="no resolve was attempted",
        )
    catalogued = CATALOG in ordered[: ordered.index(RESOLVE)]
    return Score(
        predicate="catalog_before_resolve",
        code=Code.CATALOGUED_BEFORE_RESOLVING if catalogued else Code.RESOLVED_WITHOUT_CATALOGUING,
        passed=catalogued,
        critical=False,
        explanation="listed the catalog before resolving"
        if catalogued
        else "resolved without listing first",
    )


def _directive_named_the_expected_domain(recording: Recording, case: Case) -> Score | None:
    """The semantic step #70 moved to the model: naming the domain itself.

    Read off the directive the arm SENT rather than off what came back, because
    a resolve that returned the revenue bundle after naming every domain is not
    domain selection.
    """
    if recording.arm != GOVERNED_ARM or case.family != GOVERNED_FETCH:
        return None
    named = [
        tuple((call.get("params") or {}).get("directive", {}).get("domains") or ())
        for call in calls(recording, RESOLVE)
    ]
    exact = [entry for entry in named if entry == (case.expected_domain,)]
    return Score(
        predicate="directive_named_the_expected_domain",
        code=Code.NAMED_THE_EXPECTED_DOMAIN if exact else Code.NEVER_NAMED_THE_EXPECTED_DOMAIN,
        passed=bool(exact),
        critical=True,
        explanation=f"resolved exactly {case.expected_domain!r}"
        if exact
        else f"directives named {named or 'nothing'}, never exactly ({case.expected_domain!r},)",
    )


def _plan_validated_before_the_answer(recording: Recording, case: Case) -> Score | None:
    """Validation before the answer, against the bundle that was resolved.

    The `bundle_id` is checked rather than the call alone: validating a plan
    against some other bundle is a passing tool call and a meaningless check,
    and it is the failure a scorer counting calls cannot see.

    `warnings` counts as validated and `unverifiable` does not, which is the
    contract's own distinction rather than this scorer's: an error contradicts
    governed context, while a warning is something the context does not cover
    or no longer agrees with its sources (`hyperset.bundle.plan`). Requiring
    `valid` alone would fail a plan the service deliberately declined to call
    wrong.

    An UNDECIDABLE disclosure is the exception, and it is about what the arm
    then did rather than about the verdict: the element it names is not
    governed, so answering over it is answering outside governed context even
    though the plan validated (`UNDECIDABLE_PLAN_CODES`, hy-fxym).
    """
    if not case.requires_plan_validation or recording.arm != GOVERNED_ARM:
        return None
    resolved = {
        (result.get("result") or {}).get("bundle_id")
        for result in results(recording, RESOLVE)
        if (result.get("result") or {}).get("bundle_id")
    }
    outcomes = [result.get("result") or {} for result in results(recording, VALIDATE)]
    validated = [
        outcome
        for outcome in outcomes
        if outcome.get("status") in VALIDATED_PLAN_STATUSES and not _undecidable_codes(outcome)
    ]
    disclosed = sorted({code for outcome in outcomes for code in _undecidable_codes(outcome)})
    matched = [
        call
        for call in calls(recording, VALIDATE)
        if (call.get("params") or {}).get("bundle_id") in resolved
    ]
    passed = bool(validated) and bool(matched)
    if passed:
        code = Code.VALIDATED_AGAINST_THE_RESOLVED_BUNDLE
        explanation = "validated the plan against the bundle it resolved"
    elif not calls(recording, VALIDATE):
        code = Code.NEVER_CALLED_VALIDATE
        explanation = "never called validate_analytics_plan"
    elif not matched:
        code = Code.VALIDATED_AN_UNRESOLVED_BUNDLE
        explanation = "validated against a bundle_id it had not resolved"
    elif disclosed:
        code = Code.UNDECIDABLE_ELEMENT_DISCLOSED
        explanation = (
            f"the plan validated but disclosed {', '.join(disclosed)}, "
            "so what those name is not governed"
        )
    else:
        code = Code.DID_NOT_VALIDATE
        explanation = "called validate_analytics_plan and the plan did not validate"
    return Score(
        predicate="plan_validated_before_the_answer",
        code=code,
        passed=passed,
        critical=True,
        explanation=explanation,
    )


def _undecidable_codes(outcome: dict) -> set[str]:
    return {
        violation.get("code")
        for violation in outcome.get("violations") or []
        if isinstance(violation, dict) and violation.get("code") in UNDECIDABLE_PLAN_CODES
    }


def _unfixable_ref_not_retried(recording: Recording, case: Case) -> Score | None:
    """`ref_not_observed` means stop, and the prompt says so.

    The rule this checks is a product rule rather than a preference: a ref
    nothing observed is not fixed by asking again, and an arm that retries it
    until something answers is the retry-until-success loop the planner was
    built without.
    """
    if recording.arm != GOVERNED_ARM:
        return None
    asked: set[str] = set()
    already_asked: set[str] = set()
    disclosed = False
    retried: set[str] = set()
    for step in recording.trace.get("steps") or []:
        detail = step.get("detail") or {}
        if step.get("kind") == TOOL_CALL:
            refs = {
                ref
                for ref in (detail.get("params") or {}).get("asset_refs") or []
                if isinstance(ref, str)
            }
            if disclosed:
                retried |= refs & already_asked
            asked |= refs
            continue
        if step.get("kind") != TOOL_RESULT:
            continue
        warnings = ((detail.get("result") or {}).get("resolution") or {}).get("warnings") or []
        if any(entry.get("code") == REF_NOT_OBSERVED for entry in warnings):
            # WHICH ref was unobserved is not in the payload -- the warning
            # carries the code and an English sentence naming the ref, and
            # nothing structured (`hyperset.context.evidence`). So this
            # predicate asks the question the contract can answer: after that
            # disclosure, did the arm send any ref it had already sent? Reading
            # the ref out of the message would be the prose matching this whole
            # module exists without, and would make the scorer break on a
            # reworded sentence the contract says may be reworded.
            disclosed = True
            already_asked |= asked
    if not disclosed:
        return None
    return Score(
        predicate="unfixable_ref_not_retried",
        code=Code.NO_REF_SENT_TWICE if not retried else Code.RETRIED_AN_UNFIXABLE_REF,
        passed=not retried,
        critical=True,
        explanation="sent no ref twice after a ref_not_observed disclosure"
        if not retried
        else f"re-sent {', '.join(sorted(retried))} after ref_not_observed",
    )


def _no_governed_answer_without_a_governed_domain(recording: Recording, case: Case) -> Score | None:
    """Safe no-match: nothing governs the question, so nothing may be presented
    as governed.

    Read off `resolution.status`, which the contract already carries -- the
    four statuses exist so a client can tell approved meaning from an
    observation. An arm that never resolved at all passes: reporting that
    nothing covers the question without asking is a correct answer to a
    question with no governed domain.
    """
    if case.family != NO_MATCH or recording.arm != GOVERNED_ARM:
        return None
    governed = [
        (result.get("result") or {}).get("resolution", {}).get("status")
        for result in results(recording, RESOLVE)
        if (result.get("result") or {}).get("resolution", {}).get("status") in GOVERNED_STATUSES
    ]
    return Score(
        predicate="no_governed_answer_without_a_governed_domain",
        code=Code.NOTHING_GOVERNED_WAS_RESOLVED
        if not governed
        else Code.GOVERNED_CONTEXT_FOR_AN_UNGOVERNED_QUESTION,
        passed=not governed,
        critical=True,
        explanation="no resolve returned governed context"
        if not governed
        else f"resolved {', '.join(governed)} context for a question nothing governs",
    )


def _stale_governed_context_surfaced(recording: Recording, case: Case) -> Score | None:
    """Stale governed context: when Git-approved meaning has drifted from its
    source, a governed answer must SURFACE that staleness, not answer as if the
    definition were current.

    Read off the served bundle's `linked_evidence.findings`, where the resolver
    attaches the persisted `approved_expression_drift` finding DETERMINISTICALLY
    (`bundle/resolver.py`) -- so a governed arm that resolved over the drifted
    context carries it and one that did not is presenting a stale definition as
    live. This is the substrate difference the third family exists to measure:
    the raw arm has no governed context to be stale, so it is scored on nothing
    here (governed-only, like the no-match check).

    THE DETECTOR MUST BE ABLE TO STAY SILENT: over a fresh context the findings
    list is empty and this predicate does not apply to that family at all, so a
    non-drifted governed_fetch case is never flagged stale. It fires only where
    a drift finding is expected and absent.
    """
    if case.family != STALE_GOVERNED_CONTEXT or recording.arm != GOVERNED_ARM:
        return None
    surfaced = [
        finding
        for result in results(recording, RESOLVE)
        for finding in ((result.get("result") or {}).get("linked_evidence") or {}).get(
            "findings", []
        )
        if finding.get("finding_type") in DRIFT_FINDING_TYPES
    ]
    return Score(
        predicate="stale_governed_context_surfaced",
        code=Code.SURFACED_THE_STALE_GOVERNED_CONTEXT
        if surfaced
        else Code.ANSWERED_STALE_GOVERNED_CONTEXT_AS_CURRENT,
        passed=bool(surfaced),
        critical=True,
        explanation=(
            f"surfaced the drift finding {surfaced[0].get('finding_type')!r} on "
            f"{surfaced[0].get('ref')}"
            if surfaced
            else "resolved the governed context but surfaced no drift finding, so a stale "
            "definition was presented as current"
        ),
    )
