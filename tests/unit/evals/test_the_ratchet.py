"""The expected-failure ratchet, in both directions (hy-ast).

The governed arm's first honest measurement failed two of its own five
structural guarantees (hy-pvbu, hy-9lct). Shipping that as a permanently red
REQUIRED gate would tax every other pull request in the repository to make a
point the recordings already make, so the gate compares the failure set to a
declared one and passes only on an exact match.

That is only a ratchet if it fails when the arm IMPROVES as well as when it
regresses, which is what these tests are for. A mechanism that quietly absorbed
a fix would let the declaration outlive the defect, and a list of defects that
are no longer true is how a benchmark ends up agreeing with itself.
"""

from __future__ import annotations

import json

import pytest

from hyperset.evals.cases import load_cases
from hyperset.evals.expected_failures import (
    EVERY_RUN,
    EXPECTED_FAILURES_PATH,
    FIX,
    LIMIT,
    SCHEMA_VERSION,
    SOME_RUN,
    UnusableExpectation,
    load_expected_failures,
)
from hyperset.evals.recording import ARMS, GOVERNED_ARM
from hyperset.evals.report import failed, render, score_recordings
from hyperset.evals.run import recording_path
from hyperset.evals.scorers import PREDICATE_NAMES, SHARED_PREDICATES, Code
from tests.unit.evals.test_the_gate import committed, write_suite

REPRODUCIBLE_CASE = "supply_chain_lead_time"
REPRODUCIBLE_PREDICATE = "no_governed_answer_without_a_governed_domain"
REPRODUCIBLE_CODE = Code.GOVERNED_CONTEXT_FOR_AN_UNGOVERNED_QUESTION
"""hy-9lct's declared pair, and the one `test_the_ratchet` already knows how to
repair: dropping the resolve step makes the governed arm stop answering without
a governed domain, so that run no longer exhibits the defect."""


def repaired_run(payload: dict) -> dict:
    """The same repair `test_a_declared_failure_that_stops_failing_is_ALSO_red`
    drives, as a payload transform so a suite can hold repaired and unrepaired
    runs of ONE case side by side."""
    payload = json.loads(json.dumps(payload))
    payload["trace"]["steps"] = [
        step
        for step in payload["trace"]["steps"]
        if (step.get("detail") or {}).get("operation") != "resolve_analytics_context"
    ]
    return payload


def suite_with_runs(directory, states, *, case_id=REPRODUCIBLE_CASE, mutate=None):
    """The committed suite, with `states` runs of ONE governed case.

    `states` names each stored run: a state listed in `mutate` is written
    through that transform and any other state is the recording as committed.
    So a test can build the corpus that hy-xfhr is about -- a case holding a run
    recorded before a fix and a run recorded after it -- and, with a different
    transform, the corpus hy-1pqa is about, where both runs still fail and they
    fail through different branches.
    """
    mutate = {"repaired": repaired_run} if mutate is None else mutate
    for arm in ARMS:
        for case in load_cases():
            payload = committed(arm, case.id)
            if arm == GOVERNED_ARM and case.id == case_id:
                for index, state in enumerate(states):
                    transform = mutate.get(state, lambda body: body)
                    _write(directory, arm, case.id, f"{index:016x}", transform(payload))
                continue
            _write(directory, arm, case.id, "unidentified", payload)
    return directory


