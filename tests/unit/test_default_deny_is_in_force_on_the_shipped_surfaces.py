"""Default-deny, on the client surfaces Hyperset itself ships (hy-9nrf).

ADR 0018 decision 5 lets an added VALUE in an existing served enumerated field
ship without moving `SCHEMA_VERSION`, and the thing that makes that safe is
default-deny: a client that meets a value it does not recognise never reads it
as approval. A rule about a party we do not control is not a mechanism, so the
same ADR binds it in two places -- published normatively in section 7 for
clients we do not write, and IN FORCE on the two surfaces that instruct the one
client we do write: `hyperset/planner/prompts/planner.md` and the served tool
descriptions in `hyperset/transport/operations.py`.

Writing the sentence is not enough. An ungated surface drifts exactly the way
the violation-code field drifted for sixteen codes (hy-ruui), so this is the
`WARNING_CODES` treatment: an assertion over both surfaces, plus companion
negative tests proving each half of the assertion can fail.

WHY THE FIELD SET IS PARSED OUT OF THE ADR rather than restated here. A third
copy of the list is a third thing to forget. Parsed, a row added to decision
5's table with no phrase mapped to it fails `test_the_adr_table_and_this_gate
_name_the_same_fields` before it can fail silently -- which is the drift this
gate exists to catch, arriving from the document rather than from the surface.

THE HALVES ARE CHECKED SEPARATELY, and that is the point rather than tidiness.
The carrying half is easy to assert and the qualifying half is not, so a gate
that covered the file rather than the rule would go green with the qualifying
rule shipped as unasserted prose beside it -- hy-ruui's shape, a green gate
whose SCOPE never reached the field. `_unmet` names each obligation, and the
negative tests below delete ONE sentence at a time to show that each name can
go red on its own.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from hyperset.bundle.discovery import PROPOSAL_OUTCOMES
from hyperset.planner.loop import planner_prompt
from hyperset.transport.operations import OPERATION_SPECS
from tests.name_resolution import UNRESOLVED, Names

PACKAGE = Path(__file__).resolve().parents[2] / "hyperset"
DISCOVERY = PACKAGE / "bundle" / "discovery.py"

ADR = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "adr"
    / "0018-schema-version-versions-the-answer-not-the-request.md"
)

CARRIES = "carries"
QUALIFIES = "qualifies"

# What each field of decision 5's table has to be called where a client can
# read it. A mapping rather than the ADR's own spelling, because the ADR names
# fields as a schema does (`observed_assets[].governance`) and an instruction to
# an agent names them as the payload reads (`observed_assets` entry's
# `governance`). Both error rows map to the same phrase: the surfaces address a
# client that has one error object in front of it and does not care which
# envelope produced it.
FIELD_PHRASES = {
    "resolution.status": ("resolution.status",),
    "PlanValidation.status": ("plan's status",),
    "observed_assets[].governance": ("observed_assets entry's governance",),
    "violations[].code": ("violations entry's code",),
    "operation error code": ("error code",),
    "HTTP error code": ("error code",),
    "violations[].severity": ("violations entry's severity",),
    "page.truncated[].reason": ("page.truncated entry's reason",),
    "resolution.warnings[].code": ("resolution.warnings entry's code",),
}

# The two halves as sentences an implementer can fail, which is why decision 5
# states them this way: "surfaced, and treated as no less blocking than the
# strictest known value" is checkable and "not ignored" was not. Alternatives
# per obligation, because the prompt speaks to a model in the second person and
# a tool description speaks to whatever reads the schema; requiring one
# spelling would make this a formatting gate.
CARRYING_OBLIGATIONS = {
    "an unknown value in a carrying field is not approved": (
        ("carries a verdict",),
        ("not approved",),
    ),
    "approval is not inferred from a refusal the client does not know": (
        ("absence of a refusal",),
    ),
}

QUALIFYING_OBLIGATIONS = {
    "an unknown value in a qualifying field does not invalidate the verdict": (
        ("qualifies a verdict",),
        ("does not invalidate a verdict you did recognise",),
        ("stays what it was",),
    ),
    "an unknown value in a qualifying field is surfaced with the answer": (
        ("surface",),
        ("never silently discarded", "never drop it"),
    ),
    "an unknown value in a qualifying field is not acted on": (
        ("do not act on it as though you understood it",),
    ),
    "an unrecognised severity is no less blocking than the strictest known": (
        ("no less blocking than the strictest severity",),
    ),
}


def _adr_fields() -> dict[str, str]:
    """Decision 5's table, as `{field: carries|qualifies}`.

    Asserted non-empty rather than trusted: a reformatted table that this regex
    stops matching would otherwise silently reduce this whole gate to checking
    two sentences, which is the failure mode of every check that parses a
    document.
    """
    rows = re.findall(
        rf"\|\s*([^|]+?)\s*\|\s*\d+\s*\|\s*({CARRIES}|{QUALIFIES})\s*\|", ADR.read_text()
    )
    fields = {field.replace("`", ""): klass for field, klass in rows}
    assert len(fields) == len(rows) >= 9, "ADR 0018 decision 5 no longer tabulates its fields"
    return fields


def _normalise(text: str) -> str:
    """One spelling of both surfaces, so the assertions read as English.

    Code markers are stripped -- backticks in the prompt, single quotes in the
    tool descriptions -- but only where they wrap a code token: a blunt
    `replace("'", "")` would turn "a plan's status" into "a plans status" and
    make every phrase above a typo.
    """
    flat = " ".join(text.replace("`", "").split()).lower()
    return re.sub(r"'([a-z_.\[\]]+)'", r"\1", flat)


def _unmet(text: str) -> set[str]:
    """Every obligation this surface does not carry, by name.

    A set of names rather than a boolean, because the mayor's ruling on this
    bead is that the check is PER HALF: a failure has to say which half went
    missing, or a gate covering only the carrying rule is indistinguishable
    from one covering both.
    """
    flat = _normalise(text)
    unmet = {
        name
        for name, clauses in {**CARRYING_OBLIGATIONS, **QUALIFYING_OBLIGATIONS}.items()
        if not all(any(phrase in flat for phrase in clause) for clause in clauses)
    }
    for field, klass in _adr_fields().items():
        if not any(phrase in flat for phrase in FIELD_PHRASES[field]):
            unmet.add(f"the {klass} field {field} is named")
    return unmet


def _surfaces() -> dict[str, str]:
    """The two shipped surfaces, as what is actually served rather than as
    files: the prompt through `planner_prompt()` and each description through
    `OPERATION_SPECS`, which is the object both transports hand a client."""
    return {
        "planner.md": planner_prompt(),
        **{f"{name} description": spec["description"] for name, spec in OPERATION_SPECS.items()},
    }


def test_the_adr_table_and_this_gate_name_the_same_fields():
    """The gate's scope, tied to the document that sets it. A field added to
    decision 5's table is a field a client must be told about, and the failure
    it should produce is this one rather than silence."""
    assert set(_adr_fields()) == set(FIELD_PHRASES)


def test_every_shipped_client_surface_carries_both_halves_of_default_deny():
    """Measured absent on `81b3b1b`: the prompt enumerated six warning codes,
    said "act on the code and not on the wording", and said nothing about a
    value it does not recognise; grepping `hyperset` for any unrecognised-value
    language returned nothing at all."""
    for surface, text in _surfaces().items():
        assert _unmet(text) == set(), f"{surface} is missing {sorted(_unmet(text))}"


def test_deleting_the_severity_sentence_alone_turns_the_gate_red():
    """The companion negative test the ADR requires, aimed at the half nobody
    had tested against a real surface.

    The carrying rule is left intact and one qualifying sentence is removed, so
    a gate that had covered the easy half and shipped the qualifying rule as
    decoration would pass this file's other tests and fail here.
    """
    for surface, text in _surfaces().items():
        without = re.sub(
            r"[^.]*no less blocking than the strictest severity[^.]*\.", "", text, flags=re.S | re.I
        )

        assert without != text, surface
        assert _unmet(without) == {
            "an unrecognised severity is no less blocking than the strictest known"
        }, surface
        assert _unmet(text) == set(), surface


def test_deleting_the_surfacing_obligation_alone_turns_the_gate_red():
    """The other half of the qualifying rule, and the one that is positive
    rather than restrictive: an undischarged caveat that is silently dropped is
    the silent-loss failure this repository's ADRs exist to eliminate."""
    for surface, text in _surfaces().items():
        without = re.sub(
            r"[^.]*(never silently discarded|never drop it)[^.]*\.", "", text, flags=re.S | re.I
        )

        assert without != text, surface
        assert "an unknown value in a qualifying field is surfaced with the answer" in _unmet(
            without
        ), surface


