"""What a run DID, in order, as one comparable projection (hy-9dyv).

The stability report compares predicate verdicts, answer text and evidence, and
`trace` was recorded per repetition and never compared -- so two runs that
reached the same answer through a different tool sequence read as fully stable.
That gap has a measured size: hy-hk5m recorded one session that called
`validate_analytics_plan` and two that never did, at identical pins, and every
number on the line was blind to it.

WHY THIS IS NOT `_group`, and it is the sharpest decision in the projection.
Evidence is a SET -- what a run rested on, in whatever order the tools returned
it -- and `stability._group` normalises with `sorted(set(...))` accordingly. A
trace is a SEQUENCE. The questions it exists to answer are "did the catalog come
before the resolve" and "was validate called at all", and set normalisation
answers the first wrong and loses a repeated call entirely. So nothing here
sorts and nothing here de-duplicates.

ONE TOKEN PER RECORDED STEP, which is the rule that makes the projection
checkable by reading it beside a trace:

- a `tool_call` is its operation, `resolve_analytics_context`;
- a `tool_refusal` is its operation with `!` in front, `!resolve_analytics_
  context` -- the server would not answer it, which is a different run from one
  that never asked;
- a `run_failed` is `!run_failed`.

`tool_result` and `planner_message` steps contribute nothing. A result is the
answer to a call already in the projection, so including it would double every
successful step and say nothing new; a message is the answer text, which is its
own axis and compared in full.

WHAT IS DELIBERATELY NOT IN A TOKEN. No parameters, so a directive naming the
same domain in another order is not a shape change. No timestamps: every step
carries a per-run `at`, and a projection including it would differ on every run
by construction -- which is the reason benchmark.md called this a design
question rather than a hash. No refusal CODE either: `OperationError.code` has
no registry (hy-y633), so a shape sensitive to it would be sensitive to a
vocabulary nothing in this repository enumerates.
"""

from __future__ import annotations

from collections.abc import Sequence

from hyperset.planner.trace import RUN_FAILED, TOOL_CALL, TOOL_REFUSAL

REFUSED = "!"
"""Marks a step the server refused. One character, in front of the operation,
so a refused call is legible beside the call it refused and can never be read
as the same token."""

RUN_FAILED_TOKEN = f"{REFUSED}{RUN_FAILED}"
"""A run that died -- the endpoint down, the turn budget spent. Named from
`RUN_FAILED` rather than written out, so the two cannot drift."""

UNNAMED_OPERATION = "<unnamed>"
"""A call step whose detail carries no operation.

Named rather than skipped, for the reason `UNVERSIONED` is named in
`source_identity`: a step dropped here would make a run that called something
this reader could not name indistinguishable from a run that never called it.
No recording this repository produces reaches it -- `planner.loop` writes the
operation on every call and refusal step -- so it marks a payload nobody wrote
rather than a case anybody has seen.
"""

SHAPE_SEPARATOR = ">"
"""Between steps of one shape, when a shape is rendered for a human or for the
stability line. Chosen because it reads as ordering, which is the whole content
of this projection."""


def trace_shape(trace: dict) -> tuple[str, ...]:
    """The operations one run attempted, in the order it attempted them."""
    shape: list[str] = []
    for step in trace.get("steps") or []:
        kind = step.get("kind")
        if kind == RUN_FAILED:
            shape.append(RUN_FAILED_TOKEN)
            continue
        if kind not in (TOOL_CALL, TOOL_REFUSAL):
            continue
        operation = (step.get("detail") or {}).get("operation")
        name = operation if isinstance(operation, str) and operation else UNNAMED_OPERATION
        shape.append(f"{REFUSED}{name}" if kind == TOOL_REFUSAL else name)
    return tuple(shape)


def render_shape(shape: Sequence[str]) -> str:
    """One shape as a single token a line can carry and a person can read."""
    return SHAPE_SEPARATOR.join(shape) if shape else "<no-calls>"