def _write(directory, arm, case_id, run_id, payload):
    path = recording_path(arm, case_id, run_id, directory=directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def declaring(tmp_path, revenue=None, **fields):
    """An expected-failure file that declares the reproducible pair with
    `fields`, and the OTHER committed defect exactly as the repository declares
    it -- or, with `revenue`, with those fields overridden too.

    Both, because a file holding one entry would leave `revenue_by_region`'s
    critical failure undeclared and every suite built here would be red for a
    reason that has nothing to do with the shape under test. `revenue` exists
    for the mirror of that hazard: a test isolating the CODE has to state counts
    the corpus already satisfies, or the shape rule reddens it first and the
    code assertion rides along untested.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "expected.yaml"
    path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "expected": [
                    {
                        "case_id": "revenue_by_region",
                        "predicate": "plan_validated_before_the_answer",
                        "code": Code.DID_NOT_VALIDATE.value,
                        "retirement": FIX,
                        "beads": ["hy-pvbu"],
                        "reason": "measured",
                        "shape": SOME_RUN,
                        "runs_scored": 1,
                        "runs_exhibiting": 1,
                        **(revenue or {}),
                    },
                    {
                        "case_id": REPRODUCIBLE_CASE,
                        "predicate": REPRODUCIBLE_PREDICATE,
                        "code": REPRODUCIBLE_CODE.value,
                        "retirement": FIX,
                        "beads": ["hy-9lct"],
                        "reason": "measured",
                        **fields,
                    },
                ],
            }
        )
    )
    return path


def test_the_committed_recordings_fail_exactly_what_is_declared():
    """The gate is green because the failure set MATCHES, not because failures
    are ignored: both are still reported, with their beads."""
    report = score_recordings()

    assert failed(report) is False
    assert {entry["predicate"] for entry in report["expected_failures"]} == {
        "plan_validated_before_the_answer",
        "no_governed_answer_without_a_governed_domain",
    }
    assert {entry["owner"] for entry in report["expected_failures"]} == {
        "hy-3dtc, hy-1r0h",
        "ADR 0016",
    }
    assert report["unexpected_failures"] == []
    assert report["repaired_failures"] == []


def test_a_failure_nobody_declared_is_red(tmp_path):
    """A regression cannot hide behind a known defect."""

    def silence_the_governed_arm(arm, case_id, payload):
        if arm == GOVERNED_ARM:
            payload["trace"]["steps"] = [
                step for step in payload["trace"]["steps"] if step["kind"] != "planner_message"
            ]
        return payload

    report = score_recordings(directory=write_suite(tmp_path, silence_the_governed_arm))

    assert failed(report) is True
    assert "run_completed" in {entry["predicate"] for entry in report["unexpected_failures"]}
    assert "this is the red" in render(report)


def test_a_declared_failure_that_stops_failing_is_ALSO_red(tmp_path):
    """The half that makes this a ratchet. A defect that stops being exhibited
    means deleting its entry in the same pull request, because the harness
    refuses to pass while it claims a defect that is gone -- and that holds for
    the row ADR 0016 calls permanent too. `limit` says no PATCH retires the
    entry; it does not exempt the entry from the evidence."""

    def stop_resolving_for_the_no_match_case(arm, case_id, payload):
        if arm == GOVERNED_ARM and case_id == "supply_chain_lead_time":
            payload["trace"]["steps"] = [
                step
                for step in payload["trace"]["steps"]
                if (step.get("detail") or {}).get("operation") != "resolve_analytics_context"
            ]
        return payload

    report = score_recordings(directory=write_suite(tmp_path, stop_resolving_for_the_no_match_case))

    assert failed(report) is True
    assert [entry["owner"] for entry in report["repaired_failures"]] == ["ADR 0016"]
    assert "no longer fail" in render(report).lower()


def test_retaining_the_pre_fix_run_does_not_turn_the_repair_green(tmp_path):
    """hy-xfhr, and the arm this bead exists for.

    MEASURED AT main e95ed2a2 BEFORE THE SHAPE FIELD EXISTED: one run
    exhibiting the defect, and that same run retained beside a run where it is
    fixed, produced byte-identical reports -- `expected_failures` naming
    hy-9lct, nothing repaired, green both times. The declaration was kept alive
    by a recording made before the fix, so the ratchet's second direction
    stopped being reachable the moment a case could hold two runs (hy-qc4u).
    """
    declarations = declaring(tmp_path, shape=EVERY_RUN)
    one_failing_run = score_recordings(
        directory=suite_with_runs(tmp_path / "s1", ["exhibits"]), expected_path=declarations
    )
    the_fix_beside_it = score_recordings(
        directory=suite_with_runs(tmp_path / "s3", ["exhibits", "repaired"]),
        expected_path=declarations,
    )

    assert failed(one_failing_run) is False
    assert failed(the_fix_beside_it) is True
    assert [entry["owner"] for entry in the_fix_beside_it["outdated_declarations"]] == ["hy-9lct"]
    assert the_fix_beside_it["outdated_declarations"][0]["runs_exhibiting_observed"] == 1
    assert the_fix_beside_it["outdated_declarations"][0]["runs_scored_observed"] == 2
    assert "no longer match" in render(the_fix_beside_it).lower()


def test_the_two_shapes_are_not_the_same_rule_over_one_corpus(tmp_path):
    """Mayor's condition 2: `some` at a complete corpus is not `every` wearing
    another word, and the divergence is testable now rather than when a second
    session lands.

    ONE corpus -- two stored runs of the case, BOTH exhibiting the defect --
    scored twice against two declarations. `every` absorbs the second failing
    run without an edit. `some`, measured at one of one, does not: its
    measurement is stale the moment the corpus grows, and it says so instead of
    staying quietly green. If these two ever agree here, `some` has collapsed
    into `every` and the shape field is a word rather than a rule.
    """
    corpus = suite_with_runs(tmp_path / "corpus", ["exhibits", "exhibits"])

    as_every = score_recordings(
        directory=corpus, expected_path=declaring(tmp_path, shape=EVERY_RUN)
    )
    as_some = score_recordings(
        directory=corpus,
        expected_path=declaring(
            tmp_path / "some", shape=SOME_RUN, runs_scored=1, runs_exhibiting=1
        ),
    )

    assert failed(as_every) is False
    assert [entry["owner"] for entry in as_every["expected_failures"]] == ["hy-pvbu", "hy-9lct"]
    assert failed(as_some) is True
    assert [entry["owner"] for entry in as_some["outdated_declarations"]] == ["hy-9lct"]
    assert "1 of 1" in as_some["outdated_declarations"][0]["explanation"]
    assert "2 of 2 stored run(s) exhibit it" in as_some["outdated_declarations"][0]["explanation"]


def test_a_declared_rate_that_is_not_what_the_runs_show_is_red(tmp_path):
    """Mayor's condition 1: the counts are compared, not stored.

    A `some` entry claiming a population the corpus does not have -- one
    exhibiting run of two, against a corpus of one run that exhibits it -- is
    red. Without this the counts would be decoration: the entry would be
    honoured on the strength of its shape word alone, which is the boolean
    critic measured as hy-xfhr unfixed.
    """
    report = score_recordings(
        directory=suite_with_runs(tmp_path / "corpus", ["exhibits"]),
        expected_path=declaring(tmp_path, shape=SOME_RUN, runs_scored=2, runs_exhibiting=1),
    )

    assert failed(report) is True
    assert [entry["owner"] for entry in report["outdated_declarations"]] == ["hy-9lct"]
    assert "Re-state what the corpus now shows" in report["outdated_declarations"][0]["explanation"]


def test_a_some_declaration_no_run_exhibits_is_red(tmp_path):
    """C3. `some` is weaker than `every` and it still ratchets: a declaration
    nothing exhibits is a defect that is gone, whatever shape it claims."""
    report = score_recordings(
        directory=suite_with_runs(tmp_path / "corpus", ["repaired", "repaired"]),
        expected_path=declaring(tmp_path, shape=SOME_RUN, runs_scored=2, runs_exhibiting=1),
    )

    assert failed(report) is True
    assert [entry["owner"] for entry in report["repaired_failures"]] == ["hy-9lct"]
    assert report["outdated_declarations"] == []


def test_every_declaration_names_a_real_case_and_a_real_predicate():
    """An entry that matches nothing excuses nothing, and sits in the file
    looking like an accepted defect."""
    known_cases = {case.id for case in load_cases()}

    for entry in load_expected_failures():
        assert entry.case_id in known_cases
        assert entry.predicate in PREDICATE_NAMES
        assert all(bead.startswith("hy-") for bead in entry.beads)
        assert entry.reason


def test_every_declaration_is_a_governed_only_predicate():
    """The declared failures are the governed path failing its own additional
    promises. A shared predicate accepted here would be the baseline beating
    the governed arm on ground both could stand on, which is not a defect to
    accept -- it is the result being wrong."""
    for entry in load_expected_failures():
        assert entry.predicate not in SHARED_PREDICATES


@pytest.mark.parametrize("field", ["case_id", "predicate", "code", "reason", "shape", "retirement"])
def test_a_declaration_missing_any_field_is_refused(tmp_path, field):
    entry = {
        "case_id": "revenue_by_region",
        "predicate": "plan_validated_before_the_answer",
        "code": Code.DID_NOT_VALIDATE.value,
        "retirement": FIX,
        "beads": ["hy-pvbu"],
        "reason": "measured",
        "shape": EVERY_RUN,
    }
    del entry[field]
    path = tmp_path / "expected.yaml"
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "expected": [entry]}))

    with pytest.raises(UnusableExpectation) as raised:
        load_expected_failures(path)

    assert field in str(raised.value)


def test_a_declaration_naming_an_unknown_predicate_is_refused(tmp_path):
    """No pattern syntax, and no near-misses either: a typo would otherwise
    excuse nothing while looking like it excused something."""
    path = tmp_path / "expected.yaml"
    path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "expected": [
                    {
                        "case_id": "revenue_by_region",
                        "predicate": "plan_validated",
                        "code": Code.DID_NOT_VALIDATE.value,
                        "retirement": FIX,
                        "beads": ["hy-pvbu"],
                        "reason": "measured",
                        "shape": EVERY_RUN,
                    }
                ],
            }
        )
    )

    with pytest.raises(UnusableExpectation):
        load_expected_failures(path)


@pytest.mark.parametrize(
    ("fields", "refused"),
    [
        ({"shape": "sometimes"}, "the shapes are"),
        ({"shape": EVERY_RUN, "runs_scored": 2, "runs_exhibiting": 1}, "nothing compares"),
        ({"shape": SOME_RUN}, "must carry"),
        ({"shape": SOME_RUN, "runs_scored": 2}, "must carry"),
        ({"shape": SOME_RUN, "runs_scored": 1, "runs_exhibiting": 0}, "at least one run"),
        ({"shape": SOME_RUN, "runs_scored": 1, "runs_exhibiting": 2}, "at least one run"),
    ],
)
def test_a_shape_the_rule_cannot_apply_is_refused_by_name(tmp_path, fields, refused):
    """Mayor's condition 3: a load-time refusal names the FILE and the ENTRY.

    Coercing, defaulting or skipping a malformed entry here is worse than no
    check at all, because the report then says the corpus was scored -- and a
    reader who cannot find the entry cannot delete it either. `runs_exhibiting`
    at zero is refused for the same reason C3 exists: a declaration nothing
    exhibits is not a weaker claim, it is a claim about nothing.
    """
    path = declaring(tmp_path, **fields)

    with pytest.raises(UnusableExpectation) as raised:
        load_expected_failures(path)

    assert refused in str(raised.value)
    assert str(path) in str(raised.value)
    assert f"{REPRODUCIBLE_CASE}/{REPRODUCIBLE_PREDICATE}" in str(raised.value)


ADJUDICATED_ADR = "docs/adr/0016-declared-coverage-claim-not-question-reading.md"
"""The one accepted ADR that names a declared row -- this case and this
predicate verbatim -- which is what lets the supply-chain entry be a `limit`."""


def retiring(
    tmp_path,
    case_id=REPRODUCIBLE_CASE,
    predicate=REPRODUCIBLE_PREDICATE,
    code=REPRODUCIBLE_CODE,
    **fields,
):
    """One declaration carrying `fields`, for the arms about retirement alone."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "expected.yaml"
    path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "expected": [
                    {
                        "case_id": case_id,
                        "predicate": predicate,
                        "code": Code(code).value,
                        "reason": "measured",
                        "shape": EVERY_RUN,
                        **fields,
                    }
                ],
            }
        )
    )
    return path


