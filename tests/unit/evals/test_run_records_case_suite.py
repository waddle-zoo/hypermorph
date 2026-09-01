"""run_case must stamp a recording with ITS CASE'S suite version, not a global
default (hy-esp, adversary round 3).

The gate's per-case binding (report.py) refuses a recording whose version is not
its case's suite, but that refusal only bites if run_case labels the recording
correctly in the first place. This binds the producing line directly: mutating
`task_version(case.suite)` to `task_version()` in run.py reddens this test, which
the whole suite otherwise does not catch (the corpus is single-suite).
"""

from __future__ import annotations

from types import SimpleNamespace

import yaml

from hyperset.evals import cases as cases_module
from hyperset.evals import run as run_module
from hyperset.evals.cases import CONTROL, Case, task_version
from hyperset.evals.run import run_case

BILLING_CASE = Case(
    id="billing_fetch",
    family="governed_fetch",
    question="what is billed amount by market",
    expected_domain="billing",
    must_cite=(),
    must_not_cite=(),
    must_state=(),
    requires_plan_validation=False,
    reason="",
    suite="billing",
    probe=CONTROL,
)


def _stub_run(monkeypatch, tmp_path):
    # A second suite so task_version("billing") is computable and distinct.
    (tmp_path / "revenue.yaml").write_text(cases_module.CASES_PATH.read_text())
    (tmp_path / "billing.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "suite": "billing", "cases": [{"id": "x"}]})
    )
    monkeypatch.setattr(cases_module, "CASES_DIR", tmp_path)

    spec = SimpleNamespace(instructions="i", declarations=[], executor=lambda **k: None)
    pins = SimpleNamespace(context_window=8192, to_dict=lambda: {})
    session = SimpleNamespace(run_id="run", commit="commit")
    trace = SimpleNamespace(to_dict=lambda: {"steps": []})

    monkeypatch.setattr(run_module, "arm_spec", lambda arm: spec)
    monkeypatch.setattr(run_module, "observe_pins", lambda **k: pins)
    monkeypatch.setattr(run_module, "assert_pins", lambda *a, **k: None)
    monkeypatch.setattr(run_module, "recording_session", lambda: session)
    monkeypatch.setattr(run_module, "declared_context_window", lambda *a, **k: 8192)
    monkeypatch.setattr(run_module, "OpenAIAgentsRuntime", lambda *a, **k: None)
    monkeypatch.setattr(run_module, "plan_analytics_context", lambda *a, **k: trace)
    monkeypatch.setattr(run_module, "source_refs", lambda trace_dict: ())


def test_run_case_stamps_the_recording_with_its_own_suite_version(monkeypatch, tmp_path):
    _stub_run(monkeypatch, tmp_path)

    recording = run_case(BILLING_CASE, arm="governed", session_factory=lambda: None)

    assert recording.task_version == task_version("billing")
    # The mutation to task_version() would stamp the revenue default instead.
    assert recording.task_version != task_version("revenue")
