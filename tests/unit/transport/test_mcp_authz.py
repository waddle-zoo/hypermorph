"""The MCP transport's authorization threading (hy-lrho, ADR-0030). stdio is the
local trusted reader; the hosted transport verifies the request's bearer via a
contextvar the handler binds. Default-off, the gate no-ops and the bytes are
unchanged; enabled, a hosted call with no valid bearer is denied and stdio is allowed.
"""

from __future__ import annotations

import anyio

from hyperset.security.authz import SYSTEM_PRINCIPAL, Principal
from hyperset.transport import mcp
from hyperset.transport.mcp import (
    _bind_request_authorization,
    _hosted_principal,
    _local_principal,
    _request_authorization,
    build_mcp_server,
)
from tests.unit.transport.conftest import DIRECTIVE, QUESTION, governed_bundle


def _drive(session_factory, work, *, principal_resolver=None):
    async def _run():
        from mcp.shared.memory import create_connected_server_and_client_session as connect

        kwargs = {} if principal_resolver is None else {"principal_resolver": principal_resolver}
        server = build_mcp_server(session_factory=session_factory, **kwargs)
        async with connect(server) as client:
            return await work(client)

    return anyio.run(_run)


def _resolve(client):
    return client.call_tool(
        "resolve_analytics_context", {"query": QUESTION, "directive": DIRECTIVE}
    )


# --- the two resolvers ---


def test_stdio_is_the_trusted_local_reader():
    assert _local_principal() is SYSTEM_PRINCIPAL


def test_the_hosted_principal_reads_the_bound_bearer(monkeypatch):
    # `_hosted_principal` turns the bound Authorization header into a principal via the
    # one audited verifier. Stub the verifier to prove the WIRING (the header reaches
    # it); the verifier itself is covered in test_oidc.py.
    seen = {}
    reader = Principal(subject="u", issuer="https://issuer.example/", roles=("reader",))

    def _stub(authorization):
        seen["authorization"] = authorization
        return reader

    monkeypatch.setattr(mcp, "principal_from_bearer", _stub)
    token = _request_authorization.set("Bearer abc.def.ghi")
    try:
        assert _hosted_principal() is reader
        assert seen["authorization"] == "Bearer abc.def.ghi"
    finally:
        _request_authorization.reset(token)


def test_the_hosted_principal_is_none_with_no_bound_request():
    # No request bound -> the contextvar default -> no principal (gate denies when on).
    assert _request_authorization.get() is None
    assert _hosted_principal() is None


# --- the scope binding (the hosted handler's header capture) ---


def test_binding_sets_the_bearer_for_the_request_and_resets_after():
    scope = {"type": "http", "headers": [(b"authorization", b"Bearer xyz"), (b"accept", b"*/*")]}
    assert _request_authorization.get() is None
    with _bind_request_authorization(scope):
        assert _request_authorization.get() == "Bearer xyz"
    assert _request_authorization.get() is None


def test_binding_sets_none_when_no_authorization_header():
    with _bind_request_authorization({"type": "http", "headers": [(b"accept", b"*/*")]}):
        assert _request_authorization.get() is None


def test_a_non_http_scope_binds_nothing():
    token = _request_authorization.set("Bearer pre-existing")
    try:
        with _bind_request_authorization({"type": "lifespan"}):
            # A lifespan/websocket scope must not clobber or set the request identity.
            assert _request_authorization.get() == "Bearer pre-existing"
    finally:
        _request_authorization.reset(token)


# --- end to end over a real in-memory MCP client ---


def test_disabled_default_a_tool_result_is_unchanged(resolved, session_factory):
    # Flag off: the resolver value is ignored and the bytes are the governed bundle.
    result = _drive(session_factory, _resolve)
    assert result.isError is False
    assert result.structuredContent == governed_bundle().to_dict()


def test_enabled_stdio_local_reader_is_authorized(resolved, session_factory, monkeypatch):
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    result = _drive(session_factory, _resolve, principal_resolver=_local_principal)
    assert result.isError is False
    assert result.structuredContent == governed_bundle().to_dict()


