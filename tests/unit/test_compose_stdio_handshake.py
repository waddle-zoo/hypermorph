"""The MCP stdio handshake extraction must fail DIAGNOSABLY, not with an opaque StopIteration.

hy-l614s: `tests/compose/test_agent_surface.py::test_mcp_is_reachable_..._on_stdio` extracted
the tools/list response with `next(r for r in responses if r.get("id") == 1)`, which raises a
bare StopIteration when the one-shot `mcp` container exits before emitting that line under host
load. `mcp_tools_list_response` returns None on absence instead, so the caller can retry and
then assert with the captured streams. These deterministic arms (no Docker) pin that contract.
"""

import json

from tests.compose.conftest import mcp_tools_list_response

_TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "list_context_catalog"}]}}
_INIT = {"jsonrpc": "2.0", "id": 0, "result": {}}


def _stdout(*messages):
    return "".join(json.dumps(message) + "\n" for message in messages)


def test_returns_the_id_1_tools_list_response_when_present():
    response = mcp_tools_list_response(_stdout(_INIT, _TOOLS_LIST))
    assert response == _TOOLS_LIST  # picks id==1, not the id==0 initialize response


def test_returns_none_when_the_tools_list_line_is_absent():
    # The flake shape: the container exited after `initialize` but before `tools/list`. The
    # OLD `next(...)` raised StopIteration here; the helper returns None so the caller can
    # retry and then surface stdout/stderr (this arm would ERROR under the old code).
    assert mcp_tools_list_response(_stdout(_INIT)) is None
    assert mcp_tools_list_response("") is None


def test_a_torn_json_line_is_skipped_not_fatal():
    # A container killed mid-flush can leave a truncated line; it must not mask a valid
    # tools/list that follows, and (when there is none) must not raise -- the raw stdout is
    # still available to the caller's diagnostic.
    torn = '{"jsonrpc": "2.0", "id": 1, "resu'  # truncated
    assert mcp_tools_list_response(torn + "\n" + json.dumps(_TOOLS_LIST) + "\n") == _TOOLS_LIST
    assert mcp_tools_list_response(torn + "\n") is None


def test_a_non_dict_json_line_does_not_match():
    # A bare JSON array/scalar on a line is not a response object and must not be mistaken
    # for the id==1 response.
    assert mcp_tools_list_response("[1, 2, 3]\n") is None
    assert mcp_tools_list_response(_stdout(_INIT) + "42\n") is None
