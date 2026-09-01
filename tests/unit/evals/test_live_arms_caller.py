"""The live test's own repetition loop, driven with the model faked (hy-2beh).

`tests/evals/test_live_arms.py` is the CALLER: it is the code that decides the
recording is written after the cross-repetition check rather than during the
loop. `test_the_recording_is_written_only_after_the_repetitions_agreed_on_their_pins`
in `test_stability.py` guards `repeat_and_report`, which is the mechanism -- it
cannot see a caller that stopped using it, or one that added its own write back
inside a loop of its own.

This was reported as irreducible to an hour-long live run, on the grounds that a
test would have to fake `run_case` and would then prove the fake. That was wrong
and critic proved it by running this: the marks and `parametrize` decorators only
attach attributes to the function, so the real test function is callable with no
fixture, no Postgres and no Ollama, and the ORDERING it is asked about is not a
property of the fake. What is genuinely irreducible is narrower -- that a real
model produces a scoreable trace, and that `observe_pins` agrees with a real
server.

TWO TESTS ARE NEEDED HERE AND THE FIRST VERSION SHIPPED ONE. "Nothing was
written" is satisfied by a caller that never writes, and the pre-fix control does
not close that hole -- critic measured it: reduce the shipped caller to
`record=None` and the drift test's whole body still passes while the control,
which exercises its own reimplementation, stays green either way. So the control
proves the ordering assertion can fail against a caller that writes at the wrong
TIME, and the positive test below proves the caller writes at all.
"""

from __future__ import annotations

import json
import os

import pytest

import tests.evals.test_live_arms as live
from hyperset.evals.cases import load_cases, task_version
from hyperset.evals.pins import RunPins, repository_pins
from hyperset.evals.recording import GOVERNED_ARM, RECORDING_SCHEMA_VERSION, Recording
from hyperset.evals.scorers import critical_failures, score
from hyperset.evals.stability import PinsDrifted, configured_repetitions, stability_report
from hyperset.planner.trace import PLANNER_MESSAGE

HOST = {"digest": "sha256:1c2f3d4e5a6b", "quantization": "Q4_K_M", "ollama_version": "0.32.4"}

CASE = next(case for case in load_cases() if case.id == "revenue_by_region")


def recording_of(answer: str, **pin_overrides) -> Recording:
    """A recording that passes `verify()` and scores, with no model behind it.

    `task_version()` and the repository pins are read from this commit rather
    than hard-coded: `refuse_a_different_exam` and `assert_pins` run inside the
    caller under test, so a canned value would make this test fail whenever a
    case or a prompt is edited.
    """
    pins = repository_pins(GOVERNED_ARM)
    return Recording(
        run_id="c" * 32,
        schema_version=RECORDING_SCHEMA_VERSION,
        arm=GOVERNED_ARM,
        case_id=CASE.id,
        task_version=task_version(),
        git_commit="a" * 40,
        recorded_at="2026-07-29T00:00:00+00:00",
        pins=RunPins(**{**pins, **HOST, **pin_overrides}),
        trace={
            "prompt_hash": pins["prompt_hash"],
            "tools_hash": pins["tools_hash"],
            "provenance": {
                "runtime": "openai_agents_sdk",
                "instructions_hash": pins["prompt_hash"],
                "tools_hash": pins["tools_hash"],
            },
            "steps": [
                {
                    "kind": PLANNER_MESSAGE,
                    "at": "2026-07-29T00:00:00+00:00",
                    "detail": {"text": answer},
                    "summary": "",
                }
            ],
        },
        source_refs=[],
    )


class DriftingModel:
    """A mid-run model re-pull: the last repetition carries a new digest.

    Drift the three host pins rather than the six repository ones, because
    `assert_pins` compares the repository pins by value on every repetition and
    checks the host pins for PRESENCE only -- so this is drift that nothing but
    the cross-repetition check can see, which is the whole reason the write
    moved after it.
    """

    def __init__(self, repetitions: int) -> None:
        self.repetitions = repetitions
        self.calls = 0

    def __call__(self, case, *, arm, session_factory, base_url) -> Recording:
        self.calls += 1
        if self.calls >= self.repetitions:
            return recording_of("an answer", digest="sha256:a_republished_digest")
        return recording_of("an answer")


class SteadyModel:
    """A model that does not drift, answering differently every repetition.

    The answers differ so the artifact says WHICH repetition was recorded: a
    model repeating one string would let a caller that wrote the LAST repetition
    pass the positive test below.
    """

    def __init__(self, repetitions: int) -> None:
        self.repetitions = repetitions
        self.calls = 0

    def __call__(self, case, *, arm, session_factory, base_url) -> Recording:
        self.calls += 1
        return recording_of(f"answer {self.calls}")


