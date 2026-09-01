"""The thinnest Inspect AI wrapper for a customer eval (hy-myn6).

A customer points at THEIR own cases and their recorded runs of a governed
Hyperset deployment; this builds an Inspect `Task` that scores those runs with the
one governed scorer. Inspect owns the run loop, the dataset iteration, the log and
the reporting -- this module owns only the wiring, so it stays deliberately thin.

Two things are customer-supplied and NOTHING is bundled (the "no model authors the
exam" rule has nothing to author here): the `cases` directory (a suite of `*.yaml`
in the same shape `hyperset.evals.cases` parses) and a `recordings` directory of
runs to score.

The TESTING MODEL is `gpt-5.6-luna` (`DEFAULT_TESTING_MODEL`), per the standing
directive: wherever this runner selects a model it selects that one, and the CLI
passes it to Inspect as the eval model. Scoring a RECORDED run needs no model
(the scorer is deterministic and the solver replays); DRIVING the model live to
PRODUCE the recordings is the credential-blocked follow-on (hy-bwo), so the model
is named here and used by Inspect's run loop, not hardcoded to anything else.
"""

from __future__ import annotations

from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.solver import Generate, TaskState, solver

from hyperset.eval.scorer import governed_context_predicates
from hyperset.evals.cases import load_cases
from hyperset.evals.recording import Recording
from hyperset.planner.trace import content_hash

DEFAULT_TESTING_MODEL = "gpt-5.6-luna"
"""The standing testing model for the customer runner (overseer directive). Named
once here so no call site hardcodes a different one; Inspect resolves the provider
from the customer's environment."""


def _suite_version(cases_dir: Path) -> str:
    """A content-addressed identity of the customer's suite, so an edited case
    moves the task version (a score must know which questions it belongs to). The
    bundled `hyperset.evals.cases.task_version` is pinned to the #25 suite dir, so
    this computes the same KIND of value over the customer's own files instead."""
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(cases_dir.glob("*.yaml")):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"customer@{digest.hexdigest()[:16]}"


def _suite_versions(cases_dir: Path) -> dict[str, str]:
    """The content-addressed identity of EACH suite file in the SUPPLIED cases_dir,
    keyed by suite name -- the same `<suite>@<content_hash>` shape a recording's own
    `task_version` carries (`hyperset.evals.cases.task_version`), computed over the
    customer's files rather than the bundled #25 dir.

    The scorer binds each recording to `suite_versions[case.suite]` BEFORE scoring
    (hy-myn6, #400): a recording whose `task_version` disagrees sat a DIFFERENT exam
    -- an edited or swapped customer suite -- and is INCORRECT. This is the #375
    per-suite bind, aimed at the customer runner instead of the bundled suite the
    recording's own `verify()` would check.
    """
    return {
        path.stem: f"{path.stem}@{content_hash(path.read_text())}"
        for path in sorted(cases_dir.glob("*.yaml"))
    }


@solver
def replay_recording():
    """Put a customer's recorded run where the scorer can read it. No generation.

    Checked here rather than at dataset-build time so a recording that is not
    evidence -- a scripted fixture, a trace that disagrees with what it ran --
    fails the sample it belongs to and the log names that case, instead of
    failing the whole task.

    INTEGRITY ONLY, NOT the full `verify()`:

    - `refuse_a_fixture` and `refuse_a_mismatched_record` -- a scripted trace or
      one that disagrees with what it ran is not evidence, for any recording.
    - `refuse_unrecorded_host_pins` -- the pin-COMPLETENESS half: a recording
      whose `digest`/`quantization`/`ollama_version` is blank pinned no host
      identity and must not reach scoring (hy-myn6, #400).

    `verify()` additionally runs `refuse_a_different_exam` and the full
    `assert_pins`, both #25-specific: the first checks the BUNDLED #25
    `task_version` (the exam bind is done in the scorer against the SUPPLIED
    cases_dir instead, the #375 per-suite class), and `assert_pins` compares the
    repository pins to the #25 ARM spec and would reject a customer's own
    recording. Only the pin-VALUE comparison is dropped -- the completeness check
    is kept above.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        recording = Recording.read(Path(state.metadata["recording_path"]))
        recording.refuse_a_fixture()
        recording.refuse_a_mismatched_record()
        recording.refuse_unrecorded_host_pins()
        state.metadata["recording"] = recording
        return state

    return solve


def _recording_samples(cases_by_id: dict, recordings_dir: Path) -> list[Sample]:
    """One sample per recorded run. EVERY recording becomes a sample.

    Recordings are matched to cases by the `case_id` INSIDE each recording, not by
    filename, so a customer's own layout works. A recording naming a case the suite
    does not contain is NOT dropped here (hy-myn6, #400): a silently skipped
    recording is a hole in the gate -- it could never fail. It becomes a sample
    with no case question, and the scorer marks the unknown case INCORRECT, so it
    is counted and failed. The contract is that an unknown case is INCORRECT, and a
    drop would contradict it.
    """
    samples: list[Sample] = []
    for path in sorted(recordings_dir.rglob("*.json")):
        case_id = Recording.read(path).case_id
        case = cases_by_id.get(case_id)
        samples.append(
            Sample(
                input=case.question if case is not None else "",
                target=case.id if case is not None else case_id,
                id=f"{case_id}/{path.stem}",
                metadata={"case_id": case_id, "recording_path": str(path)},
            )
        )
    return samples


def customer_eval_task(
    cases_dir: Path,
    *,
    recordings_dir: Path,
    model: str = DEFAULT_TESTING_MODEL,
) -> Task:
    """An Inspect `Task` scoring a customer's recorded runs against their own cases.

    `cases_dir` is a suite of `*.yaml` (the `hyperset.evals.cases` shape);
    `recordings_dir` holds the runs to score. Both are the customer's; nothing is
    bundled. `model` is the eval model Inspect will use (default `gpt-5.6-luna`);
    the deterministic scorer and the replay solver do not call it, so scoring
    recorded runs needs no credential.
    """
    cases = load_cases(cases_dir)
    cases_by_id = {case.id: case for case in cases}
    return Task(
        dataset=_recording_samples(cases_by_id, recordings_dir),
        solver=replay_recording(),
        scorer=governed_context_predicates(cases_by_id, _suite_versions(cases_dir)),
        version=_suite_version(cases_dir),
        metadata={"testing_model": model, "cases_dir": str(cases_dir)},
    )


@task
def customer_task() -> Task:
    """Inspect entrypoint driven by env: `HYPERSET_EVAL_CASES` and
    `HYPERSET_EVAL_RECORDINGS`. The CLI (`hyperset eval run`) sets these and calls
    Inspect, so `inspect eval` and the CLI build the SAME task."""
    import os

    cases_dir = Path(os.environ["HYPERSET_EVAL_CASES"])
    recordings_dir = Path(os.environ["HYPERSET_EVAL_RECORDINGS"])
    model = os.environ.get("HYPERSET_EVAL_MODEL", DEFAULT_TESTING_MODEL)
    return customer_eval_task(cases_dir, recordings_dir=recordings_dir, model=model)
