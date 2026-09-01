"""Blind human-review presenter (hy-hntk, hy-bwo SS3, #25 scope 2).

The deterministic scorers are the release gate. Human review is EVIDENCE ABOUT WHETHER THE
SCORERS MEASURE WHAT WE INTEND -- not an appeal court: it renders no verdict of its own and
can never overrule a failing gate into a pass. So a reviewer must judge a trace on its
merits, and to keep that judgement honest they must not know WHICH ARM produced it: a
reviewer who knows "this is the governed arm" is primed to read a pass into it.

This renders a representative PASS trace and a representative FAIL trace with the arm
IDENTITY REMOVED -- no arm label, and none of the arm-distinguishing tool vocabulary
(governed context ops vs raw metadata ops) or governed-only predicates. Both traces are the
SAME question (arm-neutral) and are shown in an identical shape, so the only thing that
differs is the answer under review and the scorer's verdict on it -- which is exactly what a
reviewer is asked to agree or disagree with, blind to the arm.
"""

from __future__ import annotations

from hyperset.evals.scorers import SHARED_PREDICATES, said, score

# The tokens that would reveal which arm produced a trace, forbidden from the rendered
# output: the arm labels and the two arms' distinguishing operation vocabularies.
ARM_REVEALING_OPERATIONS = (
    "list_context_catalog",
    "discover_analytics_context",
    "resolve_analytics_context",
    "validate_analytics_plan",
    "expand_analytics_context",
    "list_raw_assets",
    "get_raw_asset",
)


def _shared_verdicts(recording, case) -> dict[str, bool]:
    """This recording's pass/fail on the SHARED predicate set -- the only predicates both
    arms attempt, so a verdict on one never reveals the arm the way a governed-only predicate
    would."""
    return {
        s.predicate: s.passed for s in score(recording, case) if s.predicate in SHARED_PREDICATES
    }


def select_representative_pair(recordings, case_of):
    """`(passing_recording, failing_recording, predicate)` for the FIRST shared predicate that
    some recording passes and another fails -- deterministic (shared predicates in fixed
    order, recordings by case id), and arm-blind (chosen on a shared predicate, never on the
    arm). None when no shared predicate splits the corpus into a pass and a fail."""
    ordered = sorted(recordings, key=lambda r: (r.case_id, r.arm))
    verdicts = {id(r): _shared_verdicts(r, case_of[r.case_id]) for r in ordered}
    for predicate in SHARED_PREDICATES:
        passers = [r for r in ordered if verdicts[id(r)].get(predicate) is True]
        failers = [r for r in ordered if verdicts[id(r)].get(predicate) is False]
        if passers and failers:
            return passers[0], failers[0], predicate
    return None


def render_blind_trace(recording, case, predicate, label) -> str:
    """One trace, arm-anonymized: a blind label, the arm-neutral question, the answer under
    review, and the scorer's verdict on the predicate under review. Never the arm, the tool
    vocabulary, or a governed-only predicate."""
    verdict = _shared_verdicts(recording, case).get(predicate)
    return "\n".join(
        [
            label,
            f"Question: {recording.trace.get('question', '')}",
            "Answer:",
            said(recording),
            f"Scorer verdict ({predicate}): {'PASS' if verdict else 'FAIL'}",
        ]
    )


def present_blind_traces(recordings, cases) -> str:
    """Render a representative PASS and FAIL trace, arm-anonymized, for blind human review.

    READ-ONLY evidence: it returns presentation text and no gate decision, so it cannot turn
    a failing deterministic gate into a pass. When no shared predicate splits the corpus, it
    says so rather than inventing a pair.
    """
    case_of = {case.id: case for case in cases}
    recordings = [r for r in recordings if r.case_id in case_of]
    pair = select_representative_pair(recordings, case_of)
    header = (
        "Blind human-review traces (arm identity removed). The deterministic scorer is the "
        "release gate; this is evidence about whether it measures what we intend, and never "
        "overrules a failing gate into a pass."
    )
    if pair is None:
        return header + "\n\nNo shared predicate splits the corpus into a pass and a fail."
    passer, failer, predicate = pair
    return "\n\n".join(
        [
            f"{header}\nPredicate under review: {predicate}.",
            render_blind_trace(passer, case_of[passer.case_id], predicate, "Trace A"),
            render_blind_trace(failer, case_of[failer.case_id], predicate, "Trace B"),
        ]
    )
