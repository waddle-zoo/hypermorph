"""What a `Runtime` must be, checked against every implementation (hy-btty).

The Protocol changed three times in one day -- `provenance()` added as a
fourth member, `on_step(Step)` narrowed to `on_message(str)`, and the
`Executor` synchrony constraint written down -- and a person caught each one.

THREE LAYERS, because each catches something the others cannot. Measured
against the shapes this Protocol actually had:

    shape                      isinstance   signature of run()
    today                         yes            yes
    before provenance             NO             yes
    before on_message             yes            NO

A structural check sees a member deleted or renamed and is blind to a
parameter renamed underneath it; a signature check is the reverse. Neither
sees the third change at all, which is why the executor's result type is
checked by calling it.

THE RULE FOR ANYONE ADDING AN ASSERTION HERE, and it is a rule rather than an
observation: DO NOT MAKE `ScriptedRuntime` MORE CAPABLE TO SATISFY THIS SUITE.
If an assertion cannot be answered honestly by a runtime that drives no model,
delete the assertion. The fake is the instrument every boundary decision in
this package was graded with, on the argument that it reimplements almost
nothing; a suite that makes it grow destroys the thing it exists to protect.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import shutil

import pytest

from hyperset.planner import (
    CONTEXT_WINDOW_TOKENS,
    InProcessExecutor,
    Runtime,
    RuntimeConfig,
    ScriptedRuntime,
    ToolCall,
    ToolResult,
    plan_analytics_context,
)
from hyperset.planner.trace import TOOL_CALL, TOOL_RESULT


def _scripted():
    return ScriptedRuntime(script=[])


def _sdk():
    """Built inside the test, not at collection time.

    The first version constructed this in the parameter list, and constructing
    the adapter imports the SDK -- so a contributor without the optional extra
    got a COLLECTION ERROR and no unit gate at all, not a skip (hy-bzwy). CI
    syncs `--all-extras`, so CI could not see it, and hy-f6g's backstop names
    two specific guards and would not have either.
    """
    pytest.importorskip("agents")
    from hyperset.planner.openai_runtime import OpenAIAgentsRuntime

    return OpenAIAgentsRuntime(
        RuntimeConfig(
            model="m",
            base_url="http://127.0.0.1:9/v1",
            allocated_context_window=CONTEXT_WINDOW_TOKENS,
        )
    )


def _claude():
    """The second real vendor implementation (hy-2g31).

    Built inside the test for the same reason `_sdk` is, and it needs no
    endpoint: the SDK is only imported here, and `query()` -- which is what
    spawns the `claude` CLI and would need a credential -- runs in `run()`,
    which conformance never calls.
    """
    pytest.importorskip("claude_agent_sdk")
    from hyperset.planner.claude_runtime import ClaudeAgentRuntime

    return ClaudeAgentRuntime(RuntimeConfig(model="claude-sonnet-5"))


# Factories rather than instances: without the extra this degrades to
# fake-only, which is honest, instead of to nothing at all.
SHIPPED = [
    pytest.param(_scripted, id="scripted"),
    pytest.param(_sdk, id="openai_agents_sdk"),
    pytest.param(_claude, id="claude_agent_sdk"),
]


def _claude_host_gap() -> str | None:
    """Why this seat cannot host the Claude runtime, or None if it can.

    The SDK drives the `claude` CLI as a subprocess against a hosted credential
    -- pyproject splits `planner-claude` off `planner` to carry exactly that
    fact -- so a seat hosts the runtime only when BOTH the SDK imports AND that
    CLI is on PATH. `find_spec` is wrapped because the absence this must read is
    sometimes a `sys.meta_path` finder RAISING `ModuleNotFoundError`
    (`test_optional_extras_move_only_the_skip_count.py` stages an uninstalled
    extra that way), not a clean `None`.
    """
    try:
        if importlib.util.find_spec("claude_agent_sdk") is None:
            return "its 'claude_agent_sdk' import is unavailable"
    except ModuleNotFoundError:
        return "its 'claude_agent_sdk' import is unavailable"
    if shutil.which("claude") is None:
        return "no `claude` CLI runtime is on PATH for it to drive"
    return None


def test_ci_conformance_covers_every_hostable_vendor_runtime():
    """The failure hy-f6g caught, made honest about a runtime CI cannot host.

    `agents` is a pure library: wherever the `planner` extra synced it imports
    with no CLI and no credential, so CI hosting it is not optional and the
    OpenAI runtime IS conformance-checked by the parametrized arms below. This
    asserts it, and that half is the hy-f6g backstop unchanged.

    `claude_agent_sdk` drives the `claude` CLI as a subprocess against a hosted
    credential, so a seat without the SDK OR without that CLI cannot run it, and
    a gate that ASSERTED its presence reds for a reason that is not a breakage
    -- which is what turned main red once the extras-guard's own child ran this
    under a finder that makes the SDK absent (hy-kbnd, Overseer Option 1). Such
    a seat skips WITH A REASON naming the gap. A seat that CAN host it does not
    skip: it constructs the runtime and reds on a real breakage, so the skip
    cannot mask a regression -- and the parametrized `_claude` arms cover the
    Protocol itself in any seat that installed the SDK, this meta-check aside.

    Off CI the extras are optional and nothing is being gated, which is what an
    extra is for.
    """
    if not os.environ.get("CI"):
        pytest.skip("the extras are optional off the gate; nothing is being gated here")

    assert importlib.util.find_spec("agents") is not None, (
        "the 'planner' extra is not installed, so the 'agents' runtime was not "
        "conformance-checked at all: CI must sync with --all-extras"
    )

    gap = _claude_host_gap()
    if gap is not None:
        pytest.skip(f"claude_agent_sdk cannot be hosted on this seat: {gap}")

    from hyperset.planner.claude_runtime import ClaudeAgentRuntime

    assert isinstance(ClaudeAgentRuntime(RuntimeConfig(model="claude-sonnet-5")), Runtime)


@pytest.mark.parametrize("build", SHIPPED)
def test_every_shipped_runtime_satisfies_the_protocol_structurally(build):
    """The layer that catches a member deleted or renamed -- `provenance()`
    added in PR #91 is invisible to every other check here."""
    assert isinstance(build(), Runtime)