def test_deleting_the_carrying_sentence_alone_turns_the_gate_red():
    """The easy half, gated anyway. It is the one an old client applies to
    refuse a plan Hyperset validated, which is the named cost decision 5
    accepts rather than a side effect."""
    for surface, text in _surfaces().items():
        without = re.sub(r"[^.]*carries a verdict[^.]*\.", "", text, flags=re.S | re.I)

        assert without != text, surface
        assert "an unknown value in a carrying field is not approved" in _unmet(without), surface


def _files_naming_an_outcome() -> dict[str, dict[str, list[str]]]:
    """Every module holding an expression that DENOTES a `PROPOSAL_OUTCOMES`
    value, keyed by file, then by outcome, valued by how it was written.

    Read from the constant rather than spelled out, so a sixth outcome is in
    scope the moment it exists -- and RESOLVED rather than grepped, so it is in
    scope however it is written.

    This scanned for the quoted value until hy-sp3z. A consumer that imported
    the constant and compared against it wrote no literal and was invisible --
    the disciplined form, and in-tree idiom already: `hyperset/evals/cases.py`
    compares `family` against the `NO_MATCH` constant rather than a string.
    Critic measured it by adding such a consumer and watching all eight tests in
    this file pass.
    """
    names = Names(PACKAGE)
    found = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        hits = names.strings_named_in(path, set(PROPOSAL_OUTCOMES))
        if hits:
            found[str(path.relative_to(PACKAGE))] = hits
    return found


