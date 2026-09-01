"""The HTTP adapter's own rules (hy-oih): routing, decoding, status codes.

The server runs for real on a loopback port -- a handler tested through a
stub request object proves nothing about what a client receives.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from hyperset.bundle import CATALOG_MAX_LIMIT
from hyperset.security.authz import Principal
from hyperset.transport import http as http_module
from hyperset.transport import operations
from hyperset.transport.http import HEALTH_PATH, MAX_BODY_BYTES, _Handler, build_server
from tests.review_api import (
    EDIT_REVIEW_DRAFT_PATH,
    PROPOSE_REVIEW_TO_GIT_PATH,
    REFINE_REVIEW_DRAFT_PATH,
    SET_REVIEW_ASSIGNEE_PATH,
)
from tests.unit.transport.conftest import DIRECTIVE, PRIMARY, QUESTION, catalog, governed_bundle

# Every review WRITE op, as served paths: authz (the reviewer allowlist, in run_operation)
# must deny a reader on each -- the coverage the removed bespoke /v0/review/* write adapters
# carried, migrated onto the served operation surface (hy-es7z).
REVIEW_WRITE_OP_PATHS = (
    EDIT_REVIEW_DRAFT_PATH,
    REFINE_REVIEW_DRAFT_PATH,
    PROPOSE_REVIEW_TO_GIT_PATH,
    SET_REVIEW_ASSIGNEE_PATH,
)


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


def _post(base_url, path, payload, *, raw=None, headers=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _pooled(base_url) -> http.client.HTTPConnection:
    """One connection held across calls, the way an agent's HTTP client holds
    it. `_post` opens a fresh one per call and cannot see a desynced socket."""
    host, port = base_url.removeprefix("http://").split(":")
    return http.client.HTTPConnection(host, int(port))


def test_resolve_serves_the_bundle_unchanged(base_url, resolved):
    status, payload = _post(
        base_url, "/v0/resolve_analytics_context", {"query": QUESTION, "directive": DIRECTIVE}
    )

    assert status == 200
    assert payload == governed_bundle().to_dict()


def test_the_catalog_is_served_unchanged(base_url, listed):
    status, payload = _post(base_url, "/v0/list_context_catalog", {})

    assert status == 200
    assert payload == catalog().to_dict()


def test_validate_serves_the_plan_validation(base_url, resolved):
    status, payload = _post(
        base_url,
        "/v0/validate_analytics_plan",
        {
            "query": QUESTION,
            "directive": DIRECTIVE,
            "bundle_id": governed_bundle().bundle_id,
            "source_refs": [PRIMARY],
            "grain": "order_date",
        },
    )

    assert status == 200
    # This fixture's governed context declares no filters, joins, or checks, so a
    # plan that contradicts nothing is `valid_with_gaps`, not a false green: the
    # served response names the sections that could not be checked (#285).
    assert payload["status"] == "valid_with_gaps"
    assert [section["section"] for section in payload["sections_not_checkable"]] == [
        "instructions.filters",
        "instructions.joins",
        "instructions.validations",
    ]
    assert payload["bundle_id"] == governed_bundle().bundle_id


def test_a_bad_request_is_a_400_that_says_what_to_change(base_url, resolved):
    status, payload = _post(base_url, "/v0/resolve_analytics_context", {})

    assert status == 400
    assert payload["error"]["code"] == "invalid_params"
    assert payload["error"]["recovery"]


def test_a_question_with_no_directive_is_a_400_naming_the_catalog(base_url, resolved):
    status, payload = _post(base_url, "/v0/resolve_analytics_context", {"query": QUESTION})

    assert status == 400
    assert payload["error"]["code"] == "directive_required"
    assert "list_context_catalog" in payload["error"]["recovery"]


def test_a_body_that_is_not_json_names_an_example(base_url):
    status, payload = _post(base_url, "/v0/resolve_analytics_context", None, raw=b"{not json")

    assert status == 400
    assert payload["error"]["code"] == "invalid_json"
    assert QUESTION in payload["error"]["recovery"]


def test_an_unknown_path_lists_the_operations(base_url):
    status, payload = _post(base_url, "/v0/execute_sql", {})

    assert status == 404
    assert "/v0/resolve_analytics_context" in payload["error"]["recovery"]


def test_the_api_serves_the_opt_in_playground_and_its_operation_proxy(
    base_url, resolved, monkeypatch
):
    # The API serves the BUILT playground bundle (dist/index.html, referencing
    # /playground/assets/...) only if it exists; otherwise it falls back to the source
    # index.html referencing /src/main.jsx and the /playground/assets assertion below
    # fails. On a clean checkout dist/ is gitignored and unbuilt, so name the missing
    # BUILD STEP here rather than letting it read as a broken assertion (hy-r8jd, #346).
    dist_index = (
        Path(http_module.__file__).resolve().parents[2]
        / "playground"
        / "ui"
        / "dist"
        / "index.html"
    )
    if not dist_index.exists():
        pytest.fail(
            "playground/ui/dist is not built, so the API serves the source index.html "
            "(/src/main.jsx) instead of the built /playground/assets bundle. Build it "
            "with `make playground-ui` (npm ci && npm run build) -- it is in the "
            "CLAUDE.md Completion checklist before scripts/gate.py."
        )
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")

    with urllib.request.urlopen(f"{base_url}/admin/") as response:
        admin_html = response.read().decode()
    with urllib.request.urlopen(f"{base_url}/admin/graph/") as response:
        admin_graph_html = response.read().decode()
    with urllib.request.urlopen(f"{base_url}/playground/") as response:
        user_html = response.read().decode()
    with urllib.request.urlopen(f"{base_url}/playground/catalog/") as response:
        user_catalog_html = response.read().decode()
    # The reviewer surface is its own first-class page (hy-1f96): the root and a
    # client-side subpath both serve the SPA shell.
    with urllib.request.urlopen(f"{base_url}/review/") as response:
        review_html = response.read().decode()
    with urllib.request.urlopen(f"{base_url}/review/anything/") as response:
        review_subpath_html = response.read().decode()
    with urllib.request.urlopen(f"{base_url}/") as response:
        home_html = response.read().decode()

    status, payload = _post(
        base_url,
        "/admin/api/v0/resolve_analytics_context",
        {"query": QUESTION, "directive": DIRECTIVE},
    )

    assert 'id="root"' in admin_html
    assert 'id="root"' in admin_graph_html
    assert 'id="root"' in user_html
    assert 'id="root"' in user_catalog_html
    assert 'id="root"' in review_html
    assert 'id="root"' in review_subpath_html
    assert "/playground/assets/" in admin_html
    assert "Context that knows how it connects." in home_html
    assert 'href="/playground/"' in home_html
    assert 'href="/review/"' in home_html
    assert 'href="/admin/"' in home_html
    assert 'href="/playground/mcp/">MCP setup</a>' in home_html
    assert "getting-started.md#connecting-an-mcp-client" not in home_html
    assert status == 200
    assert payload == governed_bundle().to_dict()


@pytest.mark.parametrize(
    ("path", "runner"),
    (
        ("/admin/api/demo/query", "_run_demo_sql"),
        ("/playground/api/chat", "_stream_playground_chat"),
    ),
)
def test_executable_playground_routes_are_gated_before_dispatch(
    base_url, path, runner, monkeypatch
):
    """Authz must protect both the SQL demo and the agent streaming entry point."""
    from playground.ui import app as playground_app

    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")

    def must_not_run(*_args, **_kwargs):
        pytest.fail("unauthenticated playground execution reached the runner")

    monkeypatch.setattr(playground_app, runner, must_not_run)
    status, payload = _post(base_url, path, {"sql": "SELECT 1", "message": "hello"})
    assert status == 400
    assert payload["error"]["code"] == "unauthorized"


def test_the_mcp_wizard_backend_routes_answer_through_the_public_prefix(
    base_url, resolved, monkeypatch
):
    # The MCP setup wizard (hy-8u0a, V1 gap E) wires its "Test connection" to real
    # routes through the PUBLIC /playground/api prefix: health (GET) for
    # reachability plus the served tool list, then discover/resolve (POST). This
    # proves the prefix proxy forwards both verbs to those routes and the tool
    # surface the wizard names is actually served -- so a proxy or route regression
    # reds here rather than in a browser. discover's live ranking runs over a real
    # store (tests/postgres/test_transport_parity.py); resolve here rides the
    # `resolved` fixture, which is enough to prove the public POST path answers.
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")

    with urllib.request.urlopen(f"{base_url}/playground/api/v0/health") as response:
        assert response.status == 200
        health = json.loads(response.read())
    # The three tools the wizard drives must be in the served operation list, or it
    # would report a missing surface instead of a later 404.
    for operation in (
        "discover_analytics_context",
        "resolve_analytics_context",
        "validate_analytics_plan",
    ):
        assert operation in health["operations"]

    resolve_status, bundle = _post(
        base_url,
        "/playground/api/v0/resolve_analytics_context",
        {"query": QUESTION, "directive": DIRECTIVE},
    )
    assert resolve_status == 200
    # The wizard branches on exactly these resolver statuses; a value outside the
    # set would render as "Unrecognized status" rather than a classified state.
    assert bundle["resolution"]["status"] in ("governed", "mixed", "observed_only", "no_match")


def test_an_operation_answers_post_not_get(base_url):
    request = urllib.request.Request(f"{base_url}/v0/resolve_analytics_context", method="GET")
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request)

    assert excinfo.value.code == 405
    assert excinfo.value.headers["Allow"] == "POST"


def test_an_oversized_body_is_refused_without_being_read(base_url):
    status, payload = _post(
        base_url,
        "/v0/resolve_analytics_context",
        None,
        raw=b"",
        headers={"Content-Length": str(MAX_BODY_BYTES + 1)},
    )

    assert status == 413
    assert payload["error"]["code"] == "request_too_large"


def test_a_refused_request_does_not_corrupt_the_next_one_on_a_pooled_connection(base_url, resolved):
    """The failure this guards is worse than an error: the unread body of the
    refused request was parsed as the start of the next one, so the second
    call read the first call's answer -- httpx, requests.Session and aiohttp
    all pool by default."""
    connection = _pooled(base_url)
    body = json.dumps({"query": QUESTION, "directive": DIRECTIVE}).encode()
    headers = {"Content-Type": "application/json"}
    connection.request("POST", "/v0/execute_sql", body=body, headers=headers)
    refused = connection.getresponse()
    refused_payload = json.loads(refused.read())

    assert refused.status == 404
    assert refused_payload["error"]["code"] == "unknown_route"

    connection.request("POST", "/v0/resolve_analytics_context", body=body, headers=headers)
    answered = connection.getresponse()
    answered_payload = json.loads(answered.read())
    connection.close()

    # The answer first, because that is the damage: asserting the header alone
    # would fail on a server that corrupts nothing.
    assert answered.status == 200
    assert answered_payload == governed_bundle().to_dict()
    assert refused.getheader("Connection") == "close"


def test_a_served_request_does_not_leave_the_next_failure_thinking_it_read_a_body(
    base_url, resolved
):
    """`BaseHTTPRequestHandler` serves every request on a connection from one
    instance, so a consumption flag that is not reset per request survives
    into the next one. Order is the whole test: the 404 has to follow a
    successful POST, because a 404 first sees the flag in its initial state
    and passes with the bug present."""
    connection = _pooled(base_url)
    headers = {"Content-Type": "application/json"}
    body = json.dumps({"query": QUESTION, "directive": DIRECTIVE}).encode()
    connection.request("POST", "/v0/resolve_analytics_context", body=body, headers=headers)
    served = connection.getresponse()
    served.read()

    assert served.status == 200

    connection.request("POST", "/v0/execute_sql", body=body, headers=headers)
    refused = connection.getresponse()
    refused_payload = json.loads(refused.read())

    assert refused.status == 404
    assert refused_payload["error"]["code"] == "unknown_route"

    connection.request("POST", "/v0/resolve_analytics_context", body=body, headers=headers)
    answered = connection.getresponse()
    answered_payload = json.loads(answered.read())
    connection.close()

    assert answered.status == 200
    assert answered_payload == governed_bundle().to_dict()
    assert refused.getheader("Connection") == "close"


def test_a_chunked_body_is_refused_and_does_not_corrupt_the_next_call(base_url, resolved):
    """`Content-Length` absent made `int(None or 0)` zero and `read(0)` return
    nothing, so the body was declared consumed while the whole chunked body
    sat in the socket -- the keep-alive corruption on a path the rule was
    supposed to cover (hy-3ko). `http.client` frames chunked itself, so the
    request is written by hand."""
    connection = _pooled(base_url)
    connection.putrequest("POST", "/v0/resolve_analytics_context", skip_accept_encoding=True)
    connection.putheader("Content-Type", "application/json")
    connection.putheader("Transfer-Encoding", "chunked")
    connection.endheaders()
    payload = json.dumps({"query": QUESTION, "directive": DIRECTIVE}).encode()
    connection.send(b"%x\r\n%s\r\n0\r\n\r\n" % (len(payload), payload))
    refused = connection.getresponse()
    refused_payload = json.loads(refused.read())

    assert refused.status == 400

    # Deliberately not reconnecting: the corruption this guards shows up only
    # when the next call goes out on the socket the refused body was left in.
    connection.request(
        "POST",
        "/v0/resolve_analytics_context",
        body=payload,
        headers={"Content-Type": "application/json"},
    )
    answered = connection.getresponse()
    answered_payload = json.loads(answered.read())
    connection.close()

    # The next answer first: refusing chunked with the right code on a socket
    # that still corrupts the following call would fix nothing.
    assert answered.status == 200
    assert answered_payload == governed_bundle().to_dict()
    assert refused.getheader("Connection") == "close"
    assert refused_payload["error"]["code"] == "invalid_request"
    assert "Content-Length" in refused_payload["error"]["recovery"]


def test_a_get_carrying_a_body_does_not_corrupt_the_next_call_on_a_pooled_connection(
    base_url, resolved
):
    """`do_GET` reads no body on any path, so a GET carrying one used to answer
    200 and keep a connection with the body still in the socket -- the next call
    on that pooled socket was then parsed as `<body><next request>` and mis-served
    (hy-670: the follow-up POST came back a 501). The success path never reached
    `_fail`, so the close-by-default rule did not cover it. `http.client` frames
    the GET body by hand because it never sends one for GET on its own."""
    connection = _pooled(base_url)
    junk = b'{"junk":true}'
    connection.putrequest("GET", HEALTH_PATH, skip_accept_encoding=True)
    connection.putheader("Content-Length", str(len(junk)))
    connection.endheaders()
    connection.send(junk)
    health = connection.getresponse()
    health.read()

    assert health.status == 200

    # Deliberately not reconnecting by hand: `http.client` honours a close and
    # reconnects, and the corruption this guards shows up only on the socket the
    # GET body was left in.
    body = json.dumps({"query": QUESTION, "directive": DIRECTIVE}).encode()
    connection.request(
        "POST",
        "/v0/resolve_analytics_context",
        body=body,
        headers={"Content-Type": "application/json"},
    )
    answered = connection.getresponse()
    answered_payload = json.loads(answered.read())
    connection.close()

    # The answer first, because that is the damage: a server that still kept the
    # socket would parse the leftover `{"junk":true}` with this POST's request
    # line and never serve the bundle (a 501/400), so asserting the header alone
    # would pass on a server that corrupts nothing. The close header is the
    # mechanism that makes the answer above possible, asserted second.
    assert answered.status == 200
    assert answered_payload == governed_bundle().to_dict()
    assert health.getheader("Connection") == "close"


def test_a_get_with_a_body_to_a_playground_page_also_closes(base_url, monkeypatch):
    """The class is closed at `do_GET`, not at one endpoint: a playground page is
    served by `_respond_html` (assets by `_respond_file`), a different responder
    from `/v0/health`'s `_respond`, so a GET carrying a body there must close too
    (hy-670). The `Connection: close` header is the load-bearing pin for the
    non-`_respond` responders -- `do_GET` closes the socket either way, but only
    the responder edit tells the client."""
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    connection = _pooled(base_url)
    junk = b'{"junk":true}'
    connection.putrequest("GET", "/playground/", skip_accept_encoding=True)
    connection.putheader("Content-Length", str(len(junk)))
    connection.endheaders()
    connection.send(junk)
    page = connection.getresponse()
    page.read()

    assert page.status == 200

    connection.request("GET", HEALTH_PATH)
    served = connection.getresponse()
    served.read()
    connection.close()

    assert served.status == 200
    assert page.getheader("Connection") == "close"


def test_a_bodyless_get_keeps_its_keep_alive_connection(base_url):
    """The converse, and the reason the fix reads the declared length rather than
    closing every GET: `/v0/health` is the container healthcheck hitting a
    bodyless GET on a loop, which is provably clean and must not pay a TCP
    handshake per probe (hy-670). The socket itself is asserted, not just the
    header, because `http.client` reconnects on its own when the server hangs up,
    which would hide a close behind a pass."""
    connection = _pooled(base_url)
    connection.request("GET", HEALTH_PATH)
    first = connection.getresponse()
    first.read()

    assert first.status == 200
    assert first.getheader("Connection") != "close"

    socket_before = connection.sock
    connection.request("GET", HEALTH_PATH)
    second = connection.getresponse()
    second.read()
    socket_after = connection.sock
    connection.close()

    assert socket_after is socket_before
    assert second.status == 200


def test_a_get_with_conflicting_content_length_headers_does_not_corrupt_the_next_call(
    base_url, resolved
):
    """A duplicate, CONFLICTING `Content-Length` is request smuggling:
    `headers.get` reads only the FIRST, so `Content-Length: 0` then
    `Content-Length: 12` reads as a bodyless (clean) GET while 12 bytes wait in
    the socket to be parsed as the next request (hy-670, adversary on #453). The
    length resolver reads `get_all` and treats a conflict as untrusted framing,
    so the GET success path closes instead of keeping a dirty socket."""
    connection = _pooled(base_url)
    junk = b'{"junk":true}'
    connection.putrequest("GET", HEALTH_PATH, skip_accept_encoding=True)
    connection.putheader("Content-Length", "0")
    connection.putheader("Content-Length", str(len(junk)))
    connection.endheaders()
    connection.send(junk)
    health = connection.getresponse()
    health.read()

    assert health.status == 200

    body = json.dumps({"query": QUESTION, "directive": DIRECTIVE}).encode()
    connection.request(
        "POST",
        "/v0/resolve_analytics_context",
        body=body,
        headers={"Content-Type": "application/json"},
    )
    answered = connection.getresponse()
    answered_payload = json.loads(answered.read())
    connection.close()

    # The answer first: a server that read the first `0` and kept the socket would
    # parse the leftover 12 bytes with this POST and never serve the bundle.
    assert answered.status == 200
    assert answered_payload == governed_bundle().to_dict()
    assert health.getheader("Connection") == "close"


def test_a_get_with_a_signed_zero_content_length_closes(base_url, resolved):
    """`int('-0')` is `0`, so a signed-zero `Content-Length` used to read as a
    clean bodyless GET. Strict `1*DIGIT` grammar rejects it as untrusted framing,
    so the connection closes rather than trusting a length `int()` was too
    permissive to reject (hy-670, adversary on #453)."""
    connection = _pooled(base_url)
    connection.putrequest("GET", HEALTH_PATH, skip_accept_encoding=True)
    connection.putheader("Content-Length", "-0")
    connection.endheaders()
    health = connection.getresponse()
    health.read()

    assert health.status == 200
    assert health.getheader("Connection") == "close"

    # The socket was dropped rather than trusted, so a reconnecting call is served.
    connection.request("GET", HEALTH_PATH)
    served = connection.getresponse()
    served.read()
    connection.close()

    assert served.status == 200


def test_a_post_with_conflicting_content_length_headers_is_refused_without_desync(
    base_url, resolved
):
    """The SAME framing rule guards the body READ, not just the clean check: on
    the pre-existing `_read_body` path a conflicting duplicate `Content-Length`
    read the FIRST value's bytes (`0`), marked the body consumed, and left the
    rest to desync the next call (hy-670, addendum). It is now a 400 that closes,
    routed through the one length resolver so the read and the clean check cannot
    disagree."""
    connection = _pooled(base_url)
    body = json.dumps({"query": QUESTION, "directive": DIRECTIVE}).encode()
    connection.putrequest("POST", "/v0/resolve_analytics_context", skip_accept_encoding=True)
    connection.putheader("Content-Type", "application/json")
    connection.putheader("Content-Length", "0")
    connection.putheader("Content-Length", str(len(body)))
    connection.endheaders()
    connection.send(body)
    refused = connection.getresponse()
    refused_payload = json.loads(refused.read())

    assert refused.status == 400
    assert refused_payload["error"]["code"] == "invalid_request"

    # Not reconnecting by hand: the corruption shows up only on the socket the
    # unread bytes were left in.
    connection.request(
        "POST",
        "/v0/resolve_analytics_context",
        body=body,
        headers={"Content-Type": "application/json"},
    )
    answered = connection.getresponse()
    answered_payload = json.loads(answered.read())
    connection.close()

    assert answered.status == 200
    assert answered_payload == governed_bundle().to_dict()
    assert refused.getheader("Connection") == "close"


def test_a_get_with_a_control_char_padded_content_length_closes(base_url, resolved):
    """Vertical tab and form feed are NOT HTTP OWS -- OWS is SP and HTAB only (RFC
    7230 3.2.3) -- so a `Content-Length: \\x0b0` must NOT strip to a clean `0`.
    `str.strip()` with no argument removes `\\x0b`, which read a body-carrying GET
    as bodyless/clean and kept the socket (hy-670, adversary on #453). Stripping
    only OWS leaves `\\x0b0`, which fails `1*DIGIT` and is untrusted framing, so the
    GET success path closes. Sent over a raw socket because `http.client` will not
    write a control char into a header value."""
    host, port = base_url.removeprefix("http://").split(":")
    sock = socket.create_connection((host, int(port)), timeout=10)
    try:
        sock.sendall(
            b"GET /v0/health HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Length: \x0b0\r\n"
            b"\r\n"
            b'{"junk":true}'
        )
        received = b""
        while b"\r\n\r\n" not in received:
            chunk = sock.recv(4096)
            if not chunk:
                break
            received += chunk
    finally:
        sock.close()

    head = received.split(b"\r\n\r\n", 1)[0]
    # 200 (the healthcheck still answers) but closed: the leftover body is dropped
    # with the socket rather than left to be read as the next request.
    assert head.startswith(b"HTTP/1.1 200"), received[:200]
    assert b"Connection: close" in head, head


def test_a_post_with_a_control_char_padded_content_length_is_refused(base_url, resolved):
    """The same OWS rule on the `_read_body`/`_fail` path: `Content-Length: \\x0b0`
    read as `0` marked an empty body consumed and kept the socket, leaving the
    real body to desync the next call (hy-670). It is now a 400 that closes."""
    host, port = base_url.removeprefix("http://").split(":")
    sock = socket.create_connection((host, int(port)), timeout=10)
    try:
        sock.sendall(
            b"POST /v0/resolve_analytics_context HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: \x0b0\r\n"
            b"\r\n"
            b'{"junk":true}'
        )
        received = b""
        while b"\r\n\r\n" not in received:
            chunk = sock.recv(4096)
            if not chunk:
                break
            received += chunk
    finally:
        sock.close()

    head = received.split(b"\r\n\r\n", 1)[0]
    assert head.startswith(b"HTTP/1.1 400"), received[:200]
    assert b"Connection: close" in head, head


def test_a_refusal_after_a_clean_body_read_keeps_the_connection(base_url, resolved):
    """The converse, and the reason `_fail` consults `_body_consumed` instead
    of closing on everything: a refused parameter is designed behaviour on a
    socket that is provably clean, and an agent probing a limit must not pay a
    TCP handshake per probe."""
    connection = _pooled(base_url)
    headers = {"Content-Type": "application/json"}
    connection.request("POST", "/v0/resolve_analytics_context", body=b"{not json", headers=headers)
    refused = connection.getresponse()
    refused_payload = json.loads(refused.read())

    assert refused.status == 400
    assert refused_payload["error"]["code"] == "invalid_json"

    # The socket itself, not just the header: `http.client` reconnects on its
    # own when the server hangs up, which would hide a close behind a pass.
    socket_before = connection.sock
    body = json.dumps({"query": QUESTION, "directive": DIRECTIVE}).encode()
    connection.request("POST", "/v0/resolve_analytics_context", body=body, headers=headers)
    answered = connection.getresponse()
    answered_payload = json.loads(answered.read())
    socket_after = connection.sock
    connection.close()

    assert socket_after is socket_before
    assert answered.status == 200
    assert answered_payload == governed_bundle().to_dict()
    assert refused.getheader("Connection") != "close"


def test_probing_the_catalog_cap_does_not_cost_a_connection(base_url, listed):
    """The concrete caller behind the rule above, and the one case that ties
    this change to hy-2d2: `list_context_catalog` refuses a `limit` past the
    cap rather than clamping it, so a caller served a page can tell. Finding
    the cap means probing it, and a probe must not cost a TCP handshake."""
    connection = _pooled(base_url)
    headers = {"Content-Type": "application/json"}
    over_cap = json.dumps({"limit": CATALOG_MAX_LIMIT + 1}).encode()
    connection.request("POST", "/v0/list_context_catalog", body=over_cap, headers=headers)
    refused = connection.getresponse()
    refused_payload = json.loads(refused.read())

    assert refused.status == 400
    assert refused_payload["error"]["code"] == "invalid_params"

    socket_before = connection.sock
    within_cap = json.dumps({"limit": CATALOG_MAX_LIMIT}).encode()
    connection.request("POST", "/v0/list_context_catalog", body=within_cap, headers=headers)
    answered = connection.getresponse()
    answered_payload = json.loads(answered.read())
    socket_after = connection.sock
    connection.close()

    assert socket_after is socket_before
    assert answered.status == 200
    assert answered_payload == catalog().to_dict()
    assert refused.getheader("Connection") != "close"


def test_a_500_keeps_the_connection_because_the_socket_is_clean(
    base_url, broken_resolver, monkeypatch
):
    """The least obvious opt-in, and the one most likely to be reverted by a
    reader who takes 500 as severity. What is unhealthy here is the database
    session, not the socket -- and the response's own recovery text says
    retry, which would be advice to pay a handshake for."""
    connection = _pooled(base_url)
    headers = {"Content-Type": "application/json"}
    body = json.dumps({"query": QUESTION, "directive": DIRECTIVE}).encode()
    connection.request("POST", "/v0/resolve_analytics_context", body=body, headers=headers)
    failed = connection.getresponse()
    failed_payload = json.loads(failed.read())

    assert failed.status == 500
    assert failed_payload["error"]["code"] == "internal_error"

    socket_before = connection.sock
    monkeypatch.setattr(
        operations, "resolve_analytics_context", lambda **_kwargs: governed_bundle()
    )
    connection.request("POST", "/v0/resolve_analytics_context", body=body, headers=headers)
    retried = connection.getresponse()
    retried_payload = json.loads(retried.read())
    socket_after = connection.sock
    connection.close()

    assert socket_after is socket_before
    assert retried.status == 200
    assert retried_payload == governed_bundle().to_dict()
    assert failed.getheader("Connection") != "close"


def test_health_reports_what_this_server_serves(base_url):
    """The version is typed out here rather than imported from
    `hyperset.bundle` (hy-ndzz). Compared to the constant it was compiled
    from, this line passed at any number at all.
    `tests/unit/bundle/test_schema.py`'s
    `test_a_bundle_carries_the_version_this_test_types_by_hand` carries the
    argument and the limit; both hold for this surface too."""
    with urllib.request.urlopen(f"{base_url}/v0/health") as response:
        payload = json.loads(response.read())

    assert payload == {
        "status": "ok",
        "schema_version": 26,
        "operations": [
            "list_context_catalog",
            "discover_analytics_context",
            "resolve_analytics_context",
            "validate_analytics_plan",
            "expand_analytics_context",
            "search_knowledge",
            "record_answer_feedback",
            "lookup_answer_feedback",
            "list_review_tasks",
            "get_review_task",
            "edit_review_draft",
            "refine_review_draft",
            "propose_review_to_git",
            "set_review_assignee",
        ],
    }


def test_a_failure_the_server_could_not_answer_is_a_500_with_recovery(base_url, broken_resolver):
    """Not a dropped connection: a client that gets RemoteDisconnected has
    no status, no body, and nothing to act on."""
    status, payload = _post(
        base_url, "/v0/resolve_analytics_context", {"query": QUESTION, "directive": DIRECTIVE}
    )

    assert status == 500
    assert payload["error"]["code"] == "internal_error"
    assert "retry" in payload["error"]["recovery"]
    assert "no route to host" not in payload["error"]["message"]


def test_the_server_keeps_serving_after_one_failed_request(base_url, broken_resolver, monkeypatch):
    """One transient failure must not take the server with it."""
    params = {"query": QUESTION, "directive": DIRECTIVE}
    failed, _ = _post(base_url, "/v0/resolve_analytics_context", params)
    assert failed == 500

    monkeypatch.setattr(
        operations, "resolve_analytics_context", lambda **_kwargs: governed_bundle()
    )
    recovered, payload = _post(base_url, "/v0/resolve_analytics_context", params)

    assert recovered == 200
    assert payload == governed_bundle().to_dict()


def test_a_body_that_is_not_valid_utf8_is_a_400_rather_than_no_answer(base_url):
    """The last hole in hy-r1x's "every failure leaves as an OperationError"
    (hy-z9o).

    `UnicodeDecodeError` is a SIBLING of `json.JSONDecodeError` under
    `ValueError`, not a subclass, so a body that is not valid UTF-8 escaped
    `do_POST` entirely: socketserver printed a traceback, tore the connection
    down, and the client got no status line and no body at all. The bytes below
    are the ones critic measured it with.

    Note what the failure was NOT reported as, which is why the fix cannot be a
    narrower except: `json.loads` sniffs a BOM-like prefix and picks
    `utf-16-le`, so the traceback named a codec nobody chose.
    """
    status, payload = _post(base_url, "/v0/resolve_analytics_context", None, raw=b"\xff\xfe\xfa")

    assert status == 400
    assert payload["error"]["code"] == "invalid_json"
    assert QUESTION in payload["error"]["recovery"]


def test_an_undecodable_body_is_told_about_its_encoding_not_its_json(base_url):
    """One catch, two remedies. A client whose body was not UTF-8 may have
    written perfect JSON, so telling them their JSON is invalid sends them to
    edit a document that is already correct.

    And the sniffed codec stays off the wire: `json.loads` guesses `utf-16-le`
    from a BOM-like prefix, and a caller told that goes chasing an encoding
    neither side chose. The response names the offending byte instead."""
    status, payload = _post(base_url, "/v0/resolve_analytics_context", None, raw=b"\xff\xfe\xfa")
    error = payload["error"]

    assert status == 400
    assert "not valid UTF-8" in error["message"]
    assert "0xfa" in error["message"] and "position 2" in error["message"]
    assert "UTF-8" in error["recovery"]
    assert "utf-16" not in json.dumps(payload).lower()
    assert "not valid JSON" not in error["message"]


def test_a_body_that_is_valid_utf8_and_bad_json_still_says_json(base_url):
    """The other half of the branch: a syntactically broken document is a JSON
    problem and must not be reported as an encoding one."""
    status, payload = _post(base_url, "/v0/resolve_analytics_context", None, raw=b"{not json")
    error = payload["error"]

    assert status == 400
    assert "not valid JSON" in error["message"]
    assert "UTF-8" not in error["message"]


def test_an_undecodable_body_was_read_so_the_connection_survives(base_url, resolved):
    """The body WAS consumed, so keep-alive is correct here and the next
    request on the same socket must be answered normally rather than reading
    this one's leftovers (hy-3ko's rule, applied to the new path)."""
    connection = _pooled(base_url)
    try:
        connection.request(
            "POST",
            "/v0/resolve_analytics_context",
            body=b"\xff\xfe\xfa",
            headers={"Content-Type": "application/json"},
        )
        first = connection.getresponse()
        assert first.status == 400
        assert json.loads(first.read())["error"]["code"] == "invalid_json"

        connection.request(
            "POST",
            "/v0/resolve_analytics_context",
            body=json.dumps({"query": QUESTION, "directive": DIRECTIVE}).encode(),
            headers={"Content-Type": "application/json"},
        )
        second = connection.getresponse()

        assert second.status == 200
        assert json.loads(second.read()) == governed_bundle().to_dict()
        assert len(resolved) == 1
    finally:
        connection.close()


def test_the_handler_carries_a_finite_socket_timeout():
    """The shipped value, which is the half no other test can reach.

    A live test that patches `timeout` down to keep the suite fast supplies the
    fix it claims to check: `timeout` is an attribute `BaseHTTPRequestHandler`
    already has, so the patched run passes against the unfixed handler too.
    That version of this test was written, measured against the base commit,
    found to pass there, and deleted.

    `StreamRequestHandler.setup` applies `timeout` with
    `connection.settimeout`, so this one attribute is what bounds every
    blocking read on the connection.
    """
    assert isinstance(_Handler.timeout, (int, float))
    assert 0 < _Handler.timeout <= 60


def test_the_408_names_what_was_declared_and_what_arrived(base_url, monkeypatch):
    """The response itself, asserted off a stubbed read rather than a clock.

    The live test above proves the connection ends; this proves what the client
    is told when it ends as a 408, without depending on which phase the timeout
    landed in. Sending a body header and then raising `TimeoutError` from the
    read is exactly what a silent client produces one layer down.
    """
    real_read = _Handler._read_body

    def _read_that_times_out(self):
        self.rfile = _TimingOutReader()
        return real_read(self)

    monkeypatch.setattr(_Handler, "_read_body", _read_that_times_out)

    status, payload = _post(
        base_url, "/v0/resolve_analytics_context", None, raw=b"", headers={"Content-Length": "100"}
    )

    assert status == 408
    assert payload["error"]["code"] == "invalid_request"
    assert "0 of 100 declared bytes were received" in payload["error"]["message"]
    assert "no request body arrived" in payload["error"]["message"]
    assert "Content-Length" in payload["error"]["recovery"]


class _TimingOutReader:
    """A body the client promised and never sent.

    Both `read` and `read1`, because the handler moved from one to the other
    when the deadline arrived (hy-6zsk) and a stub that answers only the old
    call would fail this test for a reason that is not the defect.

    `close` is real because the handler's teardown calls it, and a stub missing
    it fails the request for the wrong reason -- which is how the first draft of
    this test "went red" against the unfixed handler.
    """

    def read(self, _length):
        raise TimeoutError("timed out")

    def read1(self, _length):
        raise TimeoutError("timed out")

    def close(self):
        return None


def test_a_body_that_arrives_too_slowly_is_refused_on_a_total_deadline(base_url, monkeypatch):
    """The half a socket timeout cannot reach (hy-6zsk).

    A socket timeout is per blocking operation, so every byte that arrives
    restarts it. This client never goes silent -- it dribbles -- and against the
    silence bound alone it holds the handler thread for as long as it likes:
    measured at 1 MiB and one byte per 29s, a single connection could hold one
    thread for 352 days before `MAX_BODY_BYTES` was reached.

    The read is stubbed rather than actually paced, so this asserts the rule and
    not the clock.
    """
    monkeypatch.setattr(_Handler, "timeout", 0.3)
    monkeypatch.setattr(_Handler, "_read_body", _read_from(_DribblingReader()))

    status, payload = _post(
        base_url, "/v0/resolve_analytics_context", None, raw=b"", headers={"Content-Length": "5000"}
    )

    assert status == 408
    assert payload["error"]["code"] == "invalid_request"
    assert "took longer than 0.3s" in payload["error"]["message"]
    assert "of 5000 declared bytes were received" in payload["error"]["message"]
    assert "without pausing" in payload["error"]["recovery"]


def test_the_two_408s_tell_a_silent_client_from_a_slow_one(base_url, monkeypatch):
    """Different failures, different remedies, so different sentences. A silent
    client must send what it promised; a dribbling one must stop pausing."""
    monkeypatch.setattr(_Handler, "timeout", 0.3)

    monkeypatch.setattr(_Handler, "_read_body", _read_from(_TimingOutReader()))
    _, silent = _post(
        base_url, "/v0/resolve_analytics_context", None, raw=b"", headers={"Content-Length": "5000"}
    )
    monkeypatch.setattr(_Handler, "_read_body", _read_from(_DribblingReader()))
    _, slow = _post(
        base_url, "/v0/resolve_analytics_context", None, raw=b"", headers={"Content-Length": "5000"}
    )

    assert "no request body arrived" in silent["error"]["message"]
    assert "took longer than" in slow["error"]["message"]
    assert silent["error"]["recovery"] != slow["error"]["recovery"]


def test_a_body_split_across_reads_is_still_a_consumed_body(base_url, resolved, monkeypatch):
    """`_body_consumed` must stay `len(body) == length` EXACTLY across the read
    loop, and this is where that is checked rather than inferred: every
    historical bug in `_read_body` lives on this line -- hy-3ko's short read,
    hy-9iz's unread body corrupting the next pooled request.

    The read chunk is shrunk to force the loop to iterate. Two TCP writes are
    NOT enough on their own -- measured: they coalesce into one segment and one
    `read1` returns the whole body, so a mutation that stopped the loop after
    its first read still passed. A test that cannot see the bug it names is the
    thing this file has now removed twice.

    The connection is then reused, because a wrong `_body_consumed` shows up as
    the second request reading the first one's leftovers or as a closed socket.
    """
    monkeypatch.setattr(http_module, "READ_CHUNK_BYTES", 8)
    body = json.dumps({"query": QUESTION, "directive": DIRECTIVE}).encode()
    connection = _pooled(base_url)
    try:
        connection.putrequest("POST", "/v0/resolve_analytics_context")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(len(body)))
        connection.endheaders()
        connection.send(body[:10])
        connection.send(body[10:])
        first = connection.getresponse()

        assert first.status == 200
        assert json.loads(first.read()) == governed_bundle().to_dict()

        connection.request(
            "POST",
            "/v0/resolve_analytics_context",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        second = connection.getresponse()

        assert second.status == 200
        assert json.loads(second.read()) == governed_bundle().to_dict()
        assert len(resolved) == 2
    finally:
        connection.close()


def test_a_deadline_refusal_closes_the_connection(base_url, monkeypatch):
    """Never answer without closing when the body is incomplete. The declared
    bytes are still owed, so anything still in flight would be read as the start
    of the next request (hy-9iz)."""
    monkeypatch.setattr(_Handler, "timeout", 0.3)
    monkeypatch.setattr(_Handler, "_read_body", _read_from(_DribblingReader()))
    connection = _pooled(base_url)
    try:
        connection.request(
            "POST",
            "/v0/resolve_analytics_context",
            body=b"",
            headers={"Content-Type": "application/json", "Content-Length": "5000"},
        )
        response = connection.getresponse()

        assert response.status == 408
        assert response.getheader("Connection") == "close"
    finally:
        connection.close()


# Captured at import, ONCE. Reading `_Handler._read_body` inside the factory
# picks up whatever a previous `monkeypatch.setattr` installed, so a test that
# stubs two readers in turn ends up wrapping the first stub with the second and
# running the first -- which is how the two-408 test first "proved" that a
# dribbling client gets the silence message.
_REAL_READ_BODY = _Handler._read_body


def _read_from(reader):
    """Run the real `_read_body` against a stubbed socket reader."""

    def _read_body(self):
        self.rfile = reader
        return _REAL_READ_BODY(self)

    return _read_body


class _DribblingReader:
    """A client that never goes silent and never finishes.

    One byte per read, with a pause short enough that no socket timeout fires
    and long enough that a total deadline does. This is the shape a socket
    timeout cannot see.
    """

    def read1(self, _length):
        time.sleep(0.02)
        return b" "

    read = read1

    def close(self):
        return None


def test_a_short_body_that_ends_in_eof_is_not_a_consumed_body(base_url, resolved):
    """`_body_consumed` is `received == length` and nothing looser, asserted on
    the socket rather than inferred from a passing request.

    This is the invariant every historical bug in `_read_body` broke -- hy-3ko's
    short read, hy-9iz's unread body parsed as the next request -- and until now
    nothing tested it directly: replacing the line with `_body_consumed = True`
    passed the entire transport suite. It no longer does.

    A half-close is used rather than a pause, so the case is EOF and not a
    clock: the client declares 100 bytes, sends 2, and shuts down its writing
    end. The server must answer AND close, because the 98 bytes it was promised
    are gone and a kept connection would read the next request from a stream
    that never finished this one.
    """
    host, port = base_url.removeprefix("http://").split(":")
    sock = socket.create_connection((host, int(port)), timeout=10)
    try:
        sock.sendall(
            b"POST /v0/resolve_analytics_context HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 100\r\n"
            b"\r\n"
            b"{}"
        )
        sock.shutdown(socket.SHUT_WR)
        received = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            received += chunk
    finally:
        sock.close()

    head = received.split(b"\r\n\r\n", 1)[0]
    assert head.startswith(b"HTTP/1.1 4"), received[:200]
    assert b"Connection: close" in head, head


def _get_raw(base_url, path):
    try:
        with urllib.request.urlopen(f"{base_url}{path}") as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_review_whoami_is_gated_behind_the_playground(base_url):
    """The caller's-own-identity read exists only when the playground is on (hy-q7pth):
    it is the reviewer UI's identity source, so an operator API with the playground off
    does not answer it."""
    status, _payload = _get_raw(base_url, "/v0/review/whoami")
    assert status == 404


def test_review_whoami_is_null_when_authz_is_off(base_url, monkeypatch):
    """With the authz gate off there is NO verified principal, so whoami is `null` --
    NOT the shared 'anonymous' id (hy-q7pth). The UI reads this null to DISABLE the
    'assigned to me' filter rather than treating every anonymous-owned task as mine."""
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.delenv("HYPERSET_AUTHZ_ENABLED", raising=False)
    status, payload = _get(base_url, "/v0/review/whoami")
    assert status == 200
    assert payload == {"identity": None}


def test_review_whoami_returns_the_verified_principals_opaque_identity(base_url, monkeypatch):
    """With authz on, whoami is the VERIFIED principal's opaque `subject@issuer` -- the
    same identity the server self-computes for a self-claim, read fresh (hy-q7pth). This
    is the sole authority the UI trusts for 'assigned to me', so a stale/edited client
    value can never forge ownership."""
    from hyperset.security.authz import Principal
    from hyperset.transport import http as http_module

    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    reviewer = Principal("subj", "https://issuer.example/", roles=("reviewer",))
    monkeypatch.setattr(
        http_module,
        "principal_from_bearer",
        lambda header: reviewer if header else None,
    )
    status, payload = _get(base_url, "/v0/review/whoami", headers={"Authorization": "Bearer t"})
    assert status == 200
    assert payload == {"identity": "subj@https://issuer.example/"}


def test_setting_the_writeback_target_is_refused_on_the_public_surface(base_url, monkeypatch):
    """The reviewer workflow is public, but re-pointing the write-back TARGET is
    a SETTINGS write and must not be reachable on the public surface (hy-529x):
    a public user must not re-point the customer's context repo.

    The gate is by SURFACE, so it is proven by surface: the SAME request routed
    through the public `/playground/api` prefix is not a route (404), while
    through the admin `/admin/api` prefix it reaches the handler,
    which then rejects the empty body for missing fields (400) -- past the
    surface gate, before any write. This is the Overseer's "not on the public
    surface" minimum; the admin surface is still unauthenticated (real admin
    auth is the follow-on hy-2nqb), so this is a surface gate, not a credential.
    """
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")

    public_status, public_payload = _post(
        base_url, "/playground/api/v0/review/writeback-config", {}
    )
    assert public_status == 404
    assert "/v0/resolve_analytics_context" in public_payload["error"]["recovery"]

    admin_status, admin_payload = _post(base_url, "/admin/api/v0/review/writeback-config", {})
    assert admin_status == 400
    assert admin_payload["error"]["code"] == "invalid_request"
    assert "repository" in admin_payload["error"]["message"]


def _get(base_url, path, *, headers=None):
    request = urllib.request.Request(f"{base_url}{path}", headers=headers or {})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _get_with_headers(base_url, path):
    """GET returning (status, body, response headers) -- for asserting response headers like
    X-Correlation-Id (hy-w9ntg)."""
    request = urllib.request.Request(f"{base_url}{path}")
    with urllib.request.urlopen(request) as response:
        return response.status, json.loads(response.read()), dict(response.headers)


def _history_spy(monkeypatch) -> list[dict]:
    """Replace the governed-history read with a recorder, so a test can tell whether
    the route REACHED it (served) or was denied before it (gated)."""
    calls: list[dict] = []

    def _spy(**kwargs):
        calls.append(kwargs)
        return {"entries": []}

    monkeypatch.setattr(http_module, "get_context_history", _spy)
    return calls


def test_context_history_is_served_unchanged_when_the_gate_is_off(base_url, monkeypatch):
    # Default (flag unset): the governed read is reached and answered as today.
    calls = _history_spy(monkeypatch)
    status, _ = _get(base_url, "/v0/context/history?repository=r&ref=f&path=p")
    assert status == 200
    assert calls and calls[0]["repository"] == "r"


def test_context_history_requires_authorization_when_the_gate_is_on(base_url, monkeypatch):
    # A served governed read outside `run_operation` is gated too: enabled + no token
    # is the uniform `unauthorized` denial, and the read is NEVER reached -- so this
    # route is not an existence oracle (an unknown vs a known ref would both 400 here).
    calls = _history_spy(monkeypatch)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    status, payload = _get(base_url, "/v0/context/history?repository=r&ref=f&path=p")
    assert status == 400
    assert payload["error"]["code"] == "unauthorized"
    assert calls == []


# --- authz bearer threading (hy-lrho, ADR-0030) ---


def test_a_resolve_with_a_verified_bearer_is_served_when_enabled(base_url, resolved, monkeypatch):
    # The HTTP handler threads the request's verified principal into run_operation.
    # Stub the verifier to a reader (its own matrix is in test_oidc.py) and assert the
    # bundle is served -- proving the header reaches the gate as a reader.
    reader = Principal(subject="u", issuer="https://issuer.example/", roles=("reader",))
    monkeypatch.setattr(http_module, "principal_from_bearer", lambda h: reader if h else None)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    status, payload = _post(
        base_url,
        "/v0/resolve_analytics_context",
        {"query": QUESTION, "directive": DIRECTIVE},
        headers={"Authorization": "Bearer good.token"},
    )
    assert status == 200
    assert payload == governed_bundle().to_dict()


def test_a_resolve_without_a_bearer_is_denied_when_enabled(base_url, resolved, monkeypatch):
    monkeypatch.setattr(http_module, "principal_from_bearer", lambda h: None)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    status, payload = _post(
        base_url, "/v0/resolve_analytics_context", {"query": QUESTION, "directive": DIRECTIVE}
    )
    assert status == 400
    assert payload["error"]["code"] == "unauthorized"
    # Deny-the-whole: the resolver never ran.
    assert resolved == []


def test_the_operator_writeback_config_get_is_gated_when_enabled(base_url, monkeypatch):
    # An operator READ behind the playground is gated too; enabled + no bearer denies
    # BEFORE the config repository is touched (fail-closed, no existence signal).
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    monkeypatch.setattr(http_module, "principal_from_bearer", lambda h: None)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    status, payload = _get(base_url, "/v0/review/writeback-config")
    assert status == 400
    assert payload["error"]["code"] == "unauthorized"


# --- admin readiness overview (hy-gh-75) ---


def _readiness_spy(monkeypatch):
    """Spy the readiness aggregator so a test can assert it is (not) reached."""
    calls: list = []

    def _spy(session_factory, **kwargs):
        calls.append(kwargs)
        return {"generated_at": "2026-08-18T00:00:00+00:00", "overall": "ready", "components": []}

    monkeypatch.setattr(http_module, "admin_readiness", _spy)
    return calls


def test_admin_readiness_is_served_on_the_admin_surface_when_authz_off(base_url, monkeypatch):
    # Playground on, gate off (default): the admin readiness overview is answered, and it
    # is the real aggregator output wrapped with the schema version.
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    status, payload = _get(base_url, "/admin/api/v0/readiness")
    assert status == 200
    assert payload["schema_version"]
    assert payload["overall"] in {"ready", "degraded", "blocked", "unknown"}
    assert isinstance(payload["components"], list) and len(payload["components"]) == 10
    assert {c["component"] for c in payload["components"]} == {
        "api",
        "database",
        "superset",
        "datahub",
        "git_context",
        "model",
        "embeddings",
        "analytics_db",
        "writeback",
        "notifications",
    }


def test_admin_readiness_is_not_on_the_public_playground_surface(base_url, monkeypatch):
    # It is an ADMIN surface, not a user one: the same path under the public playground api
    # prefix is not a route (surface-only), proven by surface exactly like writeback-config.
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    status, _payload = _get_raw(base_url, "/playground/api/v0/readiness")
    assert status == 404


def test_admin_readiness_is_gated_behind_the_playground(base_url):
    # Playground off (default in this call): the admin surface is not served at all.
    status, _payload = _get_raw(base_url, "/admin/api/v0/readiness")
    assert status == 404


def test_admin_readiness_requires_authorization_when_the_gate_is_on(base_url, monkeypatch):
    # Gate on + no bearer -> the uniform `unauthorized` denial, and the aggregator is NEVER
    # reached (no DB read, no infra topology leaked to an unauthenticated caller).
    calls = _readiness_spy(monkeypatch)
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    monkeypatch.setattr(http_module, "principal_from_bearer", lambda h: None)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    status, payload = _get(base_url, "/admin/api/v0/readiness")
    assert status == 400
    assert payload["error"]["code"] == "unauthorized"
    assert calls == []


def test_admin_readiness_is_served_to_a_verified_principal_when_enabled(base_url, monkeypatch):
    # Gate on + a verified reader -> served: the header reaches the gate and the aggregator
    # runs. (The verifier's own matrix is in test_oidc.py; here it is stubbed.)
    calls = _readiness_spy(monkeypatch)
    reader = Principal(subject="u", issuer="https://issuer.example/", roles=("reader",))
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    monkeypatch.setattr(http_module, "principal_from_bearer", lambda h: reader if h else None)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    status, payload = _get(
        base_url, "/admin/api/v0/readiness", headers={"Authorization": "Bearer t"}
    )
    assert status == 200
    assert payload["overall"] == "ready"
    assert len(calls) == 1


def test_the_writeback_config_write_path_enforces_admin_authorization(base_url, monkeypatch):
    # hy-2nqb: setting the write-back target is an admin deployment-config write. With the
    # authz gate ON (a non-loopback posture), an unauthenticated caller and an
    # insufficient role are denied SERVER-SIDE on the write path itself -- not merely by
    # the /admin surface split or the UI hiding the form. The denial short-circuits before
    # the store, so a fake session_factory is never reached; the admin-ALLOWED decision is
    # covered by the unit test of admin_config_authorization_error (no real DB here).
    from hyperset.security.authz import Principal
    from hyperset.transport import http as http_module

    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    body = {
        "repository": "/tmp/repo",
        "base_ref": "main",
        "manifest_path": "domains/x",
        "token_source": "env_ref",
    }

    monkeypatch.setattr(http_module, "principal_from_bearer", lambda header: None)
    status, payload = _post(base_url, "/admin/api/v0/review/writeback-config", body)
    assert status == 400 and payload["error"]["code"] == "unauthorized"

    # An insufficient role (a reviewer can author proposals but not CONFIGURE the target).
    monkeypatch.setattr(
        http_module,
        "principal_from_bearer",
        lambda header: Principal("u", "i", roles=("reviewer",)),
    )
    status, payload = _post(base_url, "/admin/api/v0/review/writeback-config", body)
    assert status == 400 and payload["error"]["code"] == "unauthorized"


def test_the_writeback_targets_list_read_requires_admin_configure(base_url, monkeypatch):
    # hq-1aap round 2 (adversary): the write-back-TARGET list exposes deployment
    # config -- target repositories and secret REFERENCES (env var names, App ids)
    # -- so it is gated by the ADMIN CONFIGURE authz, the SAME boundary as the
    # target mutations, NOT the generic governed-READ gate. A verified READ-only
    # principal PASSES the read gate but must be DENIED here; the denial
    # short-circuits before the store, so no target is ever listed to a reader.
    from hyperset.security.authz import Principal
    from hyperset.transport import http as http_module

    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")

    # No bearer -> denied.
    monkeypatch.setattr(http_module, "principal_from_bearer", lambda header: None)
    status, payload = _get(base_url, "/admin/api/v0/review/writeback-targets")
    assert status == 400 and payload["error"]["code"] == "unauthorized"

    # A verified READ-only principal (a reader reads governed context but may not
    # CONFIGURE) -> still DENIED, which is exactly what the generic read gate would
    # have wrongly allowed.
    monkeypatch.setattr(
        http_module,
        "principal_from_bearer",
        lambda header: Principal("u", "i", roles=("reader",)),
    )
    status, payload = _get(
        base_url,
        "/admin/api/v0/review/writeback-targets",
        headers={"Authorization": "Bearer t"},
    )
    assert status == 400 and payload["error"]["code"] == "unauthorized"


def test_the_reconcile_sweep_requires_admin_configure(base_url, monkeypatch):
    # hq-3ta2: the reconcile SWEEP reads PR state and re-syncs governed context --
    # a deployment action -- so it is gated by the ADMIN CONFIGURE authz, the SAME
    # boundary as the single reconcile and the writeback mutations, NOT a general
    # read. With the gate ON, an unauthenticated caller AND a verified READ-only
    # principal are DENIED server-side, and the denial short-circuits before any
    # task is loaded or reconciled (no store reached).
    from hyperset.security.authz import Principal
    from hyperset.transport import http as http_module

    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")

    # No bearer -> denied.
    monkeypatch.setattr(http_module, "principal_from_bearer", lambda header: None)
    status, payload = _post(base_url, "/admin/api/v0/review/tasks/reconcile-sweep", {"limit": 5})
    assert status == 400 and payload["error"]["code"] == "unauthorized"

    # A verified READ-only principal passes a read gate but must be DENIED here.
    monkeypatch.setattr(
        http_module,
        "principal_from_bearer",
        lambda header: Principal("u", "i", roles=("reader",)),
    )
    status, payload = _post(
        base_url,
        "/admin/api/v0/review/tasks/reconcile-sweep",
        {"limit": 5},
        headers={"Authorization": "Bearer t"},
    )
    assert status == 400 and payload["error"]["code"] == "unauthorized"


# --- hy-mg8p: approved-reviewer gate on the /review SURFACE (the page itself) ---


def _reviewer_bearer(monkeypatch, roles):
    from hyperset.security.authz import Principal
    from hyperset.transport import http as http_module

    monkeypatch.setattr(
        http_module, "principal_from_bearer", lambda header: Principal("u", "i", roles=roles)
    )


def test_the_review_page_redirects_an_unauthenticated_caller_to_login(base_url, monkeypatch):
    # hy-mg8p part 1: with the authz gate ON, opening the /review surface requires an
    # approved reviewer. An unauthenticated browser is sent to /login (deep-linked back),
    # never served the SPA shell -- so the page cannot be opened before identity is proven.
    from hyperset.transport import http as http_module

    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    monkeypatch.setattr(http_module, "principal_from_bearer", lambda header: None)

    for path in ("/review", "/review/", "/review/anything"):
        result = _raw_get(base_url, path)
        assert result.status == 303, path
        assert result.location.startswith("/login?return="), result.location
        assert "review" in result.location, result.location
        assert b'id="root"' not in result.body, f"{path}: served the shell before authz"


def test_the_review_page_denies_an_authenticated_non_reviewer(base_url, monkeypatch):
    # Authentication is not enough: a verified READER (no REVIEW grant) is still sent to
    # /login, so the gate requires the review ROLE, not merely a logged-in identity.
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    _reviewer_bearer(monkeypatch, ("reader",))
    result = _raw_get(base_url, "/review", headers={"Authorization": "Bearer t"})
    assert result.status == 303
    assert result.location.startswith("/login?return=")
    assert b'id="root"' not in result.body


def test_the_review_page_serves_a_verified_reviewer(base_url, monkeypatch):
    # An approved reviewer opens the surface: the SPA shell is served, no redirect.
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    _reviewer_bearer(monkeypatch, ("reviewer",))
    result = _raw_get(base_url, "/review", headers={"Authorization": "Bearer t"})
    assert result.status == 200
    assert result.location is None
    assert b'id="root"' in result.body


def test_the_review_page_is_byte_identical_with_the_gate_off(base_url, monkeypatch):
    # The rollout-safety invariant: gate OFF (the default, loopback dev) leaves the review
    # page exactly as before -- served to anyone, no redirect. So an existing dev/demo is
    # not broken by this code being present.
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.delenv("HYPERSET_AUTHZ_ENABLED", raising=False)
    result = _raw_get(base_url, "/review")
    assert result.status == 200
    assert result.location is None
    assert b'id="root"' in result.body


def test_the_review_gate_does_not_gate_other_playground_pages(base_url, monkeypatch):
    # The gate is REVIEW-scoped, not playground-wide: with authz on and no identity, the
    # /playground surface is still served (its own ops carry their own gate). A gate that
    # redirected every playground page would red here.
    from hyperset.transport import http as http_module

    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    monkeypatch.setattr(http_module, "principal_from_bearer", lambda header: None)
    result = _raw_get(base_url, "/playground")
    assert result.status == 200
    assert result.location is None
    assert b'id="root"' in result.body


@pytest.mark.parametrize("op_path", REVIEW_WRITE_OP_PATHS)
def test_the_served_review_write_ops_deny_a_reader_when_authz_is_on(op_path, base_url, monkeypatch):
    # hy-s8a6 + hy-es7z: every review WRITE op (edit/refine/propose/set_review_assignee) is a
    # REVIEW-authorized SERVED operation, so with the gate ON an unauthenticated caller AND a
    # verified READ-only principal are denied on the wire, BEFORE any task is loaded
    # (run_operation gates before dispatch). The gate is AUTHZ, in run_operation on the served
    # path -- not the playground -- which is the coverage the removed bespoke /v0/review/* write
    # adapters carried, migrated onto every served write op.
    from hyperset.security.authz import Principal
    from hyperset.transport import http as http_module

    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")

    monkeypatch.setattr(http_module, "principal_from_bearer", lambda header: None)
    status, payload = _post(base_url, op_path, {"task_id": "rt-x"})
    assert status == 400 and payload["error"]["code"] == "unauthorized"

    monkeypatch.setattr(
        http_module, "principal_from_bearer", lambda header: Principal("u", "i", roles=("reader",))
    )
    status, payload = _post(
        base_url, op_path, {"task_id": "rt-x"}, headers={"Authorization": "Bearer t"}
    )
    assert status == 400 and payload["error"]["code"] == "unauthorized"


def test_a_malformed_allowlist_denies_a_reviewer_on_the_surface_and_the_ops_over_http(
    base_url, monkeypatch, tmp_path
):
    # hy-a607k (#456 adversary): a misconfigured approved-reviewer allowlist fails CLOSED
    # over HTTP -- a verified reviewer is redirected off /review AND denied the review ops,
    # rather than the policy silently reverting to role-only or 500ing.
    from hyperset.security.authz import Principal
    from hyperset.transport import http as http_module

    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    policy = tmp_path / "policy.allow"
    # A credential-shaped (colon-bearing) entry with a real https issuer -> rejected ->
    # whole policy fails closed.
    policy.write_text(
        "good@https://iss\nuser:supersecret@https://issuer.example\n", encoding="utf-8"
    )
    monkeypatch.setenv("HYPERSET_REVIEWER_ALLOWLIST", str(policy))
    monkeypatch.setattr(
        http_module,
        "principal_from_bearer",
        lambda header: Principal("good", "https://iss", roles=("reviewer",)),
    )

    # The /review page: redirected to /login, never served the shell.
    result = _raw_get(base_url, "/review", headers={"Authorization": "Bearer t"})
    assert result.status == 303 and result.location.startswith("/login?return=")
    assert b'id="root"' not in result.body

    # The served set_review_assignee op (a REVIEW write): unauthorized on the wire, secret
    # never echoed.
    status, payload = _post(
        base_url,
        SET_REVIEW_ASSIGNEE_PATH,
        {"task_id": "rt-x", "assigned": True},
        headers={"Authorization": "Bearer t"},
    )
    assert status == 400 and payload["error"]["code"] == "unauthorized"
    assert "supersecret" not in json.dumps(payload)


# --- admin context-source management (hy-gh-75, second slice) ---


def _git_health(**over):
    base = dict(
        source_id="src-1",
        repository="org/ctx",
        ref="main",
        path="domains",
        display_name="Context",
        enabled=True,
        pinned=True,
        commit_sha="abc123",
        committed_at=None,
        content_hash="h1",
        domain="revenue",
        title="Revenue",
        last_attempt_status="synced",
        last_attempt_at=None,
        last_attempted_commit_sha="abc123",
        last_error=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_context_sources_list_is_served_on_the_admin_surface(base_url, monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    monkeypatch.setattr(http_module, "read_git_context", lambda sf, **k: [_git_health()])
    status, payload = _get(base_url, "/admin/api/v0/context/sources")
    assert status == 200
    assert payload["schema_version"]
    assert len(payload["sources"]) == 1
    src = payload["sources"][0]
    assert src["source_id"] == "src-1" and src["serving_commit"] == "abc123"
    assert src["last_attempt_status"] == "synced"
    # No secret/token field leaks into a source view.
    assert not any("token" in k or "secret" in k or "password" in k for k in src)


def test_context_source_last_error_is_redacted_server_side(base_url, monkeypatch):
    # hq-kbcy round 5: repository/ref are credential-free by construction (the add path
    # rejects a credential-bearing pointer), but `last_error` is free text a git failure
    # can echo a URL into. It is scrubbed SERVER-SIDE with the canonical
    # `scheme://userinfo@` detector (the same machinery as `_pointer_has_credentials`),
    # not a client heuristic: a real credential URL is redacted, while a port + git
    # revision (`HEAD@{1}`, no `@` before the first `/`) and an scp `git@host:path`
    # (no scheme) are PRESERVED.
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    err = (
        "fatal: could not read https://user:ghp_SECRET@github.com/o/r; "
        "also https://u:ghp_QMARK?@evil/r and https://u:ghp_HASH#@evil/r; "
        "tried https://git.corp:8443/team/repo:main HEAD@{1} and git@host:o/r"
    )
    monkeypatch.setattr(
        http_module, "read_git_context", lambda sf, **k: [_git_health(last_error=err)]
    )
    status, payload = _get(base_url, "/admin/api/v0/context/sources")
    assert status == 200
    served = payload["sources"][0]["last_error"]
    assert "ghp_SECRET" not in served  # the credential is stripped
    assert "https://github.com/o/r" in served  # host preserved, userinfo gone
    # A `?` or `#` INSIDE the userinfo must not stop the scan short and leak.
    assert "ghp_QMARK" not in served
    assert "ghp_HASH" not in served
    assert "https://git.corp:8443/team/repo:main HEAD@{1}" in served  # not over-redacted
    assert "git@host:o/r" in served  # scp identity preserved


def test_context_sources_list_is_admin_surface_only(base_url, monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    monkeypatch.setattr(http_module, "read_git_context", lambda sf, **k: [])
    status, _ = _get_raw(base_url, "/playground/api/v0/context/sources")
    assert status == 404


def test_context_sources_list_requires_authorization_when_enabled(base_url, monkeypatch):
    calls = []
    monkeypatch.setattr(http_module, "read_git_context", lambda sf, **k: calls.append(1) or [])
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    monkeypatch.setattr(http_module, "principal_from_bearer", lambda h: None)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    status, payload = _get(base_url, "/admin/api/v0/context/sources")
    assert status == 400 and payload["error"]["code"] == "unauthorized"
    assert calls == []  # the DB read never happened


def test_context_source_add_is_refused_on_the_public_surface(base_url, monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    status, _ = _post(
        base_url, "/playground/api/v0/context/sources", {"repository": "o/r", "path": "p"}
    )
    assert status == 404


def test_context_source_add_requires_repository_and_path(base_url, monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    status, payload = _post(base_url, "/admin/api/v0/context/sources", {"ref": "main"})
    assert status == 400 and payload["error"]["code"] == "invalid_request"
    assert "repository" in payload["error"]["message"]


def test_context_source_add_registers_and_returns_the_source(base_url, monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    _install_audit(monkeypatch)  # the register is now coupled to a mandatory audit append
    recorded = {}

    class _Repo:
        def __init__(self, sf):
            pass

        def register_source(
            self, *, repository, ref, path, display_name, workspace=None, session=None
        ):
            recorded.update(repository=repository, ref=ref, path=path)
            return SimpleNamespace(
                id="src-new",
                repository=repository,
                ref=ref,
                path=path,
                display_name=display_name or "d",
                enabled=True,
                current_snapshot=None,
                last_attempt_status="never_synced",
            )

    monkeypatch.setattr(http_module, "PostgresContextRepository", _Repo)
    status, payload = _post(
        base_url, "/admin/api/v0/context/sources", {"repository": "org/ctx", "path": "domains"}
    )
    assert status == 200
    assert payload["source"]["source_id"] == "src-new"
    assert recorded == {"repository": "org/ctx", "ref": "main", "path": "domains"}  # ref defaults


def test_context_source_add_requires_authorization_when_enabled(base_url, monkeypatch):
    calls = []

    class _Repo:
        def __init__(self, sf):
            pass

        def register_source(self, **kw):
            calls.append(kw)

    monkeypatch.setattr(http_module, "PostgresContextRepository", _Repo)
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    monkeypatch.setattr(http_module, "principal_from_bearer", lambda h: None)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    status, payload = _post(
        base_url, "/admin/api/v0/context/sources", {"repository": "o/r", "path": "p"}
    )
    assert status == 400 and payload["error"]["code"] == "unauthorized"
    assert calls == []  # no write happened before the gate


def test_context_source_sync_validates_and_returns_the_result(base_url, monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    _install_audit(monkeypatch)  # the sync result is now recorded by a mandatory audit append
    seen = {}

    def _sync(*, source_id, session_factory, cache_dir, workspace=None, session=None):
        seen["source_id"] = source_id
        seen["session"] = session  # the handler couples the record+audit in one txn (hy-oq1y4)
        return SimpleNamespace(
            source_id=source_id,
            status="synced",
            commit_sha="deadbeef",
            snapshot_id="snap-1",
            synced_at=None,
            reasons=[],
            findings=[],
            ok=True,
        )

    monkeypatch.setattr(http_module, "sync_git_context", _sync)
    status, payload = _post(base_url, "/admin/api/v0/context/sources/sync", {"source_id": "src-1"})
    assert status == 200
    assert payload["result"]["status"] == "synced" and payload["result"]["ok"] is True
    assert payload["result"]["commit"] == "deadbeef"
    assert seen["source_id"] == "src-1"
    # The record and the audit are coupled: the handler threads ONE session into the sync
    # so a failed audit rolls the snapshot/status back (hy-oq1y4).
    assert seen["session"] is not None


def test_context_source_sync_requires_a_source_id(base_url, monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    status, payload = _post(base_url, "/admin/api/v0/context/sources/sync", {})
    assert status == 400 and payload["error"]["code"] == "invalid_request"


def test_context_source_sync_is_admin_surface_only(base_url, monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    status, _ = _post(base_url, "/playground/api/v0/context/sources/sync", {"source_id": "x"})
    assert status == 404


@pytest.mark.parametrize(
    "path, body",
    [
        (
            "/playground/api/v0/context/sources/compare",
            {"source_id": "x", "base_commit": "a", "target_commit": "b"},
        ),
        ("/playground/api/v0/context/sources/rollback", {"source_id": "x", "commit_sha": "a"}),
    ],
)
def test_context_source_compare_and_rollback_are_admin_surface_only(
    base_url, monkeypatch, path, body
):
    # hy-bo5p: neither compare nor rollback exists on the public playground prefix -- like every
    # other context-source manage route, they are admin-surface-only (refused with a 404 before
    # the CONFIGURE gate ever runs).
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    status, _ = _post(base_url, path, body)
    assert status == 404


# --- hy-rdlo: context-source WRITES require the admin `configure` grant (admin-only) ---


@pytest.mark.parametrize("role", ["reader", "explorer", "reviewer", "git_owner", "service"])
def test_context_source_add_denies_every_non_admin_role(base_url, monkeypatch, role):
    # A context-source add is a deployment-CONFIG write: with the gate on, every role that
    # lacks the `configure` grant is denied SERVER-SIDE, before any store write.
    calls = []

    class _Repo:
        def __init__(self, sf):
            pass

        def register_source(self, **kw):
            calls.append(kw)

    monkeypatch.setattr(http_module, "PostgresContextRepository", _Repo)
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    monkeypatch.setattr(
        http_module, "principal_from_bearer", lambda h: Principal("u", "i", roles=(role,))
    )
    status, payload = _post(
        base_url, "/admin/api/v0/context/sources", {"repository": "o/r", "path": "p"}
    )
    assert status == 400 and payload["error"]["code"] == "unauthorized", role
    assert calls == [], f"{role} reached the store despite denial"


def test_context_source_add_allows_admin(base_url, monkeypatch):
    class _Repo:
        def __init__(self, sf):
            pass

        def register_source(
            self, *, repository, ref, path, display_name, workspace=None, session=None
        ):
            return SimpleNamespace(
                id="src-a",
                repository=repository,
                ref=ref,
                path=path,
                display_name="d",
                enabled=True,
                current_snapshot=None,
                last_attempt_status="never_synced",
            )

    monkeypatch.setattr(http_module, "PostgresContextRepository", _Repo)
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    _install_audit(monkeypatch)  # the register is coupled to a mandatory audit append
    monkeypatch.setattr(
        http_module, "principal_from_bearer", lambda h: Principal("a", "i", roles=("admin",))
    )
    status, payload = _post(
        base_url,
        "/admin/api/v0/context/sources",
        {"repository": "o/r", "path": "p"},
        headers={"Authorization": "Bearer t"},
    )
    assert status == 200 and payload["source"]["source_id"] == "src-a"


@pytest.mark.parametrize("role", ["reader", "explorer", "reviewer", "git_owner", "service"])
def test_context_source_sync_denies_every_non_admin_role(base_url, monkeypatch, role):
    seen = []
    monkeypatch.setattr(http_module, "sync_git_context", lambda **kw: seen.append(kw))
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    monkeypatch.setattr(
        http_module, "principal_from_bearer", lambda h: Principal("u", "i", roles=(role,))
    )
    status, payload = _post(base_url, "/admin/api/v0/context/sources/sync", {"source_id": "s1"})
    assert status == 400 and payload["error"]["code"] == "unauthorized", role
    assert seen == [], f"{role} reached sync despite denial"


def test_context_source_sync_allows_admin(base_url, monkeypatch):
    monkeypatch.setattr(
        http_module,
        "sync_git_context",
        lambda **kw: SimpleNamespace(
            source_id=kw["source_id"],
            status="synced",
            commit_sha="c",
            snapshot_id="s",
            synced_at=None,
            reasons=[],
            findings=[],
            ok=True,
        ),
    )
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    _install_audit(monkeypatch)  # the sync result is recorded by a mandatory audit append
    monkeypatch.setattr(
        http_module, "principal_from_bearer", lambda h: Principal("a", "i", roles=("admin",))
    )
    status, payload = _post(
        base_url,
        "/admin/api/v0/context/sources/sync",
        {"source_id": "s1"},
        headers={"Authorization": "Bearer t"},
    )
    assert status == 200 and payload["result"]["ok"] is True


def test_context_source_sync_reasons_are_redacted_server_side(base_url, monkeypatch):
    # hq-kbcy round 5: a sync/validate `reasons` entry is FREE TEXT -- str(GitReadError)
    # can echo the credential URL the read failed on -- so each is scrubbed server-side
    # like last_error, not left raw for the client to render.
    reason = "fatal: could not read from https://user:ghp_REASONLEAK@github.com/o/r"
    monkeypatch.setattr(
        http_module,
        "sync_git_context",
        lambda **kw: SimpleNamespace(
            source_id=kw["source_id"],
            status="failed",
            commit_sha=None,
            snapshot_id=None,
            synced_at=None,
            reasons=[reason],
            findings=[],
            ok=False,
        ),
    )
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    _install_audit(monkeypatch)
    status, payload = _post(base_url, "/admin/api/v0/context/sources/sync", {"source_id": "s1"})
    assert status == 200
    served = payload["result"]["reasons"][0]
    assert "ghp_REASONLEAK" not in served  # the credential is stripped from the reason
    assert "https://github.com/o/r" in served  # host preserved


def test_context_source_LIST_requires_admin_configure(base_url, monkeypatch):
    # hq-3fjt (reversing the earlier hy-gh-75 "list stays a read"): the GET list exposes
    # deployment config -- the configured target repositories and ref/path pointers -- so it
    # is gated by the ADMIN CONFIGURE authz, the SAME boundary as the source mutations (and
    # consistent with the write-back-targets ruling hq-1aap). A verified READ-only principal
    # PASSES the generic read gate but must be DENIED here; the denial short-circuits before
    # the DB read.
    calls = []
    monkeypatch.setattr(http_module, "read_git_context", lambda sf, **k: calls.append(1) or [])
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    monkeypatch.setattr(
        http_module, "principal_from_bearer", lambda h: Principal("u", "i", roles=("reader",))
    )
    status, payload = _get(
        base_url, "/admin/api/v0/context/sources", headers={"Authorization": "Bearer t"}
    )
    assert status == 400 and payload["error"]["code"] == "unauthorized"
    assert calls == []  # a reader never reaches the DB read

    # An admin IS allowed to list.
    monkeypatch.setattr(
        http_module, "principal_from_bearer", lambda h: Principal("a", "i", roles=("admin",))
    )
    status, payload = _get(
        base_url, "/admin/api/v0/context/sources", headers={"Authorization": "Bearer t"}
    )
    assert status == 200 and payload["sources"] == []


@pytest.mark.parametrize(
    "path, body",
    [
        ("/admin/api/v0/context/sources/validate", {"source_id": "s1"}),
        ("/admin/api/v0/context/sources/enable", {"source_id": "s1", "enabled": True}),
        ("/admin/api/v0/context/sources/remove", {"source_id": "s1"}),
        # hy-bo5p: compare (read) and rollback (re-pin) are admin CONFIGURE ops too.
        (
            "/admin/api/v0/context/sources/compare",
            {"source_id": "s1", "base_commit": "a", "target_commit": "b"},
        ),
        ("/admin/api/v0/context/sources/rollback", {"source_id": "s1", "commit_sha": "a"}),
    ],
)
@pytest.mark.parametrize("role", ["reader", "reviewer", "service"])
def test_context_source_manage_endpoints_deny_a_non_admin(base_url, monkeypatch, path, body, role):
    # hq-3fjt: validate/enable/remove are admin CONFIGURE writes -- a non-admin (including a
    # verified reader) is denied SERVER-SIDE and never reaches the work. Load-bearing: with a
    # read gate a reader passes and the spied worker fires, reddening the seen assertion.
    seen = []

    class _Spy:
        def __init__(self, *a, **k):
            seen.append("repo")

    monkeypatch.setattr(http_module, "PostgresContextRepository", _Spy)
    monkeypatch.setattr(http_module, "validate_git_context", lambda **k: seen.append("validate"))
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    monkeypatch.setattr(
        http_module, "principal_from_bearer", lambda h: Principal("u", "i", roles=(role,))
    )
    status, payload = _post(base_url, path, body, headers={"Authorization": "Bearer t"})
    assert status == 400 and payload["error"]["code"] == "unauthorized", (path, role)
    assert seen == [], f"{role} reached {path} despite denial"


# --- admin observed-evidence CONNECTION management (hq-jedd) ---


def test_observed_source_status_requires_admin_configure(base_url, monkeypatch):
    # hq-hnrf area 2: the per-source observed-status overview exposes deployment config
    # (connection identities + health), so it is gated by ADMIN CONFIGURE, not the generic
    # read gate. A verified READER is denied and the read model is never even called.
    calls = []
    monkeypatch.setattr(
        http_module, "read_observed_source_status", lambda *a, **k: calls.append("read") or []
    )
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    monkeypatch.setattr(
        http_module, "principal_from_bearer", lambda h: Principal("u", "i", roles=("reader",))
    )
    status, payload = _get(
        base_url, "/admin/api/v0/observed-sources/status", headers={"Authorization": "Bearer t"}
    )
    assert status == 400 and payload["error"]["code"] == "unauthorized"
    assert calls == []  # a reader never reaches the read model


def test_connections_LIST_requires_admin_configure(base_url, monkeypatch):
    # hq-jedd: the connection list exposes deployment config (connector types + config refs),
    # so it is gated by the ADMIN CONFIGURE authz, not the generic read gate. A verified
    # READER passes the read gate but must be DENIED here; the denial short-circuits before
    # the store.
    seen = []

    class _Spy:
        def __init__(self, *a, **k):
            seen.append("repo")

    monkeypatch.setattr(http_module, "PostgresConnectionRepository", _Spy)
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    monkeypatch.setattr(
        http_module, "principal_from_bearer", lambda h: Principal("u", "i", roles=("reader",))
    )
    status, payload = _get(
        base_url, "/admin/api/v0/connections", headers={"Authorization": "Bearer t"}
    )
    assert status == 400 and payload["error"]["code"] == "unauthorized"
    assert seen == []  # a reader never reaches the store


@pytest.mark.parametrize(
    "path, body",
    [
        (
            "/admin/api/v0/connections",
            {"connector_type": "superset", "display_name": "x", "config_ref": "/b"},
        ),
        ("/admin/api/v0/connections/enable", {"id": "c1", "enabled": True}),
        ("/admin/api/v0/connections/remove", {"id": "c1"}),
        ("/admin/api/v0/connections/probe", {"id": "c1"}),
    ],
)
@pytest.mark.parametrize("role", ["reader", "reviewer", "service"])
def test_connection_manage_endpoints_deny_a_non_admin(base_url, monkeypatch, path, body, role):
    # hq-jedd: add/enable/remove/probe are admin CONFIGURE writes -- a non-admin (incl a
    # verified reader) is denied server-side and never reaches the store. Load-bearing: with
    # a read gate a reader passes and the spied repo is constructed, reddening `seen`.
    seen = []

    class _Spy:
        def __init__(self, *a, **k):
            seen.append("repo")

    monkeypatch.setattr(http_module, "PostgresConnectionRepository", _Spy)
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    monkeypatch.setattr(
        http_module, "principal_from_bearer", lambda h: Principal("u", "i", roles=(role,))
    )
    status, payload = _post(base_url, path, body, headers={"Authorization": "Bearer t"})
    assert status == 400 and payload["error"]["code"] == "unauthorized", (path, role)
    assert seen == [], f"{role} reached {path} despite denial"


# --- admin audit trail (hy-gh-75, option B) ---


class _FakeAudit:
    """A fake admin-audit repo: records append() calls, returns canned list()."""

    appended: list = []
    listed: list = []

    def __init__(self, sf):
        pass

    def record(self, **kw):
        _FakeAudit.appended.append(kw)
        return SimpleNamespace(id="a1", **kw)

    def list(self, *, workspace=None, limit=200):
        return list(_FakeAudit.listed)


def _install_audit(monkeypatch):
    _FakeAudit.appended = []
    _FakeAudit.listed = []
    monkeypatch.setattr(http_module, "PostgresAdminAuditRepository", _FakeAudit)


def test_admin_audit_list_is_served_on_the_admin_surface(base_url, monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    _install_audit(monkeypatch)
    _FakeAudit.listed = [
        SimpleNamespace(
            id="a1",
            at=None,
            actor="alice",
            actor_issuer="i",
            action="context_source.add",
            target="src-1",
            result="ok",
            detail="org/ctx@main:domains",
            correlation_id="req-1",
        )
    ]
    status, payload = _get(base_url, "/admin/api/v0/audit")
    assert status == 200
    assert payload["entries"][0]["action"] == "context_source.add"
    assert payload["entries"][0]["actor"] == "alice"
    # The trail surfaces the request that performed the action (hy-w9ntg).
    assert payload["entries"][0]["correlation_id"] == "req-1"


def test_admin_audit_is_admin_surface_only(base_url, monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    _install_audit(monkeypatch)
    status, _ = _get_raw(base_url, "/playground/api/v0/audit")
    assert status == 404


def test_admin_audit_requires_authorization_when_enabled(base_url, monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    _install_audit(monkeypatch)
    monkeypatch.setattr(http_module, "principal_from_bearer", lambda h: None)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    status, payload = _get(base_url, "/admin/api/v0/audit")
    assert status == 400 and payload["error"]["code"] == "unauthorized"


def test_adding_a_context_source_writes_an_audit_entry(base_url, monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    _install_audit(monkeypatch)

    class _Repo:
        def __init__(self, sf):
            pass

        def register_source(
            self, *, repository, ref, path, display_name, workspace=None, session=None
        ):
            return SimpleNamespace(
                id="src-9",
                repository=repository,
                ref=ref,
                path=path,
                display_name=display_name or "d",
                enabled=True,
                current_snapshot=None,
                last_attempt_status="never_synced",
            )

    monkeypatch.setattr(http_module, "PostgresContextRepository", _Repo)
    status, _ = _post(
        base_url, "/admin/api/v0/context/sources", {"repository": "org/ctx", "path": "domains"}
    )
    assert status == 200
    assert len(_FakeAudit.appended) == 1
    entry = _FakeAudit.appended[0]
    assert entry["action"] == "context_source.add" and entry["target"] == "src-9"
    assert entry["result"] == "ok" and entry["actor"] == "anonymous"  # gate off => anonymous


def test_syncing_a_source_writes_an_audit_entry_with_the_result(base_url, monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    _install_audit(monkeypatch)
    monkeypatch.setattr(
        http_module,
        "sync_git_context",
        lambda **kw: SimpleNamespace(
            source_id=kw["source_id"],
            status="synced",
            commit_sha="c",
            snapshot_id="s",
            synced_at=None,
            reasons=[],
            findings=[],
            ok=True,
        ),
    )
    status, _ = _post(base_url, "/admin/api/v0/context/sources/sync", {"source_id": "src-1"})
    assert status == 200
    assert _FakeAudit.appended[0]["action"] == "context_source.sync"
    assert _FakeAudit.appended[0]["result"] == "ok" and _FakeAudit.appended[0]["target"] == "src-1"


def test_the_audit_entry_records_the_verified_actor_when_enabled(base_url, monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    _install_audit(monkeypatch)
    # An ADMIN, because sync is a CONFIGURE write (admin-only, hy-rdlo) -- a reader would be
    # denied before the audit is written. The audit records this verified actor's subject.
    admin = Principal(subject="alice@example", issuer="https://idp/", roles=("admin",))
    monkeypatch.setattr(http_module, "principal_from_bearer", lambda h: admin if h else None)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    monkeypatch.setattr(
        http_module,
        "sync_git_context",
        lambda **kw: SimpleNamespace(
            source_id=kw["source_id"],
            status="synced",
            commit_sha="c",
            snapshot_id="s",
            synced_at=None,
            reasons=[],
            findings=[],
            ok=True,
        ),
    )
    status, _ = _post(
        base_url,
        "/admin/api/v0/context/sources/sync",
        {"source_id": "src-1"},
        headers={"Authorization": "Bearer t"},
    )
    assert status == 200
    assert _FakeAudit.appended[0]["actor"] == "alice@example"
    assert _FakeAudit.appended[0]["actor_issuer"] == "https://idp/"


def test_a_failed_audit_append_rejects_the_operation(base_url, monkeypatch):
    # MANDATORY, not best-effort (hy-gh-75 round 2 adversary bounce): if the audit append
    # raises, the admin operation is REJECTED with a 500, not reported as a success that
    # left no audit row. Here the sync itself succeeds, so the ONLY thing that turns the
    # response into a 500 is the failed audit append -- proving the trail is not omittable
    # at its own failure mode. (The sync snapshot is immutable and idempotent, so a retry
    # once the audit store is back re-records the same result.)
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")

    class _Boom:
        def __init__(self, sf):
            pass

        def record(self, **kw):
            raise RuntimeError("audit db down")

    monkeypatch.setattr(http_module, "PostgresAdminAuditRepository", _Boom)
    monkeypatch.setattr(
        http_module,
        "sync_git_context",
        lambda **kw: SimpleNamespace(
            source_id=kw["source_id"],
            status="synced",
            commit_sha="c",
            snapshot_id="s",
            synced_at=None,
            reasons=[],
            findings=[],
            ok=True,
        ),
    )
    status, payload = _post(base_url, "/admin/api/v0/context/sources/sync", {"source_id": "src-1"})
    assert status == 500
    assert payload["error"]["code"] == "internal_error"
    assert "audit" in payload["error"]["message"]


def test_a_failed_audit_append_rejects_a_context_source_add(base_url, monkeypatch):
    # The add path couples the register and its audit append in ONE transaction: an audit
    # failure must reject the whole operation, so no source is registered without an audit
    # row. The register fake "succeeds"; the audit fake fails -- and the response is a 500,
    # not a 200 that returned a source id with no audit trail.
    #
    # The audit failure is invoked ON THE SHARED SESSION and this asserts the append ran on
    # the SAME transaction session object the register got (round 3): if a future edit drops
    # `session=session` from the audit.record call -- decoupling it into its own transaction
    # -- `audit_session` would be None and this fails, catching the omission.
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    seen: dict = {}

    class _Repo:
        def __init__(self, sf):
            pass

        def register_source(
            self, *, repository, ref, path, display_name, workspace=None, session=None
        ):
            seen["register_session"] = session
            return SimpleNamespace(id="src-x", repository=repository, ref=ref, path=path)

    class _Boom:
        def __init__(self, sf):
            pass

        def record(self, **kw):
            seen["audit_session"] = kw.get("session")
            raise RuntimeError("audit db down")

    monkeypatch.setattr(http_module, "PostgresContextRepository", _Repo)
    monkeypatch.setattr(http_module, "PostgresAdminAuditRepository", _Boom)
    status, payload = _post(
        base_url, "/admin/api/v0/context/sources", {"repository": "org/ctx", "path": "domains"}
    )
    assert status == 500
    assert payload["error"]["code"] == "internal_error"
    assert seen["audit_session"] is not None, "audit ran without the shared transaction session"
    assert seen["audit_session"] is seen["register_session"], "audit and register were decoupled"


def test_a_failed_audit_append_rejects_a_writeback_config_set(base_url, monkeypatch):
    # The writeback-config path couples the set and its audit append the same way; an audit
    # failure on the SHARED session rejects the whole write, and the assertion catches an
    # omitted session=session that would decouple them (round 3).
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    seen: dict = {}

    class _Writeback:
        def __init__(self, sf):
            pass

        def get(self, *, workspace=None):
            return None

        def set(self, *, session=None, **kw):
            seen["set_session"] = session
            return SimpleNamespace(repository=kw["repository"])

    class _Boom:
        def __init__(self, sf):
            pass

        def record(self, **kw):
            seen["audit_session"] = kw.get("session")
            raise RuntimeError("audit db down")

    monkeypatch.setattr(http_module, "PostgresWritebackConfigRepository", _Writeback)
    monkeypatch.setattr(http_module, "PostgresAdminAuditRepository", _Boom)
    status, payload = _post(
        base_url,
        "/admin/api/v0/review/writeback-config",
        {
            "repository": "org/repo",
            "base_ref": "main",
            "manifest_path": "domains/x",
            "token_source": "env_ref",
        },
    )
    assert status == 500 and payload["error"]["code"] == "internal_error"
    assert seen["audit_session"] is not None, "audit ran without the shared transaction session"
    assert seen["audit_session"] is seen["set_session"], "audit and set were decoupled"


@pytest.mark.parametrize(
    "path,body",
    [
        (
            "/admin/api/v0/context/sources",
            {"repository": "https://u:tok@github.com/o/r.git", "path": "d"},
        ),
        (
            # MALFORMED credential URL: an unbalanced IPv6 bracket that urlsplit cannot parse.
            # The fail-open bug (round 2) waved this through and let u:tok@ reach the audit
            # record; the textual userinfo check must still reject it (round 3).
            "/admin/api/v0/context/sources",
            {"repository": "https://u:tok@[::1/repo", "path": "d"},
        ),
        (
            "/admin/api/v0/review/writeback-config",
            {
                "repository": "https://u:tok@github.com/o/r.git",
                "base_ref": "main",
                "manifest_path": "domains/x",
                "token_source": "env_ref",
            },
        ),
    ],
)
def test_a_credential_bearing_repository_pointer_is_rejected(base_url, monkeypatch, path, body):
    # A repo URL with embedded userinfo (a token/password) must never be persisted: it
    # would land in the config row AND, via /admin/api/v0/audit, in a record any READ-
    # authorized principal can list. The write is refused BEFORE any repository or audit
    # write -- proven by the spy repos never being touched.
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    touched: list = []

    class _SpyContext:
        def __init__(self, sf):
            pass

        def register_source(self, **kw):
            touched.append(("register_source", kw))

    class _SpyWriteback:
        def __init__(self, sf):
            pass

        def get(self, *, workspace=None):
            return None

        def set(self, **kw):
            touched.append(("set", kw))

    class _SpyAudit:
        def __init__(self, sf):
            pass

        def record(self, **kw):
            touched.append(("record", kw))

    monkeypatch.setattr(http_module, "PostgresContextRepository", _SpyContext)
    monkeypatch.setattr(http_module, "PostgresWritebackConfigRepository", _SpyWriteback)
    monkeypatch.setattr(http_module, "PostgresAdminAuditRepository", _SpyAudit)
    status, payload = _post(base_url, path, body)
    assert status == 400 and payload["error"]["code"] == "invalid_request"
    assert "credential" in payload["error"]["message"] or "token" in payload["error"]["message"]
    assert touched == [], "a credential-bearing pointer reached a write path"


def test_the_audit_detail_redacts_url_userinfo(base_url, monkeypatch):
    # Defense in depth on the audit boundary: even if a userinfo-bearing value reaches the
    # audit detail, `_redact_pointer` strips it before persistence. `_redact_pointer` is a
    # pure function; assert it directly on the shape the add path composes.
    assert (
        http_module._redact_pointer("https://alice:s3cr3t@github.com/o/r.git@main:domains")
        == "https://github.com/o/r.git@main:domains"
    )
    assert http_module._redact_pointer("org/ctx@main:domains") == "org/ctx@main:domains"
    assert http_module._pointer_has_credentials("https://x:y@h/r") is True
    assert http_module._pointer_has_credentials("https://github.com/o/r.git") is False
    assert http_module._pointer_has_credentials("git@github.com:o/r.git") is False

    # FAIL CLOSED on a malformed credential URL urlsplit cannot parse (round 3): the
    # userinfo is detected textually, so the pointer is still flagged AND redacted with no
    # `u:tok@` surviving -- the fail-open bug returned False / the raw string here.
    malformed = "https://u:tok@[::1/repo"
    assert http_module._pointer_has_credentials(malformed) is True
    redacted = http_module._redact_pointer(malformed)
    assert "u:tok@" not in redacted and "tok" not in redacted
    assert redacted == "https://[::1/repo"

    # MULTI-@ (round 4): RFC 3986 puts the host after the LAST @, so all of `u:tok@evil@` is
    # userinfo. Redacting only through the first @ left a residual `evil@`; now every @ in the
    # authority is stripped and nothing before the host survives.
    assert http_module._pointer_has_credentials("https://u:tok@evil@host/repo") is True
    multi = http_module._redact_pointer("https://u:tok@evil@host/repo")
    assert multi == "https://host/repo"
    assert "evil" not in multi and "tok" not in multi
    # multi-@ AND a malformed bracket together: still no residual userinfo.
    assert http_module._redact_pointer("https://u:tok@evil@[::1/repo") == "https://[::1/repo"

    # MALFORMED PORT (round 4): urlsplit(...).port would RAISE ValueError on `:99999`;
    # _redact_pointer is purely textual, so it never raises and still strips the userinfo.
    assert http_module._pointer_has_credentials("https://u:tok@host:99999/repo") is True
    bad_port = http_module._redact_pointer("https://u:tok@host:99999/repo")
    assert bad_port == "https://host:99999/repo" and "tok" not in bad_port

    # _redact_pointer NEVER raises, whatever the input shape.
    for ugly in ("", "://@", "https://@@@", "https://u:p@h:notaport@x/y", "not a url @ all", "@"):
        http_module._redact_pointer(ugly)  # must not raise

    # An `@` in the PATH (not the authority) is left alone -- only userinfo is stripped.
    assert http_module._pointer_has_credentials("https://github.com/o/r@main") is False
    assert (
        http_module._redact_pointer("https://github.com/o/r@main") == "https://github.com/o/r@main"
    )


# --- F4 login/callback/logout routes (hy-jyha) ---

import http.client as _httpclient  # noqa: E402


def _raw_get(base_url, path, headers=None):
    """A GET that does NOT follow redirects, so a 302/303 + Set-Cookie can be inspected."""
    host, port = base_url.removeprefix("http://").split(":")
    conn = _httpclient.HTTPConnection(host, int(port))
    try:
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        body = resp.read()
        return SimpleNamespace(
            status=resp.status,
            location=resp.getheader("Location"),
            set_cookies=resp.msg.get_all("Set-Cookie") or [],
            body=body,
        )
    finally:
        conn.close()


def _login_env(monkeypatch):
    monkeypatch.setenv("HYPERSET_SESSION_SECRET", "test-session-secret-0123456789abcdef")
    monkeypatch.setenv("HYPERSET_OIDC_AUTHORIZATION_ENDPOINT", "https://idp.example/authorize")
    monkeypatch.setenv("HYPERSET_OIDC_TOKEN_ENDPOINT", "https://idp.example/token")
    monkeypatch.setenv("HYPERSET_OIDC_CLIENT_ID", "hyperset-web")
    monkeypatch.setenv("HYPERSET_OIDC_REDIRECT_URI", "https://app.example/callback")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")


def test_login_is_inert_when_authz_is_off(base_url, monkeypatch):
    # Off the gate (loopback dev): /login just returns the user to the (safe) deep link,
    # no IdP redirect, no cookie.
    monkeypatch.delenv("HYPERSET_AUTHZ_ENABLED", raising=False)
    r = _raw_get(base_url, "/login?return=/admin/")
    assert r.status == 303 and r.location == "/admin/"
    assert r.set_cookies == []


def test_login_redirects_to_the_idp_with_pkce_and_sets_a_login_cookie(base_url, monkeypatch):
    _login_env(monkeypatch)
    r = _raw_get(base_url, "/login?return=/admin/")
    assert r.status == 302
    assert r.location.startswith("https://idp.example/authorize?")
    # The PKCE CHALLENGE and S256 method are in the URL; the VERIFIER never is.
    assert "code_challenge=" in r.location and "code_challenge_method=S256" in r.location
    assert "response_type=code" in r.location and "client_id=hyperset-web" in r.location
    assert "state=" in r.location and "nonce=" in r.location
    assert "code_verifier" not in r.location
    # The login-transaction cookie is HttpOnly (holds the secret verifier).
    assert len(r.set_cookies) == 1
    cookie = r.set_cookies[0]
    assert (
        cookie.startswith("hyperset_login=") and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    )


def test_login_deep_link_is_allowlisted(base_url, monkeypatch):
    # An off-site return collapses to "/" -- no open redirect through login.
    monkeypatch.delenv("HYPERSET_AUTHZ_ENABLED", raising=False)
    assert _raw_get(base_url, "/login?return=//evil.example/x").location == "/"
    assert _raw_get(base_url, "/login?return=https://evil.example").location == "/"


def test_callback_is_not_a_route_when_authz_is_off(base_url, monkeypatch):
    monkeypatch.delenv("HYPERSET_AUTHZ_ENABLED", raising=False)
    assert _raw_get(base_url, "/callback?code=x&state=y").status == 404


def _valid_login_cookie(
    state="the-state",
    verifier="the-verifier-01234567890123456789012345",
    return_to="/admin/",
    nonce="the-nonce",
):
    from hyperset.security import login

    return login.issue_login_state(
        state=state, code_verifier=verifier, nonce=nonce, return_to=return_to
    )


def test_callback_happy_path_mints_a_session_and_returns_to_the_deep_link(base_url, monkeypatch):
    _login_env(monkeypatch)
    seen = {}

    def _exchange(**kw):
        seen.update(kw)
        return "id.token.value"

    monkeypatch.setattr(http_module, "exchange_code_for_id_token", _exchange)

    # Nonce-aware, like the real verifier: it yields a principal only when the callback
    # passes the SAME nonce the login cookie carries -- proving the callback threads the
    # ID-token nonce binding through the verified-claims path (hy-jyha round 2).
    def _verify(tok, *, expected_nonce=None):
        if not tok or expected_nonce != "the-nonce":
            return None
        return Principal("alice", "https://idp.example/", roles=("reader",))

    monkeypatch.setattr(http_module, "verify_bearer", _verify)
    cookie = _valid_login_cookie(state="s1")
    r = _raw_get(
        base_url,
        "/callback?code=authcode&state=s1",
        headers={"Cookie": f"hyperset_login={cookie}"},
    )
    assert r.status == 303 and r.location == "/admin/"
    # The exchange got the PKCE verifier from the signed cookie (never the URL).
    assert seen["code"] == "authcode" and seen["code_verifier"].startswith("the-verifier")
    # A session cookie is set (HttpOnly), and the single-use login cookie is cleared.
    joined = " || ".join(r.set_cookies)
    assert "hyperset_session=" in joined and "HttpOnly" in joined
    assert (
        "hyperset_login=;" in joined or "hyperset_login=; " in joined or "hyperset_login=" in joined
    )
    assert "Max-Age=0" in joined  # the login cookie is cleared


@pytest.mark.parametrize(
    "case",
    ["no_cookie", "state_mismatch", "no_code", "exchange_fails", "unverifiable_token"],
)
def test_callback_fails_closed_uniformly(base_url, monkeypatch, case):
    _login_env(monkeypatch)
    monkeypatch.setattr(
        http_module,
        "exchange_code_for_id_token",
        (lambda **kw: (_ for _ in ()).throw(http_module.TokenExchangeError("boom")))
        if case == "exchange_fails"
        else (lambda **kw: "id.token"),
    )
    monkeypatch.setattr(
        http_module,
        "verify_bearer",
        (lambda tok, *, expected_nonce=None: None)
        if case == "unverifiable_token"
        else (lambda tok, *, expected_nonce=None: Principal("a", "i", roles=())),
    )
    headers = {}
    path = "/callback?code=c&state=s1"
    if case != "no_cookie":
        headers["Cookie"] = f"hyperset_login={_valid_login_cookie(state='s1')}"
    if case == "state_mismatch":
        path = "/callback?code=c&state=WRONG"
    if case == "no_code":
        path = "/callback?state=s1"
    r = _raw_get(base_url, path, headers=headers)
    # Uniform fail-closed: redirect to the login-failed page, NO session, login cookie cleared.
    assert r.status == 303 and "/?login=failed" == r.location, case
    joined = " || ".join(r.set_cookies)
    assert "hyperset_session=" not in joined, f"{case} minted a session on failure"
    assert "Max-Age=0" in joined  # login cookie cleared


def test_callback_rejects_an_id_token_nonce_mismatch(base_url, monkeypatch):
    # OIDC replay binding (hy-jyha round 2): the callback passes the login cookie's nonce as
    # `expected_nonce`; if the verified ID token does not carry that nonce, verify_bearer
    # denies. Here the token was minted for "token-nonce" but the login cookie carries
    # "cookie-nonce" -- a mismatch -> uniform failure, NO session. Distinct from an
    # unverifiable token: state and PKCE are correct; only the nonce binding fails.
    _login_env(monkeypatch)
    monkeypatch.setattr(http_module, "exchange_code_for_id_token", lambda **kw: "id.token")

    def _verify(tok, *, expected_nonce=None):
        # emulate the real verifier: the token's own nonce claim is "token-nonce"
        if expected_nonce != "token-nonce":
            return None
        return Principal("a", "i", roles=("reader",))

    monkeypatch.setattr(http_module, "verify_bearer", _verify)
    cookie = _valid_login_cookie(state="s1", nonce="cookie-nonce")
    r = _raw_get(
        base_url,
        "/callback?code=c&state=s1",
        headers={"Cookie": f"hyperset_login={cookie}"},
    )
    assert r.status == 303 and r.location == "/?login=failed"
    joined = " || ".join(r.set_cookies)
    assert "hyperset_session=" not in joined, "a nonce-mismatched token minted a session"
    assert "Max-Age=0" in joined  # the single-use login cookie is cleared


def test_the_query_string_is_redacted_in_every_access_log_line(base_url, monkeypatch):
    # The stdlib access log prints the request line to stderr; an OAuth code/state must never
    # reach it (hy-jyha, ruling hq-l4g2). The redaction is a CLASS fix (round 4): the query of
    # EVERY logged line is masked, not an auth-path allowlist -- so trailing-slash, repeated
    # slash, case, percent-encoded, and 404 variants that still reach an auth handler are all
    # covered in one move, and no query value is ever logged. The PATH is preserved.
    logged: list[str] = []
    monkeypatch.setattr(
        http_module._Handler,
        "log_message",
        lambda self, fmt, *args: logged.append(fmt % args),
    )

    # Every path variant of the callback -- however dispatch normalizes it -- is redacted,
    # plus a non-auth route: an access log has no need for ANY query string.
    variants = [
        ("/callback?code=SEKRET1&state=STATE1", "SEKRET1"),
        ("/callback/?code=SEKRET2&state=STATE2", "SEKRET2"),
        ("/callback//?code=SEKRET3", "SEKRET3"),
        ("/CALLBACK?code=SEKRET4", "SEKRET4"),
        ("/callback%2F?code=SEKRET5", "SEKRET5"),
        ("/login/?return=/x&code=SEKRET6", "SEKRET6"),
        ("/v0/context/history?repository=SEKRET7&ref=f&path=p", "SEKRET7"),
    ]
    for path, secret in variants:
        logged.clear()
        _raw_get(base_url, path)
        line = "\n".join(logged)
        assert secret not in line, f"query value leaked for {path}: {line!r}"
        assert "<redacted>" in line, f"query not masked for {path}: {line!r}"

    # The PATH itself is preserved (only the query VALUES are hidden).
    logged.clear()
    _raw_get(base_url, "/callback?code=x")
    assert "/callback" in "\n".join(logged)


def test_redacted_requestline_masks_the_query_regardless_of_whitespace():
    # `parse_request` splits the request line on ANY run of whitespace, so a line with a
    # double space or a tab still routes -- but has more than three fields. A fixed 3-field
    # split bailed out and logged the raw code/state (round 5). Redacting `?...` up to the
    # next whitespace on the RAW line covers every whitespace shape and a malformed
    # one-field line. Driven directly (http.client cannot emit a malformed request line).
    redact = http_module._Handler._redacted_requestline
    for line in (
        "GET /callback?code=SECRET&state=S HTTP/1.1",  # single space
        "GET  /callback?code=SECRET&state=S HTTP/1.1",  # double space
        "GET\t/callback?code=SECRET&state=S\tHTTP/1.1",  # tab-separated
        "/callback?code=SECRET",  # malformed one-field line
        "GET /v0/x?repository=SECRET HTTP/1.1",  # a non-auth route too
    ):
        out = redact(SimpleNamespace(requestline=line))
        assert "SECRET" not in out, line
        assert "<redacted>" in out, line
    # The path/method/version are preserved, and a line with no query is unchanged.
    assert "/callback" in redact(SimpleNamespace(requestline="GET  /callback?code=x HTTP/1.1"))
    assert redact(SimpleNamespace(requestline="GET /health HTTP/1.1")) == "GET /health HTTP/1.1"


def test_logout_clears_the_session_cookie(base_url, monkeypatch):
    r = _raw_get(base_url, "/logout?return=/playground/")
    assert r.status == 303 and r.location == "/playground/"
    joined = " || ".join(r.set_cookies)
    assert "hyperset_session=" in joined and "Max-Age=0" in joined


def test_a_session_cookie_authenticates_a_governed_read(base_url, monkeypatch):
    # The point of the flow: a browser with a valid SESSION cookie (no bearer) is a verified
    # principal on a governed read. Gate ON + a reader session -> the read is REACHED (the
    # authz gate does not deny), proven by a spy on the downstream reader.
    from hyperset.security import login

    calls = _history_spy(monkeypatch)
    monkeypatch.setenv("HYPERSET_SESSION_SECRET", "test-session-secret-0123456789abcdef")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    session = login.issue_session(subject="alice", issuer="https://idp.example/", roles=("reader",))

    # Without the session, the gate denies (control): the read is never reached.
    status, payload = _get(base_url, "/v0/context/history?repository=r&ref=f&path=p")
    assert status == 400 and payload["error"]["code"] == "unauthorized"
    assert calls == []

    # With the session cookie, the reader is authenticated and the read IS reached.
    status, _ = _get(
        base_url,
        "/v0/context/history?repository=r&ref=f&path=p",
        headers={"Cookie": f"hyperset_session={session}"},
    )
    assert status == 200
    assert calls and calls[0]["repository"] == "r"


def _session_value(set_cookies):
    """The minted session token from a Set-Cookie list, or None if the header only
    CLEARS the cookie (Max-Age=0). Lets a test carry the browser's session forward."""
    for cookie in set_cookies:
        if cookie.startswith("hyperset_session=") and "Max-Age=0" not in cookie:
            return cookie.split(";", 1)[0].split("=", 1)[1]
    return None


def test_production_auth_lifecycle_is_observably_non_inert(base_url, monkeypatch):
    """hq-tjhr: the WHOLE session lifecycle, end to end, in production (OIDC) mode --
    login -> authed request -> logout -> rejected -- and the SAME server in loopback
    (auth-off) mode is inert. One test, because the acceptance is that production auth
    is observably DIFFERENT from the loopback default, not merely that each step works
    in isolation.

    The IdP round-trip is the only mock (no network): `exchange_code_for_id_token` and
    `verify_bearer` stand in for the real token exchange + JWKS verification, which
    `tests/unit/security/test_oidc.py` proves against real signatures. Everything else
    -- PKCE redirect, the signed login/session cookies, the authz gate, and the
    workspace reconstruction (#438) -- is the real served code path."""
    calls = _history_spy(monkeypatch)
    read = "/v0/context/history?repository=r&ref=f&path=p"

    # --- PRODUCTION MODE: authz gate ON + OIDC configured -----------------------------
    _login_env(monkeypatch)

    # 1) LOGIN is live, not inert: a real 302 to the IdP with PKCE, and a login cookie.
    started = _raw_get(base_url, "/login?return=/admin/")
    assert started.status == 302 and started.location.startswith("https://idp.example/authorize?")
    assert "code_challenge_method=S256" in started.location
    assert any(c.startswith("hyperset_login=") for c in started.set_cookies)

    # 2) A missing session on a governed read is FAIL-CLOSED (not silently loopback-open).
    denied_status, denied = _get(base_url, read)
    assert denied_status == 400 and denied["error"]["code"] == "unauthorized"
    assert calls == [], "the read was reached with no session -- production is not fail-closed"

    # 3) CALLBACK mints the session; verify_bearer reconstructs the FULL principal,
    #    including the tenant workspace (#438), which must reach the served read.
    monkeypatch.setattr(http_module, "exchange_code_for_id_token", lambda **kw: "id.token.value")
    monkeypatch.setattr(
        http_module,
        "verify_bearer",
        lambda tok, *, expected_nonce=None: (
            Principal("alice", "https://idp.example/", roles=("reader",), workspace="tenant-a")
            if tok and expected_nonce == "the-nonce"
            else None
        ),
    )
    finished = _raw_get(
        base_url,
        "/callback?code=authcode&state=s1",
        headers={"Cookie": f"hyperset_login={_valid_login_cookie(state='s1', nonce='the-nonce')}"},
    )
    assert finished.status == 303 and finished.location == "/admin/"
    session = _session_value(finished.set_cookies)
    assert session, "the callback minted no session cookie"

    # 4) The AUTHED request is served, and the reconstructed workspace threads through.
    ok_status, _ = _get(base_url, read, headers={"Cookie": f"hyperset_session={session}"})
    assert ok_status == 200
    assert calls and calls[-1]["repository"] == "r" and calls[-1]["workspace"] == "tenant-a"

    # 5) LOGOUT clears the session; the browser now presents no session -> REJECTED.
    logged_out = _raw_get(base_url, "/logout?return=/admin/")
    assert _session_value(logged_out.set_cookies) is None
    assert any("hyperset_session=" in c and "Max-Age=0" in c for c in logged_out.set_cookies)
    calls.clear()
    after_status, after = _get(base_url, read)
    assert after_status == 400 and after["error"]["code"] == "unauthorized"
    assert calls == []

    # --- LOOPBACK (auth-off) MODE: the SAME routes are inert ---------------------------
    monkeypatch.delenv("HYPERSET_AUTHZ_ENABLED", raising=False)
    calls.clear()
    inert = _raw_get(base_url, "/login?return=/admin/")
    assert inert.status == 303 and inert.location == "/admin/"  # no IdP, no login cookie
    assert inert.set_cookies == []
    # And a governed read with NO session is OPEN -- exactly the exposure production closes.
    open_status, _ = _get(base_url, read)
    assert open_status == 200 and calls, "loopback mode should serve the read unauthenticated"


def test_an_expired_session_cookie_is_rejected_on_a_real_request(base_url, monkeypatch):
    """hq-tjhr expiry, observable at the request boundary with a DETERMINISTIC clock: a
    cookie whose `exp` is fixed in the past (issued at epoch 0, 1s TTL) is no principal on
    a real governed read under the gate, while a freshly-issued one authorizes. No clock
    injection into the server is needed -- the expiry is baked into the token."""
    from hyperset.security import login

    calls = _history_spy(monkeypatch)
    monkeypatch.setenv("HYPERSET_SESSION_SECRET", "test-session-secret-0123456789abcdef")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    read = "/v0/context/history?repository=r&ref=f&path=p"

    expired = login.issue_session(
        subject="alice", issuer="https://idp.example/", roles=("reader",), now=0, ttl_seconds=1
    )
    status, payload = _get(base_url, read, headers={"Cookie": f"hyperset_session={expired}"})
    assert status == 400 and payload["error"]["code"] == "unauthorized"
    assert calls == [], "an expired session reached the governed read"

    fresh = login.issue_session(subject="alice", issuer="https://idp.example/", roles=("reader",))
    ok, _ = _get(base_url, read, headers={"Cookie": f"hyperset_session={fresh}"})
    assert ok == 200 and calls, "a fresh session should authorize the read"


def test_a_forged_session_cookie_is_rejected_on_a_real_request(base_url, monkeypatch):
    """hq-tjhr session integrity at the request boundary: a garbage cookie and a
    correctly-shaped cookie signed by the WRONG secret are both no principal under the
    gate (the HMAC signature check), so neither reaches the governed read -- forging a
    session without the server's secret cannot authenticate."""
    from hyperset.security import login

    calls = _history_spy(monkeypatch)
    monkeypatch.setenv("HYPERSET_SESSION_SECRET", "test-session-secret-0123456789abcdef")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    read = "/v0/context/history?repository=r&ref=f&path=p"

    forged = login.issue_session(
        subject="attacker",
        issuer="https://idp.example/",
        roles=("reader",),
        secret=b"an-entirely-different-signing-secret",
    )
    for cookie in ("hyperset_session=not.a.valid.token", f"hyperset_session={forged}"):
        status, payload = _get(base_url, read, headers={"Cookie": cookie})
        assert status == 400 and payload["error"]["code"] == "unauthorized", cookie
    assert calls == [], "a forged session reached the governed read"


def test_the_login_routes_add_no_mcp_operation(base_url):
    # F4 is HTTP routes only -- no served OPERATION, so tools_hash/SCHEMA_VERSION do not move.
    from hyperset.transport.operations import OPERATIONS

    assert "login" not in OPERATIONS and "callback" not in OPERATIONS and "logout" not in OPERATIONS


def test_review_preview_is_gated_behind_the_playground(base_url):
    """The ephemeral preview renders a task's UNAPPROVED draft, so -- like the review read --
    it exists only when the playground is on (hy-nauw); an operator API with it off 404s."""
    status, _payload = _get_raw(base_url, "/v0/review/tasks/preview?task_id=rt-x")
    assert status == 404


def test_review_preview_is_off_the_served_operation_and_mcp_surface():
    """hy-nauw is a bespoke HTTP route, deliberately NOT a served OPERATION: adding it to
    OPERATIONS would auto-publish an MCP tool and GROW the ADR-0025 trust-surface enumeration.
    Keeping it off ROUTES/OPERATIONS keeps it off MCP and moves no tools_hash."""
    from hyperset.planner.loop import tools_hash
    from hyperset.transport.http import REVIEW_PREVIEW_PATH, ROUTES
    from hyperset.transport.operations import OPERATIONS

    assert REVIEW_PREVIEW_PATH not in ROUTES  # not an auto-generated /v0/<op> route
    assert not any("preview" in name for name in OPERATIONS)  # not a served op -> not an MCP tool
    assert tools_hash() == "sha256:fe930a003b731211"  # the benchmark surface is unmoved


def test_review_preview_denies_an_unauthorized_reader_with_authz_on(base_url, monkeypatch):
    """The preview reveals a task's content but does not route through `run_operation`, so it
    carries the shared READ gate explicitly (hy-nauw). With authz ON and no verified principal
    it FAILS CLOSED -- decided BEFORE the task is loaded, so a denied caller learns nothing
    (no existence oracle), the same `unauthorized` denial the review reads raise."""
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    status, payload = _get(base_url, "/v0/review/tasks/preview?task_id=rt-anything")
    assert status == 400
    assert payload["error"]["code"] == "unauthorized"


def test_request_evidence_is_off_the_served_operation_and_mcp_surface():
    """hy-to8m's request-evidence is a bespoke HTTP route, NOT a served OPERATION: adding it to
    OPERATIONS would auto-publish an MCP tool and grow the ADR-0025 trust surface. Its NAME is
    in OPERATION_ACTIONS (so it is REVIEW-gated) but MUST stay out of OPERATIONS/ROUTES/MCP."""
    from hyperset.planner.loop import tools_hash
    from hyperset.transport.http import REVIEW_REQUEST_EVIDENCE_PATH, ROUTES
    from hyperset.transport.operations import (
        OPERATION_ACTIONS,
        OPERATIONS,
        REQUEST_REVIEW_EVIDENCE,
        REVIEW,
    )

    assert REQUEST_REVIEW_EVIDENCE not in OPERATIONS  # not a served op -> not an MCP tool
    assert REVIEW_REQUEST_EVIDENCE_PATH not in ROUTES  # not an auto-generated /v0/<op> route
    assert OPERATION_ACTIONS[REQUEST_REVIEW_EVIDENCE] == REVIEW  # but still REVIEW-gated
    assert tools_hash() == "sha256:fe930a003b731211"  # the benchmark surface is unmoved


def test_request_evidence_denies_an_unauthorized_caller_with_authz_on(base_url, monkeypatch):
    """Re-gathering a task's evidence is an authoring MUTATION, so with authz ON a caller that
    lacks the REVIEW grant is denied -- decided before the task is touched (hy-to8m). A plain
    `reader` holds READ but not REVIEW, so it fails closed with the shared `unauthorized`."""
    from hyperset.security.authz import Principal
    from hyperset.transport import http as http_module

    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    reader = Principal("u", "https://issuer.example/", roles=("reader",))
    monkeypatch.setattr(
        http_module, "principal_from_bearer", lambda header: reader if header else None
    )
    status, payload = _post(
        base_url,
        "/v0/review/tasks/request-evidence",
        {"task_id": "rt-anything"},
        headers={"Authorization": "Bearer t"},
    )
    assert status == 400
    assert payload["error"]["code"] == "unauthorized"


def test_citation_decide_handler_gate_is_independently_load_bearing(base_url, monkeypatch):
    """The decide-citation route gates REVIEW at BOTH the handler and the service (hy-cpkvu
    blocker 3, defense-in-depth). This proves the HANDLER layer is INDEPENDENTLY load-bearing:
    with authz ON a `reader` (READ, not REVIEW) is denied BEFORE the body is parsed, so the
    service is never reached -- a spy standing in for `_decide_citation` is never called. That
    isolates the handler gate from the service gate. Mutation-red: drop the handler gate -> the
    body parses, `_decide_citation` runs, the spy fires -> this reds."""
    from hyperset.security.authz import Principal
    from hyperset.transport import http as http_module

    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    reader = Principal("u", "https://issuer.example/", roles=("reader",))
    monkeypatch.setattr(
        http_module, "principal_from_bearer", lambda header: reader if header else None
    )
    reached = []
    monkeypatch.setattr(http_module, "_decide_citation", lambda *a, **k: reached.append(True) or {})
    status, payload = _post(
        base_url,
        "/v0/review/citations/decide",
        {"decision": "approve", "citation_ref": "cit-1"},
        headers={"Authorization": "Bearer t"},
    )
    assert status == 400
    assert payload["error"]["code"] == "unauthorized"
    assert reached == []  # the service was never reached: the handler gate alone denied


def test_propose_from_search_is_off_the_served_operation_and_mcp_surface():
    """hy-27nl6's propose-from-search is a bespoke HTTP route, NOT a served OPERATION: adding it
    to OPERATIONS would auto-publish an MCP tool and grow the ADR-0025 trust surface. Its NAME
    is in OPERATION_ACTIONS (so it is REVIEW-gated) but MUST stay out of OPERATIONS/ROUTES/MCP."""
    from hyperset.planner.loop import tools_hash
    from hyperset.transport.http import REVIEW_PROPOSE_FROM_SEARCH_PATH, ROUTES
    from hyperset.transport.operations import (
        OPERATION_ACTIONS,
        OPERATIONS,
        PROPOSE_CONTEXT_FROM_SEARCH,
        REVIEW,
    )

    assert PROPOSE_CONTEXT_FROM_SEARCH not in OPERATIONS  # not a served op -> not an MCP tool
    assert REVIEW_PROPOSE_FROM_SEARCH_PATH not in ROUTES  # not an auto-generated /v0/<op> route
    assert OPERATION_ACTIONS[PROPOSE_CONTEXT_FROM_SEARCH] == REVIEW  # but still REVIEW-gated
    assert tools_hash() == "sha256:fe930a003b731211"  # the benchmark surface is unmoved


def test_propose_from_search_handler_gate_is_independently_load_bearing(base_url, monkeypatch):
    """The propose-from-search route gates REVIEW at BOTH the handler and the service (hy-27nl6,
    #504 blocker-3 discipline). This proves the HANDLER layer is INDEPENDENTLY load-bearing:
    with authz ON a `reader` (READ, not REVIEW) is denied BEFORE the body is parsed, so the
    service is never reached -- a spy standing in for `_propose_context_from_search` never fires.
    Mutation-red: drop the handler gate -> the body parses, the service runs, the spy fires."""
    from hyperset.security.authz import Principal
    from hyperset.transport import http as http_module

    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    reader = Principal("u", "https://issuer.example/", roles=("reader",))
    monkeypatch.setattr(
        http_module, "principal_from_bearer", lambda header: reader if header else None
    )
    reached = []
    monkeypatch.setattr(
        http_module, "_propose_context_from_search", lambda *a, **k: reached.append(True) or {}
    )
    status, payload = _post(
        base_url,
        "/v0/review/proposals/from-search",
        {"domain": "revenue", "definition": {"definitions": []}, "hits": []},
        headers={"Authorization": "Bearer t"},
    )
    assert status == 400
    assert payload["error"]["code"] == "unauthorized"
    assert reached == []  # the service was never reached: the handler gate alone denied


def test_audit_export_is_configure_gated_when_authz_is_on(base_url, monkeypatch):
    """hy-w9ntg: the redacted audit export is a full config dump, higher-sensitivity than the
    list, so it is CONFIGURE-gated -- with authz on and no verified principal it fails closed
    with the shared `unauthorized`, decided before any row is read (no existence oracle)."""
    from hyperset.transport import http as http_module

    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    monkeypatch.setattr(http_module, "principal_from_bearer", lambda header: None)
    status, payload = _get(base_url, "/admin/api/v0/audit/export")
    assert status == 400
    assert payload["error"]["code"] == "unauthorized"


def test_admin_responses_carry_a_correlation_id_header(base_url, monkeypatch):
    """hy-w9ntg: every JSON response echoes the request's correlation id so an operator can tie
    it to the audit rows the request wrote. Two requests get DISTINCT header ids (minted per
    request, not per connection)."""
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.delenv("HYPERSET_AUTHZ_ENABLED", raising=False)
    first_status, _first, first_headers = _get_with_headers(base_url, "/v0/health")
    _second_status, _second, second_headers = _get_with_headers(base_url, "/v0/health")
    assert first_status == 200
    assert first_headers.get("X-Correlation-Id")
    assert first_headers.get("X-Correlation-Id") != second_headers.get("X-Correlation-Id")


def test_providers_probe_is_configure_gated_when_authz_is_on(base_url, monkeypatch):
    """hy-ng8o7: the live provider probe exposes deployment config + reachability, so it is
    CONFIGURE-gated -- with authz on and no verified principal it fails closed, decided before
    any probe runs."""
    from hyperset.transport import http as http_module

    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    monkeypatch.setattr(http_module, "principal_from_bearer", lambda header: None)
    # If the gate leaked, probe_providers would run; assert it is never reached.
    monkeypatch.setattr(
        http_module,
        "probe_providers",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("gate leaked")),
    )
    status, payload = _get(base_url, "/admin/api/v0/providers/probe")
    assert status == 400
    assert payload["error"]["code"] == "unauthorized"


def test_providers_probe_is_admin_surface_only(base_url, monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    status, _ = _get_raw(base_url, "/playground/api/v0/providers/probe")
    assert status == 404


def test_providers_probe_redacts_the_reason(base_url, monkeypatch):
    """The route serves one line per component and REDACTS the reason at the boundary -- a
    transport error string that echoed a URL with userinfo is stripped before it is served."""
    from types import SimpleNamespace

    from hyperset.transport import http as http_module

    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    canned = [
        SimpleNamespace(
            component="openai",
            status="blocked",
            configured=True,
            reachable=False,
            reason="could not reach https://user:token@gateway.example/v1",
            impact="i",
            recovery="r",
        )
    ]
    monkeypatch.setattr(http_module, "probe_providers", lambda *a, **k: canned)
    status, payload = _get(base_url, "/admin/api/v0/providers/probe")
    assert status == 200
    (provider,) = payload["providers"]
    assert provider["component"] == "openai" and provider["status"] == "blocked"
    # The userinfo in the reason is stripped at the serving boundary.
    assert "token" not in provider["reason"]
    assert provider["reason"] == "could not reach https://gateway.example/v1"


def _obj(**fields):
    from types import SimpleNamespace

    ns = SimpleNamespace(**fields)
    ns.as_dict = lambda: dict(fields)
    return ns


def test_admin_diagnostics_classifies_live_signals_into_the_five_classes_and_counts(
    base_url, monkeypatch
):
    """hy-bue7r: the maintainer view classifies readiness + observed-status + live provider
    probes into the named failure classes and counts them, with free text redacted."""
    from hyperset.transport import http as http_module

    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setattr(
        http_module,
        "admin_readiness",
        lambda sf: {
            "components": [
                {
                    "component": "git_context",
                    "status": "degraded",
                    "detail": "stale",
                    "recovery": "sync",
                }
            ]
        },
    )
    monkeypatch.setattr(
        http_module,
        "read_observed_source_status",
        lambda sf, workspace=None: [
            _obj(
                display_name="Prod",
                status="blocked",
                reachable=False,
                fresh=False,
                reason="could not reach https://user:tok@superset.internal",
                recovery="check url",
            ),
        ],
    )
    monkeypatch.setattr(
        http_module,
        "probe_providers",
        lambda: [
            _obj(
                component="openai",
                status="blocked",
                configured=True,
                reason="401",
                recovery="set key",
            )
        ],
    )
    status, payload = _get(base_url, "/admin/api/v0/diagnostics")
    assert status == 200
    by_subject = {row["subject"]: row for row in payload["diagnostics"]}
    assert by_subject["git_context"]["class"] == "stale_context"
    assert by_subject["Prod"]["class"] == "connector_outage"
    assert by_subject["openai"]["class"] == "missing_model"
    assert payload["counts"]["connector_outage"] == 1
    assert payload["counts"]["missing_model"] == 1
    assert payload["counts"]["stale_context"] == 1
    # Free text is redacted at the boundary -- the userinfo in the reason is stripped.
    assert "tok@" not in by_subject["Prod"]["detail"]
    assert by_subject["Prod"]["detail"] == "could not reach https://superset.internal"


def test_admin_diagnostics_is_configure_gated_when_authz_is_on(base_url, monkeypatch):
    from hyperset.transport import http as http_module

    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    monkeypatch.setattr(http_module, "principal_from_bearer", lambda header: None)
    monkeypatch.setattr(
        http_module,
        "admin_readiness",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("gate leaked")),
    )
    status, payload = _get(base_url, "/admin/api/v0/diagnostics")
    assert status == 400
    assert payload["error"]["code"] == "unauthorized"


def test_admin_diagnostics_is_admin_surface_only(base_url, monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "true")
    status, _ = _get_raw(base_url, "/playground/api/v0/diagnostics")
    assert status == 404