@pytest.mark.parametrize(
    ("fields", "refused"),
    [
        ({"retirement": "someday", "beads": ["hy-9lct"]}, "the values are"),
        ({"retirement": FIX, "adr": ADJUDICATED_ADR}, "names an ADR"),
        ({"retirement": FIX}, "non-empty list of bead ids"),
        ({"retirement": FIX, "beads": []}, "non-empty list of bead ids"),
        ({"retirement": FIX, "beads": "hy-9lct"}, "non-empty list of bead ids"),
        ({"retirement": FIX, "beads": ["hy-9lct", 25]}, "non-empty list of bead ids"),
        ({"retirement": LIMIT, "beads": ["hy-9lct"]}, "carries `beads`"),
        ({"retirement": LIMIT}, "must name the `adr`"),
        ({"retirement": LIMIT, "adr": ""}, "must name the `adr`"),
        ({"retirement": LIMIT, "adr": "docs/adr/0000-nonexistent.md"}, "not a file in docs/adr/"),
        ({"retirement": LIMIT, "adr": "docs/0016-elsewhere.md"}, "not a file in docs/adr/"),
        ({"retirement": LIMIT, "adr": "docs/adr/README.md"}, "Status: accepted"),
    ],
)
def test_a_retirement_the_rule_cannot_apply_is_refused_by_name(tmp_path, fields, refused):
    """hy-p8k5, built like the shape arm above because the failure is the same
    one a level out: an entry whose retirement nothing can check is an excuse
    that outlives its own fix. Both shipped entries were in exactly that state
    -- naming beads that had already closed, so no fix could ever delete them --
    while the ratchet read green. Defaulting, coercing or skipping any of these
    restores that, so each is refused by name, naming the file and the entry.
    """
    path = retiring(tmp_path, **fields)

    with pytest.raises(UnusableExpectation) as raised:
        load_expected_failures(path)

    assert refused in str(raised.value)
    assert str(path) in str(raised.value)
    assert f"{REPRODUCIBLE_CASE}/{REPRODUCIBLE_PREDICATE}" in str(raised.value)


