"""The required per-PR gate: what it scores and when it fails (#25, ADR 0013).

The gate is the one part of this harness that can be wrong in the direction
nobody notices -- a benchmark that scores whatever is on disk, or one whose
exit code never turns red, reports a healthy model forever. So each of those
is tested against a case built to trip it.
"""

from __future__ import annotations

import json

import pytest
import yaml

from hyperset.cli import main
from hyperset.evals import cases as cases_module
from hyperset.evals.cases import load_cases, task_version
from hyperset.evals.recording import ARMS, DISCLOSURE, GOVERNED_ARM, RAW_ARM, UnreadableRecording
from hyperset.evals.report import MissingRecording, failed, render, score_recordings
from hyperset.evals.run import (
    RECORDINGS_DIR,
    UNIDENTIFIED_RUN_STEM,
    case_recordings_dir,
    recording_path,
    recordings_of,
)
from hyperset.evals.scorers import SHARED_PREDICATES


def committed(arm, case_id):
    """The one committed run of a case on an arm, refusing if there are two.

    The corpus is four schema-1 recordings, one per (arm, case), and these
    helpers copy them to build suites. If a second run of a case is ever
    committed, "the committed recording" stops naming one file and every suite
    built here would silently be built from whichever sorted first (hy-qc4u).
    """
    paths = recordings_of(arm, case_id)
    assert len(paths) == 1, (
        f"{arm}/{case_id} has {len(paths)} committed runs; this helper says THE committed "
        "recording and would quietly pick the first, so the suites built from it would stop "
        "being built from what a reader thinks they are"
    )
    return json.loads(paths[0].read_text())


def write_suite(directory, mutate=None):
    """Copy the committed recordings into `directory`, optionally mutated."""
    for arm in ARMS:
        for case in load_cases():
            payload = committed(arm, case.id)
            if mutate is not None:
                payload = mutate(arm, case.id, payload)
            path = recording_path(arm, case.id, UNIDENTIFIED_RUN_STEM, directory=directory)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload))
    return directory


def test_every_committed_recording_is_evidence_about_this_commit():
    """Runs the same verification the gate does: real runtime provenance, a
    trace that matches what the adapter reported driving, the exam this commit
    asks, and pins that are still this commit's. A prompt edit lands here
    first, and so does a case edit."""
    report = score_recordings()

    assert report["scored_a_recording"] is True
    assert report["disclosure"] == DISCLOSURE
    assert set(report["arms"]) == set(ARMS)
    assert report["arms"][GOVERNED_ARM]["cases"] == len(load_cases())
    assert report["arms"][RAW_ARM]["cases"] == len(load_cases())
    # PER-SUITE (hy-esp): each run carries its own suite's version, so a
    # revenue run says `revenue@...` and a billing run `billing@...`. Asserting
    # the single default `task_version()` would accept only revenue runs and
    # break the moment the second suite lands; the contract is that every run
    # matches the version of the suite its case belongs to.
    expected_versions = {task_version(case.suite) for case in load_cases()}
    assert {run["task_version"] for run in report["runs"]} == expected_versions


