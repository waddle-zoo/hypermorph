"""What a benchmark run was pinned to, asserted rather than documented.

GitHub #25 requires a run whose pins do not match to FAIL rather than warn, so
every pin is a value this module can compare -- never a sentence in a README.
Nine of them, and they divide by who can check them:

- The REPOSITORY half -- model tag, context window, prompt hash, tool-schema
  hash, seed, temperature -- is checkable with no server and no model, which
  is what makes ADR 0013's recorded-run gate mean something. A prompt edit or
  a tool-contract edit changes planner behaviour, so a recording made before
  it is no longer evidence about today's code, and the gate says so instead of
  scoring stale behaviour.
- The HOST half -- Ollama version, model digest, quantization -- can only be
  observed where a model runs. CI cannot re-derive it, so CI checks that it is
  present and the scheduled live run checks that it matches.

The digest is in that list because a tag is mutable: `qwen2.5:7b` is a pointer
a registry can republish, and on this host it and `qwen2.5:7b-instruct` are the
same digest -- two names for one set of weights. The name pins the intent, the
digest pins the bytes.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import asdict, dataclass

from hyperset.planner.ollama import model_fingerprint, observe_context_window, server_version
from hyperset.planner.runtime import CONTEXT_WINDOW_TOKENS, PINNED_MODEL
from hyperset.planner.trace import content_hash

# The frontier arm's pinned model is READ FROM THE ENVIRONMENT rather than a repository
# constant, because WHICH frontier model (and the credential to run it) is the infra decision
# hy-2tg6 (#141), not chosen here. Every other arm pins the local `PINNED_MODEL`. The name is
# owned here, in the pin layer, because "what model an arm pins" is a pin fact; the runner
# (`evals.run`) imports it for the credential-side of the same boundary.
FRONTIER_MODEL_ENV = "HYPERSET_FRONTIER_MODEL"


def expected_model(arm: str) -> str:
    """The model THIS commit pins for `arm`. Every arm pins the local `PINNED_MODEL` EXCEPT the
    frontier arm (#25 arm 3), which pins the separately-configured frontier model read from
    `HYPERSET_FRONTIER_MODEL` -- so a configured frontier run's OBSERVED model matches its pin
    instead of being rejected as `!= PINNED_MODEL` (hy-0qr6 round 2).

    The frontier arm is only ever recorded on a configured run -- `run_case` FAILS CLOSED and
    refuses it without a frontier model + credential -- so the env is set whenever this is
    reached for the frontier arm on the record path. Absent it (e.g. a stray score-time call
    when no frontier recording exists), it falls back to `PINNED_MODEL` rather than invent a
    pin. Lazy `import_module` for the arm-name constant keeps `recording` off this module's
    import at module load (recording imports pins), mirroring `repository_pins`."""
    frontier_arm = importlib.import_module("hyperset.evals.recording").FRONTIER_ARM
    if arm == frontier_arm:
        configured = os.environ.get(FRONTIER_MODEL_ENV, "").strip()
        if configured:
            return configured
    return PINNED_MODEL


# The pins CI can re-derive from the repository, and therefore the pins a
# recording is re-checked against every time it is scored. Named as a tuple
# because both the check and its failure message read it: a field added to
# `RunPins` and forgotten here is a pin nothing enforces.
REPOSITORY_PINS = ("model", "context_window", "prompt_hash", "tools_hash", "seed", "temperature")

# The pins only a host can answer. CI cannot compare them to anything, so what
# it checks is that they were recorded at all -- an empty digest is a run that
# pinned nothing, and it must not read as a run that pinned something.
HOST_PINS = ("ollama_version", "digest", "quantization")


@dataclass(frozen=True)
class RunPins:
    """Everything GitHub #25 lists, as data.

    Frozen because a pin a caller can edit after the run is a claim rather
    than a record.
    """

    model: str
    digest: str
    quantization: str
    ollama_version: str
    context_window: int
    prompt_hash: str
    tools_hash: str
    seed: int
    temperature: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> RunPins:
        missing = sorted({field for field in REPOSITORY_PINS + HOST_PINS} - set(payload))
        if missing:
            raise PinsIncomplete(
                f"this record is missing {', '.join(missing)}; a run that did not record a pin "
                "did not make it, and an absent pin must not read as a matching one"
            )
        return cls(**{key: payload[key] for key in REPOSITORY_PINS + HOST_PINS})


class PinsIncomplete(RuntimeError):
    """A record that does not carry every pin.

    Separate from a mismatch on purpose: a mismatch is two known values that
    disagree, and this is a value nobody wrote down. Folding them together
    would let a recording pass the weaker check by omitting the field.
    """


class PinMismatch(RuntimeError):
    """A run or a recording pinned to something other than what is asked.

    Carries EVERY differing field rather than the first. A benchmark whose
    pins drifted usually drifted in more than one place -- a re-pull moves the
    digest and the Ollama version together -- and reporting one at a time
    turns one failure into three runs.
    """

    def __init__(self, differences: dict[str, tuple[object, object]]) -> None:
        self.differences = differences
        rendered = "; ".join(
            f"{field}: expected {expected!r}, got {actual!r}"
            for field, (expected, actual) in sorted(differences.items())
        )
        super().__init__(f"pinned run does not match its pins -- {rendered}")


PINNED_SEED = 20260728
"""The seed every arm sends, identical across arms because #25's comparison is
only valid if the arms differ in one variable.

A seed AND a zero temperature, which is not redundant: Ollama's
OpenAI-compatible endpoint was measured to return identical text for two calls
sharing a seed at temperature 1.0 and different text without one (hy-am2), so
the seed is the pin that survives a future case deliberately run hotter. Its
value is arbitrary and therefore stated once, here, rather than defaulted at
three call sites."""

PINNED_TEMPERATURE = 0.0
"""Greedy decoding, so a rerun of the same case is the same run.

#25 requires repeated local runs to be stable and requires the harness to
report its stability rather than assert it, and a sampled arm cannot report
either honestly."""


def repository_pins(arm: str) -> dict:
    """The six pins that come from this repository at this commit, for one arm.

    Read from the arm's own spec rather than from copies, so a prompt edit
    moves this without anyone remembering to update a fixture. The seed and
    the temperature are constants here rather than parameters for a related
    reason: a pin taken from the caller is a pin the caller can satisfy by
    asking for what it already has.

    PER ARM, because the two arms legitimately run different prompts and
    different tools -- that IS the variable #25 measures. Everything else in
    this dict is identical across arms, which is the other half of the same
    requirement, and it is identical because it is written once here rather
    than once per arm.
    """
    # Lazy, by NAME. Computing an arm's repository pins is the #25 RECORD/GATE
    # path, and it alone needs the arm spec layer (`evals.arms` -> `raw_arm` ->
    # `repositories.postgres`) and `transport.operations` (-> `bundle` ->
    # `resolver` -> `repositories.postgres`). Importing THIS module for the
    # scoring/reporting predicates must not name either (hy-quol). A string
    # `import_module` is deliberate rather than a `from ... import` inside the
    # function: the purity gate walks the static AST, where even a function-body
    # `from hyperset.evals.arms import arm_spec` still counts as reachable. This
    # keeps both off the eval reporting/scoring SOURCE closure that
    # `test_report_time_purity` pins; the call is only ever made from the
    # record/gate path (`observe_pins`, `assert_pins`), which legitimately needs
    # the arm spec.
    arm_spec = importlib.import_module("hyperset.evals.arms").arm_spec
    serialize = importlib.import_module("hyperset.transport.operations").serialize

    spec = arm_spec(arm)
    return {
        "model": expected_model(arm),
        "context_window": CONTEXT_WINDOW_TOKENS,
        "prompt_hash": content_hash(spec.instructions),
        "tools_hash": content_hash(serialize(list(spec.declarations))),
        "seed": PINNED_SEED,
        "temperature": PINNED_TEMPERATURE,
    }


def observe_pins(*, arm: str, base_url: str, model: str = PINNED_MODEL) -> RunPins:
    """The pins of the run that is about to happen, read off the live server.

    Observed rather than declared, for the reason `Runtime.provenance` is: a
    configuration echo describes a pinning that may not have happened. The
    window here is the ALLOCATED one -- `observe_context_window` loads the
    model to get it -- because a served tag's declaration is a request Ollama
    silently clamps, and `assert_pins` is what compares it to the pinned
    number rather than this function quietly substituting one for the other.
    """
    fingerprint = model_fingerprint(model, base_url=base_url)
    allocated = observe_context_window(model, base_url=base_url)
    if allocated is None:
        raise PinsIncomplete(
            f"nothing observed what window {model!r} was given -- the model is not loaded even "
            "after warming, and a run pinned to an unobserved window is pinned to nothing"
        )
    declared = repository_pins(arm)
    return RunPins(
        model=model,
        digest=fingerprint.digest,
        quantization=fingerprint.quantization,
        ollama_version=server_version(base_url),
        # The OBSERVED window, not the pinned one. Substituting the constant
        # here would make every recording claim the window it was supposed to
        # get, which is the one number Ollama was measured to give silently
        # wrong.
        context_window=allocated,
        prompt_hash=declared["prompt_hash"],
        tools_hash=declared["tools_hash"],
        seed=declared["seed"],
        temperature=declared["temperature"],
    )


def assert_host_pins_recorded(pins: RunPins) -> None:
    """Every host pin was actually written down. No comparison to any arm.

    The half of `assert_pins` that is right for ANY recording, #25 or a
    customer's own (hy-myn6, #400): a recording whose `digest`, `quantization`
    or `ollama_version` is blank pinned no host identity, and an absent pin must
    not read as a matching one. The OTHER half of `assert_pins` -- comparing
    `REPOSITORY_PINS` to a specific arm's spec -- is #25-only and would wrongly
    reject a customer recording that never ran the #25 arms; the customer runner
    keeps this completeness check without that value comparison. `from_dict`
    checks the pin KEYS exist; this checks their VALUES are non-empty.
    """
    unrecorded = [field for field in HOST_PINS if not str(getattr(pins, field)).strip()]
    if unrecorded:
        raise PinsIncomplete(
            f"{', '.join(sorted(unrecorded))} recorded empty; a run that pinned nothing must not "
            "read as a run that pinned something"
        )


def assert_pins(pins: RunPins, *, arm: str) -> None:
    """Fail a run or a recording whose repository pins are not today's.

    The half that needs no server, and the whole reason ADR 0013's per-PR gate
    is not theatre: a recording carries the prompt hash, tool-schema hash,
    model tag, window, seed and temperature it ran under, and this compares
    them to what this commit says. Scoring a recording made against a
    different prompt would report a number about behaviour this repository no
    longer produces.

    Host pins are checked for PRESENCE only, here. Nothing in CI can re-derive
    an Ollama version or a digest, so the enforceable claim is that the run
    wrote them down; the scheduled live run is what compares them (ADR 0013).
    """
    expected = repository_pins(arm)
    actual = pins.to_dict()
    differences = {
        field: (expected[field], actual[field])
        for field in REPOSITORY_PINS
        if expected[field] != actual[field]
    }
    assert_host_pins_recorded(pins)
    if differences:
        raise PinMismatch(differences)
