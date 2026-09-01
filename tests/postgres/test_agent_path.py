"""The agent path over the real slice (hy-0gq), end to end.

The unit tests prove the planner's rules against a scripted runtime. This
proves the same loop against the real catalog, the real resolver and the real
plan check, reached the way a planner reaches them -- through
`run_operation`, the entry point both transports serve.

It also carries the measurement the bead requires rather than an assertion
that it is fine. That measurement is now about the whole run, not the catalog
alone (hy-o41): bounding the catalog bounded the smaller half, since the tool
specs cost 3.9x the catalog, the system prompt was in nobody's arithmetic, and
a resolved bundle is larger than both together. What is asserted is one
derived number -- how many resolves the window affords -- because it moves
when any term grows and cannot be met by raising a limit we do not own.

A catalog that does not fit is still a finding against `INNER_LIMIT`
(hy-ncp). A run that does not fit is a finding against the loop or the window
(hy-fft), never a number to raise here. That assertion was
`xfail(strict=True)` while the denominator was an unchosen 8,192 tokens; it is
a plain assertion now that the window is a pinned product constant (hy-am2).
"""

from __future__ import annotations

import json

import pytest

from hyperset.bundle import CATALOG_DEFAULT_LIMIT
from hyperset.planner import (
    CONSERVATIVE_BYTES_PER_TOKEN,
    CONTEXT_WINDOW_TOKENS,
    InProcessExecutor,
    ScriptedRuntime,
    ToolCall,
    catalog_bytes,
    plan_analytics_context,
    planner_prompt,
    refusals,
    tool_specs,
)
from hyperset.planner.trace import TOOL_REFUSAL, TOOL_RESULT
from tests.postgres.conftest import APPROVED_DATASET

QUESTION = "Which source and rules should an analyst use for recognized revenue by region?"
# The coverage claim travels with the domain (hy-9lct): the served tools do
# not read the question, so this is where the planner says what `revenue` has
# to declare for this answer.
DIRECTIVE = {"domains": ["revenue"], "concepts": ["recognized_revenue"]}
APPROVED_REF = f"superset:dataset:{APPROVED_DATASET}"
DIMENSION_REF = "superset:dataset:5bcf01e3-3f70-50d2-bb31-562b627b09b8"

# The window is a product decision now, not a test comment (hy-am2): it lives
# in `hyperset.planner.runtime` and this file imports it. The old
# `SMALL_MODEL_WINDOW_TOKENS = 8192` was declared here, in a comment, and every
# context-budget number in the repository descended from it.
#
# `CONSERVATIVE_BYTES_PER_TOKEN` replaces a flat four bytes per token, which
# was a guess and was optimistic by 20-25% on exactly the payload mix that
# matters. The ratios are measured per model over these real payloads -- 26,622
# bytes is 8,006 tokens for llama3.2:3b and 8,810 for qwen2.5:7b -- and the
# arithmetic below uses the worst of them, because fewer bytes per token means
# a window fills faster. That worst ratio belongs to `PINNED_MODEL`, which
# hy-yx7y settled on qwen2.5:7b, so this bound is the pinned model's own.
WINDOW_BYTES = int(CONTEXT_WINDOW_TOKENS * CONSERVATIVE_BYTES_PER_TOKEN)


@pytest.mark.postgres
def test_a_planner_reaches_governed_context_through_the_served_tools(
    session_factory, revenue_slice
):
    """The whole point of part 2: a question reaches governed context again,
    and it does so because a model named the domain -- not because Hyperset
    read the question."""
    executor = InProcessExecutor(session_factory=session_factory)
    runtime = ScriptedRuntime(
        script=[
            ToolCall("list_context_catalog", {}),
            ToolCall(
                "resolve_analytics_context",
                {"query": QUESTION, "directive": DIRECTIVE},
            ),
        ]
    )

    trace = plan_analytics_context(QUESTION, runtime=runtime, executor=executor)

    catalog, bundle = (step.detail["result"] for step in trace.of_kind(TOOL_RESULT))
    assert [entry["domain"] for entry in catalog["domains"]] == ["revenue"]
    assert bundle["resolution"]["status"] == "governed"
    assert bundle["instructions"]["approved_sources"]
    assert refusals(trace) == []


# What the loop can attempt, read off what it ships rather than guessed: the
# planner resolves once, and `prompts/planner.md` then instructs a further
# resolve in two separate places -- step 3 says a `ref_malformed` ref is "wrong.
# Fix it and try again", which the retry rule in `planner/loop.py` encoded as
# product behaviour, and step 4 says to "ask for more by sending a further
# directive" when the served context is insufficient. Either instruction,
# followed once, is a second resolve, and `RuntimeConfig.max_turns` (8) leaves
# room for several.
RESOLVES_THE_LOOP_CAN_ATTEMPT = 2