def test_a_weakened_case_invalidates_every_recording(monkeypatch, tmp_path):
    """The half of the freeze that was promised and not enforced (hy-j3ms).

    Measured on this branch before the check existed: emptying `must_state` and
    `must_cite` on `revenue_by_region` and scoring the UNCHANGED committed
    recordings gave `governed shared: 4 / 4`, `raw_baseline shared: 4 / 4`, no
    unexpected and no repaired failures, and a green gate -- the entire
    published delta erased by editing the exam. No pin moves on a case edit,
    which is why this is a separate refusal and not a pin.
    """
    payload = yaml.safe_load(cases_module.CASES_PATH.read_text())
    for case in payload["cases"]:
        if case["id"] == "revenue_by_region":
            case["must_state"] = []
            case["must_cite"] = []
    (tmp_path / "revenue.yaml").write_text(yaml.safe_dump(payload))
    # task_version now reads the suite file via CASES_DIR (per-suite, hy-esp), so
    # weakening the exam is seen by pointing the directory at the edited copy.
    monkeypatch.setattr(cases_module, "CASES_DIR", tmp_path)

    with pytest.raises(UnreadableRecording) as raised:
        score_recordings(cases=load_cases(tmp_path))

    assert "answered exam" in str(raised.value)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "#25's 'governed Ollama passes every critical predicate' is NOT met by the "
        "recordings this commit ships, and this is the honest place to say so. Two "
        "critical predicates fail, and they are the two `expected_failures.yaml` "
        "owns: on revenue_by_region qwen2.5:7b resolved governed context and DID "
        "call validate_analytics_plan, at step 4, but the call came back "
        "'unverifiable' -- it sent the plan's two dataset refs in the directive's "
        "asset_refs where the bundle's own request echoes an empty list, so it "
        "re-resolved to a different bundle (planned cb-0f5046c1de99324b, resolved "
        "cb-72b9b503948a2597), one stale_bundle violation came back, the plan was "
        "never judged, and the arm spent its final message reporting the mismatch "
        "instead of answering; on supply_chain_lead_time it resolved the revenue "
        "domain for a supplier-lead-time question, a false coverage claim no check "
        "Hyperset is allowed to run can contradict without reading the question. "
        "ONE OF hy-pvbu's TWO CONTRACT DEFECTS IS NOW EXERCISED BY A SHIPPED "
        "RECORDING and one is not, which is the half of this paragraph the "
        "re-record moved (hy-qngb, and hy-yfjk for why it is narrated at all): "
        "undeclared_field_source still occurs zero times under "
        "hyperset/evals/recordings, while the re-resolution defect is exercised by "
        "the revenue_by_region call above -- the same pair of bundle ids "
        "expected_failures.yaml now carries, and the only pair in this tree a "
        "reader can verify against a recording. These recordings hold TWO validate "
        "calls, not one, and the clean one is supply_chain_lead_time's: it carries "
        "two source_refs, is checked against the bundle the same run resolved "
        "(cb-83778e1c1b087a2f both sides), and answers warnings with two "
        "missing_required_check. Read every count here off detail.params; "
        "detail.arguments is empty in these recordings and reads as a false zero. "
        "STRICT, AND THE ONE ASSERTION BELOW IS THE "
        "ARM'S OWN RESULT: the day an arm passes, this test passes, strict xfail "
        "turns a passing xfail into a failure, and somebody deletes the marker "
        "instead of the benchmark quietly agreeing with itself. That clause was "
        "false between the ratchet and hy-a89p, and it was false BY THE MECHANISM "
        "IT DESCRIBES: this test also asserted `failed(report) is False`, so an arm "
        "that passed made both declared entries REPAIRED, `failed()` True, that "
        "assertion raised, and strict xfail absorbed the failure as an ordinary "
        "xfail -- green on the one day the marker exists to be red. Measured, then "
        "deleted. An assertion here whose truth the RATCHET decides rather than the "
        "ARM defeats strictness; test_the_ratchet.py owns the ratchet."
    ),
)
def test_the_governed_arm_passes_every_critical_predicate_on_the_committed_runs():
    """#25's acceptance, against the recordings this commit ships rather than
    against a hope.

    This sentence used to say the benchmark gate is red for the same two
    failures and that `hyperset evals score` exits 1. That was true when it was
    written (cf7ba09) and the ratchet then made it false: both failures are
    DECLARED in `expected_failures.yaml`, so `failed()` is False and the command
    exits 0 while printing them -- measured at this commit. So this marker is
    not a second copy of a red gate: it keeps #25's unmet criterion as a FAILING
    test after the ratchet turned it into accepted output, and it is the only
    thing left that goes red the day the criterion is MET, which is the state
    the ratchet cannot report as anything but two more accepted lines until
    somebody deletes them.

    Two footnotes, so neither is rediscovered as a finding. "The same two
    failures" is true of the (case_id, predicate) pairs the gate and the ratchet
    key on, and false of the mechanism: at cf7ba09 `revenue_by_region` failed
    `plan_validated_before_the_answer` by calling `validate_analytics_plan` and
    not validating, where today it never calls it. And the sentence above is
    about `hyperset evals score` only -- `scripts/gate.py` is red for neither,
    because an xfail is not a failure.

    The body is ONE assertion on purpose (hy-a89p); the reason says why a second
    one was removed rather than fixed."""
    report = score_recordings()

    assert report["critical_governed_failures"] == [], render(report)


def test_the_headline_is_the_set_both_arms_can_attempt():
    """Arm 2 has no catalog, no directive and no plan check. A single fraction
    over "every predicate that applied" divides the two arms by different
    denominators and calls the quotient a comparison."""
    report = score_recordings()

    for arm in ARMS:
        summary = report["arms"][arm]
        assert set(summary["by_predicate"]) & set(SHARED_PREDICATES)
        assert (
            summary["shared_scored"] + summary["governed_only_scored"]
            == (summary["predicates_scored"])
        )

    assert report["arms"][RAW_ARM]["governed_only_scored"] == 0
    assert report["arms"][GOVERNED_ARM]["governed_only_scored"] > 0


def test_the_rendered_report_leads_with_the_shared_set_and_says_it_is_the_headline():
    printed = render(score_recordings())

    assert printed.startswith(DISCLOSURE)
    assert "SHARED predicate set" in printed
    assert "governed: shared " in printed


