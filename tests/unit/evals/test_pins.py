"""What a recording must carry before its scores mean anything (hy-ast).

ADR 0013 moved the per-PR gate from a live model to a committed recording, and
the whole weight of that decision rests here: a recording is only evidence
about this commit if the prompt, the tool schemas, the model, the window, the
seed and the temperature it ran under are still this commit's. These tests are
what stop "scored a recording" from meaning "scored a file".
"""

from __future__ import annotations

import json

import pytest

from hyperset.evals.cases import task_version
from hyperset.evals.pins import (
    HOST_PINS,
    PINNED_SEED,
    PINNED_TEMPERATURE,
    PinMismatch,
    PinsIncomplete,
    RunPins,
    assert_pins,
    repository_pins,
)
from hyperset.evals.recording import (
    DISCLOSURE,
    GOVERNED_ARM,
    RAW_ARM,
    RECOGNIZED_ARMS,
    RECORDING_SCHEMA_VERSION,
    Recording,
    UnreadableRecording,
)
from hyperset.planner.runtime import PINNED_MODEL
from hyperset.planner.trace import OPENAI_AGENTS_RUNTIME, SCRIPTED_RUNTIME

HOST = {
    "digest": "sha256:845dbda0ea48ed74",
    "quantization": "Q4_K_M",
    "ollama_version": "0.32.4",
}


def pins(arm: str = GOVERNED_ARM, **overrides) -> RunPins:
    return RunPins(**{**repository_pins(arm), **HOST, **overrides})


def recording(**overrides) -> Recording:
    payload = {
        "schema_version": RECORDING_SCHEMA_VERSION,
        "run_id": "f" * 32,
        "arm": GOVERNED_ARM,
        "case_id": "revenue_by_region",
        "task_version": task_version(),
        "git_commit": "0" * 40,
        "recorded_at": "2026-07-28T00:00:00+00:00",
        "pins": pins().to_dict(),
        "source_refs": ["superset:dataset:ae48881d-334f-54a7-94e8-1ffcc73866e2"],
        "trace": {"provenance": {"runtime": OPENAI_AGENTS_RUNTIME, "model": PINNED_MODEL}},
    }
    return Recording.from_dict({**payload, **overrides})


def test_a_recording_made_against_todays_prompt_and_tools_passes():
    assert_pins(pins(), arm=GOVERNED_ARM)


def test_a_recording_made_against_a_different_prompt_is_not_evidence_about_this_commit():
    """The defect ADR 0013 creates and this closes: an edited prompt changes
    planner behaviour, so yesterday's recording describes behaviour this
    repository no longer produces."""
    with pytest.raises(PinMismatch) as raised:
        assert_pins(pins(prompt_hash="sha256:0000000000000000"), arm=GOVERNED_ARM)

    assert set(raised.value.differences) == {"prompt_hash"}


def test_every_drifted_pin_is_reported_rather_than_the_first():
    """A re-pull moves the digest and the version together, and a benchmark
    that reports one drift per run turns one failure into three runs."""
    with pytest.raises(PinMismatch) as raised:
        assert_pins(pins(model="llama3.2:3b", seed=7, context_window=4096), arm=GOVERNED_ARM)

    assert set(raised.value.differences) == {"model", "seed", "context_window"}


def test_the_window_a_run_was_actually_given_is_what_is_checked():
    """Ollama clamps a requested window silently, so the pin that matters is
    the observed allocation -- and 4,096 is smaller than one resolved bundle."""
    with pytest.raises(PinMismatch) as raised:
        assert_pins(pins(context_window=4096), arm=GOVERNED_ARM)

    assert raised.value.differences["context_window"] == (
        repository_pins(GOVERNED_ARM)["context_window"],
        4096,
    )


def test_a_window_LARGER_than_the_pin_is_refused_too_because_the_check_is_equality():
    """benchmark.md tells a reader the window check is equality and not a floor
    (hy-z8dd), and every other arm here observes a window BELOW the pin, so a
    floor would satisfy the whole file. Two arms of one comparison ran under
    different budgets are not one measurement, whichever way the budget moved."""
    pinned = repository_pins(GOVERNED_ARM)["context_window"]

    with pytest.raises(PinMismatch) as raised:
        assert_pins(pins(context_window=pinned * 2), arm=GOVERNED_ARM)

    assert raised.value.differences["context_window"] == (pinned, pinned * 2)


