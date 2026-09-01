"""The two local arms, defined as one object each so they cannot drift (#25).

Arm 1 is the governed substrate: the planner's prompt, the three served tools,
and `InProcessExecutor` reaching `run_operation`. Arm 2 is the raw baseline:
the same model, the same SDK adapter, the same limits, the same trace and the
same scorers, given observed source metadata and nothing else.

WHY A SPEC RATHER THAN TWO CALL SITES. The prompt and the tool declarations are
needed twice per run -- the adapter drives the model with them, the loop
records their hashes -- and a caller that passed one pair to the adapter and
another to the loop would produce a recording describing a run that did not
happen. Here the pair is one value. The remaining half of that guarantee is
checked rather than argued: the adapter reports the hashes it actually used in
`provenance()`, and a recording whose trace disagrees is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hyperset.evals.raw_arm import RAW_TOOL_SPECS, RawMetadataExecutor
from hyperset.evals.recording import FRONTIER_ARM, GOVERNED_ARM, RAW_ARM
from hyperset.planner.executor import Executor, InProcessExecutor
from hyperset.planner.loop import planner_prompt, tool_specs

RAW_PROMPT_PATH = Path(__file__).parent / "prompts" / "raw_baseline.md"


@dataclass(frozen=True)
class ArmSpec:
    """Everything that differs between the arms, and nothing that does not.

    The model, seed, temperature, window, turn limit, trace and scorers are
    absent on purpose: they are identical across arms, so they live where they
    are stated once -- `hyperset.evals.pins` and the runner -- rather than
    twice here where two arms could quietly disagree about them.
    """

    name: str
    instructions: str
    declarations: tuple[dict, ...]

    def executor(self, *, session_factory) -> Executor:
        if self.name == GOVERNED_ARM:
            return InProcessExecutor(session_factory=session_factory)
        return RawMetadataExecutor(session_factory=session_factory)


def arm_spec(name: str) -> ArmSpec:
    if name == GOVERNED_ARM:
        return ArmSpec(
            name=GOVERNED_ARM,
            instructions=planner_prompt(),
            declarations=tuple(tool_specs()),
        )
    if name == RAW_ARM:
        return ArmSpec(
            name=RAW_ARM,
            instructions=RAW_PROMPT_PATH.read_text(),
            declarations=tuple(RAW_TOOL_SPECS.values()),
        )
    if name == FRONTIER_ARM:
        # The frontier arm (#25 arm 3, hy-0qr6) gets the IDENTICAL raw-metadata
        # surface as arm 2: the SAME prompt and the SAME tool declarations, so the
        # only variable between it and the raw baseline is the MODEL, never an
        # advantage or disadvantage baked into the surface. WHICH frontier model
        # -- and the credential to run it -- is the Overseer/Brandon infra
        # decision hy-2tg6 (#141), NOT chosen here. The pin layer reads the model
        # from `HYPERSET_FRONTIER_MODEL` (`pins.expected_model`) so a CONFIGURED
        # frontier run's observed model matches its pin, and the local runner FAILS
        # CLOSED without that model + a credential (`run.frontier_config_or_refuse`)
        # -- so this slice is still credential-free and commits no frontier
        # recording. This layer only makes the arm's raw surface exist and be scorable.
        return ArmSpec(
            name=FRONTIER_ARM,
            instructions=RAW_PROMPT_PATH.read_text(),
            declarations=tuple(RAW_TOOL_SPECS.values()),
        )
    raise ValueError(
        f"unknown arm {name!r}; the arms are {GOVERNED_ARM}, {RAW_ARM} and {FRONTIER_ARM}"
    )