def test_a_second_session_of_a_case_is_scored_rather_than_shadowing_the_first(tmp_path):
    """What the layout is FOR (hy-qc4u), read at the gate.

    Before this, a second session of a case overwrote the first and the gate
    could not have seen it. Refusing `n > 1` here would have been the loud
    option and was rejected: it would make committing the pair #25's close
    condition asks for break the required gate. So both runs are scored, and
    `runs` is reported beside `cases` -- they are equal at one run per case,
    which is exactly when a count labelled `cases` can be a count of recordings
    and nobody notices.
    """
    directory = write_suite(tmp_path)
    first = recordings_of(GOVERNED_ARM, load_cases()[0].id, directory=directory)[0]
    recording_path(GOVERNED_ARM, load_cases()[0].id, "b" * 32, directory=directory).write_text(
        first.read_text()
    )

    report = score_recordings(directory=directory)

    assert report["arms"][GOVERNED_ARM]["cases"] == len(load_cases())
    assert report["arms"][GOVERNED_ARM]["runs"] == len(load_cases()) + 1
    assert report["arms"][RAW_ARM]["runs"] == len(load_cases())
    assert "run(s)" in render(report)


def test_a_deleted_recording_fails_the_gate_rather_than_raising_the_average(tmp_path):
    """A suite that scores whatever is on disk reports a better score every
    time a hard case's recording is removed."""
    directory = write_suite(tmp_path)
    for path in recordings_of(GOVERNED_ARM, load_cases()[0].id, directory=directory):
        path.unlink()

    with pytest.raises(MissingRecording):
        score_recordings(directory=directory)


def test_a_governed_critical_failure_turns_the_gate_red(tmp_path):
    """The gate's whole job. Built by emptying the governed arm's answer, which
    is a failure no substrate can excuse.

    Read the title with the ratchet in it (hy-a89p): since `expected_failures`
    exists, a governed critical failure turns the gate red when it is
    UNDECLARED, and the two declared ones are printed and survived. The
    mutation here adds `run_completed` for every case, which nothing declares,
    so the red this asserts is that kind."""

    def silence_the_governed_arm(arm, case_id, payload):
        if arm == GOVERNED_ARM:
            payload["trace"]["steps"] = [
                step for step in payload["trace"]["steps"] if step["kind"] != "planner_message"
            ]
        return payload

    before = {
        (entry["case_id"], entry["predicate"])
        for entry in score_recordings()["critical_governed_failures"]
    }
    report = score_recordings(directory=write_suite(tmp_path, silence_the_governed_arm))
    after = {
        (entry["case_id"], entry["predicate"]) for entry in report["critical_governed_failures"]
    }

    assert failed(report) is True
    # The silencing is what added `run_completed` for every case, on top of
    # whatever the committed runs already fail. Asserted as a difference rather
    # than as an absolute set, so this test says what IT caused and stays true
    # as the arm's own behaviour changes.
    assert after - before >= {(case.id, "run_completed") for case in load_cases()}


def test_the_raw_baseline_failing_a_critical_predicate_is_the_measurement(tmp_path):
    """Arm 2 has no governed context, so it is expected to miss predicates arm
    1 passes. Failing the build on that would make the comparison unrunnable."""

    def silence_the_raw_arm(arm, case_id, payload):
        if arm == RAW_ARM:
            payload["trace"]["steps"] = [
                step for step in payload["trace"]["steps"] if step["kind"] != "planner_message"
            ]
        return payload

    before = score_recordings()["critical_governed_failures"]
    report = score_recordings(directory=write_suite(tmp_path, silence_the_raw_arm))

    # Not "the gate is green": this commit's governed recordings already fail
    # two critical predicates. The claim is the narrower one that matters --
    # breaking arm 2 adds nothing to what fails the build.
    assert report["critical_governed_failures"] == before
    raw = report["arms"][RAW_ARM]
    assert raw["shared_passed"] < raw["shared_scored"]


def test_the_cli_exits_nonzero_on_an_undeclared_governed_failure(monkeypatch, tmp_path, capsys):
    """#25's "CLI exits nonzero on a critical governed-arm failure", exercised
    through the command CI actually runs.

    Undeclared, because two critical failures ARE declared and filed
    (`expected_failures.yaml`, hy-pvbu and hy-9lct). The gate passes on an exact
    match and on nothing else, so what this proves is that a failure nobody
    accepted still turns it red."""

    def silence_the_governed_arm(arm, case_id, payload):
        if arm == GOVERNED_ARM:
            payload["trace"]["steps"] = [
                step for step in payload["trace"]["steps"] if step["kind"] != "planner_message"
            ]
        return payload

    monkeypatch.setattr(
        "hyperset.evals.report.RECORDINGS_DIR", write_suite(tmp_path, silence_the_governed_arm)
    )

    assert main(["evals", "score"]) == 1
    assert "UNDECLARED governed-arm failures -- this is the red:" in capsys.readouterr().out


