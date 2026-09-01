"""The playground threads MCP interaction-trace linkage into the backend (hy-z7bsw).

Live Luna answers wrote `mcp_interaction_trace` rows with blank
session_id/turn_id/tool_call_id/intent because the playground's server-side resolve call
carried no linkage headers, and the proxy stripped any the browser set. These assert the
harness now labels its resolve with a session/turn/tool-call/intent, that a client MAY
supply a stable session/turn (else one is minted) and that credential-bearing junk is
dropped, and that the proxy forwards ONLY the audit-linkage headers.
"""

from __future__ import annotations

from hyperset.observability.interaction import (
    CORRELATION_HEADER,
    INTENT_HEADER,
    TOOL_CALL_HEADER,
    TURN_HEADER,
)
from playground.ui import app
from playground.ui.app import (
    _HYPERSET_SESSION_HEADER,
    _playground_trace,
    _resolve_playground_bundle,
    _trace_headers,
)


def test_resolve_sends_the_trace_linkage_headers_to_the_backend(monkeypatch):
    captured = {}

    def fake_http_json(url, *, method="GET", payload=None, timeout=8, headers=None):
        captured["headers"] = headers
        return 200, {"bundle_id": "cb-1", "resolution": {"status": "governed"}}

    monkeypatch.setattr(app, "_http_json", fake_http_json)
    trace = {"session_id": "sess-9", "turn_id": "turn-9", "intent": "why revenue"}

    _resolve_playground_bundle("q", {"domains": ["revenue"]}, trace=trace)

    headers = captured["headers"]
    assert headers[_HYPERSET_SESSION_HEADER] == "sess-9"
    assert headers[TURN_HEADER] == "turn-9"
    assert headers[INTENT_HEADER] == "why revenue"
    # A tool_call_id is minted for THIS call so a single resolve is addressable.
    assert headers[TOOL_CALL_HEADER]


def test_resolve_without_a_trace_sends_no_linkage_headers(monkeypatch):
    captured = {}

    def fake_http_json(url, *, method="GET", payload=None, timeout=8, headers=None):
        captured["headers"] = headers
        return 200, {"bundle_id": "cb-1", "resolution": {"status": "governed"}}

    monkeypatch.setattr(app, "_http_json", fake_http_json)

    _resolve_playground_bundle("q", {"domains": ["revenue"]})

    # No trace -> the call is byte-for-byte the prior behaviour (no x-hyperset-* headers).
    assert captured["headers"] == {}


def test_playground_trace_mints_when_the_browser_supplies_none():
    trace = _playground_trace({"question": "q"}, "recognized revenue")
    assert trace["session_id"]
    assert trace["turn_id"]
    assert trace["intent"] == "recognized revenue"


def test_playground_trace_keeps_a_clean_client_session_and_turn():
    trace = _playground_trace({"session_id": "conv-42", "turn_id": "turn-3"}, "recognized revenue")
    assert trace["session_id"] == "conv-42"
    assert trace["turn_id"] == "turn-3"


def test_playground_trace_drops_a_credential_bearing_client_id():
    # A crafted URL is not an opaque token, so it is dropped and a fresh id is minted rather
    # than forwarded into the durable audit row.
    trace = _playground_trace({"session_id": "https://u:secret@evil/x"}, "recognized revenue")
    assert trace["session_id"] != "https://u:secret@evil/x"
    assert "secret" not in trace["session_id"]


def test_trace_headers_are_empty_without_a_trace():
    assert _trace_headers(None) == {}
    assert _trace_headers({}) == {}


class _FakeHandler:
    """Just enough of UIHandler to exercise the header-forwarding method."""

    def __init__(self, headers: dict) -> None:
        self.headers = headers


def test_the_proxy_forwards_only_the_audit_linkage_headers():
    handler = _FakeHandler(
        {
            _HYPERSET_SESSION_HEADER: "sess-p",
            TURN_HEADER: "turn-p",
            CORRELATION_HEADER: "corr-p",
            INTENT_HEADER: "why",
            "Authorization": "Bearer super-secret",
            "X-Whatever": "nope",
        }
    )
    forwarded = app.UIHandler._forwarded_trace_headers(handler)
    assert forwarded[_HYPERSET_SESSION_HEADER] == "sess-p"
    assert forwarded[TURN_HEADER] == "turn-p"
    assert forwarded[CORRELATION_HEADER] == "corr-p"
    assert forwarded[INTENT_HEADER] == "why"
    # Authorization and arbitrary client headers never cross the proxy.
    assert "Authorization" not in forwarded
    assert "X-Whatever" not in forwarded
    assert "super-secret" not in repr(forwarded)