def test_a_limit_resting_on_an_ADR_that_does_not_name_the_row_is_refused(tmp_path):
    """The check that decides whether `limit` is a mechanism or an escape hatch
    (mayor's ruling on hy-p8k5).

    `fix` is the expensive branch: its beads have to be open, and landing them
    deletes the entry. `limit` costs nothing after load. So the pressure is to
    relabel an inconvenient row as a limit, and naming SOME accepted ADR is free
    -- this repository ships a directory of them. What makes the downgrade cost
    something is that the named decision must adjudicate THIS row.

    Demonstrated on the real revenue entry pointed at the real ADR 0016, which
    is accepted and does adjudicate a declared row -- the OTHER one. Nothing
    about that document is wrong or stale; it simply never mentions this case or
    this predicate, and that alone is the refusal. The supply-chain row loads
    from the same ADR in the same file shape, which is what makes this arm about
    the naming rather than about anything else in the document.
    """
    with pytest.raises(UnusableExpectation) as raised:
        load_expected_failures(
            retiring(
                tmp_path,
                case_id="revenue_by_region",
                predicate="plan_validated_before_the_answer",
                code=Code.DID_NOT_VALIDATE,
                retirement=LIMIT,
                adr=ADJUDICATED_ADR,
            )
        )

    assert "never names its case_id or predicate" in str(raised.value)
    assert "rubber-stamp" in str(raised.value)

    adjudicated = load_expected_failures(
        retiring(tmp_path / "ok", retirement=LIMIT, adr=ADJUDICATED_ADR)
    )
    assert (adjudicated[0].retirement, adjudicated[0].owner) == (LIMIT, "ADR 0016")