def pre_fix_caller(case, arm, session_factory, revenue_slice, base_url):
    """The caller as it was at c9788f0: write inside the loop, compare after.

    The negative control for ORDER, and only for order: it is what makes
    `assert not written.exists()` above a claim that can fail, by exhibiting a
    caller against which it does fail. What it does NOT do -- measured, and the
    reason this file has a positive test -- is catch a caller that never writes.
    Reduce the shipped caller to `record=None` and the drift test's whole body
    passes, while this control stays green either way, because it exercises its
    own reimplementation rather than the shipped code.
    """
    recordings = []
    for index in range(configured_repetitions(os.environ)):
        recording = live.run_case(case, arm=arm, session_factory=session_factory, base_url=base_url)
        recording.verify()
        scores = score(recording, case)
        assert scores
        critical_failures(scores)
        if os.environ.get("HYPERSET_RECORD") == "1" and index == 0:
            path = live.recording_path(arm, case.id, recording.run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            recording.write(path)
        recordings.append(recording)
    return stability_report(recordings, case)


def _write_unchecked(recording: Recording, path) -> None:
    """`write_recording` without its provenance check, for these fakes only.

    The recordings here are hand-built and pin `"a" * 40`, so the shipped
    persist path would refuse them for naming a commit that resolves nowhere --
    which is the check working, and not what this file is asking about. What
    it refuses, and that it refuses before the file exists, is measured against
    real repositories in `tests/unit/evals/test_provenance.py`; substituted here
    so the ORDER of the write stays the only thing under test.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    recording.write(path)


def _wire(monkeypatch, tmp_path, make_model):
    """`HYPERSET_RECORD=1`, the inference faked, the recorder pointed at tmp.

    The repetition count is read after `REPETITIONS_ENV` is cleared and handed
    to the model, so a model that must drift on the LAST repetition stays
    correct whatever the ambient environment says.
    """
    monkeypatch.setenv("HYPERSET_RECORD", "1")
    monkeypatch.setattr(live, "write_recording", _write_unchecked)
    monkeypatch.delenv("HYPERSET_STABILITY_REPETITIONS", raising=False)
    model = make_model(configured_repetitions(os.environ))
    monkeypatch.setattr(live, "run_case", model)
    written = tmp_path / GOVERNED_ARM / CASE.id / "written.json"

    # Three arguments since hy-qc4u, and the run id is asserted rather than
    # swallowed: a fake that accepted `*args` would keep passing if the caller
    # stopped deriving the path from the recording it is about to write, which
    # is the one way two runs land back in one file.
    def only_this_run(arm, case_id, run_id, **kwargs):
        assert (arm, case_id) == (GOVERNED_ARM, CASE.id)
        assert run_id, "the caller must name the run it is writing"
        return written

    monkeypatch.setattr(live, "recording_path", only_this_run)
    return model, written


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """The real live test with a mid-run re-pull faked and nothing on disk yet."""
    return _wire(monkeypatch, tmp_path, DriftingModel)


@pytest.fixture
def steady(monkeypatch, tmp_path):
    """The same wiring, with a model that does not drift."""
    return _wire(monkeypatch, tmp_path, SteadyModel)


def test_the_live_caller_writes_nothing_when_a_later_repetition_drifts(wired):
    """Every repetition runs, the drift is refused, and the disk is untouched."""
    model, written = wired

    with pytest.raises(PinsDrifted):
        live.test_a_live_arm_answers_and_is_recorded(
            CASE, GOVERNED_ARM, session_factory=None, revenue_slice=None, base_url="unused"
        )

    assert model.calls == model.repetitions, "every repetition must run before the comparison"
    assert not written.exists(), f"a recording was written before the drift was detected: {written}"


def test_the_live_caller_writes_the_first_repetition_when_the_set_held(steady):
    """The positive half, without which "nothing was written" is free.

    A caller with `record=` deleted passes the drift test above and the control
    below, so this is the only test in the file that fails when the recording
    disappears. It asserts WHICH repetition landed as well as that one did:
    `repeat_and_report` records `recordings[0]`, and a caller that wrote the
    last repetition instead would write today's answer over the artifact the
    gate scores.
    """
    model, written = steady

    live.test_a_live_arm_answers_and_is_recorded(
        CASE, GOVERNED_ARM, session_factory=None, revenue_slice=None, base_url="unused"
    )

    assert model.calls == model.repetitions, "every repetition must run"
    assert written.exists(), f"HYPERSET_RECORD=1 and a set that held must write: {written}"
    recorded = json.loads(written.read_text())["trace"]["steps"][0]["detail"]["text"]
    assert recorded == "answer 1", f"the FIRST repetition is the recorded one, got {recorded!r}"


def test_the_same_assertions_are_red_against_the_pre_fix_caller(wired):
    """What the ORDER defect looked like, so the drift test cannot pass vacuously."""
    model, written = wired

    with pytest.raises(PinsDrifted):
        pre_fix_caller(
            CASE, GOVERNED_ARM, session_factory=None, revenue_slice=None, base_url="unused"
        )

    assert model.calls == model.repetitions
    assert written.exists(), (
        "the pre-fix caller wrote repetition 0 inside the loop; if this is green the control no "
        "longer reproduces the defect and the test above proves less than it claims"
    )
