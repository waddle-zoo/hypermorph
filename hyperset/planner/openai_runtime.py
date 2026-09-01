"""The OpenAI Agents SDK runtime (hy-0gq, GitHub #70).

Implemented first because of the local-model requirement, not preference:
GitHub #25 must drive this loop in CI against a local OpenAI-compatible
endpoint with no hosted credential. Ollama serves that shape; nothing local
serves the Anthropic one, so implementing Claude first would have left the
only complete runtime as the one CI cannot exercise.

The adapter translates and delegates. It does not manage message history,
decide a retry policy, or run a dispatch loop -- the SDK owns all three, and
#70 forbids duplicating them. `ScriptedRuntime` satisfying the same protocol
with none of that machinery is the standing check that this stayed true.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from hyperset.planner.loop import planner_prompt, tool_specs
from hyperset.planner.runtime import (
    CONTEXT_WINDOW_TOKENS,
    RuntimeConfig,
    ToolCall,
    UnusableContextWindow,
)
from hyperset.planner.trace import (
    CONTEXT_WINDOW_BELOW_PINNED,
    CONTEXT_WINDOW_UNOBSERVED,
    OPENAI_AGENTS_RUNTIME,
    content_hash,
)
from hyperset.transport.operations import serialize


class OpenAIAgentsRuntime:
    """Drives the SDK's own agent loop over the three served operations."""

    def __init__(
        self,
        config: RuntimeConfig,
        *,
        instructions: str | None = None,
        declarations: Sequence[dict] | None = None,
        responses_api: bool = False,
        reasoning_effort: str | None = None,
        enforce_context_window: bool = True,
    ) -> None:
        # Imported here rather than at module scope so the agent package stays
        # importable without the SDK installed: the fake runtime, the planner
        # rules and their tests do not need it.
        from agents import (
            Agent,
            AsyncOpenAI,
            ModelSettings,
            OpenAIChatCompletionsModel,
            OpenAIResponsesModel,
            set_tracing_disabled,
        )
        from openai.types.shared import Reasoning

        # The SDK uploads its own traces to OpenAI by default. Off, always:
        # the question and the retrieved governed context would leave the
        # process, GitHub #25 runs with no hosted credential at all, and the
        # trace this project scores is the one in `hyperset.planner.trace`.
        set_tracing_disabled(True)

        self._config = config
        self._enforce_context_window = enforce_context_window
        # Defaulted to the planner's own, and settable for GitHub #25's raw
        # baseline arm, which runs the same SDK adapter over a different tool
        # surface. One adapter rather than two: the arms must differ in the
        # substrate, and a baseline with its own adapter would differ in the
        # harness too.
        self._instructions = planner_prompt() if instructions is None else instructions
        self._declarations = tool_specs() if declarations is None else list(declarations)
        self._client_factory = AsyncOpenAI
        self._agent_factory = Agent
        self._model_type = OpenAIResponsesModel if responses_api else OpenAIChatCompletionsModel
        self._model_name = config.model
        # `seed` rides in `extra_body` because `ModelSettings` has no field for
        # it, and this is not decoration: verified against Ollama's
        # OpenAI-compatible endpoint at temperature 1.0, where two calls with
        # the same seed return identical text and two without it do not. Before
        # this the field existed on `RuntimeConfig` and reached nothing, so
        # GitHub #25's pinned-seed requirement held on paper only (hy-am2).
        #
        # No window is sent: that endpoint ignores a request-level context
        # size, verified by posting `options.num_ctx` and watching the model
        # stay loaded at Ollama's 4,096 default. The window belongs to the
        # served tag, so it is read by the caller, refused here when nothing
        # declared one, and recorded on the trace (hy-3wat).
        if responses_api:
            self._settings = ModelSettings(
                reasoning=Reasoning(effort=reasoning_effort) if reasoning_effort else None
            )
        else:
            self._settings = ModelSettings(
                temperature=config.temperature,
                extra_body={} if config.seed is None else {"seed": config.seed},
            )

    def tools(self) -> Sequence[dict]:
        """The declarations this adapter was built with, translated into the
        SDK's shape. Those default to the served descriptions, reused verbatim
        from `OPERATION_SPECS` rather than restated: one contract, and PR #83's
        guard ties it to the binding document."""
        return [
            {
                "type": "function",
                "name": spec["name"],
                "description": spec["description"],
                "parameters": spec["input_schema"],
            }
            for spec in self._declarations
        ]

    def _require_an_observed_window(self) -> None:
        """Refuse a run whose window nobody observed (hy-3wat, hy-c2tg).

        This adapter never learns what Ollama is: it holds numbers or it holds
        nothing. Obtaining them is vendor-specific and lives with the caller,
        so a second backend brings its own probe and this rule is unchanged.

        Refusing rather than warning because the condition is knowable before
        the first token, and because the failure it prevents is silent: a
        model served at a smaller window truncates the governed context away
        and still returns HTTP 200 with a `usage` block counting only what
        survived.

        THE DECLARATION IS NOT EVIDENCE, which is why its absence is not a
        refusal reason here. A tag declaring 999999 against a 131,072
        architecture maximum loads happily and the server allocates 131,072 --
        measured, not feared -- and a server configured through
        `OLLAMA_CONTEXT_LENGTH` declares nothing at all while allocating
        correctly. So a declaration below the pinned window is a cheap early
        refusal, and everything else waits for what was actually allocated.
        """
        declared = self._config.declared_context_window
        if declared is not None and declared < CONTEXT_WINDOW_TOKENS:
            raise UnusableContextWindow(
                CONTEXT_WINDOW_BELOW_PINNED,
                f"{self._config.model!r} asks for {declared} tokens, below the pinned "
                f"{CONTEXT_WINDOW_TOKENS}, so no allocation can rescue it: refused before "
                "the load rather than after it",
            )

        allocated = self._config.allocated_context_window
        if allocated is None:
            raise UnusableContextWindow(
                CONTEXT_WINDOW_UNOBSERVED,
                f"nothing observed what window {self._config.model!r} was actually given, "
                "and a server that truncates does so silently; observe it from whichever "
                "server is serving this model -- hyperset.planner.ollama."
                "observe_context_window is the Ollama one, another backend brings its own "
                "-- and pass it as RuntimeConfig.allocated_context_window",
            )
        if allocated < CONTEXT_WINDOW_TOKENS:
            raise UnusableContextWindow(
                CONTEXT_WINDOW_BELOW_PINNED,
                f"{self._config.model!r} was allocated {allocated} tokens, below the pinned "
                f"{CONTEXT_WINDOW_TOKENS}: the run would be truncated rather than refused, "
                "and the loss is invisible in the response",
            )

    def run(
        self,
        question: str,
        *,
        on_message: Callable[[str], None],
        call_tool: Callable[[ToolCall], dict],
    ) -> None:
        import asyncio

        from agents import FunctionTool, Runner

        if self._enforce_context_window:
            self._require_an_observed_window()

        def _tool(spec: dict) -> FunctionTool:
            async def invoke(_context, arguments: str) -> str:
                import json

                params = json.loads(arguments) if arguments else {}
                # No trace step here: `call_tool` records the request, its
                # result and its refusals at one site for every runtime
                # (hy-ths), so this adapter cannot disagree with the fake
                # about what a tool call looks like in a scored trace.
                return json.dumps(call_tool(ToolCall(spec["name"], params)))

            return FunctionTool(
                name=spec["name"],
                description=spec["description"],
                params_json_schema=spec["parameters"],
                on_invoke_tool=invoke,
                # Served schemas deliberately contain bounded free-form objects such as a
                # manifest draft. The executor validates those after the call; OpenAI strict
                # schemas reject them before inference because every object must enumerate
                # every property. Keep transport validation at the existing executor seam.
                strict_json_schema=False,
            )

        async def run_turn():
            # `asyncio.run` closes its loop after every synchronous turn. Build and close
            # the async HTTP client inside that same loop so a later turn never reuses a
            # keep-alive transport bound to the closed loop (hq-2yed).
            client = self._client_factory(
                base_url=self._config.base_url,
                # A local endpoint needs no credential and the SDK insists on a
                # string; the placeholder never leaves the process.
                api_key=self._config.api_key or "local",
            )
            try:
                model = self._model_type(model=self._model_name, openai_client=client)
                agent = self._agent_factory(
                    name="hyperset-planner",
                    instructions=self._instructions,
                    model=model,
                    model_settings=self._settings,
                    tools=[_tool(spec) for spec in self.tools()],
                )
                return await Runner.run(agent, question, max_turns=self._config.max_turns)
            finally:
                await client.close()

        result = asyncio.run(run_turn())
        final = getattr(result, "final_output", None)
        if final:
            on_message(str(final))

    def provenance(self) -> dict:
        """What this adapter actually drove the model with (hy-pqf3).

        Read off values held by the adapter rather than a second caller
        channel. The seed is the one it put in `extra_body`, the model is the
        immutable value given to every per-turn SDK model, and the window is
        the declaration `run()` refused to start without (hy-3wat) -- named
        `declared` here because that is all it is until hy-c2tg reconciles it
        against what the server allocated.
        """
        return {
            "runtime": OPENAI_AGENTS_RUNTIME,
            "model": self._model_name,
            "seed": (self._settings.extra_body or {}).get("seed"),
            "temperature": self._settings.temperature,
            # The prompt and tool surface THIS adapter drove the model with,
            # so a caller cannot hand the loop one pair and the adapter
            # another: the loop records the pair it was given, and a recording
            # that disagrees with what ran is refused
            # (hyperset.evals.recording). Two hashes rather than the texts,
            # because provenance is a record and not a copy of the input.
            "instructions_hash": content_hash(self._instructions),
            "tools_hash": content_hash(serialize(self._declarations)),
            # Both numbers, because they can disagree without saying so: a
            # clamped tag asks for one window and is given another, and an
            # operator reading a trace should see that even on a run that was
            # fine (hy-c2tg).
            "allocated_context_window": self._config.allocated_context_window,
            "declared_context_window": self._config.declared_context_window,
            "max_turns": self._config.max_turns,
            "context_window_enforced": self._enforce_context_window,
        }

    def close(self) -> None:
        # Each run closes its client before its event loop closes.
        return None