@pytest.mark.parametrize("field", HOST_PINS)
def test_a_host_pin_recorded_empty_is_a_run_that_pinned_nothing(field):
    """CI cannot re-derive an Ollama version or a digest, so what it enforces
    is that the run wrote them down. An empty digest must not read as a match."""
    with pytest.raises(PinsIncomplete) as raised:
        assert_pins(pins(**{field: "  "}), arm=GOVERNED_ARM)

    assert field in str(raised.value)


def test_a_pin_left_out_entirely_is_refused_rather_than_defaulted():
    payload = pins().to_dict()
    del payload["digest"]

    with pytest.raises(PinsIncomplete) as raised:
        RunPins.from_dict(payload)

    assert "digest" in str(raised.value)


def test_the_pinned_seed_and_temperature_are_the_harness_own_not_the_callers():
    """A pin taken from the caller is a pin the caller satisfies by asking for
    what it already has, which is a check that cannot fail."""
    assert repository_pins(GOVERNED_ARM)["seed"] == PINNED_SEED
    assert repository_pins(GOVERNED_ARM)["temperature"] == PINNED_TEMPERATURE

    with pytest.raises(PinMismatch):
        assert_pins(pins(seed=PINNED_SEED + 1), arm=GOVERNED_ARM)


def test_a_scripted_trace_is_refused_as_evidence_about_a_model():
    """`ScriptedRuntime` reports that no model ran (hy-pqf3) so that this check
    can exist: otherwise the cheapest green benchmark is a committed script
    that calls the right tools in the right order."""
    with pytest.raises(UnreadableRecording) as raised:
        recording(trace={"provenance": {"runtime": SCRIPTED_RUNTIME, "model": None}}).verify()

    assert "no model ran" in str(raised.value)


def test_a_trace_that_says_nothing_about_what_produced_it_is_refused():
    with pytest.raises(UnreadableRecording):
        recording(trace={"provenance": {}}).verify()


@pytest.mark.parametrize("runtime", ["totally-made-up", "gpt-5-by-hand", " ", "OPENAI_AGENTS_SDK"])
def test_a_runtime_no_adapter_reports_is_refused_rather_than_only_the_honest_fake(runtime):
    """Refusing `scripted` alone refuses the fake that admits it (hy-puiu).

    Measured on this branch before the check existed: `totally-made-up`,
    `gpt-5-by-hand` and `' '` were all accepted as evidence about a model. The
    vocabulary already existed in `planner.trace`; the reader did not read it.
    """
    with pytest.raises(UnreadableRecording) as raised:
        recording(
            trace={"provenance": {"runtime": runtime, "model": PINNED_MODEL}},
        ).verify()

    assert "no adapter in this repository reports" in str(raised.value)


def test_a_runtime_reporting_no_hashes_is_refused_the_way_an_unrecorded_pin_is():
    """The silent branch protected nothing. `ScriptedRuntime` was its stated
    reason and is refused one check earlier, so the only runtime reaching here
    is one that reports both hashes -- and a hand-written trace claiming
    `openai_agents_sdk` and reporting neither was scored as a real run."""
    with pytest.raises(UnreadableRecording) as raised:
        recording(
            trace={"provenance": {"runtime": OPENAI_AGENTS_RUNTIME, "model": PINNED_MODEL}},
        ).verify()

    assert "must not read as a hash that matched" in str(raised.value)
    assert "instructions_hash" in str(raised.value) and "tools_hash" in str(raised.value)


def test_a_fixture_is_reported_as_a_fixture_even_when_it_also_drifted():
    """Ordered deliberately: re-recording is not the fix for a fixture."""
    with pytest.raises(UnreadableRecording):
        recording(
            trace={"provenance": {"runtime": SCRIPTED_RUNTIME, "model": None}},
            pins=pins(prompt_hash="sha256:0000000000000000").to_dict(),
        ).verify()