def test_no_shipped_code_path_branches_on_a_proposal_outcome():
    """The measured half of the version argument, and it is measured rather
    than reasoned from decision 5 (the mayor's condition on hy-xq55).

    `assist.proposal.outcome` is a served enumerated field that is in NEITHER
    place decision 5 requires -- not a row in its table, not named on either
    surface above -- so it gained a fifth value (`no_governing_domain`) with the
    number MOVING to 8 rather than resting on a precondition not in force for
    it. Publishing it costs an eval re-roll and is hy-1bh1, batched into
    hy-hj9g's pass.

    This is the half that could be measured without that cost, and the mayor's
    condition on hy-xq55 was to measure rather than reason: nothing outside the
    module that PRODUCES the value names one, so there is no branch for an
    unknown value to fall through to. It is a fact about the tree, not about
    anybody remembering the rule.

    Note what it does NOT establish. This is silence about a consumer, and
    silence is not denial for a client we do not ship. That is exactly why the
    number moved.

    A branch added later is not forbidden by this test. It is required to arrive
    with its own else, which is the conversation this failure starts -- and
    until hy-sp3z that promise was only kept for a branch written with a quoted
    literal.

    WHAT IT REACHES IS A MECHANISM, NOT A LIST OF FORMS. `strings_named_in`
    reports an expression when `Names.resolve` returns one of the outcome strings
    for it, and stays silent otherwise. So the coverage is exactly: whatever the
    resolver can follow to a string. A list of spellings would read as a promise
    and is what has been wrong twice on this bead already -- both times because a
    form absent from the list was ALSO absent from the code, and a list cannot
    show you that.

    THE SILENCE WAS THE HOLE, AND IT IS NOW PARTLY CLOSED (hy-fg5z, mayor OPTION
    B). `UNRESOLVED` and "not an outcome" are the same answer HERE, in this
    resolve-to-a-string scan. The bounded UNRESOLVED arm below --
    `test_no_shipped_branch_reaches_an_outcome_through_a_form_this_suite_cannot
    _follow` -- adds the negative half for the case a branch TESTS an outcome: a
    `Compare`/`match` where one operand resolves to an outcome and another is a
    computed string the resolver cannot follow now reds, with the form quoted,
    the way the scorer guard's RESOLVE arm does. It is BOUNDED, not the exhaustive
    whole-package budget: the package holds ~1200 unfollowable string forms, so
    an assertion over every one of them would red most future edits (measured on
    hy-fg5z), and a normal `bundle.status == NO_GOVERNING_DOMAIN` must stay green.

    STILL OUT OF REACH, and named rather than left to be discovered: a branch
    whose BOTH sides are computed (neither resolves to a known outcome, so nothing
    ties it to one); and the walrus / tuple-unpack binding forms measured below,
    which `bound_by` reports as `UNRESOLVED` on purpose --

        OUT = NO_GOVERNING_DOMAIN            resolves; a consumer naming it is caught
        _KEEP = (OUT := NO_GOVERNING_DOMAIN) reports UNRESOLVED; a later use of OUT is not

    -- because following them would AMEND `tests/name_resolution.py`'s `bound_by`
    "nothing may be added to the following cases" invariant, which is a separate,
    Overseer-blessed bead ([needs invariant blessing] amend bound_by
    following-cases). Unfollowable there is the SAFE direction: a branch reaching
    an outcome through such a form reds this file, it does not pass silently.

    And this walks `*.py`; a served surface in another format that names an
    outcome is outside it, which for this repository means
    `hyperset/planner/prompts/planner.md`. At blob f74cf0b9 that file contains
    none of the five outcome strings, so nothing is currently unguarded by the
    suffix -- but nothing checks that, and a later edit to it would not fail.
    """
    assert set(_files_naming_an_outcome()) == {str(DISCOVERY.relative_to(PACKAGE))}, (
        "a module outside discovery has an expression denoting a proposal outcome, by "
        f"literal or by name; it owes an unknown-value rule: {_files_naming_an_outcome()}"
    )