def test_the_cli_scores_the_committed_recordings_and_says_what_it_scored(capsys):
    """The exit code is checked against the report rather than against a
    constant, and after the ratchet that is what keeps it honest in BOTH
    directions: this commit's recordings fail two critical governed predicates,
    both declared, so `failed()` is False and the command exits 0 today -- and
    the day an arm stops failing them they become REPAIRED and it exits 1. A
    hard-coded constant would be wrong on one side or the other, and the day it
    flipped is the day nobody would notice."""
    expected = 1 if failed(score_recordings()) else 0

    assert main(["evals", "score"]) == expected

    printed = capsys.readouterr().out
    assert printed.startswith(DISCLOSURE)
    assert "governed: shared " in printed


def test_every_recording_lives_where_the_gate_looks_for_it():
    """A recording filed under the wrong name is scored against the wrong
    case, and both files would still parse.

    Since hy-qc4u the name is a directory per case and a file per RUN, so this
    checks the directory rather than one path: every stored run of a case sits
    under that case, and every one of them records that case.
    """
    for arm in ARMS:
        for case in load_cases():
            paths = recordings_of(arm, case.id)
            assert paths, f"no committed run of {case.id!r} on {arm!r}"
            for path in paths:
                assert path.parent == case_recordings_dir(arm, case.id)
                assert path.parent.parent == RECORDINGS_DIR / arm
                assert json.loads(path.read_text())["case_id"] == case.id


BILLING_SUITE = {
    "schema_version": 1,
    "suite": "billing",
    "cases": [
        {
            "id": "billing_fetch",
            "family": "governed_fetch",
            "question": "what is billed amount by market",
            "expected_domain": "billing",
        }
    ],
}


def _two_suites(tmp_path, monkeypatch):
    """A real revenue suite plus a synthetic billing suite, pointed at by
    CASES_DIR. Returns the cases dir. billing.yaml is what the human owns
    (hy-unks); here it is a TEST fixture exercising the gate machinery, not the
    graded exam."""
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "revenue.yaml").write_text(cases_module.CASES_PATH.read_text())
    (cases_dir / "billing.yaml").write_text(yaml.safe_dump(BILLING_SUITE))
    monkeypatch.setattr(cases_module, "CASES_DIR", cases_dir)
    return cases_dir


def _write_two_suite_corpus(recordings_dir, *, swap):
    """Valid recordings for both suites, built from the committed revenue runs.

    A billing recording is a real revenue run relabelled to `billing_fetch` and
    carrying billing's version -- valid because trace/pins/git_commit are a real
    run's. With `swap=True` the two suites' `task_version`s are exchanged: each
    still SELF-VERIFIES against the other suite's file and the version SET is
    unchanged, which is exactly the swap the per-case binding must reject."""
    rev_version = task_version("revenue")
    bil_version = task_version("billing")
    rev_cases = [case for case in load_cases() if case.suite == "revenue"]
    for arm in ARMS:
        for case in rev_cases:
            payload = committed(arm, case.id)
            payload["task_version"] = bil_version if swap else rev_version
            path = recording_path(arm, case.id, UNIDENTIFIED_RUN_STEM, directory=recordings_dir)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload))
        billing = json.loads(json.dumps(committed(arm, rev_cases[0].id)))
        billing["case_id"] = "billing_fetch"
        billing["task_version"] = rev_version if swap else bil_version
        path = recording_path(arm, "billing_fetch", UNIDENTIFIED_RUN_STEM, directory=recordings_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(billing))


def test_a_valid_billing_recording_is_accepted_alongside_revenue(tmp_path, monkeypatch):
    """The gate scores a real second suite: a correctly-labelled billing
    recording is accepted and scored beside the revenue ones (hy-esp)."""
    _two_suites(tmp_path, monkeypatch)
    recordings = tmp_path / "recordings"
    _write_two_suite_corpus(recordings, swap=False)

    report = score_recordings(directory=recordings)

    assert report["arms"][GOVERNED_ARM]["cases"] == len(load_cases())
    assert "billing_fetch" in {run["case_id"] for run in report["runs"]}


def test_swapped_suite_versions_are_rejected(tmp_path, monkeypatch):
    """The correctness bug the per-case binding fixes (hy-esp, adversary): a
    revenue recording carrying billing's version and a billing recording
    carrying revenue's SELF-VERIFY and satisfy the version SET, yet each answers
    the wrong exam. Without the per-case binding this passes; with it, the gate
    refuses the recording whose version is not its own case's suite."""
    _two_suites(tmp_path, monkeypatch)
    recordings = tmp_path / "recordings"
    _write_two_suite_corpus(recordings, swap=True)

    with pytest.raises(UnreadableRecording) as raised:
        score_recordings(directory=recordings)

    assert "another suite's version" in str(raised.value)
