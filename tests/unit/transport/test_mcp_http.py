"""The hosted Streamable HTTP transport, over a real port (hy-gh-117).

Acceptance #1: hyperset serves MCP over Streamable HTTP on a local port. This
starts the real app under uvicorn and connects a real MCP HTTP client to
http://127.0.0.1:<port>/mcp -- no subprocess spawned, a connection made -- and
checks the same tool surface and the same result bytes the stdio and in-memory
paths serve. The catalog service is stubbed so the transport is what is under
test, not Postgres.
"""

from __future__ import annotations

import socket
import threading
import time
from datetime import UTC, datetime

import anyio
import pytest

from hyperset.bundle import ContextCatalog
from hyperset.transport import operations
from hyperset.transport.mcp import HTTP_PATH, streamable_http_app
from hyperset.transport.operations import OPERATIONS, serialize


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _catalog(*, session_factory, limit, offset, workspace=None):
    return ContextCatalog(
        domains=[{"domain": "revenue", "concepts": ["recognized revenue"]}],
        observed=[],
        page={
            "limit": limit,
            "offset": offset,
            "domain_count": 1,
            "next_offset": None,
            "truncated": [],
            "recovery": None,
        },
        generated_at=datetime(2026, 7, 28, tzinfo=UTC),
    )


@pytest.fixture
def hosted_endpoint(monkeypatch):
    # FUNCTION-level guard: without the `mcp` extra (which brings uvicorn/starlette too) every
    # test using this fixture SKIPS individually and stays COLLECTED -- the skip count moves, the
    # denominator does not (hy-fabp). Placed before `import uvicorn` so the extra is checked first.
    pytest.importorskip("mcp")
    import uvicorn

    monkeypatch.setattr(operations, "list_context_catalog", _catalog)
    port = _free_port()
    app = streamable_http_app(session_factory=object())
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    assert server.started, "hosted MCP server did not start"
    try:
        yield f"http://127.0.0.1:{port}{HTTP_PATH}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _exchange(url, work, *, headers=None):
    async def _run():
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        # follow_redirects: the SDK's own client does; the server 307s /mcp -> /mcp/.
        http_client = httpx.AsyncClient(headers=headers, follow_redirects=True) if headers else None
        try:
            async with streamable_http_client(url, http_client=http_client) as (read, write, _):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    return await work(client)
        finally:
            if http_client is not None:
                await http_client.aclose()

    return anyio.run(_run)


def test_the_hosted_endpoint_lists_the_operations_with_exact_schemas(hosted_endpoint):
    listed = _exchange(hosted_endpoint, lambda c: c.list_tools())
    assert [tool.name for tool in listed.tools] == list(OPERATIONS)
    for tool in listed.tools:
        assert tool.inputSchema == operations.OPERATION_SPECS[tool.name]["input_schema"]


def test_the_hosted_endpoint_allows_the_local_settings_wizard_cors_preflight(hosted_endpoint):
    async def _run():
        import httpx

        async with httpx.AsyncClient(follow_redirects=True) as client:
            preflight = await client.options(
                hosted_endpoint,
                headers={
                    "Origin": "http://localhost:8000",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type,mcp-protocol-version",
                },
            )
            response = await client.post(
                hosted_endpoint,
                headers={
                    "Origin": "http://localhost:8000",
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "cors-test", "version": "1"},
                    },
                },
            )
            return preflight, response

    preflight, response = anyio.run(_run)
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:8000"
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:8000"
    assert "Mcp-Session-Id" in response.headers["access-control-expose-headers"]
    assert response.headers.get("Mcp-Session-Id"), (
        "stateful MCP transport must issue a traceable session"
    )


def test_the_hosted_endpoint_returns_run_operation_bytes(hosted_endpoint):
    result = _exchange(
        hosted_endpoint, lambda c: c.call_tool("list_context_catalog", {"limit": 50})
    )
    assert result.isError is False
    assert result.structuredContent["domains"] == [
        {"domain": "revenue", "concepts": ["recognized revenue"]}
    ]
    assert result.content[0].text == serialize(result.structuredContent)