def test_a_recording_from_a_shape_this_reader_does_not_know_is_refused():
    """Scoring an unknown shape would score the fields it recognises and
    ignore the rest, which is how a truncated recording reads as a clean run."""
    with pytest.raises(UnreadableRecording):
        recording(schema_version=RECORDING_SCHEMA_VERSION + 1)


def test_an_arm_nothing_defines_is_refused():
    # `frontier_raw` is now a RECOGNIZED arm (hy-0qr6, #25 arm 3), so the stand-in
    # for "an arm nothing defines" can no longer be that name -- a label this
    # repository DOES define must not pose as an undefined one, or the refusal
    # this guards would read as tested while testing a recognized arm.
    assert "no_such_arm" not in RECOGNIZED_ARMS
    with pytest.raises(UnreadableRecording):
        recording(arm="no_such_arm")


def test_a_written_recording_carries_the_disclosure_adr_0013_requires(tmp_path):
    """Every report states that it scored a recording. Carried as a value on
    the artifact rather than left to a person to remember."""
    path = tmp_path / "run.json"
    recording().write(path)

    written = json.loads(path.read_text())
    assert written["disclosure"] == DISCLOSURE
    assert "does not" in DISCLOSURE and "live model" in DISCLOSURE
    assert Recording.read(path).pins == pins()


def test_the_arms_differ_in_the_substrate_and_in_nothing_else():
    """#25's comparison is only valid if the arms differ in exactly one
    variable. That variable is the prompt and the tool surface; everything a
    run is otherwise pinned to has to be identical, and is identical because it
    is written once rather than once per arm."""
    governed = repository_pins(GOVERNED_ARM)
    raw = repository_pins(RAW_ARM)

    assert governed["prompt_hash"] != raw["prompt_hash"]
    assert governed["tools_hash"] != raw["tools_hash"]
    assert {key: governed[key] for key in ("model", "context_window", "seed", "temperature")} == {
        key: raw[key] for key in ("model", "context_window", "seed", "temperature")
    }


def test_a_recording_is_checked_against_its_own_arms_pins():
    """A raw-arm run carrying the governed arm's prompt hash is a raw run
    wearing arm 1's identity, and every downstream check would confirm it."""
    assert_pins(pins(RAW_ARM), arm=RAW_ARM)

    with pytest.raises(PinMismatch) as raised:
        assert_pins(pins(GOVERNED_ARM), arm=RAW_ARM)

    assert set(raised.value.differences) == {"prompt_hash", "tools_hash"}


def test_a_trace_that_disagrees_with_what_the_adapter_drove_is_refused():
    """The loop records the pair it was HANDED and the adapter reports the pair
    it USED. Handing those two different values is the one way to produce an
    internally consistent record of a run that did not happen."""
    with pytest.raises(UnreadableRecording) as raised:
        recording(
            trace={
                "provenance": {
                    "runtime": OPENAI_AGENTS_RUNTIME,
                    "model": PINNED_MODEL,
                    "instructions_hash": "sha256:1111111111111111",
                    "tools_hash": "sha256:2222222222222222",
                },
                "prompt_hash": repository_pins(GOVERNED_ARM)["prompt_hash"],
                "tools_hash": repository_pins(GOVERNED_ARM)["tools_hash"],
            }
        ).verify()

    assert "did not happen" in str(raised.value)


def test_a_trace_agreeing_with_its_adapter_passes_that_check():
    recording(
        trace={
            "provenance": {
                "runtime": OPENAI_AGENTS_RUNTIME,
                "model": PINNED_MODEL,
                "instructions_hash": repository_pins(GOVERNED_ARM)["prompt_hash"],
                "tools_hash": repository_pins(GOVERNED_ARM)["tools_hash"],
            },
            "prompt_hash": repository_pins(GOVERNED_ARM)["prompt_hash"],
            "tools_hash": repository_pins(GOVERNED_ARM)["tools_hash"],
        }
    ).verify()
