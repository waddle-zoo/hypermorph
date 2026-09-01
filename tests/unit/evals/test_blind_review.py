"""Blind human-review presenter (hy-hntk, hy-bwo SS3): a reviewer must not be able to tell
which arm produced a trace, and the presenter must never be able to overrule the gate."""

from __future__ import annotations

from hyperset.evals.blind_review import (
    ARM_REVEALING_OPERATIONS,
    present_blind_traces,
    render_blind_trace,
    select_representative_pair,
)
from hyperset.evals.cases import load_cases
from hyperset.evals.recording import ARMS, FRONTIER_ARM, GOVERNED_ARM, RAW_ARM, Recording
from hyperset.evals.run import recordings_of
from hyperset.evals.scorers import SHARED_PREDICATES, said
from tests.unit.evals.test_report_time_purity import import_closure


def _all_committed():
    return [
        Recording.read(p) for arm in ARMS for c in load_cases() for p in recordings_of(arm, c.id)
    ]


def test_blind_traces_carry_a_representative_pass_and_fail():
    out = present_blind_traces(_all_committed(), load_cases())
    assert "Trace A" in out and "Trace B" in out
    assert ": PASS" in out and ": FAIL" in out  # one representative pass and one fail
    # The predicate under review is a SHARED one -- arm-neutral, so its verdict alone reveals
    # no arm (a governed-only predicate would).
    assert any(f"Predicate under review: {p}." in out for p in SHARED_PREDICATES)


def test_the_presenter_emits_no_arm_label_or_arm_operation_vocabulary():
    out = present_blind_traces(_all_committed(), load_cases())
    # The machine arm labels that would uniquely tag a trace are absent. (GOVERNED_ARM is not
    # asserted as a bare substring: "governed" legitimately occurs in the arm-neutral question
    # and in shared predicate names, identical across both traces, so it is not an identifier.)
    assert RAW_ARM not in out
    assert FRONTIER_ARM not in out
    # None of the arm-distinguishing tool vocabulary (governed context ops vs raw metadata
    # ops) is rendered.
    for operation in ARM_REVEALING_OPERATIONS:
        assert operation not in out, f"the arm-revealing operation {operation!r} leaked"


def test_a_governed_and_a_raw_trace_render_with_identical_arm_free_scaffolding():
    """The load-bearing blindness guarantee: for the SAME case and predicate, the governed and
    raw traces render with byte-identical scaffolding once the answer (the review subject) and
    the verdict are masked. So the presenter adds no arm-specific field -- a reviewer sees the
    same shape and cannot tell them apart by anything but the answer they are judging."""
    case = next(c for c in load_cases() if c.id == "revenue_by_region")
    gov = Recording.read(recordings_of(GOVERNED_ARM, "revenue_by_region")[0])
    raw = Recording.read(recordings_of(RAW_ARM, "revenue_by_region")[0])

    def scaffold(recording):
        rendered = render_blind_trace(recording, case, "evidence_cited", "Trace X")
        masked = rendered.replace(said(recording), "<ANSWER>")
        return masked.replace("PASS", "<V>").replace("FAIL", "<V>")

    assert scaffold(gov) == scaffold(raw), "the trace scaffolding differs by arm -- not blind"


def test_no_shared_split_says_so_rather_than_inventing_a_pair():
    # Only the governed arm (which passes every shared predicate here): no predicate has both a
    # pass and a fail, so there is no representative pair to show -- said, not invented.
    governed_only = [r for r in _all_committed() if r.arm == GOVERNED_ARM]
    assert select_representative_pair(governed_only, {c.id: c for c in load_cases()}) is None
    out = present_blind_traces(governed_only, load_cases())
    assert "No shared predicate splits the corpus" in out
    assert "Trace A" not in out


def test_the_presenter_is_read_only_and_cannot_reach_the_gate_or_a_model_runtime():
    """It is EVIDENCE, not an appeal court: its source closure names neither the report/gate
    (so it cannot flip a verdict) nor a model runtime or credential path (credential-free)."""
    closure = import_closure("hyperset.evals.blind_review")
    forbidden = (
        "hyperset.evals.report",
        "hyperset.planner.openai_runtime",
        "agents",
        "hyperset.evals.run",
        "subprocess",
    )
    reached = sorted(
        name for name in closure for bad in forbidden if name == bad or name.startswith(bad + ".")
    )
    assert not reached, f"the blind presenter must not reach {reached}"
    # It returns presentation text, never a pass/fail decision.
    assert isinstance(present_blind_traces(_all_committed(), load_cases()), str)
