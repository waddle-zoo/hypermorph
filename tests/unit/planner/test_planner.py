"""The planner's own rules (hy-0gq): what it may retry, what it records, and
what it refuses to do with a question.

Driven by `ScriptedRuntime` rather than a live model. That is not only for
determinism -- it is the check that the runtime boundary is a boundary: a fake
plugs into the same seam a real SDK does, holding no retry policy, no message
history and no dispatch loop of its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from hyperset.bundle import RETRYABLE_WARNING_CODES
from hyperset.context.schema import REF_MALFORMED, REF_NOT_OBSERVED
from hyperset.planner import (
    CONTEXT_WINDOW_BELOW_PINNED,
    CONTEXT_WINDOW_TOKENS,
    CONTEXT_WINDOW_UNOBSERVED,
    OPENAI_AGENTS_RUNTIME,
    RESOLVE_PATH_OPERATIONS,
    RUN_FAILURE_CODES,
    RUNTIME_NAMES,
    SCRIPTED_RUNTIME,
    InProcessExecutor,
    PlannerRefusal,
    RuntimeConfig,
    ScriptedRuntime,
    ToolCall,
    ToolResult,
    UnusableContextWindow,
    fixable_warnings,
    plan_analytics_context,
    refusals,
    tool_specs,
    tools_hash,
)
from hyperset.planner.ollama import (
    UnreadableAllocation,
    _allocated_from,
    ollama_root,
    parse_num_ctx,
)
from hyperset.planner.trace import (
    PLANNER_MESSAGE,
    RUN_FAILED,
    TOOL_CALL,
    TOOL_REFUSAL,
    TOOL_RESULT,
)
from hyperset.transport.operations import (
    DIRECTIVE_REQUIRED,
    DISCOVER,
    OPERATION_SPECS,
    OPERATIONS,
    OperationError,
)

QUESTION = "Which source and rules should an analyst use for recognized revenue by region?"


@contextlib.contextmanager
def _recording_endpoint():
    """A local OpenAI-compatible endpoint that records what it was sent.

    Small on purpose: one canned completion, no model, no network beyond
    loopback. It exists so a test can assert what left the process rather than
    what a settings object held.
    """
    seen: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 -- http.server's spelling
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append(json.loads(body))
            payload = json.dumps(
                {
                    "id": "rec-1",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "m",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "done"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _Executor:
    """Answers each call from a queue, so a test states what came back."""

    def __init__(self, answers: list[ToolResult]) -> None:
        self._answers = list(answers)
        self.calls: list[tuple[str, dict]] = []

    def call(self, operation: str, params: dict) -> ToolResult:
        self.calls.append((operation, params))
        return self._answers.pop(0)


def _fixable_on_the_wire(answer: dict) -> list[str]:
    """What a planner can act on, from the channel a planner really has.

    `ToolResult.to_dict()` is what a runtime receives, so this reads
    `resolution.warnings` and filters by the exported vocabulary. It exists to
    keep the tests honest about which side of the boundary each fact lives on
    (hy-amtg).
    """
    resolution = answer.get("resolution") or {}
    return [
        entry["code"]
        for entry in resolution.get("warnings") or []
        if entry.get("code") in RETRYABLE_WARNING_CODES
    ]


def _refusal(code: str) -> ToolResult:
    return ToolResult(error=OperationError(code, f"{code} happened", recovery="do the thing"))


# Idioms that inspect text. Not a general-purpose detector -- a source scan is
# a proxy for the property, and proxies drift from what they stand for -- but
# these are what reintroducing `_match` actually looks like.
_INSPECTION = ("lower()", "upper()", "casefold()", ".split(", "re.search", "re.match", "re.findall")


def _inspects_question(root) -> list[str]:
    """Every place in a package that both names `question` and inspects text.

    Scoped to the PACKAGE rather than to `planner.py`, because the violation
    will not arrive in the file named planner: it arrives as "I will put the
    normalisation in a small helper". A test that reads one file passes on
    that change, and its name -- never_reads_the_question -- would stop the
    next person thinking about it.
    """
    found = []
    for path in sorted(root.rglob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            code = line.split("#", 1)[0]
            if "question" in code and any(idiom in code for idiom in _INSPECTION):
                found.append(f"{path.name}:{number}")
    return found


class _Untouchable(str):
    """A question that refuses to be read as text.

    The property itself rather than a proxy for it: if any Hyperset code on
    this path indexes, slices, lowercases or searches the question, the run
    raises instead of quietly succeeding.
    """

    def _refuse(self, *_args, **_kwargs):
        raise AssertionError("Hyperset inspected the question text; that is `_match` returning")

    __getitem__ = __contains__ = _refuse
    lower = upper = casefold = split = strip = find = startswith = endswith = _refuse


def test_no_hyperset_code_inspects_the_question_at_runtime(tmp_path):
    """Part 1 deleted domain-guessing from a question's wording, and this is
    the invariant rather than a promise about one file: the question travels
    as an opaque value to the runtime and no further."""
    executor = _Executor([ToolResult(payload={"domains": []})])
    runtime = ScriptedRuntime(script=[ToolCall("list_context_catalog", {})])

    trace = plan_analytics_context(_Untouchable(QUESTION), runtime=runtime, executor=executor)

    assert trace.of_kind(TOOL_RESULT)


def test_the_agent_package_contains_no_question_inspection():
    """The static half, covering paths a single run does not exercise."""
    package = __import__("pathlib").Path(__file__).parents[3] / "hyperset" / "planner"

    assert _inspects_question(package) == []


def test_that_scan_catches_the_evasion_it_exists_for(tmp_path):
    """The guard's own guard, twice over: the obvious violation, and the
    tidy one that a planner-only scan would miss."""
    package = tmp_path / "agent"
    package.mkdir()
    (package / "planner.py").write_text("def plan(question):\n    return question.lower()\n")

    assert _inspects_question(package) == ["planner.py:2"]

    (package / "planner.py").write_text("def plan(question):\n    return normalise(question)\n")
    (package / "util.py").write_text("def normalise(question):\n    return question.lower()\n")

    assert _inspects_question(package) == ["util.py:2"]


def test_a_refusal_reaches_the_caller_instead_of_being_absorbed():
    """`directive_required` rather than a ref code, because that is one the
    server can actually raise: ref problems come back as warnings inside a
    SERVED bundle, not as refusals (hy-amtg)."""
    executor = _Executor([_refusal(DIRECTIVE_REQUIRED)])
    runtime = ScriptedRuntime(
        script=[ToolCall("resolve_analytics_context", {"query": QUESTION, "directive": {}})]
    )

    trace = plan_analytics_context(QUESTION, runtime=runtime, executor=executor)

    (refused,) = refusals(trace)
    assert refused.kind == TOOL_REFUSAL
    assert refused.detail["code"] == DIRECTIVE_REQUIRED
    assert refused.detail["recovery"]


def test_a_refusal_is_not_retried_by_a_planner_reading_the_served_disclosures():
    """A refusal is final, and a planner that reads the answer stops (hy-amtg).

    The script decides from `resolution.warnings` filtered by the exported
    vocabulary, which is what a real planner must do: the trace is WRITE-ONLY.
    A runtime and the model behind it see `ToolResult.to_dict()`; only a
    post-hoc reader sees `retryable_warnings` on a step. An earlier version of
    this test read the step's key from inside the loop, where it can never
    appear -- an unreachable condition in place of the always-false one it
    replaced.
    """
    executor = _Executor([_refusal(DIRECTIVE_REQUIRED), _refusal(DIRECTIVE_REQUIRED)])

    def script(results):
        yield ToolCall("resolve_analytics_context", {"query": QUESTION, "directive": {}})
        if results and _fixable_on_the_wire(results[-1]):
            yield ToolCall("resolve_analytics_context", {"query": QUESTION, "directive": {}})

    trace = plan_analytics_context(
        QUESTION, runtime=ScriptedRuntime(script=script), executor=executor
    )

    assert len(executor.calls) == 1
    assert len(trace.of_kind(TOOL_REFUSAL)) == 1
    # And the refusal no longer claims a retryability it never had.
    (refused,) = trace.of_kind(TOOL_REFUSAL)
    assert "retryable" not in refused.detail


def _bundle(*codes: str, status: str = "governed") -> ToolResult:
    """A served bundle carrying the disclosures a resolve really returns: a
    bad ref is `refused=False` with a warning, measured across all three
    operations before this rule moved (hy-amtg)."""
    return ToolResult(
        payload={
            "bundle_id": "bundle-1",
            "resolution": {
                "status": status,
                "warnings": [{"code": code, "message": f"{code} happened"} for code in codes],
            },
        }
    )


def test_a_served_bundle_says_which_of_its_disclosures_are_fixable():
    """The rule reads the surface the condition actually reaches (hy-amtg).

    hy-6ae split the ref codes by what a caller must DO -- edit, qualify, or
    neither -- and that is a retryability rule about WARNINGS. A bundle that
    came back with a fixable problem is usable and incomplete, which is the
    state a planner has to decide about, and the trace now says so instead of
    leaving a scorer to re-derive it from the payload.
    """
    executor = _Executor([_bundle(REF_MALFORMED, REF_NOT_OBSERVED)])

    trace = plan_analytics_context(
        QUESTION,
        runtime=ScriptedRuntime(
            script=[ToolCall("resolve_analytics_context", {"query": QUESTION, "directive": {}})]
        ),
        executor=executor,
    )

    (answered,) = trace.of_kind(TOOL_RESULT)
    # `ref_not_observed` is disclosed and is NOT fixable by asking again: the
    # estate was read and the asset is not in it, so nothing the planner sends
    # changes the answer, and a planner that retries it is the
    # retry-until-something-works loop.
    assert answered.detail["retryable_warnings"] == [REF_MALFORMED]


def test_the_three_states_of_the_fixable_list_are_distinguishable():
    """Absent, empty and present are three facts, not two (hy-amtg).

    `None` says the answer carried no resolution at all -- a catalog listing
    or a plan check. `[]` says a resolution came back with nothing fixable. A
    list says a caller could act. Collapsing the first two would let a scorer
    counting clean resolves count every catalog step as one.
    """
    executor = _Executor([_bundle()])

    trace = plan_analytics_context(
        QUESTION,
        runtime=ScriptedRuntime(
            script=[ToolCall("resolve_analytics_context", {"query": QUESTION, "directive": {}})]
        ),
        executor=executor,
    )

    (answered,) = trace.of_kind(TOOL_RESULT)
    assert answered.detail["retryable_warnings"] == []
    assert fixable_warnings({"domains": []}) is None
    assert fixable_warnings({"resolution": {"status": "governed", "warnings": []}}) == []


def test_the_trace_states_the_policy_a_run_was_scored_under():
    """What the recorded field actually buys, stated accurately (hy-amtg).

    NOT that these two runs were previously indistinguishable -- they were
    not. The payload is retained in the trace, so a scorer could always
    re-derive which disclosures were fixable, and these two scripts call
    different operations on their second step anyway.

    What it buys is that the POLICY is pinned at run time, exactly as
    `prompt_hash` and `tools_hash` pin the inputs. A scorer reading a trace
    does not import `RETRYABLE_WARNING_CODES` and apply today's version of it
    to a run recorded under an older one -- which is how a vocabulary change
    silently rescores history.
    """

    def run(script, answers):
        return plan_analytics_context(
            QUESTION,
            runtime=ScriptedRuntime(script=script),
            executor=_Executor(answers),
        )

    resolve = ToolCall("resolve_analytics_context", {"query": QUESTION, "directive": {}})
    validate = ToolCall("validate_analytics_plan", {"query": QUESTION, "bundle_id": "bundle-1"})

    obedient = run([resolve, resolve], [_bundle(REF_MALFORMED), _bundle()])
    indifferent = run([resolve, validate], [_bundle(REF_MALFORMED), ToolResult(payload={})])

    def shape(trace):
        return [
            (step.detail["operation"], step.detail.get("retryable_warnings"))
            for step in trace.of_kind(TOOL_RESULT)
        ]

    assert shape(obedient) == [
        ("resolve_analytics_context", [REF_MALFORMED]),
        ("resolve_analytics_context", []),
    ]
    # `None`, not `[]`: a plan check carries no resolution, so it cannot
    # disclose a ref problem, and saying "nothing fixable" about it would let
    # a scorer count it as a clean resolve.
    assert shape(indifferent) == [
        ("resolve_analytics_context", [REF_MALFORMED]),
        ("validate_analytics_plan", None),
    ]


def test_every_step_is_structured_and_the_pinned_inputs_are_hashed():
    """The trace is what GitHub #25 scores, so a scorer branches on `kind` and
    reads `detail`, never prose. Both content-addressed inputs are recorded:
    a spec edit changes planner behaviour as surely as a prompt edit."""
    executor = _Executor([ToolResult(payload={"domains": []})])
    runtime = ScriptedRuntime(script=["reading the catalog", ToolCall("list_context_catalog", {})])

    trace = plan_analytics_context(QUESTION, runtime=runtime, executor=executor)
    payload = trace.to_dict()

    assert payload["prompt_hash"].startswith("sha256:")
    assert payload["tools_hash"] == tools_hash()
    assert payload["prompt_hash"] != payload["tools_hash"]
    # The whole sequence, not its tail: a runtime that also emitted the call
    # would double it, and that is the hy-ths defect in its observable form.
    assert [step["kind"] for step in payload["steps"]] == [
        PLANNER_MESSAGE,
        TOOL_CALL,
        TOOL_RESULT,
    ]
    assert all(isinstance(step["detail"], dict) for step in payload["steps"])


def test_a_runtime_cannot_choose_how_the_model_message_is_recorded():
    """The shape is stated once, and a runtime has no way to state it (hy-kz6).

    hy-ths moved the tool step kinds in front of the boundary because two
    adapters could disagree about them. `PLANNER_MESSAGE` had to stay with the
    runtime -- only it can see what the model said -- but its DETAIL SHAPE did
    not, and two adapters were building `{"text": ...}` independently and
    agreeing by coincidence. The callback now takes a string, so a third
    runtime cannot invent `{"message": ...}` even by accident.
    """

    class _Talking:
        def tools(self):
            return ()

        def provenance(self):
            return {"runtime": "talking", "model": None}

        def run(self, question, *, on_message, call_tool):
            on_message("reading the catalog")

        def close(self):
            return None

    trace = plan_analytics_context(QUESTION, runtime=_Talking(), executor=_Executor([]))

    (said,) = trace.of_kind(PLANNER_MESSAGE)
    assert said.detail == {"text": "reading the catalog"}
    assert said.summary == "reading the catalog"


def test_a_runtime_that_reports_something_other_than_text_is_refused():
    """Narrowing the callback only helps if the narrowing is enforced
    (hy-kz6). A runtime handing over a dict would otherwise have it stored
    under `text`, which is the per-adapter shape arriving through the
    parameter introduced to prevent it."""

    class _Structured:
        def tools(self):
            return ()

        def provenance(self):
            return {"runtime": "structured", "model": None}

        def run(self, question, *, on_message, call_tool):
            on_message({"message": "not text"})

        def close(self):
            return None

    trace = plan_analytics_context(QUESTION, runtime=_Structured(), executor=_Executor([]))

    # It fails the run rather than corrupting the record: the planner catches
    # what a runtime raises and records `RUN_FAILED` (hy-edj).
    (failed,) = trace.of_kind(RUN_FAILED)
    assert failed.detail["exception"] == "TypeError"
    assert trace.of_kind(PLANNER_MESSAGE) == []


def test_a_runtime_that_emits_no_steps_still_produces_a_complete_trace():
    """Step emission is the planner's, not each adapter's (hy-ths).

    A step kind every runtime has to implement independently is a step kind
    two runtimes can disagree about, and the trace is what GitHub #25 scores,
    so a disagreement is a scoring difference. The runtime here does nothing
    but call the tool -- the least an adapter can do -- and the call is still
    recorded, paired with its result.
    """

    class Mute:
        def tools(self):
            return ()

        def provenance(self):
            return {"runtime": "mute", "model": None}

        def run(self, question, *, on_message, call_tool):
            call_tool(ToolCall("list_context_catalog", {"limit": 5}))

        def close(self):
            return None

    executor = _Executor([ToolResult(payload={"domains": []})])

    trace = plan_analytics_context(QUESTION, runtime=Mute(), executor=executor)

    assert [step.kind for step in trace.steps] == [TOOL_CALL, TOOL_RESULT]
    (called,) = trace.of_kind(TOOL_CALL)
    assert called.detail == {"operation": "list_context_catalog", "params": {"limit": 5}}


def test_the_planner_is_given_the_resolve_path_descriptions_verbatim_and_no_assist_op():
    """The planner's tool surface is the resolve-path ALLOWLIST, not every
    served operation. Two guards in one, and both are load-bearing (hy-gh-206):

    - the descriptions of catalog/resolve/validate are the served ones VERBATIM,
      so doc drift on them still reds here (PR #83's guarantee, kept);
    - a served assist operation -- DISCOVER, or any future one -- is NOT a
      planner tool, so a leak into tool_specs() reds here. That absence is what
      keeps serving discover from moving tools_hash and invalidating the
      committed #25 recordings.
    """
    planner = {spec["name"]: spec["description"] for spec in tool_specs()}
    assert set(planner) == set(RESOLVE_PATH_OPERATIONS)
    for name in RESOLVE_PATH_OPERATIONS:
        assert planner[name] == OPERATION_SPECS[name]["description"]
    # Served, but never a planner tool: the leak this guard exists to catch.
    assert DISCOVER in OPERATIONS
    assert DISCOVER not in planner


def test_the_planner_tool_surface_is_a_strict_subset_of_what_is_served():
    """The allowlist's safe default made real, not vacuous: at least one served
    operation is excluded from the planner, so 'excluded by default' is
    exercised. If tool_specs() ever reverted to 'every served OPERATION_SPECS',
    the subset stops being strict and this reds -- which is the leak that would
    move tools_hash and invalidate the committed #25 recordings."""
    planner_tools = {spec["name"] for spec in tool_specs()}
    assert planner_tools == set(RESOLVE_PATH_OPERATIONS)
    assert planner_tools < set(OPERATIONS)


def test_a_run_that_raises_returns_a_degraded_trace_rather_than_none():
    """The endpoint being down is a scored outcome, not an absent one (hy-edj).

    `RUN_FAILED` is in `STEP_KINDS` and `refusals()` reads it, which only makes
    sense if the trace survives the failure. Re-raising would lose it -- the
    trace is a local -- and hanging it on the exception is the side channel
    this module's docstring rejects. So the exception is recorded and the
    partial trace is returned: GitHub #25 can tell "the endpoint was down"
    from "the planner called nothing", which are the same absence today.

    The cost is taken knowingly: a bug in Hyperset's own code now arrives as a
    step rather than a traceback, so the exception type is recorded as its own
    field instead of being folded into a sentence.
    """
    executor = _Executor([ToolResult(payload={"domains": []})])

    class Exploding(ScriptedRuntime):
        def run(self, question, *, on_message, call_tool):
            call_tool(ToolCall("list_context_catalog", {}))
            raise RuntimeError("the model went away")

    runtime = Exploding(script=[])

    trace = plan_analytics_context(QUESTION, runtime=runtime, executor=executor)

    assert runtime.closed
    # What happened before the raise is still in the trace, so a scorer sees a
    # degraded run rather than a discarded one.
    assert [step.kind for step in trace.steps] == [TOOL_CALL, TOOL_RESULT, RUN_FAILED]
    (failed,) = trace.of_kind(RUN_FAILED)
    assert failed.detail["exception"] == "RuntimeError"
    assert failed.detail["reason"] == "the model went away"
    assert refusals(trace) == [failed]


def test_a_teardown_defect_is_not_recorded_as_a_failed_run():
    """`close()` sits outside the catch on purpose: if it did not, one step
    kind would mean both "the run failed" and "our teardown is broken", and a
    scorer reading `RUN_FAILED` could not tell which it had."""

    class Unclosable(ScriptedRuntime):
        def close(self):
            raise RuntimeError("teardown is broken")

    with pytest.raises(RuntimeError, match="teardown is broken"):
        plan_analytics_context(QUESTION, runtime=Unclosable(script=[]), executor=_Executor([]))


def test_a_fixture_trace_says_it_came_from_a_fake():
    """A trace from `ScriptedRuntime` is a FIXTURE, not a run (hy-pqf3).

    Nothing in the record distinguished them before, and GitHub #25 scores
    traces -- so a fixture could be counted as evidence of a model doing
    something. "No model" is the honest answer and a first-class one: a scorer
    branches on it rather than inferring it from an absence.
    """
    trace = plan_analytics_context(
        QUESTION,
        runtime=ScriptedRuntime(script=[ToolCall("list_context_catalog", {})]),
        executor=_Executor([ToolResult(payload={"domains": []})]),
    )
    payload = trace.to_dict()

    assert payload["provenance"] == {"runtime": SCRIPTED_RUNTIME, "model": None}
    assert payload["model"] == ""
    # And nothing fabricated beside it: no seed, no window, no model name.
    assert "seed" not in payload["provenance"]
    assert "declared_context_window" not in payload["provenance"]


def test_no_caller_can_name_the_model_a_run_used():
    """The record has ONE writer for what ran (hy-pqf3 review).

    Removing the config channel left a `model` parameter that overrode the
    runtime's own report, so the field a scorer most needs to trust was still
    writable by a caller -- the channel narrowed rather than removed. The
    parameter is gone, and this fails loudly if it comes back rather than
    quietly preferring whatever a caller passed.
    """
    with pytest.raises(TypeError, match="model"):
        plan_analytics_context(
            QUESTION,
            runtime=ScriptedRuntime(script=[]),
            executor=_Executor([]),
            model="scripted",
        )


def test_the_planner_records_what_the_runtime_used_not_what_a_caller_says():
    """The channel is the runtime's, not a parameter beside it (hy-pqf3).

    A runtime that ignored its configuration must report what it ignored it in
    favour of, so this one reports values no caller passed to
    `plan_analytics_context` at all -- which is exactly what the old
    `config=` parameter could not guarantee.
    """

    class _Honest:
        def tools(self):
            return ()

        def provenance(self):
            return {"runtime": "honest", "model": "actually-ran-this", "seed": 11}

        def run(self, question, *, on_message, call_tool):
            return None

        def close(self):
            return None

    trace = plan_analytics_context(QUESTION, runtime=_Honest(), executor=_Executor([]))
    payload = trace.to_dict()

    assert payload["provenance"]["model"] == "actually-ran-this"
    assert payload["provenance"]["seed"] == 11
    assert payload["model"] == "actually-ran-this"


def test_the_sdk_adapter_reports_what_it_drove_the_model_with():
    """Read off the objects the adapter built, not echoed from the config it
    was handed: the seed reported is the one in `extra_body`, which is the one
    that goes on the wire (hy-pqf3)."""
    pytest.importorskip("agents")
    from hyperset.planner.openai_runtime import OpenAIAgentsRuntime

    runtime = OpenAIAgentsRuntime(
        RuntimeConfig(
            model="llama3.2:3b",
            base_url="http://127.0.0.1:9/v1",
            seed=7,
            allocated_context_window=CONTEXT_WINDOW_TOKENS,
        )
    )

    reported = runtime.provenance()

    assert reported["runtime"] == OPENAI_AGENTS_RUNTIME
    assert reported["runtime"] in RUNTIME_NAMES
    assert reported["model"] == "llama3.2:3b"
    assert reported["seed"] == 7
    assert reported["allocated_context_window"] == CONTEXT_WINDOW_TOKENS
    assert reported["temperature"] == 0.0


def test_an_unseeded_adapter_reports_no_seed_rather_than_a_default():
    """A run nobody pinned says so."""
    pytest.importorskip("agents")
    from hyperset.planner.openai_runtime import OpenAIAgentsRuntime

    runtime = OpenAIAgentsRuntime(
        RuntimeConfig(
            model="m",
            base_url="http://127.0.0.1:9/v1",
            allocated_context_window=CONTEXT_WINDOW_TOKENS,
        )
    )

    assert runtime.provenance()["seed"] is None


def test_the_sdk_adapter_puts_the_seed_on_the_wire():
    """Against a recording endpoint, because the bug being fixed was a value
    that existed everywhere except on the wire (hy-am2).

    Asserting `ModelSettings.extra_body` would pass on the day the SDK stops
    forwarding it, which is the same shape as the defect: a seed that is
    configured, recorded, and never sent. This serves one canned completion
    from a local socket and reads the request body the SDK actually posted.
    """
    pytest.importorskip("agents")
    from hyperset.planner.openai_runtime import OpenAIAgentsRuntime

    with _recording_endpoint() as (base_url, requests):
        pinned = RuntimeConfig(
            model="m",
            base_url=base_url,
            seed=7,
            allocated_context_window=CONTEXT_WINDOW_TOKENS,
        )
        runtime = OpenAIAgentsRuntime(pinned)
        runtime.run(QUESTION, on_message=lambda text: None, call_tool=lambda call: {})

        unseeded = OpenAIAgentsRuntime(
            RuntimeConfig(
                model="m", base_url=base_url, allocated_context_window=CONTEXT_WINDOW_TOKENS
            )
        )
        unseeded.run(QUESTION, on_message=lambda text: None, call_tool=lambda call: {})

    assert requests[0]["seed"] == 7
    assert "seed" not in requests[1]


def test_two_sequential_sdk_turns_do_not_reuse_a_client_from_a_closed_loop(monkeypatch):
    """Each synchronous turn owns its async client and loop (hq-2yed)."""
    pytest.importorskip("agents")
    from hyperset.planner.openai_runtime import OpenAIAgentsRuntime

    clients = []

    class LoopBoundClient:
        def __init__(self, **_kwargs):
            self.loop = None
            self.closed = False
            clients.append(self)

        async def use(self):
            loop = asyncio.get_running_loop()
            if self.loop is not None and self.loop is not loop:
                raise RuntimeError("Event loop is closed")
            self.loop = loop

        async def close(self):
            assert asyncio.get_running_loop() is self.loop
            self.closed = True

    class Result:
        def __init__(self, output):
            self.final_output = output

    async def run(agent, question, *, max_turns):
        assert max_turns == 8
        await agent.model._client.use()
        return Result(question)

    monkeypatch.setattr("agents.AsyncOpenAI", LoopBoundClient)
    monkeypatch.setattr("agents.Runner.run", run)
    runtime = OpenAIAgentsRuntime(
        RuntimeConfig(
            model="m",
            base_url="http://127.0.0.1:9/v1",
            allocated_context_window=CONTEXT_WINDOW_TOKENS,
        )
    )
    messages = []

    runtime.run("first turn", on_message=messages.append, call_tool=lambda _call: {})
    runtime.run("second turn", on_message=messages.append, call_tool=lambda _call: {})

    assert messages == ["first turn", "second turn"]
    assert len(clients) == 2
    assert all(client.closed for client in clients)


# Captured from this host, `POST /api/show`. Two tags: one built with
# `PARAMETER num_ctx 32768`, one the base tag it was built FROM. The base tag
# declares no window and Ollama serves it at 4,096 while `model_info` reports
# the architecture maximum of 131,072 -- reading that number would confirm a
# window nobody is serving, which is the mistake `parse_num_ctx` must not make.
_SERVED_TAG_PARAMETERS = (
    "num_ctx                        32768\n"
    'stop                           "<|start_header_id|>"\n'
    'stop                           "<|eot_id|>"'
)
_BASE_TAG_PARAMETERS = (
    'stop                           "<|start_header_id|>"\n'
    'stop                           "<|eot_id|>"'
)


def test_the_window_probe_reads_only_what_the_tag_declares():
    """`parse_num_ctx` against real captured responses (hy-3wat). The probe
    itself cannot run in CI -- reading a declaration needs a live Ollama -- so
    what is tested here is the parsing, against text this host produced."""
    assert parse_num_ctx(_SERVED_TAG_PARAMETERS) == 32768
    assert parse_num_ctx(_BASE_TAG_PARAMETERS) is None
    assert parse_num_ctx("") is None
    assert parse_num_ctx("num_ctx                        not-a-number") is None


def test_the_probe_reads_the_allocation_out_of_a_loaded_model():
    """`/api/ps` parsing, against a captured response (hy-c2tg).

    The probe cannot run in CI -- observing an allocation needs a live Ollama
    with the model loaded -- so what is tested is the reading. The capture is
    from this host: a tag declaring `num_ctx 999999` against llama3.2's
    131,072 architecture maximum, loaded, and reported by the server at
    131,072. That gap is the whole reason this function exists.
    """
    loaded = {
        "models": [
            {
                "name": "hyperset-probe-999999:latest",
                "model": "hyperset-probe-999999:latest",
                "context_length": 131072,
                "size": 17774506145,
                "size_vram": 10081533951,
            }
        ]
    }

    assert _allocated_from(loaded, "hyperset-probe-999999:latest") == 131072
    # A model nothing has asked for is absent entirely, which is "not loaded"
    # rather than "no window" -- and is why `warm_up` exists.
    assert _allocated_from(loaded, "llama3.2:3b") is None
    assert _allocated_from({"models": []}, "anything") is None

    # Loaded but unreadable is a different fact from not loaded: warming fixes
    # the second and never the first, and collapsing them makes a caller warm
    # forever (hy-c2tg review).
    malformed = {"models": [{"model": "m", "context_length": "lots"}]}
    with pytest.raises(UnreadableAllocation, match="not a window this reader understands"):
        _allocated_from(malformed, "m")


def test_the_probe_finds_the_server_beside_the_openai_path():
    """`/api/show` sits beside `/v1`, not under it."""
    assert ollama_root("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434"
    assert ollama_root("http://127.0.0.1:11434/v1/") == "http://127.0.0.1:11434"
    assert ollama_root("http://127.0.0.1:11434") == "http://127.0.0.1:11434"


@pytest.mark.parametrize(
    ("declared", "allocated", "code"),
    [
        # Nothing observed the allocation: refused, whatever the tag asked for.
        (None, None, CONTEXT_WINDOW_UNOBSERVED),
        (CONTEXT_WINDOW_TOKENS, None, CONTEXT_WINDOW_UNOBSERVED),
        # The cheap early exit: a tag asking for less than the pinned window
        # cannot be rescued by any allocation, so it is refused before a load
        # that can take 30 seconds.
        (4096, None, CONTEXT_WINDOW_BELOW_PINNED),
        (8192, CONTEXT_WINDOW_TOKENS, CONTEXT_WINDOW_BELOW_PINNED),
        # And the case the declaration cannot see: clamped down by the server.
        (None, 4096, CONTEXT_WINDOW_BELOW_PINNED),
        (999999, 131072 // 32, CONTEXT_WINDOW_BELOW_PINNED),
    ],
)
def test_a_real_model_run_is_refused_unless_the_window_was_observed(declared, allocated, code):
    """The adapter's half of hy-3wat, and the half CI can actually test.

    The rule is vendor-neutral: a runtime driving a real model may not rely on
    a window nobody OBSERVED. It never learns what Ollama is -- it holds the
    numbers or it refuses. A declaration is not evidence: a tag asking for
    999999 against a 131,072 maximum is clamped and run, measured rather than
    feared, and a server configured through an environment variable declares
    nothing while allocating correctly. Refusing rather than warning because the
    condition is knowable before the first token and the failure it prevents
    is silent: a tag served at 4,096 truncates the governed context away and
    still returns HTTP 200 with a `usage` block counting only what survived.
    """
    pytest.importorskip("agents")
    from hyperset.planner.openai_runtime import OpenAIAgentsRuntime

    runtime = OpenAIAgentsRuntime(
        RuntimeConfig(
            model="m",
            base_url="http://127.0.0.1:9/v1",
            declared_context_window=declared,
            allocated_context_window=allocated,
        )
    )
    called = []

    with pytest.raises(UnusableContextWindow) as excinfo:
        runtime.run(QUESTION, on_message=lambda text: None, call_tool=called.append)

    assert excinfo.value.code == code
    # Refused before the first token, so nothing was asked of the substrate.
    assert called == []


def test_a_server_that_declares_nothing_but_allocates_enough_is_not_refused():
    """The false refusal this bead exists for (hy-c2tg).

    A server started with `OLLAMA_CONTEXT_LENGTH=32768` serves exactly the
    pinned window and puts no `num_ctx` on the tag, so the declaration is
    absent and the allocation is correct. That is the documented way to raise
    the default and the likeliest CI configuration, and the earlier rule --
    refuse when nothing is declared -- rejected it.

    The run still fails, at the endpoint, because there is no server on that
    port. What matters is WHICH failure: anything but our own refusal means
    the window check let it through.
    """
    pytest.importorskip("agents")
    from hyperset.planner.openai_runtime import OpenAIAgentsRuntime

    runtime = OpenAIAgentsRuntime(
        RuntimeConfig(
            model="m",
            base_url="http://127.0.0.1:9/v1",
            declared_context_window=None,
            allocated_context_window=CONTEXT_WINDOW_TOKENS,
        )
    )

    with pytest.raises(Exception) as excinfo:
        runtime.run(QUESTION, on_message=lambda text: None, call_tool=lambda call: {})

    assert not isinstance(excinfo.value, UnusableContextWindow), excinfo.value


def test_the_refusal_reaches_the_trace_with_its_code():
    """A run-level refusal is a scored outcome like any other: the trace
    carries the stable code, so a scorer branches on it rather than on the
    sentence -- the same contract `TOOL_REFUSAL` keeps (hy-3wat)."""

    class _Refusing:
        def tools(self):
            return ()

        def provenance(self):
            return {"runtime": "fake", "model": None}

        def run(self, question, *, on_message, call_tool):
            raise UnusableContextWindow(
                CONTEXT_WINDOW_UNOBSERVED, "nothing observed the allocated window"
            )

        def close(self):
            return None

    trace = plan_analytics_context(QUESTION, runtime=_Refusing(), executor=_Executor([]))

    (failed,) = refusals(trace)
    assert failed.kind == RUN_FAILED
    assert failed.detail["code"] == CONTEXT_WINDOW_UNOBSERVED
    assert failed.detail["exception"] == "UnusableContextWindow"


def test_an_ordinary_failure_carries_no_invented_code():
    """Only a refusal this project raised deliberately has a code. Giving one
    to every exception would be the classification `RUN_FAILED` deliberately
    does not make."""

    class _Exploding:
        def tools(self):
            return ()

        def provenance(self):
            return {"runtime": "fake", "model": None}

        def run(self, question, *, on_message, call_tool):
            raise RuntimeError("the endpoint went away")

        def close(self):
            return None

    trace = plan_analytics_context(QUESTION, runtime=_Exploding(), executor=_Executor([]))

    (failed,) = trace.of_kind(RUN_FAILED)
    assert "code" not in failed.detail
    assert failed.detail["exception"] == "RuntimeError"


def test_a_vendor_exception_cannot_forge_one_of_our_refusal_codes():
    """The code is read by TYPE, never by attribute (hy-3wat review).

    `openai.APIError` carries a `.code` and fills it from the RESPONSE BODY, so
    lifting the attribute would let a remote endpoint choose the string a
    scorer branches on -- an endpoint answering 400 with
    `{"error": {"code": "context_window_below_pinned"}}` would forge one of our
    own refusals into the record. This stands in for that shape exactly: an
    exception that is not a `PlannerRefusal` but looks like one.
    """

    class _Forging:
        def tools(self):
            return ()

        def provenance(self):
            return {"runtime": "fake", "model": None}

        def run(self, question, *, on_message, call_tool):
            error = RuntimeError("the endpoint said so")
            error.code = CONTEXT_WINDOW_BELOW_PINNED
            raise error

        def close(self):
            return None

    trace = plan_analytics_context(QUESTION, runtime=_Forging(), executor=_Executor([]))

    (failed,) = trace.of_kind(RUN_FAILED)
    assert "code" not in failed.detail
    assert failed.detail["exception"] == "RuntimeError"


def test_a_refusal_code_outside_the_registry_is_rejected_at_the_source():
    """`RUN_FAILURE_CODES` is what a scorer must be able to enumerate, so a
    code invented at a call site is refused the way `warning()` refuses one --
    otherwise the set is unbounded and no client can handle it."""
    with pytest.raises(ValueError, match="unknown run failure code"):
        PlannerRefusal("something_new", "invented at a call site")

    assert CONTEXT_WINDOW_UNOBSERVED in RUN_FAILURE_CODES
    assert CONTEXT_WINDOW_BELOW_PINNED in RUN_FAILURE_CODES


def test_the_in_process_executor_reaches_only_the_served_surface():
    """No privileged path into the resolver: the executor calls
    `run_operation`, which is the same entry point both transports call."""
    executor = InProcessExecutor(session_factory=object())

    result = executor.call("resolve_analytics_context", {"not_a_parameter": 1})

    assert result.refused
    assert result.error.code == "unknown_parameter"


def test_the_sdk_adapter_disables_its_hosted_telemetry():
    """The `openai-agents` SDK POSTs its own traces to a hosted endpoint by
    default (hy-az4). It failed here only with a 401 because no credential was
    configured; with one it would have succeeded silently, sending the
    question and the retrieved governed context out of the process.

    Asserted rather than commented, because a comment does not survive a
    dependency upgrade that changes the default and a one-line call is exactly
    what gets removed while tidying. `is_tracing_disabled()` is a proxy for
    the property, which is that nothing leaves the process; it is the strongest
    observation the SDK exposes.
    """
    pytest.importorskip("agents")
    from agents.tracing import get_trace_provider

    from hyperset.planner.openai_runtime import OpenAIAgentsRuntime
    from hyperset.planner.runtime import RuntimeConfig

    OpenAIAgentsRuntime(RuntimeConfig(model="local", base_url="http://127.0.0.1:11434/v1"))

    # Behavioural, not a flag read: the provider now hands out a no-op trace,
    # so there is nothing for the SDK to export.
    created = get_trace_provider().create_trace(name="probe")
    assert type(created).__name__.lower().startswith("noop"), type(created).__name__


def test_the_real_adapter_does_not_read_the_question_either():
    """Outcome measured against the live SDK rather than the fake: the
    question survives agent construction, message building and serialization
    untouched, so the invariant holds from the library entry point to the
    wire rather than up to a boundary. The run is expected to fail at the
    endpoint -- that is past every point where text inspection would occur."""
    pytest.importorskip("agents")
    from hyperset.planner.openai_runtime import OpenAIAgentsRuntime
    from hyperset.planner.runtime import RuntimeConfig

    runtime = OpenAIAgentsRuntime(
        RuntimeConfig(model="not-a-model", base_url="http://127.0.0.1:9/v1")
    )

    with pytest.raises(Exception) as excinfo:
        runtime.run(_Untouchable(QUESTION), on_message=lambda text: None, call_tool=lambda call: {})

    assert not isinstance(excinfo.value, AssertionError), excinfo.value


# The two tests above, by name, so that renaming one orphans this backstop
# loudly instead of leaving it asserting nothing.
SDK_GUARDS = (
    "test_the_sdk_adapter_disables_its_hosted_telemetry",
    "test_the_real_adapter_does_not_read_the_question_either",
)


def test_ci_runs_the_two_sdk_guards_rather_than_skipping():
    """The gate must install the extra those two guards need (hy-f6g).

    Both guard the SDK, so both stand behind `importorskip("agents")`, and the
    SDK is an extra rather than a dependency group. CI installed groups only,
    so for the life of the branch both guards skipped: the telemetry guard had
    never run there, and neither had the invariant it pins.

    Narrow on purpose, and deliberately not a general "a skip is red" policy:
    such a policy needs an allow-list within a release -- `tests/compose` has
    seven legitimate skips, four behind `HYPERSET_COMPOSE_DATAHUB` and three
    behind `HYPERSET_COMPOSE_DEMO` -- and a security guard eventually lands on
    it. This
    asserts one condition about two named tests. Skipping off CI is the case
    the extra being optional is actually about: a dev machine gates nothing.
    """
    if not os.environ.get("CI"):
        pytest.skip("the extra is optional off the gate; nothing is being gated here")

    module = sys.modules[__name__]
    for name in SDK_GUARDS:
        assert hasattr(module, name), f"{name} was renamed; repoint this backstop at it"
    assert importlib.util.find_spec("agents") is not None, (
        "the agent extra is not installed, so "
        f"{' and '.join(SDK_GUARDS)} skipped instead of running: "
        "CI must sync with --all-extras, not --all-groups alone"
    )


def test_the_adapter_reports_the_prompt_and_tools_it_was_actually_built_with():
    """The mismatch guard needs something true to compare against (hy-ast).

    A benchmark arm hands the same prompt and declarations to this adapter and
    to `plan_analytics_context`. Handing them different pairs would produce a
    trace describing a run that did not happen, and the only thing that can
    detect it is the adapter reporting what it holds rather than what it was
    told about.
    """
    pytest.importorskip("agents")
    from hyperset.planner.loop import planner_prompt
    from hyperset.planner.openai_runtime import OpenAIAgentsRuntime
    from hyperset.planner.trace import content_hash
    from hyperset.transport.operations import serialize

    other = [{"name": "list_raw_assets", "description": "raw", "input_schema": {}}]
    runtime = OpenAIAgentsRuntime(
        RuntimeConfig(model="m", base_url="http://127.0.0.1:9/v1", allocated_context_window=32768),
        instructions="a different prompt entirely",
        declarations=other,
    )

    reported = runtime.provenance()

    assert reported["instructions_hash"] == content_hash("a different prompt entirely")
    assert reported["instructions_hash"] != content_hash(planner_prompt())
    assert reported["tools_hash"] == content_hash(serialize(other))
    assert [tool["name"] for tool in runtime.tools()] == ["list_raw_assets"]


def test_an_adapter_built_with_no_overrides_still_reports_the_planners_own():
    """The default path is the one the product ships, so it is the one whose
    report has to stay true."""
    pytest.importorskip("agents")
    from hyperset.planner.loop import planner_prompt, tools_hash
    from hyperset.planner.openai_runtime import OpenAIAgentsRuntime
    from hyperset.planner.trace import content_hash

    runtime = OpenAIAgentsRuntime(
        RuntimeConfig(model="m", base_url="http://127.0.0.1:9/v1", allocated_context_window=32768)
    )

    reported = runtime.provenance()

    assert reported["instructions_hash"] == content_hash(planner_prompt())
    assert reported["tools_hash"] == tools_hash()
