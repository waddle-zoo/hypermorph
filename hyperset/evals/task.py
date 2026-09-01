"""The Inspect AI task (GitHub #25).

Inspect owns the dataset, the run log and the score plumbing; this project owns
what a case is, what a recording is and what a predicate means. The task below
scores COMMITTED RECORDINGS, which is what ADR 0013 made the required gate, so
its solver calls no model at all -- it replays what an arm did. Running it
needs no hosted credential and no local model, and every sample carries the
disclosure saying so.

    inspect eval hyperset/evals/task.py --model mockllm/model

The model argument is Inspect's, not this task's: the solver never generates.
`mockllm/model` is the way to satisfy the runner without a credential, and if
it were ever asked to generate, a mock's answer would fail every predicate
rather than quietly pass one.

The live arms are not a task here. They are a recorder, not an exam sat in CI
(`hyperset.evals.run`, `tests/evals`), and folding them in would put a
314-second model run behind a command whose entire promise is that it needs no
model.
"""

from __future__ import annotations

from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, accuracy, scorer, stderr
from inspect_ai.solver import Generate, TaskState, solver

from hyperset.evals.cases import combined_task_version, load_cases
from hyperset.evals.recording import ARMS, DISCLOSURE, Recording
from hyperset.evals.run import recordings_of
from hyperset.evals.scorers import critical_failures
from hyperset.evals.scorers import score as score_recording


@solver
def replay_the_recording():
    """Put the recorded run where the scorer can read it. No generation.

    The recording is read here rather than at dataset build time so that a
    recording that is not evidence -- a scripted fixture, a drifted pin -- fails
    the sample it belongs to instead of the whole task, and the log says which
    case it was.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        recording = Recording.read(Path(state.metadata["recording_path"]))
        recording.verify()
        state.metadata["recording"] = recording
        return state

    return solve


@scorer(metrics=[accuracy(), stderr()])
def deterministic_predicates():
    """#25's release gate: every predicate, and no model judging anything."""
    cases = {case.id: case for case in load_cases()}

    async def compute(state: TaskState, target: Target) -> Score:
        recording: Recording = state.metadata["recording"]
        case = cases[recording.case_id]
        scores = score_recording(recording, case)
        failures = critical_failures(scores)
        return Score(
            value=CORRECT if not failures else INCORRECT,
            answer=f"{len(scores) - len(failures)}/{len(scores)} predicates",
            explanation="\n".join(
                f"{'PASS' if entry.passed else 'FAIL'} {entry.predicate}: {entry.explanation}"
                for entry in scores
            ),
            metadata={
                "disclosure": DISCLOSURE,
                "arm": recording.arm,
                "pins": recording.pins.to_dict(),
                "scores": [entry.to_dict() for entry in scores],
            },
        )

    return compute


@task
def recorded_arms() -> Task:
    """Every committed recording of every arm, scored.

    ONE SAMPLE PER STORED RUN rather than per (arm, case) (hy-qc4u). A case can
    now hold more than one session's run, and a dataset keyed on the pair would
    score one of them and leave the rest unread while still reporting that it
    scored the arm. The sample id carries the run so two runs of one case are
    two rows in the log rather than one row scored twice.
    """
    return Task(
        dataset=[
            Sample(
                input=case.question,
                target=case.id,
                id=f"{arm}/{case.id}/{path.stem}",
                metadata={
                    "arm": arm,
                    "case_id": case.id,
                    "recording_path": str(path),
                    "disclosure": DISCLOSURE,
                },
            )
            for arm in ARMS
            for case in load_cases()
            for path in recordings_of(arm, case.id)
        ],
        solver=replay_the_recording(),
        scorer=deterministic_predicates(),
        version=combined_task_version(),
        metadata={"disclosure": DISCLOSURE, "scored_a_recording": True},
    )
