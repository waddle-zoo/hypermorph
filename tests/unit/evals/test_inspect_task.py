"""The Inspect task, which is how #25's harness reports and logs (hy-ast).

Thin on purpose. The predicates are tested against traces in
`test_scorers.py` and the gate is tested in `test_the_gate.py`; what is left
here is the wiring Inspect owns -- that a sample exists for every arm and case,
that the task carries the version its cases hash to, and that the disclosure
ADR 0013 requires reaches the sample metadata rather than only the summary a
person might not read.
"""

from __future__ import annotations

import pytest
import yaml

from hyperset.evals import cases as cases_module
from hyperset.evals.cases import combined_task_version, load_cases, task_version
from hyperset.evals.recording import ARMS, DISCLOSURE
from hyperset.evals.run import recordings_of


@pytest.fixture
def recorded_arms():
    """The task builder, or a skip -- and the skip is now one PER TEST.

    The guard used to sit at module level, where it does not skip these tests:
    it removes the module from collection and leaves ONE skip entry standing in
    for all four, so an agent without the extra measures a suite four tests
    smaller than an agent with it and neither number is wrong (hy-a2ou).

    The import moves with the guard because it has to. `hyperset/evals/task.py`
    imports `inspect_ai` at top level -- measured with a `sys.meta_path` finder,
    not read off the file -- so a bare function-level `importorskip` with this
    import left at module scope would not skip anything; it would fail
    collection outright, which is worse than what was there. The other three
    imports above stay where they are: the same probe shows `cases`,
    `recording` and `run` all import without `inspect_ai`.
    """
    pytest.importorskip("inspect_ai")

    from hyperset.evals.task import recorded_arms

    return recorded_arms


def test_every_arm_answers_every_case(recorded_arms):
    """One sample per STORED RUN since hy-qc4u, so a case with two sessions
    recorded is two rows rather than one row scored twice -- and the oracle is
    what is on disk enumerated through `recordings_of`, which is the same
    enumerator the gate uses."""
    task = recorded_arms()

    assert {sample.id for sample in task.dataset} == {
        f"{arm}/{case.id}/{path.stem}"
        for arm in ARMS
        for case in load_cases()
        for path in recordings_of(arm, case.id)
    }
    assert {(sample.metadata["arm"], sample.metadata["case_id"]) for sample in task.dataset} == {
        (arm, case.id) for arm in ARMS for case in load_cases()
    }


def test_the_task_version_moves_when_the_exam_does(recorded_arms):
    """Content-addressed from the case files: two recordings claiming the same
    task version ran the same questions. The task spans every present suite, so
    its version is the COMBINED per-suite identity -- asserting the single-suite
    `task_version()` would pass only while billing.yaml is absent and break the
    moment the second suite lands (hy-esp)."""
    assert recorded_arms().version == combined_task_version()


def test_task_version_is_the_combined_identity_with_a_second_suite(
    tmp_path, monkeypatch, recorded_arms
):
    """Bound to the REAL task.py wiring with a SECOND suite actually present,
    where the combined identity differs from any single suite's -- so collapsing
    `combined_task_version()` back to `task_version()` in task.py fails HERE.
    The assertion above cannot catch that collapse on main, because with only
    revenue.yaml present the two are equal (hy-esp, adversary round 3)."""
    (tmp_path / "revenue.yaml").write_text(cases_module.CASES_PATH.read_text())
    (tmp_path / "billing.yaml").write_text(
        yaml.safe_dump(
            {
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
        )
    )
    monkeypatch.setattr(cases_module, "CASES_DIR", tmp_path)

    version = recorded_arms().version
    assert version == combined_task_version()
    # Proves two suites are in play: a collapse to a single suite equals one.
    assert version != task_version("revenue")
    assert version != task_version("billing")


def test_every_sample_carries_the_disclosure(recorded_arms):
    """A report that reads like a live model run is the defect ADR 0013's split
    creates, so the disclosure travels with the data rather than in a heading."""
    assert all(sample.metadata["disclosure"] == DISCLOSURE for sample in recorded_arms().dataset)


def test_the_arm_is_a_scored_sample_field_and_not_inferred_from_the_question(recorded_arms):
    """Both arms answer the SAME question, so nothing downstream can tell them
    apart by input -- the sample says which arm it is."""
    by_input: dict[str, set[str]] = {}
    for sample in recorded_arms().dataset:
        by_input.setdefault(sample.input, set()).add(sample.metadata["arm"])

    assert all(arms == set(ARMS) for arms in by_input.values())
