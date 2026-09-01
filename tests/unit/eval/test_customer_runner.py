"""The customer eval runner (eval migration 1/3, hy-myn6).

Exercises the relocated governed scorer and the thin Inspect task factory using
the #25 fixtures as stand-in CUSTOMER data (a suite dir + a recordings dir) -- the
runner bundles NO cases of its own, so a test supplies them, which is exactly the
customer path. No model runs: the scorer is deterministic and the solver replays.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from inspect_ai.scorer import CORRECT, INCORRECT, Target

from hyperset.eval.runner import (
    DEFAULT_TESTING_MODEL,
    _recording_samples,
    _suite_versions,
    customer_eval_task,
    replay_recording,
)
from hyperset.eval.scorer import governed_context_predicates
from hyperset.evals.cases import load_cases
from hyperset.evals.pins import PinsIncomplete
from hyperset.evals.recording import Recording

REPO = Path(__file__).resolve().parents[3]
CASES_DIR = REPO / "hyperset" / "evals" / "cases"
RECORDINGS_DIR = REPO / "hyperset" / "evals" / "recordings"
A_GOVERNED_RECORDING = RECORDINGS_DIR / "governed" / "revenue_by_region" / "unidentified.json"


def _cases_by_id() -> dict:
    return {case.id: case for case in load_cases(CASES_DIR)}


def _score(state_metadata: dict, cases: dict, suite_versions: dict | None = None):
    # Default to the SUPPLIED suite's real per-suite versions, the value a
    # legitimate recording of these cases carries -- so the exam bind passes and
    # the predicates run. A test aiming at the bind passes a wrong map explicitly.
    if suite_versions is None:
        suite_versions = _suite_versions(CASES_DIR)
    sc = governed_context_predicates(cases, suite_versions)
    state = SimpleNamespace(metadata=state_metadata)
    return asyncio.run(sc(state, Target("")))


def test_default_testing_model_is_gpt_5_6_luna():
    # The standing overseer directive: this runner selects gpt-5.6-luna, hardcoded
    # nowhere else. If this changes, the runner is selecting a different model.
    assert DEFAULT_TESTING_MODEL == "gpt-5.6-luna"


def test_the_governed_scorer_scores_a_recording_against_its_case():
    recording = Recording.read(A_GOVERNED_RECORDING)
    result = _score({"recording": recording}, _cases_by_id())
    assert result.value in (CORRECT, INCORRECT)
    # The exam bind PASSED (the recording sat this suite's version), so the scorer
    # ran the predicates rather than short-circuiting to "wrong exam".
    assert result.answer.endswith("predicates"), result.answer
    # The per-predicate verdicts ride in metadata, and at least the governed
    # predicates that apply were evaluated -- the relocated scorer is really
    # running hyperset.evals.scorers, not a stub.
    assert result.metadata["scores"], "no predicates were scored"
    assert result.metadata["case_id"] == recording.case_id
    assert all("predicate" in s and "passed" in s for s in result.metadata["scores"])


def test_a_recording_whose_case_is_absent_is_incorrect_not_a_crash():
    recording = Recording.read(A_GOVERNED_RECORDING)
    result = _score({"recording": recording}, cases={})  # empty suite
    assert result.value == INCORRECT
    assert "does not contain" in result.explanation


def test_a_recording_with_no_matching_case_is_still_a_sample_never_dropped():
    # Bug 1 (hy-myn6, #400): _recording_samples used to `continue` on an unmatched
    # case, so that recording vanished and could never fail -- a hole in the gate.
    # Every recording must become a sample; the scorer marks the unknown INCORRECT.
    all_recordings = list(RECORDINGS_DIR.rglob("*.json"))
    assert all_recordings, "fixture has no recordings"

    # A suite that contains NONE of the recorded cases: the old code produced zero
    # samples here and the task scored nothing.
    none_known = _recording_samples({}, RECORDINGS_DIR)
    assert len(none_known) == len(all_recordings)

    # Mixed known + unknown: keep exactly one case, the rest are unknown -- and the
    # count is unchanged, so no unknown recording was silently dropped.
    cases = _cases_by_id()
    one_id = next(iter(cases))
    mixed = _recording_samples({one_id: cases[one_id]}, RECORDINGS_DIR)
    assert len(mixed) == len(all_recordings)

    # And the unknown recording, forced in as a sample, is COUNTED and FAILED.
    recording = Recording.read(A_GOVERNED_RECORDING)
    unknown = _score({"recording": recording}, {"other_case": object()})
    assert unknown.value == INCORRECT
    assert "does not contain" in unknown.explanation


def test_a_recording_that_sat_a_different_suite_version_is_wrong_exam_incorrect():
    # Bug 2 (hy-myn6, #400, the #375 per-suite class): a recording must be bound to
    # the SUPPLIED cases_dir's version before scoring. An edited/swapped customer
    # suite (same suite name, different content hash) means the recording answered
    # yesterday's questions -- it must FAIL, not score against today's cases.
    recording = Recording.read(A_GOVERNED_RECORDING)
    cases = _cases_by_id()
    suite = cases[recording.case_id].suite
    wrong = {suite: f"{suite}@deadbeefdeadbeef"}  # a different exam identity
    result = _score({"recording": recording}, cases, suite_versions=wrong)
    assert result.value == INCORRECT
    assert result.answer == "wrong exam", result.answer
    # Mutation guard: if the bind is removed, the recording scores its predicates
    # and the answer becomes "<n>/<m> predicates" -- this equality then fails.

    # And the legit case: the recording's OWN suite version scores the predicates,
    # so the bind does not false-reject a real customer recording.
    right = {suite: recording.task_version}
    ok = _score({"recording": recording}, cases, suite_versions=right)
    assert ok.answer.endswith("predicates"), ok.answer


def _replay(path: Path):
    solve = replay_recording()
    state = SimpleNamespace(metadata={"recording_path": str(path)})
    return asyncio.run(solve(state, generate=None))


def test_the_replay_solver_refuses_a_recording_with_a_blank_host_pin(tmp_path):
    # hy-myn6, #400: dropping the #25 pin-VALUE comparison (correct, for customer
    # freedom) must NOT drop the pin-COMPLETENESS check. A recording whose host
    # pin is blank pinned no host identity and must fail before scoring; from_dict
    # only checks the KEYS exist, so an empty value slips past it.
    payload = json.loads(A_GOVERNED_RECORDING.read_text())
    assert payload["pins"]["digest"], "fixture already has a blank digest"
    payload["pins"]["digest"] = ""  # a run that pinned nothing
    blank = tmp_path / "blank_pin.json"
    blank.write_text(json.dumps(payload))
    with pytest.raises(PinsIncomplete):
        _replay(blank)

    # Mutation guard: a complete-pin recording (the untouched fixture) still
    # replays, so the check is not rejecting everything.
    state = _replay(A_GOVERNED_RECORDING)
    assert isinstance(state.metadata["recording"], Recording)


def test_customer_eval_task_binds_the_customer_cases_recordings_and_governed_scorer():
    task = customer_eval_task(CASES_DIR, recordings_dir=RECORDINGS_DIR)
    # A sample per recorded run whose case is in the supplied suite; nothing bundled.
    assert task.dataset, "no samples built from the customer recordings"
    suite_ids = set(_cases_by_id())
    for sample in task.dataset:
        assert sample.metadata["case_id"] in suite_ids
        assert Path(sample.metadata["recording_path"]).exists()
    # Content-addressed customer suite version, and the testing model recorded.
    assert task.version.startswith("customer@")
    assert task.metadata["testing_model"] == DEFAULT_TESTING_MODEL


def test_the_task_uses_the_selected_model_not_a_hardcoded_one():
    task = customer_eval_task(CASES_DIR, recordings_dir=RECORDINGS_DIR, model="some/other-model")
    assert task.metadata["testing_model"] == "some/other-model"


def test_the_eval_run_command_parses_and_dispatches():
    import hyperset.cli

    parser = hyperset.cli.build_parser()
    args = parser.parse_args(["eval", "run", "--cases", "c", "--recordings", "r"])
    assert args.func.__name__ == "cmd_eval_run"
    assert args.model is None  # resolves to DEFAULT_TESTING_MODEL in the command


def test_core_cli_does_not_import_the_optional_inspect_ai_extra():
    # A core `hyperset` install must acquire no eval dependency: the CLI imports
    # inspect_ai ONLY inside the eval command. Checked in a CLEAN interpreter --
    # this test module itself imports inspect_ai, so an in-process check is
    # vacuous.
    import subprocess

    probe = (
        "import sys, hyperset.cli; "
        "hyperset.cli.build_parser().parse_args("
        "['eval','run','--cases','c','--recordings','r']); "
        "assert 'inspect_ai' not in sys.modules, 'core cli imported inspect_ai'; "
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, cwd=str(REPO)
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
