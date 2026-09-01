"""Every failure branch in `scorers.py` names itself, and the enum is complete
in both directions (hy-1pqa).

WHY THIS IS PARSED RATHER THAN EXERCISED. The alternative is a corpus that
drives all 23 branches, which does not exist and would not be cheap: four of
`plan_validated_before_the_answer`'s five arms need a recording shaped to hit
them. A branch nothing exercises is exactly the branch that ships without a
code, so the check has to be static or it is a check of the branches somebody
already thought about.

WHY THE WHOLE FUNCTION BODY AND NOT THE `Score(...)` ARGUMENTS, which is the
mayor's build note and the thing that made an earlier version of this file
useless. `code` reaches the constructor in three different shapes:

    a literal            `code=Code.RUN_FAILED`                     3 sites
    inside a conditional `code=Code.A if x else Code.B`             7 sites
    a bare local         `code=code`, assigned in an if/elif chain  1 site

That last site is `_plan_validated_before_the_answer` -- the five-branch
predicate, the largest in the file. A guard with a blind spot at its most
important site is not a guard. Walking the body finds the member wherever the
branch that assigns it puts it.

MEASURED at f597ecf0, both designs against one injection -- a sixth arm added
to that chain, assigning a new `Code` member no entry in `FAILURE_CODES`
names, which is what "somebody adds a branch" looks like:

    body walk (shipped)   1 failed, 11 passed
    argument walk         sees NOTHING at that site; undeclared found: NONE

The rejected design does not miss the branch by a little. The `code=` argument
is the bare `Name` there, so it carries no members to compare, and the empty
set matches every declaration. That is this bead's own defect -- a check whose
key cannot tell two things apart -- rebuilt one level up.

AND THE BODY WALK WAS THE SAME MISTAKE ONE SPELLING OVER (hy-sp3z). Critic put
`_ALIAS = Code.NEVER_CALLED_VALIDATE` at module level and assigned `code =
_ALIAS` in a sixth arm: the body carries no `Code.` attribute, the count is one,
and the file stayed at 21 passed. Three guards, three keys, all keyed on how the
value is SPELLED where it is used, each found by whoever wrote the next
spelling.

The collector therefore RESOLVES, through `tests/name_resolution.py`: it follows
the name to the string the member carries, so a literal, an attribute, an
imported constant and an alias of any of them are one fact. And a form it cannot
follow is RED rather than absent -- `test_every_branch_supplies_a_code_this
_suite_can_RESOLVE` -- because failing toward green on an unrecognised form is
what all three predecessors did.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from hyperset.evals import scorers
from hyperset.evals.scorers import FAILURE_CODES, PREDICATE_NAMES, Code, Score
from tests.name_resolution import UNRESOLVED, Names, bound_by, unparse

SOURCE = Path(inspect.getsourcefile(scorers))
NAMES = Names(SOURCE.parents[1])


def _branches(expression, function, seen=frozenset()) -> list[ast.AST]:
    """Every node that can reach one `code=` argument, one per BRANCH.

    A conditional contributes both arms. A bare name contributes what every
    statement in the function that BINDS it binds it to, which is the if/elif
    chain at the five-branch predicate. Chains are followed and cycles stop, so
    the result is the set of values the argument can take, one entry per way of
    taking it -- which is what "two branches sharing a code" has to be counted
    over.

    Which statements bind it is `bound_by`'s question, not this function's, and
    that is both hy-sp3z bounces. This walked `ast.Assign` targets that were
    `ast.Name`, so a tuple target bound nothing here, the resolver never saw it,
    and the RESOLVE arm below had nothing to fire on -- an enumerator keyed on how
    the BINDING is spelled, under a resolver keyed on what the expression denotes.
    Asking `bound_by` fixed that and then had the defect again internally, one
    line up, for seven more forms.

    So this states no coverage of its own. What is reported is whatever
    `bound_by` reports, and its docstring carries the mechanism; a second copy of
    that claim here is a second thing to be wrong.
    """
    if isinstance(expression, ast.IfExp):
        return _branches(expression.body, function, seen) + _branches(
            expression.orelse, function, seen
        )
    if isinstance(expression, ast.Name) and expression.id not in seen:
        bindings = [
            bound
            for statement in ast.walk(function)
            if isinstance(statement, ast.stmt)
            for name, bound in bound_by(statement).items()
            if name == expression.id
        ]
        if bindings:
            return [
                branch
                for bound in bindings
                for branch in _branches(bound, function, seen | {expression.id})
            ]
    return [expression]


def _scorer_functions() -> dict[str, list[str]]:
    """Predicate name -> the code each BRANCH of its scorer supplies, resolved.

    Keyed off the `predicate=` literal at the construction rather than off the
    function name, because the function name is a convention and the predicate
    string is the thing the rest of the harness matches on.

    A list rather than a set, because a code supplied by TWO branches is two
    defects answering to one name -- this bead's own failure one level down --
    and a set is the data structure that cannot see it.

    RESOLVED rather than pattern-matched, which is hy-sp3z. Reading `Code.X`
    attribute nodes made this the third guard in the repository keyed on how a
    value is SPELLED at the use site: critic put `_ALIAS = Code.NEVER_CALLED
    _VALIDATE` at module level, assigned `code = _ALIAS` in a sixth branch, and
    the file stayed at 21 passed. The values below come back as the strings the
    members carry, so the alias, the attribute and a bare literal are one fact.
    """
    tree = ast.parse(SOURCE.read_text())
    found: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        calls = [
            call
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "Score"
        ]
        predicates = {
            keyword.value.value
            for call in calls
            for keyword in call.keywords
            if keyword.arg == "predicate" and isinstance(keyword.value, ast.Constant)
        }
        if not predicates:
            continue
        assert len(predicates) == 1, f"{node.name} constructs Score for {sorted(predicates)}"
        found[predicates.pop()] = [
            NAMES.resolve(branch, SOURCE)
            for call in calls
            for keyword in call.keywords
            if keyword.arg == "code"
            for branch in _branches(keyword.value, node)
        ]
    return found


def test_every_predicate_has_exactly_one_scorer_and_every_scorer_a_known_predicate():
    """The mapping this file's other assertions are stated over. Two scorers
    producing one predicate name would make "the branches of this predicate" a
    set no single function owns, and the per-predicate assertions below would
    each be true of half of it."""
    assert set(_scorer_functions()) == set(PREDICATE_NAMES)


@pytest.mark.parametrize("predicate", sorted(PREDICATE_NAMES))
def test_a_scorer_mentions_exactly_its_declared_failures_and_one_passing_branch(predicate):
    """Both directions, which is what stops `FAILURE_CODES` from drifting.

    A member declared for this predicate and mentioned nowhere in its scorer is
    a branch that was deleted or renamed while the declaration kept claiming it
    -- and `expected_failures.yaml` may pin it, so the entry would be honoured
    against a branch nothing produces. A member mentioned by the scorer and not
    declared is the opposite and the worse one: a new branch shipped without
    saying whether it is a failure, which is the defect hy-1pqa is about.

    The leftover is asserted to be exactly one because every predicate here has
    one passing branch. That is the partition -- it is why no second table of
    passing codes exists to be kept in sync.
    """
    mentioned = set(_scorer_functions()[predicate])
    declared = {code.value for code in FAILURE_CODES[predicate]}

    assert declared <= mentioned, f"declared but never produced: {sorted(declared - mentioned)}"
    assert len(mentioned - declared) == 1, (
        f"{predicate} mentions {sorted(mentioned - declared)} beyond its declared failures; "
        "exactly one passing branch is expected"
    )


def test_no_code_is_shared_between_predicates_and_none_is_unused():
    """A code is only comparable if it identifies ONE branch. A member two
    scorers both produce would make a declaration ambiguous about which defect
    it pinned, and an unused member is a token a declaration could name that no
    run can ever carry."""
    owners: dict[str, list[str]] = {}
    for predicate, mentioned in _scorer_functions().items():
        for name in set(mentioned):
            owners.setdefault(name, []).append(predicate)

    assert {name: who for name, who in owners.items() if len(who) > 1} == {}
    assert set(owners) == {code.value for code in Code}


@pytest.mark.parametrize("predicate", sorted(PREDICATE_NAMES))
def test_no_two_branches_of_one_scorer_assign_the_same_code(predicate):
    """The other half of "a code identifies ONE branch", and the half this file
    could not see until it was mutated.

    The test above catches a member two SCORERS share. A member two branches of
    ONE scorer share is the same ambiguity inside a single predicate, and it is
    the closer call: adding an arm to an if/elif chain that reuses the code next
    to it is a plausible edit, and it puts two distinct defects under one name --
    hy-1pqa's own defect, one level down from the entry.

    MEASURED at 97ac35f2, from the mayor's build note. Adding a sixth arm to
    `_plan_validated_before_the_answer` assigning a code another arm there
    already assigns left this file at `12 passed`. The collector returned a SET,
    so a repeat was not a difference. It returns a list now and this assertion
    is what reads the repeats.

    Two branches that really do warrant one code are two spellings of one
    outcome and belong in one branch; there are none today, at 23 branches
    carrying 23 distinct codes.
    """
    mentioned = _scorer_functions()[predicate]

    assert sorted(mentioned) == sorted(set(mentioned)), (
        f"{predicate} assigns "
        f"{sorted({name for name in mentioned if mentioned.count(name) > 1})} "
        "from more than one branch; a code names one branch or it names nothing"
    )


@pytest.mark.parametrize("predicate", sorted(PREDICATE_NAMES))
def test_every_branch_supplies_a_code_this_suite_can_RESOLVE(predicate):
    """The arm that stops the fourth spelling, rather than waiting to be blind
    to it (hy-sp3z).

    Three guards in this repository have shipped keyed on how a value is written
    at the use site -- a quoted literal, a `Code.` attribute, a constructor
    argument -- and each was found by someone writing the next spelling. Every
    one of them failed toward GREEN, silent exactly where an author had used the
    more disciplined form.

    So the collector resolves rather than matches, and an expression it CANNOT
    follow is red here instead of absent from the counts. A branch reaching its
    code through an f-string, a lookup in a dict, or any binding this suite does
    not implement now fails with the expression quoted, which is the failure the
    three predecessors owed and did not pay. `tests/name_resolution.py` states
    what it resolves and what it does not.
    """
    unresolved = [
        unparse(branch)
        for function in ast.parse(SOURCE.read_text()).body
        if isinstance(function, ast.FunctionDef)
        for call in ast.walk(function)
        if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "Score"
        for keyword in call.keywords
        if keyword.arg == "predicate" and getattr(keyword.value, "value", None) == predicate
        for code in call.keywords
        if code.arg == "code"
        for branch in _branches(code.value, function)
        if NAMES.resolve(branch, SOURCE) is UNRESOLVED
    ]

    assert unresolved == [], (
        f"{predicate} supplies its code as {unresolved}, which this suite cannot resolve to a "
        "branch; the guard would count it as no code at all"
    )


def test_a_score_cannot_be_built_positionally():
    """`kw_only`, asserted rather than trusted to the decorator staying put.

    All twelve constructions in `scorers.py` were positional before this bead.
    Adding a field anywhere but last would have rebound four of them in silence
    -- `critical` taking a string and `explanation` a bool, both truthy enough
    to reach a wrong verdict rather than crash. This is the arm that keeps the
    next field from being able to do it.
    """
    with pytest.raises(TypeError) as raised:
        Score("run_completed", Code.ANSWERED, True, True, "built the old way")  # type: ignore[misc]

    assert "positional" in str(raised.value)