@pytest.mark.postgres
def test_the_window_affords_the_resolves_the_loop_can_attempt(session_factory, revenue_slice):
    """One derived quantity instead of a threshold per term (hy-o41).

    Bounding the catalog alone bounded the smaller half: the tool specs cost
    3.9x the catalog, the system prompt was in nobody's arithmetic, and a
    resolved bundle is larger than both together. Two thresholds would be the
    same disease with better manners -- each gets raised on its own merits by
    whoever hits it first, and neither says what the budget is for.

    So the number asserted is the one a caller experiences: how many resolves
    the window affords.

        affordable = (window - preamble) / bundle

    It moves when ANY term grows -- prompt, tool specs, catalog or bundle --
    and it cannot be satisfied by ratcheting a limit, because the limit is the
    context window and we do not own it.

    The size of each term is reported in the failure message rather than
    asserted separately: which term drifted is what a reader needs, and pinning
    each one is how a budget acquires four numbers nobody can defend.

    It was `xfail(strict=True)` against hy-fft while the denominator was an
    unchosen 8,192 tokens. It is a plain assertion now that the window is
    pinned at 32k (hy-am2), which is what hy-fft was implicitly assuming all
    along: the loop was never the defect, the denominator was.

    What this cannot see: the request envelope, measured on the wire at 121%
    of an 8k window where these terms sum to 109.5%, and `max_hops`, which the
    resolver accepts and the planner prompt never mentions. `max_hops=0` drops
    `domain_graph` -- 36% of the bundle, and the one term `context_budget`
    cannot touch -- so a cheaper path exists that nothing instructs the model
    to take (hy-p1cw).
    """
    executor = InProcessExecutor(session_factory=session_factory)
    served = executor.call("list_context_catalog", {})
    resolved = executor.call(
        "resolve_analytics_context", {"query": QUESTION, "directive": DIRECTIVE}
    )
    assert not served.refused
    assert not resolved.refused
    assert served.payload["page"]["limit"] == CATALOG_DEFAULT_LIMIT

    budget = WINDOW_BYTES
    prompt = len(planner_prompt().encode())
    tools = len(json.dumps(tool_specs()).encode())
    catalog = catalog_bytes(served.payload)
    bundle = len(json.dumps(resolved.payload).encode())
    preamble = prompt + tools + catalog
    affordable = (budget - preamble) // bundle

    assert affordable >= RESOLVES_THE_LOOP_CAN_ATTEMPT, (
        f"the window affords {affordable} resolve(s) and the loop can attempt "
        f"{RESOLVES_THE_LOOP_CAN_ATTEMPT}: budget {budget} bytes "
        f"({CONTEXT_WINDOW_TOKENS} tokens x {CONSERVATIVE_BYTES_PER_TOKEN} measured bytes per "
        f"token), preamble {preamble} = prompt {prompt} + tool specs {tools} + catalog "
        f"{catalog}, one bundle {bundle}. A catalog that does not fit is a finding against "
        f"INNER_LIMIT (hy-ncp); a run that does not fit is a finding against the loop or "
        f"the window (hy-fft). Neither is a reason to raise a number here"
    )


@pytest.mark.postgres
def test_a_directive_naming_nothing_is_refused_and_the_refusal_survives(
    session_factory, revenue_slice
):
    """Part 1 made this refusal; the planner must not absorb it. The trace
    carries the code and the recovery, and claims no retryability: nothing a
    refusal can carry is fixable by sending the same call again, and what IS
    fixable arrives on a served bundle instead (hy-amtg)."""
    executor = InProcessExecutor(session_factory=session_factory)
    runtime = ScriptedRuntime(
        script=[ToolCall("resolve_analytics_context", {"query": QUESTION, "directive": {}})]
    )

    trace = plan_analytics_context(QUESTION, runtime=runtime, executor=executor)

    (step,) = trace.of_kind(TOOL_REFUSAL)
    assert step.detail["code"] == "directive_required"
    assert "retryable" not in step.detail
    assert "list_context_catalog" in step.detail["recovery"]


@pytest.mark.postgres
def test_a_plan_built_from_the_served_bundle_validates_through_the_same_path(
    session_factory, revenue_slice
):
    executor = InProcessExecutor(session_factory=session_factory)
    resolved = executor.call(
        "resolve_analytics_context",
        {"query": QUESTION, "directive": DIRECTIVE},
    )
    runtime = ScriptedRuntime(
        script=[
            ToolCall(
                "validate_analytics_plan",
                {
                    "query": QUESTION,
                    "directive": DIRECTIVE,
                    "bundle_id": resolved.payload["bundle_id"],
                    "source_refs": [APPROVED_REF, DIMENSION_REF],
                    "fields": ["recognized_revenue", "region"],
                    "filters": [
                        "finance_orders_daily.status = 'completed'",
                        "customer_dim.is_test = false",
                    ],
                    "joins": [
                        {
                            "from": "finance_orders_daily.customer_id",
                            "to": "customer_dim.customer_id",
                            "type": "inner",
                        }
                    ],
                    "checks": [
                        "recognized_revenue is non-negative",
                        "monthly totals reconcile within 1% of the fixture close value",
                    ],
                    "grain": "order_date by customer_dim.region",
                },
            )
        ]
    )

    trace = plan_analytics_context(QUESTION, runtime=runtime, executor=executor)

    (validated,) = trace.of_kind(TOOL_RESULT)
    assert validated.detail["result"]["status"] in ("valid", "warnings")
    assert (
        validated.detail["result"]["checked_against"]["planned_bundle_id"]
        == (resolved.payload["bundle_id"])
    )
