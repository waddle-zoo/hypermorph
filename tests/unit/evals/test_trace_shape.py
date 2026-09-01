"""The ordered projection of what a run did (hy-9dyv).

The axis exists because hy-hk5m measured a session that called
`validate_analytics_plan` and two that never did, at identical pins, with every
number on the v1 line identical. So what these arms hold is the property that
makes the projection able to see that: ORDER and MULTIPLICITY survive, and the
set normalisation every other comparison in `stability` uses is exactly what
must not happen here.
"""

from __future__ import annotations

from hyperset.evals.trace_shape import (
    REFUSED,
    RUN_FAILED_TOKEN,
    UNNAMED_OPERATION,
    render_shape,
    trace_shape,
)
from hyperset.planner.trace import (
    PLANNER_MESSAGE,
    RUN_FAILED,
    TOOL_CALL,
    TOOL_REFUSAL,
    TOOL_RESULT,
)

CATALOG = "list_context_catalog"
RESOLVE = "resolve_analytics_context"
VALIDATE = "validate_analytics_plan"


def step(kind: str, operation: str | None = None) -> dict:
    detail = {} if operation is None else {"operation": operation}
    return {"kind": kind, "detail": detail, "summary": "", "at": "2026-07-30T00:00:00+00:00"}


def trace(*steps: dict) -> dict:
    return {"steps": list(steps)}


def test_the_shape_is_the_calls_in_the_order_they_were_made():
    assert trace_shape(
        trace(
            step(TOOL_CALL, CATALOG),
            step(TOOL_RESULT, CATALOG),
            step(TOOL_CALL, RESOLVE),
            step(TOOL_RESULT, RESOLVE),
            step(PLANNER_MESSAGE),
        )
    ) == (CATALOG, RESOLVE)


def test_a_run_that_validated_is_a_different_shape_from_one_that_did_not():
    """The finding this axis was built for, at its own level. Both sessions
    below were observed at one tree and one set of pins (hy-hk5m)."""
    validated = trace(step(TOOL_CALL, CATALOG), step(TOOL_CALL, RESOLVE), step(TOOL_CALL, VALIDATE))
    did_not = trace(step(TOOL_CALL, CATALOG), step(TOOL_CALL, RESOLVE))

    assert trace_shape(validated) != trace_shape(did_not)


def test_the_same_calls_in_another_order_are_another_shape():
    """The one property `stability._group` would destroy: it normalises with
    `sorted(set(...))`, so a set-keyed projection would call these one shape and
    report "catalog before resolve" as agreement whichever way it happened."""
    forwards = trace(step(TOOL_CALL, CATALOG), step(TOOL_CALL, RESOLVE))
    backwards = trace(step(TOOL_CALL, RESOLVE), step(TOOL_CALL, CATALOG))

    assert trace_shape(forwards) == (CATALOG, RESOLVE)
    assert trace_shape(backwards) == (RESOLVE, CATALOG)
    assert trace_shape(forwards) != trace_shape(backwards)


def test_a_call_made_twice_is_not_a_call_made_once():
    """The other half of the same argument, and the one a `set` loses silently:
    a run that retried a resolve did something a run that resolved once did
    not. Measured on the committed raw-baseline recordings, which differ between
    two sessions by exactly one repeated `get_raw_asset`."""
    once = trace(step(TOOL_CALL, RESOLVE))
    twice = trace(step(TOOL_CALL, RESOLVE), step(TOOL_CALL, RESOLVE))

    assert trace_shape(twice) == (RESOLVE, RESOLVE)
    assert trace_shape(once) != trace_shape(twice)


def test_a_refused_call_is_not_the_same_as_a_call_never_made():
    refused = trace(step(TOOL_CALL, RESOLVE), step(TOOL_REFUSAL, RESOLVE))
    never = trace(step(TOOL_CALL, RESOLVE))

    assert trace_shape(refused) == (RESOLVE, f"{REFUSED}{RESOLVE}")
    assert trace_shape(refused) != trace_shape(never)


def test_a_run_that_died_says_so_in_the_shape():
    assert trace_shape(trace(step(TOOL_CALL, CATALOG), step(RUN_FAILED))) == (
        CATALOG,
        RUN_FAILED_TOKEN,
    )


def test_results_and_messages_contribute_nothing():
    """A result is the answer to a call already in the shape and a message is
    the answer text, which is its own axis. Including either would double every
    step or fold two axes into one."""
    assert (
        trace_shape(
            trace(step(TOOL_RESULT, CATALOG), step(PLANNER_MESSAGE), step(TOOL_RESULT, RESOLVE))
        )
        == ()
    )


def test_a_step_timestamp_is_not_part_of_the_shape():
    """Every step carries a per-run `at`, so a projection over it would differ
    on every run by construction -- which is why benchmark.md called this a
    design question rather than a hash."""
    early = trace(step(TOOL_CALL, CATALOG))
    late = trace({**step(TOOL_CALL, CATALOG), "at": "2027-01-01T00:00:00+00:00"})

    assert trace_shape(early) == trace_shape(late)


def test_a_call_this_reader_cannot_name_is_marked_rather_than_dropped():
    """A dropped step would make a run that called something unnameable
    indistinguishable from a run that never called it -- the shape of the defect
    `UNVERSIONED` exists to prevent one module over."""
    assert trace_shape(trace(step(TOOL_CALL), step(TOOL_CALL, RESOLVE))) == (
        UNNAMED_OPERATION,
        RESOLVE,
    )


def test_a_run_with_no_calls_renders_as_something_rather_than_as_blank():
    """An empty rendering on a line would read as a missing field, and a
    missing field is what this whole change refuses to let mean agreement."""
    assert render_shape(trace_shape(trace(step(PLANNER_MESSAGE)))) == "<no-calls>"
