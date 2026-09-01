"""The Claude Agent SDK adapter's own contract (hy-2g31).

Never drives a model. The SDK's loop is the SDK's, and what is asserted here
is the only part this project wrote: the translation into the SDK's shapes,
the options it is driven with, and the two pins it refuses rather than drops.
`tests/unit/planner/test_runtime_conformance.py` covers the Protocol itself.
"""

from __future__ import annotations

import json

import pytest

from hyperset.planner.claude_runtime import ClaudeAgentRuntime, UnpinnableRuntime
from hyperset.planner.runtime import RuntimeConfig, ToolCall
from hyperset.planner.trace import CLAUDE_AGENT_RUNTIME, RUNTIME_NAMES


@pytest.fixture
def claude_agent_sdk():
    """The SDK, or a skip -- requested by name, so the skip is one PER TEST.

    The guard used to sit at module level, where it does not skip these tests:
    it removes the module from collection and leaves ONE skip entry standing in
    for all seven, so an agent without the extra measures a suite six tests
    smaller than an agent with it (hy-a2ou).

    NOTHING HERE NEEDS THE SDK TO IMPORT, which is the asymmetry with
    `tests/unit/evals/test_inspect_task.py` and the reason the imports above
    could move to the top rather than into a fixture. `ClaudeAgentRuntime`
    imports `claude_agent_sdk` inside `__init__`, deliberately -- measured with
    a `sys.meta_path` finder, `hyperset.planner.claude_runtime` imports clean
    with the package absent. So the module-level guard was not load-bearing at
    all; it was buying an unimportable module a skip it did not need.

    What DOES need the SDK is construction, because that import is the first
    statement of `__init__` -- ahead of the two refusals -- so even the arms
    that assert a constructor refusal reach it.
    """
    return pytest.importorskip("claude_agent_sdk")


MODEL = "claude-sonnet-5"
DECLARATIONS = [
    {"name": "list_context_catalog", "description": "List domains.", "input_schema": {"a": 1}}
]


def _runtime(**overrides):
    config = RuntimeConfig(**{"model": MODEL, **overrides})
    return ClaudeAgentRuntime(config, instructions="be exact", declarations=DECLARATIONS)


def test_the_declarations_are_translated_not_restated(claude_agent_sdk):
    """The served descriptions reach the model verbatim; a planner-local copy
    would be a second statement of one contract."""
    assert _runtime().tools() == [
        {"name": "list_context_catalog", "description": "List domains.", "input_schema": {"a": 1}}
    ]


def test_a_seed_is_refused_rather_than_silently_dropped(claude_agent_sdk):
    """`ClaudeAgentOptions` has no seed. Accepting one would produce a trace
    claiming a pinning that did not happen, which is the defect hy-am2 fixed
    one layer down and the reason `provenance()` exists."""
    with pytest.raises(UnpinnableRuntime) as refused:
        _runtime(seed=20260728)

    assert "20260728" in str(refused.value)
    assert "exposes no seed" in str(refused.value)


def test_a_run_with_no_model_named_is_refused(claude_agent_sdk):
    """The CLI picks a default when none is named, and `provenance()` would
    then report `None` for a model that really ran."""
    with pytest.raises(UnpinnableRuntime) as refused:
        ClaudeAgentRuntime(RuntimeConfig(model=""))

    assert "explicit model" in str(refused.value)


def test_provenance_reports_the_two_pins_it_cannot_make_as_absent(claude_agent_sdk):
    """`None` is a fact here, not a gap: the SDK can send neither, so echoing
    the configured temperature would be the configuration echo this member
    exists to prevent."""
    reported = _runtime(temperature=0.7).provenance()

    assert reported["runtime"] == CLAUDE_AGENT_RUNTIME
    assert reported["model"] == MODEL
    assert reported["seed"] is None
    assert reported["temperature"] is None
    # And nothing claims to know what the SDK actually billed. A probe showed
    # it bills models beyond the one requested, but that list only exists on
    # the SDK's final result message while the planner stamps the trace from
    # this BEFORE the run -- so the key would be empty on every trace this
    # project can produce, and would read as "nothing else was billed".
    assert "observed_models" not in reported


def test_the_runtime_name_is_in_the_shared_vocabulary():
    """A scorer telling this apart from the benchmark arm reads this name, and
    a name only its own module knows is one nothing can enumerate."""
    assert CLAUDE_AGENT_RUNTIME in RUNTIME_NAMES


def test_a_tool_call_is_delegated_to_the_planner_and_never_answered_here(
    claude_agent_sdk, monkeypatch
):
    """The whole adapter, in one assertion.

    The SDK dispatches; this translates. The tool handed to the SDK forwards
    the model's arguments to `call_tool` unchanged and hands back whatever the
    planner returned -- it does not interpret the operation, record a trace
    step, or answer anything itself.
    """
    captured = {}

    def fake_server(*, name, version, tools):
        captured["name"] = name
        captured["tools"] = tools
        return {"type": "sdk", "name": name}

    def fake_query(*, prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options

        async def nothing():
            return
            yield  # pragma: no cover - an empty async generator

        return nothing()

    monkeypatch.setattr(claude_agent_sdk, "create_sdk_mcp_server", fake_server)
    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)

    asked = []
    _runtime().run(
        "which domains exist?",
        on_message=lambda text: None,
        call_tool=lambda call: asked.append(call) or {"domains": ["revenue"]},
    )

    (declared,) = captured["tools"]
    assert declared.name == "list_context_catalog"

    import anyio

    answer = anyio.run(declared.handler, {"limit": 5})

    assert asked == [ToolCall("list_context_catalog", {"limit": 5})]
    # Returned in the SDK's content shape, carrying the planner's payload
    # verbatim -- the adapter adds no commentary of its own.
    assert json.loads(answer["content"][0]["text"]) == {"domains": ["revenue"]}


def test_the_sdk_is_driven_with_no_built_in_tools_and_no_inherited_settings(
    claude_agent_sdk, monkeypatch
):
    """Two isolations that are measurements rather than caution.

    The SDK ships a file-editing, shell-running agent by default: left on, a
    planner could answer from the repository instead of from governed context.
    And without `setting_sources=[]` a probe run inherited this developer's own
    CLAUDE.md and appended an unrelated remark to the model's answer.
    """
    captured = {}
    monkeypatch.setattr(
        claude_agent_sdk,
        "create_sdk_mcp_server",
        lambda *, name, version, tools: {"type": "sdk", "name": name},
    )

    def fake_query(*, prompt, options):
        captured["options"] = options

        async def nothing():
            return
            yield  # pragma: no cover - an empty async generator

        return nothing()

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)

    _runtime().run("q", on_message=lambda text: None, call_tool=lambda call: {})

    options = captured["options"]
    assert options.tools == []
    assert options.setting_sources == []
    assert options.model == MODEL
    assert options.system_prompt == "be exact"
    # Declared and permitted under the SDK's namespaced spelling, so the model
    # is not shown a tool it is then refused.
    assert options.allowed_tools == ["mcp__hyperset__list_context_catalog"]
