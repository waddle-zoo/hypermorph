"""The MCP adapter's rules on the official SDK (hy-oih, hy-gh-117).

Driven through a real in-memory MCP client session, so these assert the bytes a
tool-using model actually receives -- not a hand-rolled framing this module no
longer owns. The SDK owns JSON-RPC framing and protocol negotiation; what this
file guards is what this module owns: the tool surface is the operation
registry verbatim, a tool result is byte-identical to run_operation, and there
is no surface beyond the read-only tools -- no resources, no prompts.
"""

from __future__ import annotations

import anyio
import pytest

from hyperset.transport import operations
from hyperset.transport.mcp import build_mcp_server
from hyperset.transport.operations import OPERATION_SPECS, OPERATIONS, serialize
from tests.unit.transport.conftest import DIRECTIVE, QUESTION, governed_bundle


def _drive(session_factory, work):
    """Run one exchange against a real client connected to the built server.

    The in-memory helper performs the initialize handshake, so `work` may list
    or call straight away. anyio.run keeps these tests synchronous without an
    async plugin."""
    # FUNCTION-level (via this shared helper), never module-level: without the `mcp` extra each
    # test SKIPS individually and stays COLLECTED, so an absent extra moves the skip count and
    # not the denominator (hy-fabp; the thesis of test_optional_extras_move_only_the_skip_count).
    pytest.importorskip("mcp")

    async def _run():
        from mcp.shared.memory import create_connected_server_and_client_session as connect

        server = build_mcp_server(session_factory=session_factory)
        async with connect(server) as client:
            return await work(client)

    return anyio.run(_run)


def test_the_operations_are_listed_with_their_exact_schemas(session_factory):
    listed = _drive(session_factory, lambda c: c.list_tools())
    assert [tool.name for tool in listed.tools] == list(OPERATIONS)
    for tool in listed.tools:
        spec = OPERATION_SPECS[tool.name]
        # The whole point of the low-level Server over FastMCP: the served
        # schema is the operation's own, not one inferred from a signature.
        assert tool.inputSchema == spec["input_schema"]
        assert tool.title == spec["title"]
        assert serialize(spec["example"]) in tool.description


def test_a_tool_result_is_byte_identical_to_run_operation(resolved, session_factory):
    result = _drive(
        session_factory,
        lambda c: c.call_tool(
            "resolve_analytics_context", {"query": QUESTION, "directive": DIRECTIVE}
        ),
    )
    assert result.isError is False
    assert result.structuredContent == governed_bundle().to_dict()
    # The text block is the same serialization the HTTP body uses.
    assert result.content[0].text == serialize(result.structuredContent)


def test_an_operation_failure_is_an_isError_result_not_a_protocol_error(
    broken_resolver, session_factory
):
    result = _drive(
        session_factory,
        lambda c: c.call_tool(
            "resolve_analytics_context", {"query": QUESTION, "directive": DIRECTIVE}
        ),
    )
    # A resolver blow-up comes back as a result the model can read and retry,
    # the same as the HTTP adapter, never as a raised protocol error.
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "internal_error"


def test_an_unknown_tool_is_refused_as_an_isError_result(session_factory):
    result = _drive(session_factory, lambda c: c.call_tool("get_provenance", {}))
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "unknown_tool"


def test_a_stdio_connection_keeps_one_traceable_feedback_chain(monkeypatch, session_factory):
    pytest.importorskip("mcp")

    from hyperset.observability.interaction import current_trace_context, set_trace_context
    from hyperset.transport import mcp

    observed = []

    def _run(_name, _arguments, **_kwargs):
        observed.append(current_trace_context())
        return {"ok": True}

    monkeypatch.setattr(mcp, "run_operation", _run)
    set_trace_context(None)
    with mcp._bind_stdio_trace_context():
        mcp._call("search_knowledge", {}, session_factory=session_factory)
        mcp._call("record_answer_feedback", {}, session_factory=session_factory)
    assert len(observed) == 2
    assert observed[0].session_id and observed[0].correlation_id
    assert observed[1] == observed[0]
    assert current_trace_context().session_id is None


def test_a_tool_call_binds_the_sdk_rpc_id_and_search_intent(monkeypatch, session_factory):
    pytest.importorskip("mcp")

    from hyperset.observability.interaction import current_trace_context, set_trace_context
    from hyperset.transport import mcp

    observed = []

    def _run(_name, _arguments, **_kwargs):
        observed.append(current_trace_context())
        return {"ok": True}

    monkeypatch.setattr(mcp, "run_operation", _run)
    set_trace_context(None)
    result = _drive(
        session_factory,
        lambda client: client.call_tool(
            "search_knowledge", {"query": "revenue", "intent": "answer revenue"}
        ),
    )
    assert result.isError is False
    (context,) = observed
    assert context.turn_id and context.turn_id.startswith("mcp-turn-")
    assert context.tool_call_id and context.tool_call_id.startswith("mcp-call-")
    assert context.intent == "answer revenue"
    assert current_trace_context().session_id is None


def test_a_sync_operation_runs_outside_the_mcp_event_loop(monkeypatch, session_factory):
    import asyncio
    import threading

    seen = {}

    def _run(name, arguments, **kwargs):
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        seen["thread"] = threading.current_thread().name
        return {"operation": name}

    monkeypatch.setattr("hyperset.transport.mcp.run_operation", _run)

    result = _drive(session_factory, lambda c: c.call_tool("list_context_catalog", {}))

    assert result.structuredContent == {"operation": "list_context_catalog"}
    assert "worker thread" in seen["thread"].lower()


def test_only_read_only_tools_are_served_no_resources_or_prompts(session_factory):
    init = _drive(session_factory, lambda c: c.initialize())
    assert init.capabilities.tools is not None
    assert init.capabilities.resources is None
    assert init.capabilities.prompts is None
    assert init.serverInfo.name == "hyperset"
    assert init.instructions and "never infers a domain" in init.instructions


def test_the_catalog_and_discover_tools_are_present(session_factory):
    listed = _drive(session_factory, lambda c: c.list_tools())
    names = {tool.name for tool in listed.tools}
    assert {"list_context_catalog", "discover_analytics_context"} <= names
    assert operations.OPERATIONS == tuple(tool.name for tool in listed.tools)