def test_enabled_hosted_without_a_bearer_is_denied(resolved, session_factory, monkeypatch):
    # Default resolver + no bound request -> principal None -> fail-closed denial, and
    # the resolver never ran (deny-the-whole).
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    result = _drive(session_factory, _resolve)
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "unauthorized"
    assert resolved == []


def test_enabled_hosted_with_a_verified_reader_is_authorized(
    resolved, session_factory, monkeypatch
):
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    reader = Principal(subject="u", issuer="https://issuer.example/", roles=("reader",))
    result = _drive(session_factory, _resolve, principal_resolver=lambda: reader)
    assert result.isError is False
    assert result.structuredContent == governed_bundle().to_dict()


def test_a_malformed_allowlist_denies_a_reviewer_over_mcp(session_factory, monkeypatch, tmp_path):
    # hy-a607k (#456 adversary): the approved-reviewer allowlist gates the MCP transport too
    # (one shared executor). A reviewer whose IdP identity is otherwise valid is DENIED a
    # review op when the policy is malformed (fail-closed), so the error is `unauthorized`
    # (the gate), not the `invalid_request` a good policy would let it reach.
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    policy = tmp_path / "policy.allow"
    # A credential-shaped (colon-bearing) entry with a real https issuer -> rejected ->
    # whole policy fails closed.
    policy.write_text(
        "good@https://iss\nuser:supersecret@https://issuer.example\n", encoding="utf-8"
    )
    monkeypatch.setenv("HYPERSET_REVIEWER_ALLOWLIST", str(policy))
    reviewer = Principal(subject="good", issuer="https://iss", roles=("reviewer",))

    def _edit(client):
        return client.call_tool("edit_review_draft", {"task_id": "rt-x", "definition": {}})

    result = _drive(session_factory, _edit, principal_resolver=lambda: reviewer)
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "unauthorized"
    # The rejected credential-shaped line never leaks in the error.
    assert "supersecret" not in (result.content[0].text if result.content else "")


def test_a_configured_allowlist_admits_a_listed_reviewer_over_mcp(
    session_factory, monkeypatch, tmp_path
):
    # The converse, so the test above is not vacuous: a WELL-FORMED policy listing the
    # reviewer lets it PAST the gate over MCP -- the call then fails DOWNSTREAM (not at
    # authz), so the code is anything but `unauthorized`, proving the allowlist admitted it.
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    policy = tmp_path / "policy.allow"
    policy.write_text("good@https://iss\n", encoding="utf-8")
    monkeypatch.setenv("HYPERSET_REVIEWER_ALLOWLIST", str(policy))
    reviewer = Principal(subject="good", issuer="https://iss", roles=("reviewer",))

    def _edit(client):
        return client.call_tool("edit_review_draft", {"task_id": "rt-x", "definition": {}})

    result = _drive(session_factory, _edit, principal_resolver=lambda: reviewer)
    assert result.structuredContent["error"]["code"] != "unauthorized"


def test_assign_to_an_unlisted_user_is_refused_over_mcp(session_factory, monkeypatch, tmp_path):
    # hy-ip8do over MCP: assigning ANOTHER user validates the target against the known
    # allowlist on the shared executor, so an unlisted (here credential-shaped) target is an
    # isError result whose text never echoes the value. The reject is before any task load,
    # so the stub session is untouched.
    allow = tmp_path / "reviewers.allow"
    allow.write_text("alice@https://issuer.example\n", encoding="utf-8")
    monkeypatch.setenv("HYPERSET_REVIEWER_ALLOWLIST", str(allow))

    def _assign(client):
        return client.call_tool(
            "set_review_assignee",
            {
                "task_id": "rt-x",
                "assigned": True,
                "assignee": "user:supersecret@https://issuer.example",
            },
        )

    result = _drive(session_factory, _assign)
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "invalid_params"
    assert "supersecret" not in (result.content[0].text if result.content else "")
