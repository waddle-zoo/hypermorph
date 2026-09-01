"""The MCP adapter over the v0 operations, on the official SDK (hy-oih, hy-gh-117).

A tool-using model reaches the same operations the HTTP adapter serves and gets
the same bytes: a tool call returns exactly what `run_operation` returned,
serialized once. The surface includes governed reads plus explicitly labelled
assist/audit and proposal-only review tools; it has no resources, prompts, or
sampling, and no tool can approve or merge a context change.

WHY THE SDK LOW-LEVEL SERVER, NOT HAND-ROLLED, NOT FASTMCP. The previous
version framed JSON-RPC, negotiated the protocol version, and listed tools by
hand; #117 replaces that bespoke framing with the official MCP SDK so one
definition serves both stdio and Streamable HTTP. It is the SDK's LOW-LEVEL
`Server`, not the FastMCP high-level wrapper, for one measured reason: FastMCP
derives a tool's `inputSchema` from the Python function signature and cannot
serve our exact `OPERATION_SPECS` JSON Schema, which would break the byte
parity between this transport and HTTP that the contract requires. The
low-level `Server` takes `types.Tool(inputSchema=...)` verbatim and returns a
`types.CallToolResult` whose `structuredContent` is exactly `run_operation`'s
output, so parity is preserved by construction.

The SDK transport (starlette/uvicorn) is an optional `mcp` extra, kept out of
the core install for the same reason the planner SDK is: a customer installing
Hyperset for the contract and the operations should not acquire a web server
because an optional hosted surface exists. `serve_stdio`/`serve_http` import the
SDK lazily and say what to install when it is absent.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
from dataclasses import replace

from hyperset import __version__
from hyperset.observability.correlation import new_correlation_id
from hyperset.observability.interaction import (
    TraceContext,
    current_trace_context,
    opaque_token,
    set_trace_context,
    trace_context_from_headers,
)
from hyperset.security.authz import SYSTEM_PRINCIPAL
from hyperset.security.oidc import principal_from_bearer
from hyperset.transport.operations import (
    OPERATION_SPECS,
    OPERATIONS,
    OperationError,
    run_operation,
    serialize,
)

SERVER_NAME = "hyperset"

# The raw `Authorization` header of the current HOSTED (Streamable HTTP) MCP request,
# captured by `_handle` for the duration of that request. `None` on stdio and off the
# flag. A `ContextVar` rather than a passed argument because the SDK's tool dispatch
# gives the handler no request object -- the value is set on the ASGI task and read by
# the tool call it drives.
_request_authorization: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mcp_request_authorization", default=None
)


def _hosted_principal():
    """The verified caller for a HOSTED MCP request, from the `Authorization` bearer
    `_handle` captured, or `None`. `None` when the gate is disabled (the default) or no
    valid bearer is present, so the gate no-ops (disabled) or denies (enabled). This is
    a network boundary, so it verifies a bearer exactly as the HTTP transport does."""
    return principal_from_bearer(_request_authorization.get())


def _local_principal():
    """stdio is a LOCAL trusted subprocess -- the OS process that spawned Hyperset is
    the identity -- so it authorizes as the trusted in-process reader rather than
    presenting a bearer. Never used by the hosted transport, so external input can
    never select it."""
    return SYSTEM_PRINCIPAL


@contextlib.contextmanager
def _bind_request_authorization(scope):
    """Bind the current HTTP request's `Authorization` header to the contextvar the
    tool call reads, for the duration of the request, then reset it -- so no request's
    identity ever leaks into the next. A non-HTTP scope binds nothing. Set BEFORE the
    request is handled so the tool-execution task inherits the value."""
    if scope.get("type") != "http":
        yield
        return
    headers = dict(scope.get("headers") or [])
    raw = headers.get(b"authorization")
    reset = _request_authorization.set(raw.decode("latin-1") if raw is not None else None)

    # Bind the MCP interaction-trace linkage (hy-oqevj) from the SAME request headers,
    # for the duration of this request. Opaque trace tokens (session/turn/tool_call/
    # correlation ids + intent), never identity -- the traced principal is still derived
    # server-side from the bearer above. ASGI header names are lowercased bytes.
    def _header(name: str) -> str | None:
        value = headers.get(name.encode("latin-1"))
        return value.decode("latin-1") if value is not None else None

    trace_context = trace_context_from_headers(_header)
    # A connected Streamable HTTP client always has the SDK-issued MCP session id
    # after initialization. Use it as the default correlation chain when the
    # client does not add Hyperset-specific correlation headers, so search,
    # resolve, and feedback remain linked without requiring client changes.
    if trace_context.session_id and trace_context.correlation_id is None:
        trace_context = replace(trace_context, correlation_id=trace_context.session_id)
    set_trace_context(trace_context)
    try:
        yield
    finally:
        _request_authorization.reset(reset)
        set_trace_context(None)


@contextlib.contextmanager
def _bind_stdio_trace_context():
    """Bind one traceable session and correlation chain to a spawned stdio client.

    Stdio has no HTTP headers or MCP session header to carry Hyperset's audit linkage. The
    subprocess itself is the trusted connection boundary, so mint one opaque session and
    correlation for that connection and keep them stable for every tool call. This lets
    search, resolve, and feedback share a durable chain without adding metadata to tool
    arguments or emitting anything into the MCP stdout protocol stream.
    """
    set_trace_context(
        TraceContext(session_id=new_correlation_id(), correlation_id=new_correlation_id())
    )
    try:
        yield
    finally:
        set_trace_context(None)


@contextlib.contextmanager
def _bind_tool_call_trace_context(server, name: str, arguments: dict):
    """Add the SDK's real RPC id and a search intent to the current trace.

    Transport headers establish the connection/session chain. The SDK dispatches the
    JSON-RPC request to this callback, so bind its request id here before the synchronous
    operation moves to the worker thread. Streamable HTTP dispatch runs the SDK handler in
    a long-lived session task, so the ASGI task's contextvars do not carry headers from
    later requests; read the SDK-attached request here to keep the negotiated session and
    bearer tied to every tool call. Search intent is taken from the existing ``intent``
    argument when the caller supplies it; no intent is invented server-side.
    """
    previous = current_trace_context()
    request_headers = None
    try:
        request = server.request_context.request
        request_headers = request.headers if request is not None else None
    except (AttributeError, LookupError):
        pass

    # The low-level SDK exposes the originating Starlette request on its per-call
    # RequestContext. Prefer it over the outer ASGI context: stateful Streamable HTTP
    # sessions process later POSTs on the session task created by initialization, while
    # the headers (including Mcp-Session-Id and Authorization) belong to each POST.
    context = previous
    authorization_reset = None
    if request_headers is not None:
        trace_context = trace_context_from_headers(request_headers.get)
        if trace_context.session_id and trace_context.correlation_id is None:
            trace_context = replace(trace_context, correlation_id=trace_context.session_id)
        context = trace_context
        raw_authorization = request_headers.get("authorization")
        authorization_reset = _request_authorization.set(raw_authorization)

    try:
        raw_request_id = server.request_context.request_id
    except (AttributeError, LookupError):
        raw_request_id = None
    request_id = opaque_token(str(raw_request_id)) if raw_request_id is not None else None
    if request_id:
        context = replace(
            context,
            turn_id=context.turn_id or f"mcp-turn-{request_id}",
            tool_call_id=context.tool_call_id or f"mcp-call-{request_id}",
        )
    if context.intent is None and isinstance(arguments, dict):
        raw_intent = arguments.get("intent")
        if isinstance(raw_intent, str) and raw_intent.strip():
            context = replace(context, intent=raw_intent.strip())
    set_trace_context(context)
    try:
        yield
    finally:
        if authorization_reset is not None:
            _request_authorization.reset(authorization_reset)
        set_trace_context(previous)


INSTRUCTIONS = (
    "Hyperset serves two flows.\n\n"
    "ANSWER WITH GOVERNED CONTEXT: call list_context_catalog first to see the domains "
    "and refs that exist; use discover_analytics_context to rank them for a question "
    "(assist-class, non-authoritative -- it says where to look, not what is true); then "
    "resolve_analytics_context to compile one ContextBundle from the exact domains/refs "
    "your 'directive' names; then validate_analytics_plan to check the fetch you intend "
    "to run against that bundle. Hyperset retrieves what you NAME and never infers a "
    "domain from the wording of a question, and it never executes or validates SQL or "
    "results.\n\n"
    "CURATE CONTEXT (the flywheel): when a question finds no governed context, a review "
    "task holds an UNAPPROVED agent-drafted definition. Read it with list_review_tasks "
    "and get_review_task; improve the draft with edit_review_draft (an exact edit) or "
    "refine_review_draft (re-run the authoring agent with feedback) -- both mutate ONLY "
    "the unapproved assist draft; then propose_review_to_git to open a proposal-only pull "
    "request into the customer's Git context repo, and STOP.\n\n"
    "FEEDBACK: after using a traced search hit or bundle, call record_answer_feedback "
    "with the same MCP session and correlation metadata; use lookup_answer_feedback to "
    "read that append-only audit trail. Feedback is not approval and changes no governed "
    "context or review-task state.\n\n"
    "'governed' means the customer's Git context owns the meaning -- NOT that Hyperset "
    "approved it. Nothing here approves, merges, or writes authority: the only path to "
    "authority is a human Git merge (ADR 0012)."
)

# The path the Streamable HTTP transport serves on, so a client connects to
# http://<host>:<port>/mcp. Stated once here and used by serve_http and the docs.
HTTP_PATH = "/mcp"
DEFAULT_HTTP_HOST = "127.0.0.1"
# Not 8000: that is the demo `api` service's published host port, so a hosted
# MCP server on the old default collided with it and the demo could not run both
# (hy-3kpk). 8010 is the demo's published MCP port too, so the default matches
# what the compose stack and the README connect instructions use.
DEFAULT_HTTP_PORT = 8010

_MISSING_EXTRA = (
    "the MCP SDK transport is not installed; install it with `pip install 'hyperset[mcp]'` "
    "(or `uv sync --extra mcp`). It is an optional extra so the core install does not carry a "
    "web server."
)


def _browser_cors_origins() -> list[str]:
    """Return explicitly allowed browser origins for the local MCP wizard."""
    raw = os.environ.get("HYPERSET_MCP_CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
    return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]


def tool_definitions() -> list[dict]:
    """The tool list, built from the one operation registry so a tool can
    never advertise parameters the operation does not accept. The description
    carries a checked-in example, the same as the HTTP tool docs."""
    return [
        {
            "name": name,
            "title": OPERATION_SPECS[name]["title"],
            "description": (
                f"{OPERATION_SPECS[name]['description']}\n\n"
                f"Example arguments: {serialize(OPERATION_SPECS[name]['example'])}"
            ),
            "inputSchema": OPERATION_SPECS[name]["input_schema"],
        }
        for name in OPERATIONS
    ]


def _call(name: str, arguments: dict, *, session_factory, principal=None):
    """Run one operation and shape it as an MCP tool result.

    `structuredContent` is exactly `run_operation`'s dict and the text block is
    the same serialization the HTTP body uses, so the two transports return the
    same bytes. An operation-level failure comes back as an `isError` result the
    model can read and retry, never as a protocol error -- the parity the HTTP
    adapter keeps by returning the error payload rather than raising.

    `principal` is the verified caller the transport resolved (a hosted request's
    bearer, or the local stdio identity); the authorization gate at `run_operation`
    reads it. With the gate disabled (the default) it is ignored and the bytes are
    unchanged.
    """
    import mcp.types as types

    if name not in OPERATIONS:
        payload = {
            "error": {
                "code": "unknown_tool",
                "message": f"unknown tool {name!r}; this server serves {', '.join(OPERATIONS)}",
            }
        }
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=serialize(payload))],
            structuredContent=payload,
            isError=True,
        )
    try:
        payload = run_operation(
            name, arguments, session_factory=session_factory, principal=principal
        )
        is_error = False
    except OperationError as exc:
        payload = exc.to_dict()
        is_error = True
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=serialize(payload))],
        structuredContent=payload,
        isError=is_error,
    )


def build_mcp_server(*, session_factory, principal_resolver=_hosted_principal):
    """A low-level MCP `Server` serving the v0 operations. Requires the `mcp` extra.

    `principal_resolver` produces the verified caller for each tool call: the hosted
    transport uses `_hosted_principal` (the request's bearer, the default), and stdio
    uses `_local_principal` (the trusted local identity). It is resolved per call so a
    hosted request reads its own `Authorization` header, not another request's."""
    try:
        import mcp.types as types
        from mcp.server.lowlevel import Server
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised via serve_*
        raise RuntimeError(_MISSING_EXTRA) from exc

    # Inject the refine model runtime (hy-jis1): operations names no inference
    # module, so the served refine op gets its runtime on the MCP transport too.
    from hyperset.transport.review_runtime import install_authoring_runtime

    install_authoring_runtime()

    server: Server = Server(SERVER_NAME, version=__version__, instructions=INSTRUCTIONS)

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=spec["name"],
                title=spec["title"],
                description=spec["description"],
                inputSchema=spec["inputSchema"],
            )
            for spec in tool_definitions()
        ]

    # validate_input=False: `run_operation` is the sole validator, so a bad
    # argument comes back as the same isError payload the HTTP adapter returns
    # rather than a schema error the client cannot act on the same way.
    @server.call_tool(validate_input=False)
    async def _call_tool(name: str, arguments: dict):
        import anyio

        with _bind_tool_call_trace_context(server, name, arguments):
            principal = principal_resolver()
            return await anyio.to_thread.run_sync(
                lambda: _call(
                    name,
                    arguments,
                    session_factory=session_factory,
                    principal=principal,
                )
            )

    return server


