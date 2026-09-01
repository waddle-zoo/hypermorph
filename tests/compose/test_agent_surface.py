"""The served surface, in the stack that ships it (hy-oih, hy-x7f).

The parity and behaviour proofs live in `tests/postgres` and `tests/unit`;
what only the platform can prove is that the `api` service actually starts,
answers its healthcheck, and serves every operation from the container --
and that MCP is reachable in the same stack without a port.
"""

import json
import subprocess
import time
import urllib.request

import pytest

from tests.compose.conftest import REPO_ROOT, mcp_tools_list_response

# The one-shot stdio `mcp` container can exit 0 before it emits the tools/list line when the
# host is under load (hy-l614s); retry the whole handshake a few times before failing.
_STDIO_HANDSHAKE_ATTEMPTS = 3

QUESTION = "Which source and rules should an analyst use for recognized revenue by region?"
# Carries the coverage claim because a domain without one is now a malformed
# directive refused with `invalid_params` (hy-bdff), and this stack is testing
# the served shape rather than that refusal. Nothing is configured here, so
# the claim is never checked against anything -- `no_context_source` refuses
# first.
DIRECTIVE = {"domains": ["revenue"], "concepts": ["recognized_revenue"]}


def _compose(*args, env, timeout=300):
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def image(compose_env):
    """Build once, explicitly. `api` and `mcp` share the one Hyperset image,
    and compose reuses a cached tag: without this the stack would serve
    whatever code was current the last time someone built it."""
    built = _compose("build", "api", env=compose_env, timeout=600)
    assert built.returncode == 0, built.stderr
    return built


@pytest.fixture(scope="module")
def api_base_url(compose_env, image):
    env = dict(compose_env)
    env["HYPERSET_API_PORT"] = "0"  # let the OS pick a free host port
    up = _compose("up", "-d", "--wait", "api", env=env)
    assert up.returncode == 0, up.stderr

    port = _compose("port", "api", "8080", env=env)
    assert port.returncode == 0, port.stderr
    try:
        yield f"http://127.0.0.1:{port.stdout.strip().rsplit(':', 1)[1]}"
    finally:
        _compose("stop", "api", env=env)