@pytest.mark.parametrize("version", [1, 2, 3])
def test_an_older_schema_is_refused_rather_than_defaulted(tmp_path, version):
    """The bump is the point, twice over. Loading a shapeless entry would have
    to pick a shape for it (ruling hy-5e1p); loading a schema-2 entry would have
    to pick a RETIREMENT, and defaulting to `fix` would re-assert the pending
    fix ADR 0016 says does not exist, while defaulting to `limit` would excuse
    every real defect in the file (hy-p8k5). Both are decisions the file has to
    make in words."""
    path = tmp_path / "expected.yaml"
    path.write_text(
        json.dumps(
            {
                "schema_version": version,
                "expected": [
                    {
                        "case_id": REPRODUCIBLE_CASE,
                        "predicate": REPRODUCIBLE_PREDICATE,
                        "reason": "measured",
                        "shape": EVERY_RUN,
                    }
                ],
            }
        )
    )

    with pytest.raises(UnusableExpectation) as raised:
        load_expected_failures(path)

    assert f"understands {SCHEMA_VERSION}" in str(raised.value)
    assert str(path) in str(raised.value)


def test_the_declaration_file_holds_only_the_two_accepted_defects():
    """A ratchet that grows is a demotion with extra steps. This is the line a
    reviewer checks first, and it is asserted rather than trusted."""
    assert len(load_expected_failures()) == 2
    assert "*" not in EXPECTED_FAILURES_PATH.read_text()


def test_both_shipped_declarations_state_their_shape_and_only_some_carries_counts():
    """The migration, asserted rather than left to the file's own comments
    (ruling hy-5e1p): both entries state a shape explicitly, the revenue row's
    rate is measured over the committed runs, and the supply-chain row carries
    no counts to go stale."""
    by_row = {(entry.case_id, entry.predicate): entry for entry in load_expected_failures()}
    revenue = by_row[("revenue_by_region", "plan_validated_before_the_answer")]
    supply_chain = by_row[(REPRODUCIBLE_CASE, REPRODUCIBLE_PREDICATE)]

    assert revenue.shape == SOME_RUN
    assert (revenue.runs_scored, revenue.runs_exhibiting) == (1, 1)
    assert supply_chain.shape == EVERY_RUN
    assert supply_chain.runs_scored is None
    assert supply_chain.runs_exhibiting is None