def test_the_outcome_scan_can_find_something():
    """The canary. Without it the assertion above passes just as well on a
    scan that matches nothing at all -- a misquoted constant, a changed file
    suffix, a `rglob` that walked the wrong tree, a resolver that returns
    `UNRESOLVED` for every expression it is handed -- and a vacuous scan reports
    the same clean result as a real one.

    Both directions are asserted, because resolution made the first one weaker:
    every outcome is found, AND at least one is found through a NAME rather than
    a literal. A resolver that had silently degraded to a literal scan would
    still satisfy the first.
    """
    found = _files_naming_an_outcome()
    discovery = found.get(str(DISCOVERY.relative_to(PACKAGE)), {})

    assert found, "the scan matched no file at all, so it measured nothing"
    assert set(discovery) == set(PROPOSAL_OUTCOMES)
    assert [
        outcome
        for outcome, forms in discovery.items()
        if any(not form.startswith("'") for form in forms)
    ], "every hit is a quoted literal, so the resolver measured nothing a grep would not have"


# The UNRESOLVED arm the scorer guard has and this one lacked (hy-fg5z, mayor OPTION B).
# `test_no_shipped_code_path_branches_on_a_proposal_outcome` reports only expressions the
# resolver can FOLLOW to an outcome string, and stays silent on a branch that reaches an
# outcome through a COMPUTED string it cannot follow -- an f-string, a `.join`/`.format`, or a
# mapping read at run time. That silence is the hole: the scorer guard reds on such a form via
# `test_every_branch_supplies_a_code_this_suite_can_RESOLVE`; this file had no equivalent.
#
# SCOPE, and why it is BOUNDED rather than an exhaustive whole-package UNRESOLVED assertion:
# the package holds ~1200 unfollowable string forms (f-strings alone), so waiving each is
# unmaintainable and reds most future edits (measured on hy-fg5z; mayor's ruling took OPTION B
# over that). The bounded, meaningful set is a branch that TESTS an outcome: a `Compare` or a
# `match` where ONE operand resolves to a known outcome and ANOTHER is a computed string this
# suite cannot follow. That is exactly "a consumer branches on an outcome through a form the
# resolver cannot follow"; a plain `bundle.status == NO_GOVERNING_DOMAIN` is NOT flagged (its
# runtime side is a Name/Attribute, not a computed string), so a normal consumer stays green.
#
# The walrus / tuple-unpack binding forms the bead measured stay out of reach here on purpose:
# following them would AMEND `tests/name_resolution.py`'s `bound_by` "nothing may be added to
# the following cases" invariant, which needs an Overseer blessing and is a separate bead
# ([needs invariant blessing] amend bound_by following-cases to add walrus/tuple-unpack).
# Unfollowable there is the SAFE direction: the guard would red on such a form, not pass it.
_WAIVED_COMPUTED_OUTCOME_BRANCHES: set[str] = set()


def _is_computed_string(node: ast.AST) -> bool:
    """A string this resolver reports as UNRESOLVED because it is BUILT at run time: an
    f-string, a `.join`/`.format`, or a value read from a mapping (`x[key]`). Named as the
    forms `tests/name_resolution.py` says it cannot follow, not as a denylist of nodes."""
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr in {"join", "format"}
    return isinstance(node, ast.Subscript)


