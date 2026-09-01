"""What the wire actually delivers, against what section 7 promises (hy-lqla).

The section-7 guard binds documented prose to a constant TUPLE, which is why
it could not catch PR #94 promising clients `unknown_operation`: the constant
legitimately contains that code, the registry legitimately gates it, and
neither knows that no transport can deliver it. HTTP builds its routes from
`OPERATIONS`, so an unknown name is a 404 `unknown_route`; the MCP server lists
only the operations and answers an unknown tool with an `isError` result coded
`unknown_tool`, never `unknown_operation`.

So this exercises both surfaces and reads the codes out of the DELIVERED
payloads rather than out of the code that constructs them -- delivery is the
property, and the MCP unknown-tool path never constructs an `OperationError` at
all.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import threading

import pytest

from hyperset.transport import operations
from hyperset.transport.http import MAX_BODY_BYTES, build_server
from hyperset.transport.operations import (
    HTTP_ERROR_CODES,
    OPERATION_ERROR_CODES,
    UNKNOWN_OPERATION,
)
from tests.unit.transport.conftest import DIRECTIVE, QUESTION

# Reachable by `run_operation` and by no transport: both check the operation
# name before dispatch. Named here so the assertion below can require that it
# is documented AND never delivered -- the pair of facts a constant cannot
# hold (hy-lqla).
IN_PROCESS_ONLY = (UNKNOWN_OPERATION,)

WIRE_CODES = tuple(code for code in OPERATION_ERROR_CODES if code not in IN_PROCESS_ONLY)


@contextlib.contextmanager
def _gate_enabled():
    """Turn the authorization gate on for the duration, so a call with no bearer
    token is DENIED and delivers `unauthorized`. Restored on exit -- every other
    case in this file runs with the gate off (the default), where the wire is
    unchanged. `unauthorized` is a WIRE code, so both surfaces must deliver it."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
        yield


