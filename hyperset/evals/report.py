"""Scoring the committed recordings, and the exit code that follows (#25).

The required per-PR gate is this function and nothing more mysterious: read
every committed recording, refuse the ones that are not evidence, score the
rest with the deterministic predicates, and exit nonzero when the GOVERNED arm
fails a critical one. The raw baseline failing a critical predicate is the
measurement -- that is what a baseline without governed context is expected to
do -- so it is reported and does not fail the build.

Every report states what it scored. ADR 0013 requires that sentence, and it is
attached here rather than left to whoever writes the summary, because the
report that most needs the disclosure is the one whose numbers look like a live
run's.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hyperset.evals.cases import PROBES, Case, load_cases, task_version
from hyperset.evals.expected_failures import ExpectedFailure, load_expected_failures
from hyperset.evals.provenance_completeness import grade_recording_completeness
from hyperset.evals.recording import (
    ARMS,
    DISCLOSURE,
    FRONTIER_ARM,
    GOVERNED_ARM,
    Recording,
    UnreadableRecording,
    describe_run_id,
)
from hyperset.evals.run import RECORDINGS_DIR, case_recordings_dir, recordings_of
from hyperset.evals.scorers import SHARED_PREDICATES, Code, Score, critical_failures, score


@dataclass(frozen=True)
class ScoredRun:
    recording: Recording
    scores: list[Score]

    def to_dict(self) -> dict:
        return {
            "arm": self.recording.arm,
            "case_id": self.recording.case_id,
            # Rendered rather than the value, so this dict stays JSON. The
            # residual, stated rather than papered over: two schema-1 runs
            # render the identical `<no run id: schema 1>` here, so this field
            # says which run a line came from ONLY when it is a real id.
            # Deciding whether two runs are the same run is done on
            # `Recording.run_id`, which refuses instead of answering.
            "run_id": describe_run_id(self.recording.run_id),
            "task_version": self.recording.task_version,
            "git_commit": self.recording.git_commit,
            "recorded_at": self.recording.recorded_at,
            "pins": self.recording.pins.to_dict(),
            "source_refs": list(self.recording.source_refs),
            "scores": [entry.to_dict() for entry in self.scores],
        }


class MissingRecording(RuntimeError):
    """A case with no recording for an arm.

    Fatal rather than skipped: a suite that silently scores whatever happens to
    be on disk reports a rising average every time a hard case's recording is
    deleted.
    """


def score_recordings(
    *,
    directory: Path | None = None,
    cases: tuple[Case, ...] | None = None,
    expected_path: Path | None = None,
) -> dict:
    """Score every committed recording against its case. No model runs here.

    The directory is resolved at call time rather than bound as a default, so a
    test can point the gate at a suite built to fail it. A gate whose red path
    is unreachable from a test is a gate nobody has seen turn red. The
    declarations are resolved the same way and for the same reason: since
    hy-xfhr a declaration's SHAPE decides the colour, and two shapes over one
    corpus are only comparable if a caller can supply the file.

    EVERY STORED RUN OF A CASE IS SCORED, not one per (arm, case) (hy-qc4u).
    Refusing when a case has two runs would have made committing a second
    session break the required gate, which is the thing the new layout exists to
    allow. Today every case has exactly one run and no number in this report
    moves; the arm summary carries `runs` beside `cases` so that stops being an
    assumption a reader has to make.
    """
    directory = RECORDINGS_DIR if directory is None else directory
    cases = load_cases() if cases is None else cases
    runs: list[ScoredRun] = []
    for arm in ARMS:
        for case in cases:
            paths = recordings_of(arm, case.id, directory=directory)
            if not paths:
                raise MissingRecording(
                    f"no recording for case {case.id!r} on arm {arm!r} in "
                    f"{case_recordings_dir(arm, case.id, directory=directory)}; a suite that "
                    "scores whatever is on disk reports a better average every time a hard "
                    "case is deleted"
                )
            for path in paths:
                runs.append(_score_one(path, case))

    # Arm 3 (frontier) is scored ONLY when a recording is present -- it is
    # recognized but NOT required (hy-0qr6), because a live frontier run needs a
    # hosted credential and an authorized decision (Brandon fork hy-2tg6). No
    # `MissingRecording` here: an absent frontier arm is the default, and it
    # leaves the "beats frontier raw" claim withheld by construction.
    for case in cases:
        for path in recordings_of(FRONTIER_ARM, case.id, directory=directory):
            runs.append(_score_one(path, case))

    probe_of = {case.id: case.probe for case in cases}
    return _report(runs, load_expected_failures(expected_path), probe_of)


def _score_one(path: Path, case: Case) -> ScoredRun:
    """Read, verify, suite-bind and score one committed recording.

    Shared by the required arms and the optional frontier arm so the two cannot
    drift into two definitions of a valid recording.
    """
    recording = Recording.read(path)
    if recording.case_id != case.id:
        raise UnreadableRecording(
            f"{path} is filed under {case.id!r} and records case {recording.case_id!r}"
        )
    recording.verify()
    # BIND THE RECORDING TO ITS CASE'S SUITE, not just to any suite (hy-esp).
    # `verify()` -> `refuse_a_different_exam` parses the suite out of the
    # recording's OWN `task_version` and checks it against that suite's file, so a
    # recording labelled `billing@...` self-verifies against billing.yaml even
    # when it is filed under a revenue case. Two suites' recordings could then
    # SWAP their versions and still satisfy both self-verification and the
    # report's version SET. The case knows which suite it belongs to, so the
    # recording must carry THAT suite's version.
    expected = task_version(case.suite)
    if recording.task_version != expected:
        raise UnreadableRecording(
            f"{case.id!r} belongs to suite {case.suite!r} (exam {expected!r}) but its "
            f"recording carries {recording.task_version!r}; a recording labelled with "
            "another suite's version self-verifies against that suite and could swap "
            "with it, so the per-case suite binding is checked here"
        )
    return ScoredRun(recording=recording, scores=score(recording, case))


def _report(
    runs: list[ScoredRun],
    expected: tuple[ExpectedFailure, ...],
    probe_of: dict[str, str],
) -> dict:
    governed_failures = [
        {
            "case_id": run.recording.case_id,
            "predicate": entry.predicate,
            "code": entry.code.value,
            "explanation": entry.explanation,
        }
        for run in runs
        if run.recording.arm == GOVERNED_ARM
        for entry in critical_failures(run.scores)
    ]
    declared = {entry.key: entry for entry in expected}
    still_declared, repaired, outdated = _declarations(runs, declared)
    arms = {arm: _arm_summary(runs, arm, probe_of) for arm in ARMS}
    # The frontier arm's numbers are shown ONLY when it actually ran -- absent by
    # default, so the report never carries an empty arm 3 (hy-0qr6).
    if any(run.recording.arm == FRONTIER_ARM for run in runs):
        arms[FRONTIER_ARM] = _arm_summary(runs, FRONTIER_ARM, probe_of)
    report = {
        "disclosure": DISCLOSURE,
        "scored_a_recording": True,
        "arms": arms,
        "critical_governed_failures": governed_failures,
        # Known and still failing AS DECLARED: the accepted defects, each with
        # its bead and the shape of the claim it makes over the stored runs.
        "expected_failures": still_declared,
        # Failing and undeclared: a regression, or a defect nobody has filed.
        "unexpected_failures": [
            entry
            for entry in governed_failures
            if (entry["case_id"], entry["predicate"]) not in declared
        ],
        # Declared and no longer failing at all: the defect is fixed and the
        # entry outlived it. Red on purpose -- the fix deletes the line.
        "repaired_failures": repaired,
        # Still failing, but not the way the entry says: an `every` declaration
        # a run no longer exhibits, or a `some` declaration whose measured rate
        # is not what the corpus now shows. Red too, and the fix is a
        # restatement rather than a deletion (hy-xfhr).
        "outdated_declarations": outdated,
        # The governed arm's release-evidence completeness, surfaced so the benchmark job
        # shows WHETHER each governed recording's served evidence chain is 100% resolvable,
        # not only its scores (hy-u772, SS4). Governed arm only: the raw/frontier-raw arms
        # serve no governed bundle, so grading them for a governed chain is not a signal.
        # The provenance-completeness GATE (SS3) is enforced separately; this only SURFACES.
        "provenance_completeness": [
            {
                "arm": run.recording.arm,
                "case_id": run.recording.case_id,
                "complete": grade.complete,
                "missing": list(grade.missing),
            }
            for run in runs
            if run.recording.arm == GOVERNED_ARM
            # A real Recording always carries a `trace`; a record without one is graded as
            # maximally incomplete (the grader's own contract), never a crash.
            for grade in (
                grade_recording_completeness({"trace": getattr(run.recording, "trace", {}) or {}}),
            )
        ],
        "runs": [run.to_dict() for run in runs],
    }
    # WITHHELD BY DEFAULT. The 'beats frontier raw' claim is ADDED only when the
    # governed arm strictly outscored an actually-present frontier arm; a tie, a
    # loss, or an absent frontier arm adds nothing, so the report has no claim to
    # walk back (hy-0qr6, #25: 'no claim is made when it does not'). The key's
    # PRESENCE is the claim; its absence is the default.
    if _beats_frontier_raw(runs) is True:
        report["beats_frontier_raw"] = True
    return report


def _beats_frontier_raw(runs: list[ScoredRun]) -> bool | None:
    """Whether the governed arm STRICTLY outscored the frontier-raw arm on the
    shared predicate set, over the cases both ran -- or `None` when there is no
    frontier evidence to compare against.

    Returns `True` only on a strict win; a tie or a loss returns `False`, and an
    absent frontier arm returns `None`. `_report` adds the claim key only on
    `True`, so withholding is the default code path -- not a discipline a caller
    has to remember (#25 scope 1, hy-0qr6). The metric is the SHARED predicate
    set (the only set both arms attempt), restricted to the cases BOTH arms ran,
    so a frontier arm that ran fewer cases cannot flatter the governed count.
    """
    frontier = [run for run in runs if run.recording.arm == FRONTIER_ARM]
    if not frontier:
        return None
    governed = [run for run in runs if run.recording.arm == GOVERNED_ARM]
    both = {run.recording.case_id for run in frontier} & {run.recording.case_id for run in governed}

    def shared_passed(subset: list[ScoredRun]) -> int:
        return sum(
            1
            for run in subset
            if run.recording.case_id in both
            for entry in run.scores
            if entry.predicate in SHARED_PREDICATES and entry.passed
        )

    return shared_passed(governed) > shared_passed(frontier)


def _declarations(
    runs: list[ScoredRun], declared: dict[tuple[str, str], ExpectedFailure]
) -> tuple[list[dict], list[dict], list[dict]]:
    """Split the declarations into holds, repaired and outdated.

    PER DECLARATION OVER THE RUNS OF ITS OWN CASE, which is the correction
    hy-xfhr names. The rule this replaces built one set of failing keys over
    every stored run at once, so a declaration counted as still-failing if ANY
    run failed it -- and the run that failed it could be the one recorded
    before the fix. That was invisible while every case had exactly one run,
    and both are the same set at n=1, which is why nothing moved when the store
    stopped overwriting (hy-qc4u).
    """
    holds: list[dict] = []
    repaired: list[dict] = []
    outdated: list[dict] = []
    for key in sorted(declared):
        entry = declared[key]
        scored, exhibiting, codes = _observed(runs, entry)
        if entry.holds(runs_scored=scored, runs_exhibiting=exhibiting, codes=codes):
            holds.append(entry.to_dict())
            continue
        line = entry.to_dict() | {
            "runs_scored_observed": scored,
            "runs_exhibiting_observed": exhibiting,
            "codes_observed": sorted(code.value for code in codes),
            "explanation": entry.describe(
                runs_scored=scored, runs_exhibiting=exhibiting, codes=codes
            ),
        }
        (repaired if exhibiting == 0 else outdated).append(line)
    return holds, repaired, outdated


def _observed(runs: list[ScoredRun], entry: ExpectedFailure) -> tuple[int, int, frozenset[Code]]:
    """How many stored runs of the declared case SCORED that predicate, how many
    of those exhibited the declared defect, and THROUGH WHICH BRANCHES.

    The denominator counts runs the predicate was scored on rather than every
    run of the case: `score()` returns nothing for a predicate that did not
    apply, and a run that never attempted the predicate is not evidence that
    the defect is gone.

    The codes are collected from the EXHIBITING runs only. A passing run's code
    is a passing branch, and folding it in would put every declaration
    permanently at two codes -- which would red the whole file rather than the
    rows that drifted (hy-1pqa).
    """
    scored = 0
    exhibiting = 0
    codes: set[Code] = set()
    for run in runs:
        if run.recording.arm != GOVERNED_ARM or run.recording.case_id != entry.case_id:
            continue
        if not any(score.predicate == entry.predicate for score in run.scores):
            continue
        scored += 1
        failures = [
            failure
            for failure in critical_failures(run.scores)
            if failure.predicate == entry.predicate
        ]
        exhibiting += int(bool(failures))
        codes.update(failure.code for failure in failures)
    return scored, exhibiting, frozenset(codes)


def _arm_summary(runs: list[ScoredRun], arm: str, probe_of: dict[str, str]) -> dict:
    """One arm's numbers, split by what the other arm could even attempt.

    THE HEADLINE IS THE SHARED SET, and that is the difference between a
    comparison and two unrelated fractions. Arm 2 has no catalog, no directive
    and no plan check, so scoring both arms over "every predicate that applied"
    divides by different denominators and calls the quotient a delta. The
    governed-only predicates are reported beside it as capability of the
    governed path -- real, worth seeing, and not comparable to anything arm 2
    did.

    Predicates that did not apply are still counted nowhere, which is why
    `score()` returns nothing for them.

    `cases` COUNTS DISTINCT CASES AND `runs` COUNTS RECORDINGS, because since
    hy-qc4u they can differ: the store holds every session's run of a case, and
    the old count was of recordings while its name said cases. They are equal
    today at 1 run per case, which is exactly when a wrong label is invisible.

    WHAT THIS SUMMARY STILL CANNOT DO, stated because the number looks like it
    can: with two runs of one case and one of another, the predicate totals
    weight the first case twice. That is arithmetic, not a bug in the loop, and
    it is why #25's close condition asks for variance rather than for a bigger
    denominator -- filed as hy-pgtt.
    """
    arm_runs = [run for run in runs if run.recording.arm == arm]
    summary = _predicate_stats([entry for run in arm_runs for entry in run.scores])
    summary["cases"] = len({run.recording.case_id for run in arm_runs})
    summary["runs"] = len(arm_runs)
    # PARAPHRASE AND CONTROL REPORTED SEPARATELY, per step (hy-esp). A governed
    # arm that wins on controls (the domain is named) and only ties on
    # paraphrases (the planner had to route it) has not demonstrated #70's
    # claim, and one blended fraction hides exactly that. `by_probe` carries the
    # same per-predicate breakdown restricted to each probe, so the reader sees
    # WHERE the win is, not just that there was one. A probe with no runs yet
    # (paraphrase, until the human fixture lands) is absent rather than a
    # zero-over-zero that reads as a tie.
    by_probe: dict[str, dict] = {}
    for probe in PROBES:
        probe_scores = [
            entry
            for run in arm_runs
            if probe_of.get(run.recording.case_id) == probe
            for entry in run.scores
        ]
        if probe_scores:
            by_probe[probe] = _predicate_stats(probe_scores)
    summary["by_probe"] = by_probe
    return summary


def _predicate_stats(scores: list[Score]) -> dict:
    """The shared/governed-only/per-predicate breakdown of one set of scores.
    Shared by the whole-arm summary and each probe split so the two cannot drift
    apart into two different definitions of 'passed'."""
    shared = [entry for entry in scores if entry.predicate in SHARED_PREDICATES]
    governed_only = [entry for entry in scores if entry.predicate not in SHARED_PREDICATES]
    by_predicate: dict[str, dict] = {}
    for entry in scores:
        cell = by_predicate.setdefault(entry.predicate, {"passed": 0, "scored": 0})
        cell["scored"] += 1
        cell["passed"] += int(entry.passed)
    return {
        "shared_scored": len(shared),
        "shared_passed": len([entry for entry in shared if entry.passed]),
        "governed_only_scored": len(governed_only),
        "governed_only_passed": len([entry for entry in governed_only if entry.passed]),
        "predicates_scored": len(scores),
        "predicates_passed": len([entry for entry in scores if entry.passed]),
        "by_predicate": by_predicate,
    }


def failed(report: dict) -> bool:
    """Red unless the governed arm's critical failures are EXACTLY what the
    declarations claim (hy-pvbu, hy-9lct).

    Three directions since hy-xfhr, and the third is what keeps the second
    reachable once a case holds more than one run: a failure nobody declared is
    red; a declared failure that stopped failing anywhere is red, so the fix
    deletes its own entry; and a declaration the corpus no longer matches is
    red as well, so a pre-fix recording retained beside a fixed one cannot turn
    the second direction off.
    """
    return bool(
        report["unexpected_failures"]
        or report["repaired_failures"]
        or report["outdated_declarations"]
    )


def render(report: dict) -> str:
    """A person-readable summary that leads with the disclosure."""
    lines = [report["disclosure"], ""]
    lines.append(
        "Headline is the SHARED predicate set, which is the only set both arms can "
        "attempt. Governed-only predicates are the governed path's own capability and "
        "are not a comparison."
    )
    lines.append("")
    for arm, summary in report["arms"].items():
        lines.append(
            f"{arm}: shared {summary['shared_passed']}/{summary['shared_scored']}, "
            f"governed-only {summary['governed_only_passed']}/{summary['governed_only_scored']}, "
            f"over {summary['cases']} case(s) in {summary['runs']} run(s)"
        )
        for predicate, counts in sorted(summary["by_predicate"].items()):
            lines.append(f"  {predicate}: {counts['passed']}/{counts['scored']}")
    # Rendered ONLY when the report carries the claim (governed strictly won over
    # a present frontier arm). No `else` line -- a withheld claim says nothing, so
    # a reader never sees a hedge that could be mistaken for a weak claim.
    if report.get("beats_frontier_raw"):
        lines.append("")
        lines.append(
            "The governed arm OUTSCORED the pinned frontier-raw arm on the shared predicate "
            "set. (A live public claim still needs a fresh authorized frontier run with exact "
            "versions and full disclosures.)"
        )
    if report["expected_failures"]:
        lines.append("")
        lines.append(
            "KNOWN governed-arm failures, accepted and filed. These are the governed path "
            "failing its OWN structural guarantees; on every predicate both arms could "
            "attempt, the governed arm did not lose to the baseline:"
        )
        for failure in report["expected_failures"]:
            lines.append(
                f"  {failure['case_id']} {failure['predicate']} ({failure['owner']}): "
                f"{failure['reason']}"
            )
    if report["unexpected_failures"]:
        lines.append("")
        lines.append("UNDECLARED governed-arm failures -- this is the red:")
        for failure in report["unexpected_failures"]:
            lines.append(f"  {failure['case_id']} {failure['predicate']}: {failure['explanation']}")
    if report["repaired_failures"]:
        lines.append("")
        lines.append(
            "DECLARED FAILURES THAT NO LONGER FAIL -- also red. The defect is fixed and its "
            "declaration outlived it; delete the entry from expected_failures.yaml:"
        )
        for failure in report["repaired_failures"]:
            lines.append(f"  {failure['case_id']} {failure['predicate']} ({failure['owner']})")
    if report["outdated_declarations"]:
        lines.append("")
        lines.append(
            "DECLARATIONS THE STORED RUNS NO LONGER MATCH -- also red. The defect is still "
            "there, and not in the shape the entry claims; restate it against what the runs "
            "now show, or delete it:"
        )
        for failure in report["outdated_declarations"]:
            lines.append(
                f"  {failure['case_id']} {failure['predicate']} ({failure['owner']}): "
                f"{failure['explanation']}"
            )
    if report.get("provenance_completeness"):
        lines.append("")
        lines.append(
            "Provenance completeness (governed arm) -- whether each governed recording's "
            "served evidence chain is 100% resolvable, or which reference it dropped:"
        )
        for entry in report["provenance_completeness"]:
            status = (
                "complete"
                if entry["complete"]
                else "INCOMPLETE: missing " + ", ".join(entry["missing"])
            )
            lines.append(f"  {entry['case_id']}: {status}")
    return "\n".join(lines)