def test_both_shipped_declarations_state_what_retires_them():
    """The hy-p8k5 migration, asserted for the same reason. The revenue row is a
    pending fix and lists the OPEN beads whose landing deletes it; the
    supply-chain row is an architectural limit and points at the accepted ADR
    that says so, which is why its closed bead is a record rather than rot."""
    by_row = {(entry.case_id, entry.predicate): entry for entry in load_expected_failures()}
    revenue = by_row[("revenue_by_region", "plan_validated_before_the_answer")]
    supply_chain = by_row[(REPRODUCIBLE_CASE, REPRODUCIBLE_PREDICATE)]

    assert (revenue.retirement, revenue.beads, revenue.adr) == (FIX, ("hy-3dtc", "hy-1r0h"), None)
    assert revenue.owner == "hy-3dtc, hy-1r0h"
    assert (supply_chain.retirement, supply_chain.beads) == (LIMIT, ())
    assert supply_chain.adr == "docs/adr/0016-declared-coverage-claim-not-question-reading.md"
    assert supply_chain.owner == "ADR 0016"


DRIFTING_CASE = "revenue_by_region"
DRIFTING_PREDICATE = "plan_validated_before_the_answer"
"""hy-1pqa's own row. It is the one predicate with five branches, and the entry
declaring it has already covered two different defects under one name."""


def without_the_validate_call(payload: dict) -> dict:
    """The mechanism change this bead was filed on, replayed as a transform.

    Between two re-rolls of the same case the defect moved from a validate call
    that never happened to a call that happens and comes back unjudged. Removing
    the call from the committed recording moves it back, which reproduces the
    drift in the direction the harness has to notice.
    """
    payload = json.loads(json.dumps(payload))
    payload["trace"]["steps"] = [
        step
        for step in payload["trace"]["steps"]
        if (step.get("detail") or {}).get("operation") != "validate_analytics_plan"
    ]
    return payload


def revenue_runs(directory, states):
    """The committed suite, with `states` runs of the drifting case.

    "declared" is the recording as committed, which fails through
    `did_not_validate`; "drifted" is the same run with its validate call
    removed, which fails the SAME predicate through `never_called_validate`.
    """
    return suite_with_runs(
        directory,
        states,
        case_id=DRIFTING_CASE,
        mutate={"drifted": without_the_validate_call},
    )


def test_a_declared_failure_that_changes_MECHANISM_is_red(tmp_path):
    """THE BEAD. Same case, same predicate, same verdict, different defect.

    MEASURED AT main 2b3b53ae, BEFORE `code` EXISTED: this exact corpus scored
    green. `failed(report)` was False, `expected_failures` named the entry, and
    `repaired_failures` and `outdated_declarations` were both empty -- because
    the ratchet matched `(case_id, predicate)` and both defects answer to that
    pair. A missing validate call and a call that happens and is never judged
    are not the same defect, and the harness could not tell them apart.

    The red is not "the module gained a field". It is this corpus, which the old
    rule accepted and the new rule does not.
    """
    report = score_recordings(directory=revenue_runs(tmp_path / "drifted", ["drifted"]))

    assert failed(report) is True
    assert report["repaired_failures"] == []
    assert report["unexpected_failures"] == []
    outdated = report["outdated_declarations"]
    assert [(entry["case_id"], entry["predicate"]) for entry in outdated] == [
        (DRIFTING_CASE, DRIFTING_PREDICATE)
    ]
    assert outdated[0]["code"] == "did_not_validate"
    assert outdated[0]["codes_observed"] == ["never_called_validate"]
    assert "not the one the entry describes" in outdated[0]["explanation"]
    assert "no longer match" in render(report).lower()


