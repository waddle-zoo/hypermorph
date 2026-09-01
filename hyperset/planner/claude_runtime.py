"""The Claude Agent SDK runtime (hy-2g31, GitHub #70, GitHub #73).

The second real vendor implementation, and that is its main job. GitHub #70
names Claude Agent SDK and OpenAI Agents SDK as the two supported runtimes and
forbids inventing a bespoke framework "unless a concrete SDK limitation is
demonstrated". Until this landed, the `Runtime` Protocol had exactly one real
vendor behind it, which is the shape #70 warns about: a boundary one SDK
satisfies is an interface drawn around that SDK. `ScriptedRuntime` is the
argument that the boundary is small; this is the argument that it is a
boundary.

The adapter translates and delegates, exactly as `openai_runtime` does. The
SDK's own loop drives the conversation: `query()` manages message history,
decides what to retry, and dispatches tool calls to the in-process MCP server
built here. Those are the three things #70 forbids duplicating, and none of
them is written in this file.

WHAT IT CANNOT DO, stated here because the omission is load-bearing rather
than pending. This runtime cannot be an arm of GitHub #25's benchmark:

  * `ClaudeAgentOptions` has no `seed` and no `temperature` field, and the CLI
    it drives exposes no flag for either. #25 pins both, and a run that cannot
    honour a pin must not be recorded as one that did -- so a `RuntimeConfig`
    carrying an explicit seed is REFUSED here rather than silently ignored.
  * It requires the `claude` CLI on PATH and a hosted Anthropic credential.
    #25 requires a local endpoint with no hosted credential, which is why
    `openai_runtime` was implemented first and why Ollama is the arm.

So this ships for GitHub #73's reference example and for the Protocol claim,
not for the benchmark. Nothing here weakens ADR 0013's gate, because nothing
here can run in it.

ONE THING PROVENANCE DELIBERATELY DOES NOT REPORT, written here because the
absence is a decision and not an oversight. The SDK bills more models than the
one asked for -- a probe requesting `claude-sonnet-5` returned usage for
`claude-sonnet-5` AND `claude-haiku-4-5-20251001`, the CLI's own internal work
-- and the full list arrives on the SDK's final result message, at the END of
a run. `plan_analytics_context` reads `provenance()` BEFORE calling `run()` to
stamp the trace, so a field holding that list would be `[]` on every trace
this project can produce. A field whose only consumer always sees it empty
reads as "nothing else was billed", which is worse than not answering, so the
fact lives in this paragraph instead.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from hyperset.planner.loop import planner_prompt, tool_specs
from hyperset.planner.runtime import RuntimeConfig, ToolCall
from hyperset.planner.trace import CLAUDE_AGENT_RUNTIME, content_hash
from hyperset.transport.operations import serialize

MCP_SERVER_NAME = "hyperset"


# The SDK namespaces an in-process MCP tool as `mcp__<server>__<tool>`, and
# `allowed_tools` is matched against that spelling rather than ours. Written
# once, because a tool declared under one name and permitted under another is
# a tool the model is shown and then refused.
def _sdk_tool_name(operation: str) -> str:
    return f"mcp__{MCP_SERVER_NAME}__{operation}"


class UnpinnableRuntime(ValueError):
    """A run was configured with a pin this SDK cannot honour.

    Not a `PlannerRefusal`: those carry a `RUN_FAILURE_CODES` code and are
    recorded on a trace as a run that started and failed. This is refused at
    CONSTRUCTION, before there is a run to record, and is a caller error in
    the same category as a malformed directive.
    """


class ClaudeAgentRuntime:
    """Drives the Claude Agent SDK's own agent loop over the served operations."""

    def __init__(
        self,
        config: RuntimeConfig,
        *,
        instructions: str | None = None,
        declarations: Sequence[dict] | None = None,
    ) -> None:
        # Imported here rather than at module scope for the reason
        # `openai_runtime` does it: the planner rules, the fake and their
        # tests must stay importable without the optional SDK installed.
        from claude_agent_sdk import ClaudeAgentOptions

        if not config.model:
            raise UnpinnableRuntime(
                "this runtime requires an explicit model: the CLI picks a default when none "
                "is named, and provenance would then report None for a model that ran"
            )
        if config.seed is not None:
            # The defect `provenance()` exists for, caught one constructor
            # earlier. The SDK has nowhere to put a seed, so accepting one
            # would produce a trace claiming a pinning that did not happen --
            # which is exactly hy-am2, where a seed was configured and never
            # sent.
            raise UnpinnableRuntime(
                f"RuntimeConfig.seed={config.seed} cannot be honoured: the Claude Agent SDK "
                "exposes no seed, so a run configured with one would be recorded as pinned "
                "and would not be; drop the seed, or use a runtime that can send it"
            )

        self._config = config
        # Defaulted to the planner's own and settable for the same reason as
        # the other adapter: one adapter over two tool surfaces, so arms
        # differ in the substrate rather than in the harness.
        self._instructions = planner_prompt() if instructions is None else instructions
        self._declarations = tool_specs() if declarations is None else list(declarations)
        self._options_factory = ClaudeAgentOptions

    def tools(self) -> Sequence[dict]:
        """The declarations this adapter was built with, in the shape the
        SDK's `tool()` primitive takes. Reused verbatim from `OPERATION_SPECS`
        rather than restated, so the model reads the served contract."""
        return [
            {
                "name": spec["name"],
                "description": spec["description"],
                "input_schema": spec["input_schema"],
            }
            for spec in self._declarations
        ]

    def run(
        self,
        question: str,
        *,
        on_message: Callable[[str], None],
        call_tool: Callable[[ToolCall], dict],
    ) -> None:
        import anyio
        from claude_agent_sdk import (
            AssistantMessage,
            TextBlock,
            create_sdk_mcp_server,
            query,
            tool,
        )

        def _tool(spec: dict):
            @tool(spec["name"], spec["description"], spec["input_schema"])
            async def invoke(arguments: dict) -> dict:
                # No trace step here: `call_tool` records the request, its
                # result and its refusals at one site for every runtime
                # (hy-ths), so this adapter cannot disagree with the fake
                # about what a tool call looks like in a scored trace.
                payload = call_tool(ToolCall(spec["name"], dict(arguments or {})))
                return {"content": [{"type": "text", "text": serialize(payload)}]}

            return invoke

        server = create_sdk_mcp_server(
            name=MCP_SERVER_NAME, version="0.1.0", tools=[_tool(spec) for spec in self.tools()]
        )
        options = self._options_factory(
            mcp_servers={MCP_SERVER_NAME: server},
            allowed_tools=[_sdk_tool_name(spec["name"]) for spec in self.tools()],
            # The SDK ships a file-editing, shell-running, web-fetching agent
            # by default. Hyperset's planner may reach the three served
            # operations and nothing else, so every built-in is off: an agent
            # that can read the repository could answer from it instead of
            # from governed context, which is the measurement destroyed.
            tools=[],
            # Inherit no user, project or local settings. Measured, not
            # precautionary: without this the probe run picked up this
            # developer's own CLAUDE.md and appended an unrelated remark to
            # the model's answer, which on a scored surface is contamination
            # from whichever machine happened to run it.
            setting_sources=[],
            system_prompt=self._instructions,
            model=self._config.model,
            max_turns=self._config.max_turns,
            # The tools are ours and the built-ins are off, so there is no
            # prompt to answer; the permission surface exists for interactive
            # Claude Code, and a planning run has no human at the keyboard.
            permission_mode="bypassPermissions",
        )

        async def drive() -> None:
            async for message in query(prompt=question, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            on_message(block.text)

        anyio.run(drive)

    def provenance(self) -> dict:
        """What this adapter drove, and what it could not pin.

        `model` is the one this adapter asked for, read off its own config
        rather than echoed from a caller's -- the planner reads this BEFORE
        the run to stamp the trace, so a value only knowable afterwards would
        leave the field empty on every run.

        `seed` and `temperature` are `None` and that is a fact rather than a
        gap: the SDK can send neither, the constructor refuses a seed that
        would be silently dropped, and reporting the configured temperature
        here would be the configuration echo this member exists to prevent.
        """
        return {
            "runtime": CLAUDE_AGENT_RUNTIME,
            "model": self._config.model,
            "seed": None,
            "temperature": None,
            "instructions_hash": content_hash(self._instructions),
            "tools_hash": content_hash(serialize(self._declarations)),
            "max_turns": self._config.max_turns,
        }

    def close(self) -> None:
        # `query()` owns the subprocess for the duration of its own iteration;
        # nothing outlives `run()` to tear down here.
        return None
