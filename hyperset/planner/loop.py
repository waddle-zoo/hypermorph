"""The agent path: a question in, a governed answer or an intact refusal out
(hy-0gq, GitHub #70).

Part 1 deleted the code that guessed a domain from the words of a question.
Nothing here brings it back: no line in this module reads `question`. It is
carried to the runtime and echoed into the trace, and the semantic work of
turning it into a `ContextDirective` belongs entirely to the model. If a
future edit here inspects the question text, that is `_match` returning under
a new name.

What this module owns is the substrate around the model: which tools exist,
how a tool call reaches them, what is recorded, and -- the part that is a
product rule rather than plumbing -- which refusals a planner may retry.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from hyperset.bundle import RETRYABLE_WARNING_CODES
from hyperset.planner.executor import Executor, ToolResult
from hyperset.planner.runtime import PlannerRefusal, Runtime, ToolCall
from hyperset.planner.trace import (
    PLANNER_MESSAGE,
    RUN_FAILED,
    TOOL_CALL,
    TOOL_REFUSAL,
    TOOL_RESULT,
    Step,
    Trace,
    content_hash,
)
from hyperset.transport.operations import CATALOG, OPERATION_SPECS, RESOLVE, VALIDATE, serialize

# The planner's governed tool surface: an explicit ALLOWLIST of the resolve-path
# operations, NOT "every served operation." The safe state is structural -- a
# new served operation (the assist-class DISCOVER, or a future one) is excluded
# from the planner BY DEFAULT and cannot silently join `tools_hash`, which is
# what the committed #25 recordings are pinned against. Adding a served op to
# this surface must be a deliberate edit here, and `tests/unit/planner/
# test_planner.py` reds if a served op leaks in (hy-gh-206, Overseer ruling).
RESOLVE_PATH_OPERATIONS = (CATALOG, RESOLVE, VALIDATE)

PROMPT_PATH = Path(__file__).parent / "prompts" / "planner.md"

# WHERE THE RETRY RULE LIVES NOW, and why it moved (hy-amtg). It used to be a
# set here, tested against `OperationError.code` on a refusal -- and no
# operation raises a ref code. A bad ref comes back inside a SERVED bundle,
# `refused=False`, disclosed in `resolution.warnings`. So the rule was read
# against a surface the condition never reaches, and `retryable` was false on
# every real run while a scorer read that absence as good behaviour.
#
# The rule itself was never wrong: hy-6ae divided the ref codes by what a
# caller must DO, which is exactly a retryability rule. It governs the WARNING
# vocabulary, and it is declared there, beside the codes and the gate --
# `hyperset.bundle.RETRYABLE_WARNING_CODES`. Three earlier commit messages
# cite that split as the specification for the refusal rule; they were wrong
# about the surface, not about the split.


def planner_prompt() -> str:
    return PROMPT_PATH.read_text()


def tool_specs() -> list[dict]:
    """The resolve-path descriptions, verbatim, in allowlist order.

    Descriptions and schemas are reused from `OPERATION_SPECS`, what both
    transports serve and what `tests/unit/test_section_7_matches_the_served_
    contract.py` ties to the binding document, so the planner's instructions
    stay transitively guarded against doc drift on these operations. But WHICH
    operations is `RESOLVE_PATH_OPERATIONS`, not every key of `OPERATION_SPECS`:
    a served assist operation is not automatically a planner tool, so it cannot
    move `tools_hash` or invalidate a committed recording by merely existing.
    """
    return [
        {
            "name": name,
            "description": OPERATION_SPECS[name]["description"],
            "input_schema": OPERATION_SPECS[name]["input_schema"],
        }
        for name in RESOLVE_PATH_OPERATIONS
    ]


def tools_hash() -> str:
    return content_hash(serialize(tool_specs()))


def catalog_bytes(catalog: dict) -> int:
    """What the catalog actually costs a context window, as a number.

    The bound exists for the small model's window (hy-ncp), so whether it
    holds is measured rather than asserted: a catalog that does not fit is a
    finding against that bound, never a reason to raise the window.
    """
    return len(serialize(catalog).encode())


def plan_analytics_context(
    question: str,
    *,
    runtime: Runtime,
    executor: Executor,
    instructions: str | None = None,
    declarations: Sequence[dict] | None = None,
) -> Trace:
    """Drive one planning run and return its trace.

    The trace is the return value rather than a side channel because it is a
    product surface: GitHub #25 scores the chain step by step, so the caller
    that wants the answer and the caller that wants the reasoning read the
    same structured record.

    What produced the run is asked of the runtime rather than accepted beside
    it (hy-pqf3). Only the runtime knows what it actually used, and a channel
    that takes those values from a caller can describe a pinning that never
    happened -- the same defect hy-am2 fixed one layer down, where a seed was
    configured and never sent.

    There is deliberately no `model` parameter. One survived the first cut of
    this change and overrode the runtime's own report, which left the record
    for the one field a scorer most needs to trust still writable by a caller
    -- the channel narrowed rather than removed. Closing a channel means
    enumerating every writer of the field, not only the one the bead named.

    `instructions` and `declarations` default to the planner's own and exist
    for GitHub #25's raw baseline arm, which must run through THIS loop rather
    than a second copy of it: the two arms have to differ in exactly one
    variable, and a benchmark whose baseline had its own driver would differ in
    the harness as well as in the substrate. They are recorded, not merely
    used -- a run under a different prompt with the planner's prompt hash on
    its trace is the pinning-that-did-not-happen defect this loop already
    refuses one field over, so the hashes come from what was passed. That the
    caller passed the SAME pair to the runtime is checked where both are
    visible, against `provenance()` (hyperset.evals.recording).
    """
    instructions = planner_prompt() if instructions is None else instructions
    declarations = tool_specs() if declarations is None else list(declarations)
    provenance = runtime.provenance()
    trace = Trace(
        question=question,
        prompt_hash=content_hash(instructions),
        tools_hash=content_hash(serialize(declarations)),
        model=provenance.get("model") or "",
        provenance=provenance,
    )

    def call_tool(call: ToolCall) -> dict:
        # The request is recorded here rather than by each runtime (hy-ths).
        # A step kind every adapter implements for itself is a step kind two
        # adapters can disagree about, and the trace is what GitHub #25
        # scores, so a disagreement between runtimes would be a scoring
        # difference. All three tool step kinds are emitted at this one site,
        # which is also what pairs a call with its own result.
        trace.record(
            TOOL_CALL,
            {"operation": call.operation, "params": call.params},
            f"call {call.operation}",
        )
        result = executor.call(call.operation, call.params)
        trace.steps.append(_step_for(call, result))
        return result.to_dict()

    def on_message(text: str) -> None:
        # The planner builds the step, so its shape is stated once (hy-kz6).
        # A runtime reports what the model said; it does not decide how that
        # is recorded.
        #
        # And the type is checked rather than trusted, because narrowing the
        # callback was the whole point: a runtime handing over a dict would
        # otherwise put that dict under `text` and reintroduce the
        # per-adapter shape through the parameter meant to prevent it. Cheap,
        # and it fails at the boundary rather than in a scorer.
        if not isinstance(text, str):
            raise TypeError(
                f"a runtime reports what the model SAID, as text; got {type(text).__name__}"
            )
        trace.record(PLANNER_MESSAGE, {"text": text}, text)

    try:
        runtime.run(question, on_message=on_message, call_tool=call_tool)
    # Recorded and returned rather than re-raised (hy-edj). A run that dies --
    # the local endpoint down, the model not pulled, the turn budget spent --
    # is the failure `RUN_FAILED` names, and a scorer has to tell it from "the
    # planner called nothing"; both are the absence of a trace if this
    # propagates, since the trace is a local. `exception` carries the type as
    # its own field so a bug in Hyperset's own code stays legible here instead
    # of being folded into a sentence -- and it is the type and the message,
    # with no classification: whether a failure is the endpoint being down or
    # a defect of ours is not knowable at this site, and guessing it here is
    # the heuristic this package exists without.
    #
    # `Exception`, not `BaseException`: the SDK path runs `asyncio.run` inside
    # `runtime.run`, and a `CancelledError` or an interrupt is not a run
    # outcome to be scored. `close()` stays in the `finally`, out of the
    # catch's reach, so a teardown defect still raises rather than arriving as
    # one more `RUN_FAILED`.
    except Exception as exc:
        detail = {"exception": type(exc).__name__, "reason": str(exc)}
        # Branched on TYPE, never on the attribute. `openai.APIError` also has
        # a `.code` and fills it FROM THE RESPONSE BODY, so reading the
        # attribute would let a remote endpoint choose the code a scorer
        # branches on -- an endpoint returning 400 with
        # {"error": {"code": "context_window_below_pinned"}} would forge one of
        # our own refusals into the record. `PlannerRefusal` is the only source
        # trusted, and its constructor already rejects a code outside
        # `RUN_FAILURE_CODES`, so the set a scorer must handle is enumerable.
        # An exception from anywhere else gets no code: inventing one would be
        # the classification this step kind deliberately does not make.
        if isinstance(exc, PlannerRefusal):
            detail["code"] = exc.code
        trace.record(RUN_FAILED, detail, f"run failed: {type(exc).__name__}")
    finally:
        runtime.close()
    return trace


def fixable_warnings(payload: dict) -> list[str] | None:
    """Which disclosures on a served bundle a caller could fix by asking again.

    THREE STATES, THREE VALUES, because two of them are not the same fact:
    `None` for an answer that carries no resolution at all -- a catalog listing
    or a plan check, which cannot disclose a ref problem; `[]` for a resolution
    with nothing fixable; a list for one a caller could act on. Returning `[]`
    for all three would let a scorer counting clean resolves count every
    catalog and validate step as one.

    Read from the payload rather than inferred, and gated by the vocabulary
    that owns it: a code is fixable only if `hyperset.bundle` says so.
    """
    resolution = payload.get("resolution")
    if not isinstance(resolution, dict):
        return None
    return [
        entry["code"]
        for entry in resolution.get("warnings") or []
        if isinstance(entry, dict) and entry.get("code") in RETRYABLE_WARNING_CODES
    ]


def _step_for(call: ToolCall, result: ToolResult) -> Step:
    if not result.refused:
        # Recorded on the RESULT, because that is where the condition arrives
        # (hy-amtg). The bundle itself is retained one key away, so this is a
        # derived value sitting beside its own input -- kept anyway for the
        # reason `prompt_hash` and `tools_hash` are kept: it pins the POLICY at
        # run time. A scorer re-deriving it from the payload would apply
        # today's `RETRYABLE_WARNING_CODES` to a run recorded under an older
        # one, and score old behaviour by new rules.
        #
        # THE TRACE IS WRITE-ONLY. A runtime -- and the model behind it -- sees
        # `ToolResult.to_dict()`, which carries `resolution.warnings` and not
        # this field; only a post-hoc reader sees the trace. A planner decides
        # from the warnings, as `prompts/planner.md` step 3 already teaches;
        # nothing in the loop can read what is written here.
        return Step(
            kind=TOOL_RESULT,
            detail={
                "operation": call.operation,
                "result": result.payload,
                "retryable_warnings": fixable_warnings(result.payload),
            },
            summary=f"{call.operation} answered",
        )
    error = result.error
    # No `retryable` here. A refusal is a request this server would not answer
    # -- an unknown parameter, a directive naming nothing -- and none of the
    # codes it can carry is fixable by sending the same call again. What IS
    # fixable arrives on a served bundle, and is recorded there (hy-amtg).
    return Step(
        kind=TOOL_REFUSAL,
        detail={
            "operation": call.operation,
            "code": error.code,
            "recovery": error.recovery,
        },
        summary=f"{call.operation} refused: {error.code}",
    )


def refusals(trace: Trace) -> list[Step]:
    """Every refusal, unabsorbed. A caller reads this to see what the planner
    could not do; nothing here summarizes a refusal into prose or promotes a
    bounded answer to a complete one.

    Both levels: a tool that refused, and a run that died before it could ask
    again. The second is why `plan_analytics_context` returns the trace after
    a failure instead of raising -- an unreachable trace could never be read
    here."""
    return trace.of_kind(TOOL_REFUSAL) + trace.of_kind(RUN_FAILED)