def test_a_corpus_failing_one_entry_through_TWO_branches_is_red(tmp_path):
    """The mayor's ruling (a): EVERY exhibiting run carries the declared code.

    The rejected alternative was "at least one exhibiting run matches", which is
    the "at least one run failed" rule hy-xfhr already removed once, wearing a
    new field. This corpus is where the two rules differ and it is the corpus
    that matters: one run failing as declared, one failing the same predicate
    through another branch. Under the rejected rule the declared run answers for
    both and the drift is invisible on exactly the rows where drift is happening
    -- a gate that cannot fire on its motivating case.

    THE COUNTS ARE STATED TO MATCH so that they cannot be what reddens this.
    MEASURED at 2b3b53ae against the SHIPPED declaration -- some, 1 of 1 -- this
    same two-run corpus was already red, on `declared some, measured 1 of 1;
    2 of 2 stored run(s) exhibit it`. That red is hy-xfhr's shape rule firing on
    the run count and it would have passed this test for a reason predating the
    field under test. Declaring 2 of 2 satisfies the shape rule exactly, leaving
    one difference between the corpus and the declaration: the branch.
    """

    def scored(states, name):
        return score_recordings(
            directory=revenue_runs(tmp_path / name, states),
            expected_path=declaring(
                tmp_path / f"{name}-declarations",
                revenue={"shape": SOME_RUN, "runs_scored": 2, "runs_exhibiting": 2},
                shape=EVERY_RUN,
            ),
        )

    # The control the red needs: the same two-run corpus and the same
    # declaration, both runs failing through the declared branch. Green -- so
    # what reddens the corpus below is the second BRANCH and not the second run.
    assert failed(scored(["declared", "declared"], "control")) is False

    report = scored(["declared", "drifted"], "mixed")

    assert failed(report) is True
    outdated = report["outdated_declarations"]
    assert [entry["predicate"] for entry in outdated] == [DRIFTING_PREDICATE]
    assert outdated[0]["runs_exhibiting_observed"] == 2
    assert outdated[0]["runs_scored_observed"] == 2
    assert outdated[0]["codes_observed"] == ["did_not_validate", "never_called_validate"]
    assert "not the one the entry describes" in outdated[0]["explanation"]


@pytest.mark.parametrize(
    ("code", "refused"),
    [
        ("plan_was_bad", "which no scorer produces"),
        (Code.VALIDATED_AGAINST_THE_RESOLVED_BUNDLE.value, "never produces as a failure"),
        (Code.SAID_NOTHING.value, "never produces as a failure"),
    ],
)
def test_a_branch_the_predicate_never_fails_through_is_refused_by_name(tmp_path, code, refused):
    """Three ways to name a branch that can never match, refused at load.

    A code no scorer produces is a typo or a deleted branch. A PASSING code of
    the right predicate declares that the arm is expected to succeed and calls
    it an accepted defect. A real failing code belonging to ANOTHER predicate is
    the likeliest of the three, because these tokens are English and read
    plausibly out of context.

    All three leave an entry that matches nothing -- which excuses nothing while
    sitting in the file looking like an accepted defect, the failure the
    `case_id` and `predicate` refusals already exist for. Compared rather than
    coerced, and refused by name with the file and the entry.
    """
    path = retiring(
        tmp_path,
        case_id=DRIFTING_CASE,
        predicate=DRIFTING_PREDICATE,
        code=Code.DID_NOT_VALIDATE,
        retirement=FIX,
        beads=["hy-1pqa"],
    )
    path.write_text(path.read_text().replace(Code.DID_NOT_VALIDATE.value, code))

    with pytest.raises(UnusableExpectation) as raised:
        load_expected_failures(path)

    assert refused in str(raised.value)
    assert str(path) in str(raised.value)
    assert f"{DRIFTING_CASE}/{DRIFTING_PREDICATE}" in str(raised.value)


def test_both_shipped_declarations_name_the_branch_they_were_measured_on():
    """The hy-1pqa migration, asserted rather than left to the file's comments.

    The revenue row's code was MEASURED off the committed recording rather than
    read out of its reason: the validate call names a bundle_id the arm did
    resolve, so it is not `validated_an_unresolved_bundle` -- the service
    re-resolved internally and answered unjudged, which is `did_not_validate`.
    The supply-chain row's predicate has exactly one failing branch and carries
    the field anyway, because an exemption for single-branch predicates is a
    default by another name.
    """
    by_row = {(entry.case_id, entry.predicate): entry for entry in load_expected_failures()}

    assert by_row[(DRIFTING_CASE, DRIFTING_PREDICATE)].code is Code.DID_NOT_VALIDATE
    assert by_row[(REPRODUCIBLE_CASE, REPRODUCIBLE_PREDICATE)].code is REPRODUCIBLE_CODE