def _post(base_url, operation, params):
    request = urllib.request.Request(
        f"{base_url}/v0/{operation}",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


@pytest.mark.compose
def test_the_api_service_serves_the_catalog(api_base_url):
    """The first call an agent makes: what exists. Empty in this stack -- no
    context repository and no connection is configured -- and an empty
    catalog is an answer, not an error."""
    catalog = _post(api_base_url, "list_context_catalog", {})

    assert catalog["domains"] == []
    assert catalog["observed"] == []


@pytest.mark.compose
def test_the_api_service_serves_discovery(api_base_url):
    """Discover is served from the container. This stack has no context, so
    there is nothing to rank and no embedding backend is needed: an empty
    candidate list is a valid answer, the same way the empty catalog is."""
    result = _post(api_base_url, "discover_analytics_context", {"query": QUESTION})

    assert result["query"] == QUESTION
    assert result["candidates"] == []
    assert "schema_version" in result


@pytest.mark.compose
def test_the_api_service_serves_a_bundle(api_base_url):
    bundle = _post(
        api_base_url, "resolve_analytics_context", {"query": QUESTION, "directive": DIRECTIVE}
    )

    # No context repository is configured in this stack, and saying so is a
    # valid answer -- the shape is the point here, not the verdict.
    assert bundle["resolution"]["status"] == "no_match"
    assert bundle["execution"] == {
        "performed_by_hyperset": False,
        "result_validated_by_hyperset": False,
    }


@pytest.mark.compose
def test_the_api_service_serves_plan_validation(api_base_url):
    bundle = _post(
        api_base_url, "resolve_analytics_context", {"query": QUESTION, "directive": DIRECTIVE}
    )
    result = _post(
        api_base_url,
        "validate_analytics_plan",
        {
            "query": QUESTION,
            "directive": DIRECTIVE,
            "bundle_id": bundle["bundle_id"],
            "source_refs": ["superset:dataset:whatever"],
        },
    )

    # Nothing is governed here, so nothing about the plan is approved.
    assert result["status"] == "unverifiable"
    assert [violation["code"] for violation in result["violations"]] == ["no_governed_context"]


@pytest.mark.compose
def test_mcp_is_reachable_in_the_same_stack_on_stdio(compose_env, image):
    # The official SDK stdio server requires the MCP handshake before it will
    # list tools: initialize, the initialized notification, then tools/list.
    handshake = [
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "compose-test", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ]
    payload = "".join(json.dumps(message) + "\n" for message in handshake)
    # Readiness wait (hy-l614s): the one-shot `mcp` container can exit 0 before it flushes the
    # tools/list line under host load, so retry the handshake until it responds. Only a MISSING
    # response (or a container that failed to start) is retried; a present-but-wrong surface
    # returns immediately, so the surface assertion below still catches a real drift (hy-cdo6)
    # deterministically rather than being masked by the retry.
    attempts: list[subprocess.CompletedProcess] = []
    tools_response = None
    for attempt in range(_STDIO_HANDSHAKE_ATTEMPTS):
        if attempt:
            time.sleep(2)  # let the host/container settle before re-running the one-shot
        call = subprocess.run(
            ["docker", "compose", "run", "--rm", "-T", "mcp"],
            cwd=REPO_ROOT,
            env=compose_env,
            input=payload,
            capture_output=True,
            text=True,
            timeout=300,
        )
        attempts.append(call)
        if call.returncode != 0:
            continue  # the one-shot container failed to start under load; retry
        tools_response = mcp_tools_list_response(call.stdout)
        if tools_response is not None:
            break

    last = attempts[-1]
    # Diagnosable, not an opaque StopIteration: a tools/list that never arrived surfaces the
    # exit code and BOTH captured streams so the flake can be read rather than guessed at.
    assert tools_response is not None, (
        f"the MCP stdio handshake returned no tools/list (id=1) response in {len(attempts)} "
        f"attempt(s); the one-shot `mcp` container may be slow to respond under host load.\n"
        f"last returncode: {last.returncode}\n--- stdout ---\n{last.stdout}\n"
        f"--- stderr ---\n{last.stderr}"
    )
    tools = tools_response["result"]["tools"]
    # The served surface in `hyperset.transport.operations.OPERATIONS` order, spelled out as an
    # INDEPENDENT oracle (not imported) so this end-to-end test catches a container that serves
    # a different surface than intended -- catalog, discover, resolve, validate, then
    # expand_analytics_context (bounded governed NAVIGATION, #230 slice 4; a sanctioned served
    # op per docs/v0-foundation.md section 7, navigation-class and tools_hash-neutral), then
    # search_knowledge (grep-MVP, #500; also served, non-authoritative, tools_hash-neutral) at
    # index 5, the two trace-linked feedback ops, then the six review ops. hy-cdo6 corrected
    # this list once (it omitted expand at
    # index 4); the search_knowledge landing (#500) likewise added an op this oracle had not
    # caught up to.
    assert [tool["name"] for tool in tools] == [
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
    ]


@pytest.fixture(scope="module")
def mcp_http_url(compose_env, image):
    """The hosted MCP Streamable HTTP endpoint, on a PUBLISHED host port, from
    the compose stack alone -- no host-side `serve mcp` (hy-3kpk)."""
    env = dict(compose_env)
    env["HYPERSET_MCP_HTTP_PORT"] = "0"  # let the OS pick a free host port
    up = _compose("up", "-d", "--wait", "mcp-http", env=env)
    assert up.returncode == 0, up.stderr

    port = _compose("port", "mcp-http", "8010", env=env)
    assert port.returncode == 0, port.stderr
    container = _compose("ps", "-q", "mcp-http", env=env).stdout.strip()
    host_port = port.stdout.strip().rsplit(":", 1)[1]
    try:
        yield f"http://127.0.0.1:{host_port}/mcp"
    finally:
        # Raw `docker stop` + `docker rm` by container id, NOT `compose
        # stop/rm/down`: a published-port container removed as part of a compose
        # operation can leave a userland-proxy endpoint on the shared network,
        # so a later module's `docker compose down` fails to remove it ("has
        # active endpoints"). Stopping and removing the container on its own --
        # separated from any network removal -- releases the endpoint cleanly.
        if container:
            subprocess.run(["docker", "stop", container], capture_output=True)
            subprocess.run(["docker", "rm", container], capture_output=True)


@pytest.mark.compose
def test_mcp_over_http_answers_the_handshake_on_the_published_port(mcp_http_url):
    """The demo pillar: a fresh MCP client completes the Streamable HTTP
    handshake (initialize, initialized, tools/list) against the PUBLISHED port
    from OUTSIDE the container, and gets the served tool surface (hy-3kpk).

    The expected list is derived from OPERATIONS rather than hard-coded, so it
    tracks the served surface as it grows (e.g. the review ops) rather than
    pinning a count this packaging change does not own."""
    import anyio

    from hyperset.transport.operations import OPERATIONS

    async def _list_tools():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with streamable_http_client(mcp_http_url) as (read, write, _):
            async with ClientSession(read, write) as client:
                await client.initialize()
                return await client.list_tools()

    listed = anyio.run(_list_tools)
    assert [tool.name for tool in listed.tools] == list(OPERATIONS)
