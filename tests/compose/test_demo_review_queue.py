"""The demo's producer end, in the stack that ships it (hy-y1ng8).

Opt-in: needs an already-running demo stack (`make up-demo`) and
`HYPERSET_COMPOSE_DEMO=1`. Booting the pinned Superset takes 2-3 minutes, so
this runs against a stack an operator (or the mayor's Docker verify) already
brought up, never per push -- same scope as the other demo-gated suites.

`tests/postgres/test_demo_processor_finding.py` proves the SEQUENCE (observe ->
corroborate -> drift -> real processor -> Finding + ReviewTask) in Postgres with
no Docker. What only the platform can prove is that a clean `make up-demo`
actually ran that sequence end to end and left the human review queue populated:
this POSTs the SERVED `list_review_tasks` on the running `api` service and sees
the processor-created task, the row the review UI and MCP clients read.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path

import pytest

from tests.compose.conftest import require_healthy_service

REPO_ROOT = Path(__file__).resolve().parents[2]


def _compose(*args: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.fixture(scope="module")
def demo_api_url():
    if os.environ.get("HYPERSET_COMPOSE_DEMO") != "1":
        pytest.skip("set HYPERSET_COMPOSE_DEMO=1 with a running `make up-demo` stack")

    env = os.environ.copy()
    dotenv = REPO_ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            if line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env.setdefault(key.strip(), value.strip())

    # The api runs under the demo profile, so a plain `ps` hides it.
    listing = _compose("--profile", "demo", "ps", "--format", "json", env=env)
    require_healthy_service(
        listing,
        "api",
        "the demo api is not healthy; start it with `make up-demo`",
    )
    port = _compose("--profile", "demo", "port", "api", "8080", env=env)
    assert port.returncode == 0, port.stderr
    host_port = port.stdout.strip().rsplit(":", 1)[1]
    return f"http://127.0.0.1:{host_port}"


def _post(base_url: str, operation: str, params: dict) -> dict:
    request = urllib.request.Request(
        f"{base_url}/v0/{operation}",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


@pytest.mark.compose
def test_up_demo_leaves_a_real_processor_task_on_the_review_queue(demo_api_url):
    result = _post(demo_api_url, "list_review_tasks", {})
    tasks = result["tasks"]
    assert tasks, "make up-demo must leave the real processor's ReviewTask on the queue"

    # The task the demo's controlled drift produced: the approved-expression-drift
    # finding on recognized_revenue, whose reason names both expressions. This is
    # the processor's own explanation, not a fixture string -- a seeded row would
    # not carry it.
    drift_tasks = [t for t in tasks if "recognized_revenue" in t["reason"]]
    assert drift_tasks, (
        "the review queue must carry the recognized_revenue drift task the real processor "
        f"opened; got reasons {[t['reason'][:80] for t in tasks]}"
    )
    (task,) = drift_tasks
    assert "SUM(gross_amount - tax_amount)" in task["reason"]
    assert "SUM(gross_amount)" in task["reason"]