@pytest.fixture
def base_url(session_factory):
    server = build_server(session_factory=session_factory, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _delivered_code(payload) -> str | None:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"]
    return None


def _http_codes(base_url: str, exploding) -> set[str]:
    """Every error code an HTTP client can be handed, by asking for them.

    Driven through `http.client` rather than `urllib`, and the reason recorded
    here first was WRONG: I claimed `urllib` repairs these before they reach
    the socket. Measured against this server, it does not -- an explicit
    oversized `Content-Length` still yields 413, a non-numeric one still
    yields `invalid_request`, a GET with a body still yields
    `method_not_allowed`, and `Transfer-Encoding: chunked` is still sent. The
    original failure that produced that theory was a wrong route prefix, not
    `urllib`.

    `http.client` stays because an envelope test should send exactly the bytes
    it means to and nothing should be free to normalise them, but that is a
    preference for control rather than a defect being avoided.
    """
    seen: set[str] = set()
    host, port = base_url.rsplit("/", 1)[-1].split(":")

    def post(path, body, *, headers=None, method="POST"):
        connection = http.client.HTTPConnection(host, int(port), timeout=5)
        try:
            connection.putrequest(method, path, skip_accept_encoding=True)
            sent = {"Content-Type": "application/json", **(headers or {})}
            if body is not None and "Content-Length" not in sent:
                sent["Content-Length"] = str(len(body))
            for key, value in sent.items():
                connection.putheader(key, value)
            connection.endheaders()
            if body is not None and method != "GET":
                connection.send(body)
            response = connection.getresponse()
            try:
                return json.loads(response.read())
            except json.JSONDecodeError:
                return None
        finally:
            connection.close()

    good = json.dumps({"query": QUESTION, "directive": DIRECTIVE}).encode()
    cases = [
        ("/v0/resolve_analytics_context", b"{not json", None, "POST"),
        ("/v0/resolve_analytics_context", json.dumps({"query": QUESTION}).encode(), None, "POST"),
        (
            "/v0/resolve_analytics_context",
            json.dumps({"query": QUESTION, "directive": DIRECTIVE, "nope": 1}).encode(),
            None,
            "POST",
        ),
        ("/v0/resolve_analytics_context", json.dumps({"query": 7}).encode(), None, "POST"),
        ("/v0/not_an_operation", good, None, "POST"),
        ("/v0/resolve_analytics_context", None, None, "GET"),
        (
            "/v0/resolve_analytics_context",
            good,
            {"Content-Length": str(MAX_BODY_BYTES + 1)},
            "POST",
        ),
        ("/v0/resolve_analytics_context", good, {"Content-Length": "not-a-number"}, "POST"),
        ("/v0/resolve_analytics_context", good, {"Transfer-Encoding": "chunked"}, "POST"),
    ]
    for path, body, headers, method in cases:
        code = _delivered_code(post(path, body, headers=headers, method=method))
        if code:
            seen.add(code)

    with exploding():
        code = _delivered_code(post("/v0/resolve_analytics_context", good))
        if code:
            seen.add(code)

    # Gate on, no Authorization header: the fail-closed denial, delivered over HTTP.
    with _gate_enabled():
        code = _delivered_code(post("/v0/resolve_analytics_context", good))
        if code:
            seen.add(code)
    return seen


def _mcp_codes(session_factory, exploding) -> set[str]:
    """The same question of the MCP surface, through a real client tool call."""
    # FUNCTION-level guard on the shared MCP helper: the four MCP-surface tests SKIP without the
    # `mcp` extra while the HTTP-only test still runs, and all stay COLLECTED so the skip count
    # moves and the denominator does not (hy-fabp; this file has HTTP-only tests too).
    pytest.importorskip("mcp")
    import anyio

    from hyperset.transport.mcp import build_mcp_server

    seen: set[str] = set()

    def call(name, arguments):
        async def _run():
            from mcp.shared.memory import create_connected_server_and_client_session as connect

            server = build_mcp_server(session_factory=session_factory)
            async with connect(server) as client:
                return await client.call_tool(name, arguments)

        result = anyio.run(_run)
        return _delivered_code(result.structuredContent)

    cases = [
        ("resolve_analytics_context", {"query": QUESTION}),
        ("resolve_analytics_context", {"query": QUESTION, "directive": DIRECTIVE, "nope": 1}),
        ("resolve_analytics_context", {"query": 7, "directive": DIRECTIVE}),
        ("list_context_catalog", {"limit": "lots"}),
        ("validate_analytics_plan", {"query": QUESTION, "directive": DIRECTIVE}),
    ]
    for name, arguments in cases:
        code = call(name, arguments)
        if code:
            seen.add(code)

    with exploding():
        code = call("resolve_analytics_context", {"query": QUESTION, "directive": DIRECTIVE})
        if code:
            seen.add(code)

    # Gate on, no bearer threaded through the MCP boundary: the same fail-closed
    # denial, delivered over MCP (`run_operation` defaults `principal` to None).
    with _gate_enabled():
        code = call("resolve_analytics_context", {"query": QUESTION, "directive": DIRECTIVE})
        if code:
            seen.add(code)
    return seen


@pytest.fixture
def exploding():
    """A resolver that raises something the transport did not expect.

    `internal_error` is a documented code and the only one no malformed
    request can produce -- it is the server's own fault by definition. Without
    this it would look undeliverable, which is the false negative this test
    exists to avoid.
    """
    import contextlib

    @contextlib.contextmanager
    def raising():
        def boom(*_args, **_kwargs):
            raise RuntimeError("the resolver fell over")

        # A nested `monkeypatch` so this undoes only what it set: the outer
        # fixture's `undo()` would also revert patches another fixture made,
        # which is the kind of coupling that produces an unexplainable failure
        # months later.
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(operations, "resolve_analytics_context", boom)
            yield

    return raising


def test_every_documented_wire_code_is_delivered_by_mcp(session_factory, exploding):
    """PER SURFACE, because a union cannot see a one-sided regression.

    The first version of this file asserted over `http | mcp`, and HTTP alone
    delivers all nine required codes -- so MCP could regress from four
    delivered codes to one and every assertion stayed green. That is PR #94's
    defect on the MCP surface specifically: a documented code no MCP client
    can receive, invisible because the other transport covered for it.

    A per-surface assertion is also non-vacuous by construction, which is what
    retired the separate emptiness check that used to sit below: when a guard
    needs a second guard to be meaningful, the first one's scope is the
    defect.
    """
    assert set(WIRE_CODES) <= _mcp_codes(session_factory, exploding)


def test_every_documented_code_is_delivered_by_http(base_url, exploding):
    """The same question of the other surface, including the five envelope
    codes an MCP client can never see."""
    assert set(WIRE_CODES) | set(HTTP_ERROR_CODES) <= _http_codes(base_url, exploding)


def test_every_delivered_code_is_documented(base_url, session_factory, exploding):
    """The more dangerous direction: a code arriving at a client that the
    contract never mentioned, which is a surprise rather than a dead branch."""
    delivered = _http_codes(base_url, exploding) | _mcp_codes(session_factory, exploding)

    assert delivered <= set(OPERATION_ERROR_CODES) | set(HTTP_ERROR_CODES)


def test_the_in_process_codes_stay_off_the_wire(base_url, session_factory, exploding):
    """The pair a constant cannot hold: `unknown_operation` is documented, and
    must never be delivered. Both transports check the operation name before
    dispatch, so if this ever fires, either a transport stopped checking or the
    documentation should stop calling it in-process only."""
    delivered = _http_codes(base_url, exploding) | _mcp_codes(session_factory, exploding)

    # Both halves of the pair, where the pair is stated: documented, and never
    # delivered. Asserting only the second would let the code be quietly
    # dropped from the contract and still pass.
    assert set(IN_PROCESS_ONLY) <= set(OPERATION_ERROR_CODES)
    assert delivered.isdisjoint(IN_PROCESS_ONLY)


def test_an_mcp_client_never_receives_an_http_envelope_code(session_factory, exploding):
    """The transport split section 7 states, asserted against delivery rather
    than against where the constant sits."""
    assert _mcp_codes(session_factory, exploding).isdisjoint(HTTP_ERROR_CODES)
