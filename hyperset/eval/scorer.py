"""The ONE deterministic domain scorer, in a shipped home (hy-myn6).

Its body is the predicate logic in `hyperset.evals.scorers` -- the nine governed
predicates plus `Score`, `Code` and `critical_failures`. That logic is what stock
Inspect AI scorers cannot express: whether a run stated the GOVERNED rule (not a
paraphrase), cited the right SOURCE IDENTITY, validated the plan before answering,
and refused (`no_match`) rather than inventing a governed answer. It is REUSED
here, not copied: this module wraps `hyperset.evals.scorers.score` in a single
Inspect `@scorer` so a customer's Inspect run and the #25 benchmark score by the
exact same predicates.

Decoupled from the bundled #25 cases on purpose: the cases are passed IN (a
customer's own suite), so this scorer bundles NO exam of its own -- the "no model
authors the exam" rule (#25) has nothing to author here.

`inspect_ai` is imported at module load, so this module is imported only behind the
optional `evals` extra (the CLI does so lazily); core Hyperset never imports it.
"""

from __future__ import annotations

from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, accuracy, scorer, stderr
from inspect_ai.solver import TaskState

from hyperset.evals.cases import Case
from hyperset.evals.recording import Recording
from hyperset.evals.scorers import critical_failures
from hyperset.evals.scorers import score as score_recording


@scorer(metrics=[accuracy(), stderr()])
def governed_context_predicates(cases: dict[str, Case], suite_versions: dict[str, str]):
    """Score a recorded run against the governed predicates.

    A sample is CORRECT only when no CRITICAL predicate failed -- a non-critical
    failure is reported in the explanation but does not fail the sample, the same
    rule the #25 gate uses. The per-predicate verdicts ride in `metadata` so a
    reader sees exactly what the trace did, not a restated pass/fail.

    Two identity binds run BEFORE any predicate, so a score always names the exact
    exam it belongs to (the #375 class of gate-integrity bug):

    - `cases` is the customer's own suite, keyed by id. A recording whose case is
      absent from the suite is INCORRECT, never dropped -- a recording that could
      not fail would be a hole in the gate.
    - `suite_versions` is the content-addressed per-suite identity of the SUPPLIED
      cases_dir (`<suite>@<hash of that dir's suite file>`). A recording's own
      `task_version` must equal the supplied suite's version, or it sat a DIFFERENT
      exam -- a shipped-suite recording against edited customer cases, or a swapped
      suite -- and is INCORRECT. This binds the exam to the customer directory, not
      to the bundled #25 suite the recording's global `verify()` would check.
    """

    async def compute(state: TaskState, target: Target) -> Score:
        recording: Recording = state.metadata["recording"]
        case = cases.get(recording.case_id)
        if case is None:
            return Score(
                value=INCORRECT,
                answer="unknown case",
                explanation=(
                    f"recording names case {recording.case_id!r}, which the supplied "
                    f"suite does not contain -- it is counted and failed, never dropped"
                ),
            )
        expected = suite_versions.get(case.suite)
        if expected is None or recording.task_version != expected:
            return Score(
                value=INCORRECT,
                answer="wrong exam",
                explanation=(
                    f"recording sat exam {recording.task_version!r}, but case "
                    f"{recording.case_id!r} belongs to the supplied {case.suite!r} suite at "
                    f"{expected!r}; an edited or swapped suite scores yesterday's answers "
                    f"against today's questions"
                ),
            )
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
                "arm": recording.arm,
                "case_id": recording.case_id,
                "scores": [entry.to_dict() for entry in scores],
                "critical_failures": [entry.to_dict() for entry in failures],
            },
        )

    return compute
