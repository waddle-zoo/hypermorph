"""hy-0qr6 (hy-bwo SS2, #25 arm 3): the frontier arm's vocabulary, its
recognized-but-NOT-required recording, and report.py withholding the
'beats frontier raw' claim by default.

The withholding is tested at the report layer with SYNTHETIC scored runs rather
than committed recordings, on purpose. This slice is CREDENTIAL-FREE and commits
no frontier recording -- a live frontier run needs a hosted credential and the
pinned frontier-model identity that infra decision hy-2tg6 (#141) owns, so no
run built here claims a frontier model's pins. What is under test is the CONTROL
FLOW: the claim key exists only on a strict governed win, and its ABSENCE (not a
False a reader must remember to check) is the default. The one end-to-end test
scores the real committed corpus, which ships no frontier arm, to prove the
default holds without anyone arranging it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hyperset.evals.arms import arm_spec
from hyperset.evals.recording import (
    ARMS,
    FRONTIER_ARM,
    GOVERNED_ARM,
    RAW_ARM,
    RECOGNIZED_ARMS,
    Recording,
    UnreadableRecording,
)
from hyperset.evals.report import (
    ScoredRun,
    _beats_frontier_raw,
    _report,
    render,
    score_recordings,
)
from hyperset.evals.scorers import SHARED_PREDICATES, Code, Score

SHARED = SHARED_PREDICATES[0]
OTHER_SHARED = SHARED_PREDICATES[1]


def _score(predicate: str, passed: bool) -> Score:
    # critical=False throughout so no synthetic run contributes a
    # critical_governed_failures line; these runs exercise the arm-to-arm
    # comparison, not the gate's exit code.
    return Score(
        predicate=predicate,
        code=Code.ANSWERED if passed else Code.RUN_FAILED,
        passed=passed,
        critical=False,
        explanation="",
    )


def _run(arm: str, case_id: str, scores: list[Score]) -> ScoredRun:
    """A scored run whose recording carries only what the report reads.

    A SimpleNamespace rather than a real Recording because the point under test
    is _report/_beats_frontier_raw, which read arm, case_id and the scores --
    never a pin. Building a real frontier Recording here would have to invent the
    pinned frontier model this slice deliberately does not choose.
    """
    recording = SimpleNamespace(
        arm=arm,
        case_id=case_id,
        run_id=f"{arm}-{case_id}",
        task_version="revenue@x",
        git_commit="deadbeef",
        recorded_at="2026-01-01T00:00:00Z",
        pins=SimpleNamespace(to_dict=lambda: {}),
        source_refs=[],
    )
    return ScoredRun(recording=recording, scores=scores)


def _report_of(runs: list[ScoredRun]) -> dict:
    return _report(runs, (), {})


def _payload(arm: str) -> dict:
    """A schema-2 payload Recording.from_dict can READ (not verify). Used only to
    check which arm labels are recognized at the door."""
    return {
        "schema_version": 2,
        "run_id": "abc123",
        "arm": arm,
        "case_id": "revenue_by_region",
        "task_version": "revenue@x",
        "git_commit": "deadbeef",
        "recorded_at": "2026-01-01T00:00:00Z",
        "pins": {
            "model": "qwen2.5:7b",
            "context_window": 8192,
            "prompt_hash": "p",
            "tools_hash": "t",
            "seed": 1,
            "temperature": 0.0,
            "ollama_version": "0.0",
            "digest": "d",
            "quantization": "q",
        },
        "trace": {},
        "source_refs": [],
    }


# --- the arm's vocabulary --------------------------------------------------


def test_frontier_arm_shares_the_raw_surface_exactly():
    # Arm 3 is the raw baseline's surface with a frontier MODEL, so it must get
    # the SAME prompt and tool declarations -- no advantage, no disadvantage.
    frontier = arm_spec(FRONTIER_ARM)
    raw = arm_spec(RAW_ARM)
    assert frontier.name == FRONTIER_ARM
    assert frontier.instructions == raw.instructions
    assert frontier.declarations == raw.declarations


def test_frontier_arm_is_not_the_governed_surface():
    # The inverse of the equality above: were the frontier arm to inherit the
    # governed prompt/tools it would gain the very advantage arm 2 lacks, and the
    # comparison would be meaningless in the way #25 warns of.
    frontier = arm_spec(FRONTIER_ARM)
    governed = arm_spec(GOVERNED_ARM)
    assert frontier.instructions != governed.instructions
    assert frontier.declarations != governed.declarations


def test_an_unknown_arm_still_raises_and_names_every_arm():
    with pytest.raises(ValueError) as raised:
        arm_spec("made_up_arm")
    message = str(raised.value)
    for arm in (GOVERNED_ARM, RAW_ARM, FRONTIER_ARM):
        assert arm in message


# --- recognized but not required -------------------------------------------


def test_frontier_arm_is_recognized_but_not_a_required_arm():
    # ARMS drives the gate's MissingRecording; adding the frontier arm there
    # would make a credential-free CI demand a frontier recording it cannot
    # produce. It is recognized (readable/scorable) without being required.
    assert FRONTIER_ARM in RECOGNIZED_ARMS
    assert FRONTIER_ARM not in ARMS
    assert set(ARMS) == {GOVERNED_ARM, RAW_ARM}


def test_a_frontier_labelled_recording_is_readable():
    recording = Recording.from_dict(_payload(FRONTIER_ARM))
    assert recording.arm == FRONTIER_ARM


def test_an_arm_no_spelling_defines_is_refused_toward_red():
    # `frontier` is NOT the literal (`frontier_raw`); a near-miss must be refused
    # rather than quietly scored, and the refusal names the arms it knows.
    assert "frontier" not in RECOGNIZED_ARMS
    with pytest.raises(UnreadableRecording) as raised:
        Recording.from_dict(_payload("frontier"))
    message = str(raised.value)
    for arm in RECOGNIZED_ARMS:
        assert arm in message


# --- the withholding logic --------------------------------------------------


def test_no_frontier_evidence_returns_none():
    runs = [_run(GOVERNED_ARM, "c1", [_score(SHARED, True)])]
    assert _beats_frontier_raw(runs) is None


def test_a_strict_governed_win_is_true():
    runs = [
        _run(GOVERNED_ARM, "c1", [_score(SHARED, True), _score(OTHER_SHARED, True)]),
        _run(FRONTIER_ARM, "c1", [_score(SHARED, True), _score(OTHER_SHARED, False)]),
    ]
    assert _beats_frontier_raw(runs) is True


def test_a_tie_is_false_not_a_claim():
    runs = [
        _run(GOVERNED_ARM, "c1", [_score(SHARED, True)]),
        _run(FRONTIER_ARM, "c1", [_score(SHARED, True)]),
    ]
    assert _beats_frontier_raw(runs) is False


def test_a_loss_is_false():
    runs = [
        _run(GOVERNED_ARM, "c1", [_score(SHARED, False)]),
        _run(FRONTIER_ARM, "c1", [_score(SHARED, True)]),
    ]
    assert _beats_frontier_raw(runs) is False


def test_a_frontier_that_ran_fewer_cases_cannot_flatter_the_governed_count():
    # Restricted to the cases BOTH ran (only c1, a tie), the extra governed pass
    # on c2 -- which frontier never attempted -- must not manufacture a win.
    runs = [
        _run(GOVERNED_ARM, "c1", [_score(SHARED, True)]),
        _run(GOVERNED_ARM, "c2", [_score(SHARED, True)]),
        _run(FRONTIER_ARM, "c1", [_score(SHARED, True)]),
    ]
    assert _beats_frontier_raw(runs) is False


def test_governed_only_predicates_do_not_create_a_win():
    governed_only = "catalog_before_resolve"
    assert governed_only not in SHARED_PREDICATES
    runs = [
        _run(GOVERNED_ARM, "c1", [_score(SHARED, True), _score(governed_only, True)]),
        _run(FRONTIER_ARM, "c1", [_score(SHARED, True)]),
    ]
    # A tie on the SHARED set; the governed-only pass is capability, not a win.
    assert _beats_frontier_raw(runs) is False


# --- the claim's presence in the assembled report ---------------------------


def test_report_adds_the_claim_key_only_on_a_strict_win():
    runs = [
        _run(GOVERNED_ARM, "c1", [_score(SHARED, True), _score(OTHER_SHARED, True)]),
        _run(FRONTIER_ARM, "c1", [_score(SHARED, True), _score(OTHER_SHARED, False)]),
    ]
    report = _report_of(runs)
    assert report["beats_frontier_raw"] is True
    assert FRONTIER_ARM in report["arms"]
    assert "OUTSCORED" in render(report)


def test_report_withholds_the_claim_by_absence_on_a_tie():
    runs = [
        _run(GOVERNED_ARM, "c1", [_score(SHARED, True)]),
        _run(FRONTIER_ARM, "c1", [_score(SHARED, True)]),
    ]
    report = _report_of(runs)
    # ABSENCE is the withholding -- not a False a caller must remember to read.
    assert "beats_frontier_raw" not in report
    # The frontier arm's numbers still show: it ran, it just did not lose to.
    assert FRONTIER_ARM in report["arms"]
    assert "OUTSCORED" not in render(report)


def test_report_has_no_frontier_arm_and_no_claim_when_none_ran():
    runs = [
        _run(GOVERNED_ARM, "c1", [_score(SHARED, True)]),
        _run(RAW_ARM, "c1", [_score(SHARED, False)]),
    ]
    report = _report_of(runs)
    assert "beats_frontier_raw" not in report
    assert FRONTIER_ARM not in report["arms"]
    assert "OUTSCORED" not in render(report)


def test_the_committed_report_makes_no_frontier_claim_and_shows_no_frontier_arm():
    # End-to-end over the REAL committed corpus, which ships no frontier
    # recording (credential-free slice): the default code path withholds the
    # claim and omits the arm with nobody arranging it.
    report = score_recordings()
    assert "beats_frontier_raw" not in report
    assert FRONTIER_ARM not in report["arms"]
    assert set(report["arms"]) == set(ARMS)


# --- The fail-closed frontier-arm boundary in the LOCAL runner (hy-0qr6 round 2) ---
#
# The integrity bug: run_case() built the runtime with the LOCAL pinned model for EVERY arm,
# and repository_pins() returns the local pin for every arm, so run_case(arm=FRONTIER_ARM)
# would produce a `frontier_raw` recording that actually ran on the local model -- a FALSE
# frontier comparison with no frontier model and no credential, which then passes the
# same-arm pin check and can be published by score_recordings()/_beats_frontier_raw(). The
# runner now FAILS CLOSED: no configured frontier model + credential, no frontier recording.


def test_run_case_refuses_the_frontier_arm_without_a_configured_model_and_credential(monkeypatch):
    # The credential-free path CANNOT create frontier evidence. The refusal precedes any pin
    # observation or inference, so a run is never even attempted -- proven by pointing both at
    # a tripwire: if the guard did not fire first, the tripwire (not FrontierArmNotConfigured)
    # would be raised.
    from hyperset.evals import run
    from hyperset.evals.cases import load_cases

    for env in (run.FRONTIER_MODEL_ENV, run.FRONTIER_API_KEY_ENV, run.FRONTIER_BASE_URL_ENV):
        monkeypatch.delenv(env, raising=False)

    def _tripwire(*_args, **_kwargs):
        raise AssertionError("the frontier arm must refuse BEFORE observing pins or inferring")

    monkeypatch.setattr(run, "observe_pins", _tripwire)
    monkeypatch.setattr(run, "plan_analytics_context", _tripwire)

    case = next(iter(load_cases()))
    with pytest.raises(run.FrontierArmNotConfigured):
        run.run_case(case, arm=FRONTIER_ARM, session_factory=None)


@pytest.mark.parametrize("model, key", [("", ""), ("", "a-real-key"), ("some-frontier-model", "")])
def test_frontier_config_refuses_without_both_model_and_credential(monkeypatch, model, key):
    # Both the pinned frontier model and its credential are required; either missing is a
    # fail-closed refusal (an empty value is treated as unset).
    from hyperset.evals import run

    monkeypatch.setenv(run.FRONTIER_MODEL_ENV, model)
    monkeypatch.setenv(run.FRONTIER_API_KEY_ENV, key)
    with pytest.raises(run.FrontierArmNotConfigured):
        run.frontier_config_or_refuse()


def test_frontier_config_refuses_the_local_pinned_model_even_with_a_credential(monkeypatch):
    # A frontier arm configured to run the LOCAL pinned model is exactly the impostor this
    # boundary exists to reject: it would measure the raw baseline against itself.
    from hyperset.evals import run
    from hyperset.planner.runtime import PINNED_MODEL

    monkeypatch.setenv(run.FRONTIER_MODEL_ENV, PINNED_MODEL)
    monkeypatch.setenv(run.FRONTIER_API_KEY_ENV, "a-real-key")
    with pytest.raises(run.FrontierArmNotConfigured):
        run.frontier_config_or_refuse()


def test_frontier_config_returns_a_configured_frontier_model_and_credential(monkeypatch):
    # The only path that legitimises a frontier recording: a real frontier model (distinct
    # from the local pinned one) plus its credential. This slice wires the boundary; the
    # pinned frontier-model identity + credential are hy-2tg6.
    from hyperset.evals import run

    monkeypatch.setenv(run.FRONTIER_MODEL_ENV, "gpt-5.6-luna")
    monkeypatch.setenv(run.FRONTIER_API_KEY_ENV, "a-real-key")
    monkeypatch.setenv(run.FRONTIER_BASE_URL_ENV, "https://frontier.example/v1")
    assert run.frontier_config_or_refuse() == (
        "gpt-5.6-luna",
        "a-real-key",
        "https://frontier.example/v1",
    )


def test_a_configured_frontier_run_reaches_runtime_and_records_without_pin_mismatch(monkeypatch):
    # The other half of the boundary (hy-0qr6 round 2): a CONFIGURED frontier run must actually
    # work. repository_pins is now model-aware, so the frontier run's observed model matches its
    # pin and assert_pins does NOT raise PinMismatch. observe_pins needs a live model, so it is
    # faked to return the REAL repository pins carrying the configured frontier model -- and
    # assert_pins runs FOR REAL over them. Proven end to end: the distinct frontier model AND
    # api key reach the runtime config, and a recording is produced on the frontier arm.
    from hyperset.evals import run
    from hyperset.evals.cases import load_cases
    from hyperset.evals.pins import RunPins, expected_model, repository_pins
    from hyperset.planner.runtime import PINNED_MODEL

    FRONTIER = "gpt-5.6-luna"
    monkeypatch.setenv(run.FRONTIER_MODEL_ENV, FRONTIER)
    monkeypatch.setenv(run.FRONTIER_API_KEY_ENV, "a-real-key")
    monkeypatch.delenv(run.FRONTIER_BASE_URL_ENV, raising=False)

    # Model-aware pins: the frontier arm pins the configured frontier model; the others still
    # pin the local model. This is exactly what made assert_pins reject a valid frontier run
    # before the fix (it required model == PINNED_MODEL for every arm).
    assert expected_model(FRONTIER_ARM) == FRONTIER
    assert repository_pins(FRONTIER_ARM)["model"] == FRONTIER
    assert repository_pins(GOVERNED_ARM)["model"] == PINNED_MODEL

    host = {"digest": "sha256:deadbeefcafe", "quantization": "Q4_K_M", "ollama_version": "0.42.0"}

    def _fake_observe(*, arm, base_url, model):
        assert model == FRONTIER, "run_case must observe the FRONTIER model, not PINNED_MODEL"
        return RunPins(**{**repository_pins(arm), **host})

    captured: dict = {}

    class _SpyRuntime:
        def __init__(self, config, **_kwargs):
            captured.update(model=config.model, api_key=config.api_key, base_url=config.base_url)

    class _Trace:
        def to_dict(self):
            return {"steps": [], "provenance": {}, "prompt_hash": "p", "tools_hash": "t"}

    monkeypatch.setattr(run, "observe_pins", _fake_observe)
    monkeypatch.setattr(run, "OpenAIAgentsRuntime", _SpyRuntime)
    monkeypatch.setattr(run, "plan_analytics_context", lambda *a, **k: _Trace())
    monkeypatch.setattr(run, "declared_context_window", lambda *a, **k: None)
    monkeypatch.setattr(run, "source_refs", lambda _trace: [])
    monkeypatch.setattr(
        run, "recording_session", lambda: SimpleNamespace(run_id="a" * 32, commit="b" * 40)
    )

    recording = run.run_case(next(iter(load_cases())), arm=FRONTIER_ARM, session_factory=None)

    # assert_pins did NOT raise PinMismatch -> a recording exists, on the frontier arm, pinned
    # to the frontier model, and the frontier model + api key reached the runtime config.
    assert recording.arm == FRONTIER_ARM
    assert recording.pins.model == FRONTIER
    assert captured == {
        "model": FRONTIER,
        "api_key": "a-real-key",
        "base_url": run.DEFAULT_BASE_URL,
    }
