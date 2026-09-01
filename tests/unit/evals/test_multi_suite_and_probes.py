"""hy-esp (hy-gh-25 part 2): the multi-suite loader, the hidden-paraphrase
guard, and the per-step per-arm report split by probe.

These exercise the HARNESS machinery with synthetic suites and a CONTROL from
the existing revenue domain. They do NOT author a governed exam -- the second
graded domain is human-owned (hy-unks) and no model, including this test,
writes it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from hyperset.evals import cases as cases_module
from hyperset.evals.cases import (
    CONTROL,
    PARAPHRASE,
    PRIMARY_SUITE,
    UnusableCase,
    combined_task_version,
    discover_suites,
    load_cases,
    load_suite,
    task_version,
)
from hyperset.evals.report import _arm_summary
from hyperset.evals.scorers import Code, Score
from hyperset.planner.trace import content_hash

REVENUE = cases_module.CASES_DIR / "revenue.yaml"


def _suite(path, *, suite, cases, domain_literals=None):
    payload = {"schema_version": 1, "suite": suite, "cases": cases}
    if domain_literals is not None:
        payload["domain_literals"] = domain_literals
    path.write_text(yaml.safe_dump(payload))


def _fetch(case_id, *, question, probe, domain="acme"):
    return {
        "id": case_id,
        "family": "governed_fetch",
        "question": question,
        "expected_domain": domain,
        "probe": probe,
    }


def test_discover_suites_tolerates_the_unwritten_second_suite():
    # Only revenue.yaml exists on main; billing.yaml is hy-unks and absent. Its
    # absence is not an error (the ruling: guard/skip, no hard-fail).
    assert discover_suites() == (PRIMARY_SUITE,)


def test_revenue_task_version_is_byte_identical_to_pre_part_2():
    # A revenue recording must still verify: the per-suite value is exactly what
    # the single-suite `revenue@<hash>` produced before part 2.
    assert task_version("revenue") == f"revenue@{content_hash(REVENUE.read_text())}"
    # One suite today, so the combined identity is that one value unchanged.
    assert combined_task_version() == task_version("revenue")


def test_existing_revenue_cases_are_controls_by_default():
    for case in load_cases():
        assert case.suite == "revenue"
        assert case.probe == CONTROL


def test_paraphrase_naming_its_domain_is_refused(tmp_path):
    _suite(
        tmp_path / "acme.yaml",
        suite="acme",
        cases=[_fetch("p1", question="What is acme revenue by region?", probe=PARAPHRASE)],
    )
    with pytest.raises(UnusableCase) as raised:
        load_suite(tmp_path / "acme.yaml")
    assert "domain literal" in str(raised.value)
    assert "acme" in str(raised.value)


def test_hidden_paraphrase_is_accepted(tmp_path):
    _suite(
        tmp_path / "acme.yaml",
        suite="acme",
        cases=[
            _fetch(
                "p1",
                question="What did we actually earn in Canada last month?",
                probe=PARAPHRASE,
            )
        ],
    )
    (case,) = load_suite(tmp_path / "acme.yaml")
    assert case.probe == PARAPHRASE


def test_a_paraphrase_may_not_leak_another_suites_domain(tmp_path):
    # The literal set is collected across ALL suites, so a paraphrase in one
    # suite cannot name another suite's domain either.
    _suite(
        tmp_path / "revenue.yaml",
        suite="revenue",
        cases=[
            _fetch("r1", question="recognized revenue by region", probe=CONTROL, domain="revenue")
        ],
    )
    _suite(
        tmp_path / "billing.yaml",
        suite="billing",
        cases=[
            _fetch(
                "b1",
                question="which revenue number should finance use?",
                probe=PARAPHRASE,
                domain="billing",
            )
        ],
    )
    with pytest.raises(UnusableCase) as raised:
        load_cases(tmp_path)
    assert "revenue" in str(raised.value)


def test_duplicate_ids_across_suites_are_refused(tmp_path):
    _suite(tmp_path / "a.yaml", suite="a", cases=[_fetch("dup", question="q a", probe=CONTROL)])
    _suite(tmp_path / "b.yaml", suite="b", cases=[_fetch("dup", question="q b", probe=CONTROL)])
    with pytest.raises(UnusableCase) as raised:
        load_cases(tmp_path)
    assert "duplicate case ids" in str(raised.value)


def _score(predicate, passed):
    return Score(
        predicate=predicate,
        code=next(iter(Code)),
        passed=passed,
        critical=False,
        explanation="",
    )


def _run(arm, case_id, scores):
    return SimpleNamespace(recording=SimpleNamespace(arm=arm, case_id=case_id), scores=scores)


def test_report_splits_each_step_by_probe():
    # Governed arm passes the shared predicate on a control but fails it on a
    # paraphrase -- the split is exactly what makes that visible.
    runs = [
        _run("governed", "ctrl", [_score("evidence_cited", True)]),
        _run("governed", "para", [_score("evidence_cited", False)]),
    ]
    probe_of = {"ctrl": CONTROL, "para": PARAPHRASE}
    summary = _arm_summary(runs, "governed", probe_of)

    assert set(summary["by_probe"]) == {CONTROL, PARAPHRASE}
    assert summary["by_probe"][CONTROL]["by_predicate"]["evidence_cited"] == {
        "passed": 1,
        "scored": 1,
    }
    assert summary["by_probe"][PARAPHRASE]["by_predicate"]["evidence_cited"] == {
        "passed": 0,
        "scored": 1,
    }
    # The blended number would read 1/2 and hide that the paraphrase failed.
    assert summary["shared_passed"] == 1
    assert summary["shared_scored"] == 2


def test_report_omits_a_probe_with_no_runs():
    # Until the paraphrase fixture lands, only controls run; a zero-over-zero
    # paraphrase row would read as a tie, so it is absent, not empty.
    runs = [_run("governed", "ctrl", [_score("evidence_cited", True)])]
    summary = _arm_summary(runs, "governed", {"ctrl": CONTROL})
    assert set(summary["by_probe"]) == {CONTROL}


def test_combined_task_version_spans_every_present_suite(tmp_path, monkeypatch):
    # The contract the two edited gate/inspect tests encode, made non-latent
    # here where a SECOND suite actually exists: combined_task_version joins
    # both per-suite identities, and each suite's own version is distinct. The
    # single-suite assertions passed only because billing.yaml is absent on
    # main; this fails if combined ever collapses to one suite (hy-esp).
    _suite(
        tmp_path / "revenue.yaml",
        suite="revenue",
        cases=[_fetch("r1", question="q r", probe=CONTROL, domain="revenue")],
    )
    _suite(
        tmp_path / "billing.yaml",
        suite="billing",
        cases=[_fetch("b1", question="q b", probe=CONTROL, domain="billing")],
    )
    monkeypatch.setattr(cases_module, "CASES_DIR", tmp_path)

    assert discover_suites() == ("billing", "revenue")
    assert task_version("revenue") != task_version("billing")
    assert combined_task_version() == "+".join([task_version("billing"), task_version("revenue")])
