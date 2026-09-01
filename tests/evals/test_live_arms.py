"""The live arms, run against real Ollama, repeated and recorded (#25, ADR 0013).

Not a per-PR gate. ADR 0013 measured one happy-path run of the pinned model at
314 seconds CPU-only, so this runs on a schedule and writes the recordings the
required gate scores.

Writing is opt-in through `HYPERSET_RECORD=1`. A benchmark that rewrites its
own evidence whenever it is executed cannot be used to detect a regression: the
recording would silently become whatever the model last did. It goes through
`write_recording`, which refuses to persist a recording pinned to a commit no
ref reaches (hy-r1i0).

EACH CASE RUNS N TIMES and the repetitions are compared (hy-2beh). #25 asks the
harness to report its stability rather than assert it, so what the repetitions
produce is a `HYPERSET-STABILITY` line and the exact answers that differed --
never a threshold, never a failure.

The first repetition is what `HYPERSET_RECORD=1` writes, and it is written
AFTER the cross-repetition check has accepted the set. Each repetition asserts
the six repository pins by value on its own, but the three host pins -- digest,
quantization, Ollama version -- are only checked for presence, so a model
re-pull or a server upgrade mid-run is drift that `stability_report` is the
first thing to see. Writing inside the loop would leave a refreshed recording
on disk behind a red run.
"""

from __future__ import annotations

import os

import pytest

from hyperset.evals.cases import load_cases
from hyperset.evals.recording import ARMS
from hyperset.evals.run import recording_path, run_case, write_recording
from hyperset.evals.scorers import critical_failures, score
from hyperset.evals.stability import configured_repetitions, repeat_and_report

CASES = load_cases()


@pytest.mark.postgres
@pytest.mark.parametrize("arm", ARMS)
@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
def test_a_live_arm_answers_and_is_recorded(case, arm, session_factory, revenue_slice, base_url):
    """One case, one arm, N identically pinned runs, one recording.

    The assertion is deliberately weak: that every repetition completed and
    produced a scoreable trace. Whether the arm BEHAVED is what the scorers
    say, and failing this test on a predicate would make the raw baseline --
    which is supposed to do worse -- a red build. Whether it behaved the SAME
    WAY twice is what the stability report says, and failing on that would be
    the gate the ruling on hy-2beh forbids.
    """

    def run_one(index: int):
        recording = run_case(case, arm=arm, session_factory=session_factory, base_url=base_url)
        recording.verify()
        scores = score(recording, case)
        assert scores, "a recording nothing scores is not evidence about anything"

        failures = critical_failures(scores)
        print(
            f"\n{arm}/{case.id} rep {index}: {len(scores) - len(failures)}/{len(scores)} predicates"
        )
        for entry in scores:
            print(f"  {'PASS' if entry.passed else 'FAIL'} {entry.predicate}: {entry.explanation}")
        return recording

    def write(recording) -> None:
        # The path comes off the recording being written, so two sessions of
        # this case land in two files rather than the second overwriting the
        # first (hy-qc4u).
        write_recording(recording, recording_path(arm, case.id, recording.run_id))

    report = repeat_and_report(
        run_one,
        case,
        repetitions=configured_repetitions(os.environ),
        record=write if os.environ.get("HYPERSET_RECORD") == "1" else None,
    )
    print(f"\n{report.render()}")