def _outcome_branch_operands(node: ast.AST) -> list[ast.expr]:
    """The operands a `Compare` or a `match` tests against each other, or []. A `match`
    contributes its subject and every `case`'s value patterns (`case OUTCOME:`)."""
    if isinstance(node, ast.Compare):
        return [node.left, *node.comparators]
    if isinstance(node, ast.Match):
        operands: list[ast.expr] = [node.subject]
        for case in node.cases:
            operands.extend(
                pattern.value
                for pattern in ast.walk(case.pattern)
                if isinstance(pattern, ast.MatchValue)
            )
        return operands
    return []


def _unfollowable_outcome_branches(names: Names, path: Path, outcomes: set[str]) -> list[str]:
    """Every operand in `path` that reaches a known outcome through a computed string this
    resolver cannot follow: a `Compare`/`match` where one operand resolves to an outcome and
    another is a computed string that resolves to `UNRESOLVED`. The form, unparsed, for a
    message."""
    found: list[str] = []
    for node in ast.walk(names.tree(path)):
        operands = _outcome_branch_operands(node)
        if not any(names.resolve(operand, path) in outcomes for operand in operands):
            continue
        found.extend(
            ast.unparse(operand)
            for operand in operands
            if _is_computed_string(operand) and names.resolve(operand, path) is UNRESOLVED
        )
    return found


def _computed_outcome_branches() -> dict[str, list[str]]:
    """Every module that branches on a proposal outcome through an unfollowable computed
    string, keyed by file. Empty today, so a new such branch is what turns this red."""
    names = Names(PACKAGE)
    found: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        hits = [
            form
            for form in _unfollowable_outcome_branches(names, path, set(PROPOSAL_OUTCOMES))
            if form not in _WAIVED_COMPUTED_OUTCOME_BRANCHES
        ]
        if hits:
            found[str(path.relative_to(PACKAGE))] = sorted(set(hits))
    return found


def test_no_shipped_branch_reaches_an_outcome_through_a_form_this_suite_cannot_follow():
    """The UNRESOLVED arm (hy-fg5z, mayor OPTION B): a branch that tests an outcome through a
    computed string the resolver cannot follow is RED here, not silent -- the conversation the
    sibling `test_no_shipped_code_path_branches_on_a_proposal_outcome` docstring promises but
    could not start for a computed form. None exists today; one added later fails with the
    form quoted, and either becomes resolvable or is waived deliberately."""
    assert _computed_outcome_branches() == {}, (
        "a branch tests a proposal outcome through a computed string this suite cannot follow, "
        f"so the guard would count it as no outcome at all: {_computed_outcome_branches()}"
    )


def test_the_unfollowable_branch_arm_reds_on_a_computed_outcome_compare(tmp_path):
    """The arm's own red-green, so it is not vacuous. A consumer package compares a computed
    f-string against a known outcome AND compares a runtime value against the same outcome; the
    arm must flag ONLY the computed form -- firing on the hole, silent on a normal consumer."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "outcomes.py").write_text('NO_GOVERNING_DOMAIN = "no_governing_domain"\n')
    consumer = package / "consumer.py"
    consumer.write_text(
        "from pkg.outcomes import NO_GOVERNING_DOMAIN\n"
        "\n"
        "def consume(kind, status):\n"
        "    if f'no_{kind}_domain' == NO_GOVERNING_DOMAIN:\n"
        "        return 1\n"
        "    if status == NO_GOVERNING_DOMAIN:\n"
        "        return 2\n"
        "    return 0\n"
    )
    names = Names(package)

    hits = _unfollowable_outcome_branches(names, consumer, {"no_governing_domain"})

    # The computed f-string branch is caught; the plain `status == OUTCOME` consumer is not.
    assert hits == ["f'no_{kind}_domain'"]


def test_dropping_one_named_field_turns_the_gate_red():
    """Scope, not just wording. A surface that states both rules and then names
    five of the six carrying fields leaves a client with no rule for the sixth,
    which is precisely how a value arrives unhandled."""
    for surface, text in _surfaces().items():
        without = text.replace("observed_assets", "assets")

        assert without != text, surface
        assert "the carries field observed_assets[].governance is named" in _unmet(without), surface