def test_hosted_cors_allows_trace_linkage_headers(hosted_endpoint):
    async def _run():
        import httpx

        async with httpx.AsyncClient(follow_redirects=True) as client:
            return await client.options(
                hosted_endpoint,
                headers={
                    "Origin": "http://localhost:8000",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": (
                        "content-type,mcp-protocol-version,mcp-session-id,x-correlation-id,"
                        "x-hyperset-session-id,x-hyperset-turn-id,x-hyperset-tool-call-id,x-hyperset-intent"
                    ),
                },
            )

    response = anyio.run(_run)
    assert response.status_code == 200
    allowed = {
        item.strip().lower() for item in response.headers["access-control-allow-headers"].split(",")
    }
    assert {
        "content-type",
        "mcp-protocol-version",
        "mcp-session-id",
        "x-correlation-id",
        "x-hyperset-session-id",
        "x-hyperset-turn-id",
        "x-hyperset-tool-call-id",
        "x-hyperset-intent",
    } <= allowed


def test_hosted_session_metadata_survives_across_sdk_tool_calls(hosted_endpoint, monkeypatch):
    """The SDK's stateful HTTP session keeps trace and feedback linkage on later POSTs.

    The session manager runs the low-level server in a task created by the initialize
    request. A contextvar set only by the outer ASGI request therefore goes stale after
    initialization; this drives two real SDK calls so the second call proves the handler
    reads the request attached to the SDK's per-call RequestContext instead.
    """
    from hyperset.observability.interaction import current_trace_context
    from hyperset.transport import mcp as mcp_module

    observed = []

    def _run(name, arguments, **_kwargs):
        observed.append((name, current_trace_context()))
        return {"ok": True}

    monkeypatch.setattr(mcp_module, "run_operation", _run)

    async def _work(client):
        search = await client.call_tool(
            "search_knowledge", {"query": "recognized revenue", "intent": "release review"}
        )
        feedback = await client.call_tool(
            "record_answer_feedback", {"outcome": "accept", "source_ref": "source:docs/a.md"}
        )
        return search, feedback

    _exchange(hosted_endpoint, _work)

    assert [name for name, _context in observed] == [
        "search_knowledge",
        "record_answer_feedback",
    ]
    search_context = observed[0][1]
    feedback_context = observed[1][1]
    assert search_context.session_id
    assert feedback_context.session_id == search_context.session_id
    assert feedback_context.correlation_id == search_context.correlation_id
    assert search_context.intent == "release review"
    assert search_context.tool_call_id != feedback_context.tool_call_id


def _catalog_call(client):
    return client.call_tool("list_context_catalog", {"limit": 50})


def test_the_hosted_endpoint_verifies_each_request_bearer_and_leaks_no_identity(
    hosted_endpoint, monkeypatch
):
    """The real ASGI path, enabled: each request's Authorization bearer is verified in
    the task that request spawns (the contextvar `_handle` binds), and NO request
    inherits another's identity. This is the leak-proof the resolver-injection unit
    test cannot give -- it drives `_hosted_principal` + `_bind_request_authorization`
    + StreamableHTTPSessionManager for real. The verifier is stubbed (its matrix is in
    test_oidc.py) so the token->principal MAPPING is what the transport is proven to
    carry per request."""
    from hyperset.security.authz import Principal
    from hyperset.transport import mcp as mcp_module

    reader = Principal(subject="alice", issuer="https://issuer.example/", roles=("reader",))
    monkeypatch.setattr(
        mcp_module,
        "principal_from_bearer",
        lambda authorization: reader if authorization == "Bearer alice-token" else None,
    )
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")

    # A: the authorized reader is served.
    served = _exchange(
        hosted_endpoint, _catalog_call, headers={"Authorization": "Bearer alice-token"}
    )
    assert served.isError is False
    assert served.structuredContent["domains"] == [
        {"domain": "revenue", "concepts": ["recognized revenue"]}
    ]

    # B: a DIFFERENT bearer, right after A -- denied. If A's identity had leaked across
    # requests (a stale contextvar / pooled task), this would be served. It is not.
    other = _exchange(
        hosted_endpoint, _catalog_call, headers={"Authorization": "Bearer mallory-token"}
    )
    assert other.isError is True
    assert other.structuredContent["error"]["code"] == "unauthorized"

    # C: no bearer at all -- denied (the contextvar was reset, not left set from A).
    none = _exchange(hosted_endpoint, _catalog_call)
    assert none.isError is True
    assert none.structuredContent["error"]["code"] == "unauthorized"

    # D: the reader again -- still served, so a denial did not poison the path.
    again = _exchange(
        hosted_endpoint, _catalog_call, headers={"Authorization": "Bearer alice-token"}
    )
    assert again.isError is False