def serve_stdio(*, session_factory) -> None:
    """Serve MCP on stdio, for a client that spawns Hyperset as a subprocess. The
    caller is the local trusted process, so the gate authorizes it as the in-process
    reader (relevant only when the gate is enabled)."""
    import anyio
    from mcp.server.stdio import stdio_server

    server = build_mcp_server(session_factory=session_factory, principal_resolver=_local_principal)

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            with _bind_stdio_trace_context():
                await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(_run)


def streamable_http_app(*, session_factory):
    """The Starlette app that serves MCP over Streamable HTTP at `HTTP_PATH`.

    Built separately from the server run so a test can drive it under its own
    uvicorn with shutdown control. Stateless sessions, the right shape for a
    read-only tool surface with no subscriptions. The SDK-managed session ID is
    retained so every request from a connected MCP client carries the same
    traceable session into the interaction audit; no governed state is mutated.
    """
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.routing import Mount

    server = build_mcp_server(session_factory=session_factory)
    manager = StreamableHTTPSessionManager(app=server, stateless=False)

    async def _handle(scope, receive, send) -> None:
        # The gateway terminates OIDC/SAML and forwards the bearer; bind it so the tool
        # call this request drives resolves its OWN caller, and `_hosted_principal`
        # verifies it. Bound before the request is handled, reset after.
        with _bind_request_authorization(scope):
            await manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def _lifespan(_app):
        async with manager.run():
            yield

    app = Starlette(routes=[Mount(HTTP_PATH, app=_handle)], lifespan=_lifespan)
    # Streamable HTTP clients do not need CORS, but the local Settings wizard is
    # a browser client on the API port. Keep the default limited to loopback and
    # allow an explicit deployment override; never use '*'.
    from starlette.middleware.cors import CORSMiddleware

    return CORSMiddleware(
        app,
        allow_origins=_browser_cors_origins(),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "MCP-Protocol-Version",
            "Mcp-Session-Id",
            "X-Correlation-Id",
            "X-Hyperset-Session-Id",
            "X-Hyperset-Turn-Id",
            "X-Hyperset-Tool-Call-Id",
            "X-Hyperset-Intent",
        ],
        expose_headers=["Mcp-Session-Id"],
        max_age=600,
    )


def serve_streamable_http(
    *, session_factory, host: str = DEFAULT_HTTP_HOST, port: int = DEFAULT_HTTP_PORT
) -> None:
    """Serve MCP over Streamable HTTP at http://<host>:<port>/mcp (hy-gh-117).

    A hosted, connectable endpoint: a client points at the URL rather than
    spawning a subprocess.
    """
    import uvicorn

    uvicorn.run(streamable_http_app(session_factory=session_factory), host=host, port=port)