@pytest.mark.parametrize("build", SHIPPED)
def test_every_shipped_runtime_has_the_signatures_the_planner_calls(build):
    """The layer `isinstance` cannot provide: a Protocol is structural about
    NAMES, so `run(question, *, on_step, call_tool)` satisfied it right up to
    the moment the planner called it with `on_message` (PR #98)."""
    runtime = build()
    run = inspect.signature(runtime.run).parameters
    # PRESENT AND KEYWORD-ONLY, not an exact set. The first version asserted
    # equality, which forbade an implementation with an extra defaulted
    # parameter or `**kwargs` -- legitimate under a structural Protocol and
    # forbidden by nothing this project documents. A conformance suite
    # stricter than the contract becomes the contract, and then a legal
    # runtime fails for a reason no document states (hy-p7oi).
    assert "question" in run
    for name in ("on_message", "call_tool"):
        assert run[name].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize("build", SHIPPED)
def test_every_shipped_runtime_reports_provenance_it_can_answer_honestly(build):
    """Two keys, and `model` may be `None` -- that is the honest answer for a
    runtime that drove no model, and the reason a fourth member did not cost
    the fake anything. Absent is not permitted; `None` is."""
    reported = build().provenance()

    assert isinstance(reported, dict)
    assert isinstance(reported["runtime"], str)
    assert "model" in reported


def test_the_executor_returns_a_result_rather_than_something_to_await():
    """A NECESSARY condition, and not the property itself (hy-enxz).

    `Executor.call` must stay synchronous because `TOOL_CALL` and
    `TOOL_RESULT` pair by ADJACENCY in the trace, which is why `ToolCall`
    needs no call id. Two checks were candidates and neither is the property:
    `inspect.iscoroutinefunction` catches an `async def call` and PASSES a
    synchronous one returning a `Future`, and this assertion catches the
    `Future` and passes an executor that returns a `ToolResult` after
    disturbing the order of its own calls. The pairing is asserted below, on
    the trace, where it actually lives.
    """
    executor = InProcessExecutor(session_factory=object())

    result = executor.call("resolve_analytics_context", {"not_a_parameter": 1})

    assert isinstance(result, ToolResult)
    assert not inspect.isawaitable(result)


def test_every_tool_call_is_adjacent_to_its_own_result():
    """The property itself, on the surface it lives on (hy-enxz).

    `ToolCall` carries no SDK call id because a result is matched to its
    request by position: the planner records both at one site, in order. The
    result-type check above is necessary and not sufficient -- an executor
    can return a `ToolResult` and still answer out of order -- so this drives
    a run with several calls and asserts the trace alternates.
    """
    answers = [ToolResult(payload={"n": index}) for index in range(3)]
    executor = _RecordingExecutor(answers)
    calls = [ToolCall("list_context_catalog", {"limit": index}) for index in range(3)]

    trace = plan_analytics_context("q", runtime=ScriptedRuntime(script=calls), executor=executor)

    steps = [step for step in trace.steps if step.kind in (TOOL_CALL, TOOL_RESULT)]
    assert [step.kind for step in steps] == [TOOL_CALL, TOOL_RESULT] * 3
    # And each pair is the SAME call: the request's params beside its answer.
    for index, (asked, answered) in enumerate(zip(steps[::2], steps[1::2], strict=True)):
        assert asked.detail["params"] == {"limit": index}
        assert answered.detail["result"] == {"n": index}


class _RecordingExecutor:
    """Answers in the order it was asked, which is what the planner assumes."""

    def __init__(self, answers):
        self._answers = list(answers)

    def call(self, operation: str, params: dict) -> ToolResult:
        return self._answers.pop(0)
