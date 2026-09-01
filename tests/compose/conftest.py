"""Docker Compose platform tests (hy-gh-37).

Uses a `COMPOSE_PROJECT_NAME` that is distinct PER RUN, which separates two
different things that were once conflated (hy-xjch).

DEV VS TEST. The prefix keeps these tests off a developer's own already-running
`docker compose up` stack and its volumes. That was the original reason for a
distinct name and it is still needed.

SEAT VS SEAT, which the original constant did not address and which this rig
produces by design. Every seat on this box shares one Docker daemon, so a name
fixed at import time put two concurrent runs in the SAME compose project, each
tearing down the other's containers mid-assertion. Measured on 2026-08-01 across
two branches, neither of which touched compose: three runs of one tree gave
three different answers, and the signatures were `removal of container ... is
already in progress` and `failed to remove network ... has active endpoints`.

What makes that worse than a flake is that it is invisible from the seat that
pays for it. The error names a network or a container id, never the other run,
so nothing in a seat's own output, tree, or history distinguishes "my change
broke this" from "someone else's `down` ran". A clean-base control fails the
same way, which reads as exculpatory and is not: the other seat explains both
rows, so the pair says nothing about the diff either way. The cost is
misattribution in both directions, and the expensive direction is a real compose
regression waved through as "probably the other seat".

Uniqueness is therefore load-bearing, not hygiene, and `_project_name` is unique
per CALL rather than per repo or per branch. A name derived from anything stable
for a checkout -- the path, the branch, the tree oid -- would read as "not a
constant" while still colliding, because the colliding seats are in the same
repo at nearly the same commit. That is exactly the condition that produced
every run above.

WHAT THIS COSTS, stated rather than discovered later (hy-n5b0). The old shared
name meant a run killed before teardown was cleaned up by the NEXT run, since
`down -v` under one name reaped whatever was there. Leakage was self-limiting
for the same reason the suite was unreliable. It is not any more: a run that
never reaches teardown leaks a project nothing will name again. That is disk
rather than correctness, and the reap that would fix it is only safe on AGE --
"not my project" is not "abandoned" while another seat is running.

Skips the whole directory if Docker isn't reachable, same pattern as
`tests/postgres/conftest.py`.

The full demo profile (real pinned Superset 6.1.0: migrations, admin
creation, roles, webserver boot) takes ~2-3 minutes end to end -- too slow
to run on every CI push. It has been verified manually end to end
(`make up-demo`, idempotent re-bootstrap, real export generation fed back
through the hy-gh-27 connector, restart-survives-with-state, `make reset`
destroys) -- see the hy-gh-37 completion notes. `#36` (CI/release gates)
owns wiring a scheduled or pre-release job for this; only the fast core
stack (`postgres` + `hyperset-migrate`, no Superset) is exercised here on
every run.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from collections.abc import Mapping
from pathlib import Path

import pytest

from tests.docker_probe import _docker_available

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PROJECT_PREFIX = "hyperset-compose-test"


def _seat() -> str:
    """The current seat, as a compose-safe fragment, or empty off the rig.

    Attribution rather than separation: `uuid4` alone already cannot collide.
    A container named for the seat that owns it turns "which run is holding
    this network" from an investigation into a read, which is the cost hy-xjch
    is actually about. Empty is fine and expected on a laptop or in CI.
    """
    seat = os.environ.get("BD_ACTOR", "").rsplit("/", 1)[-1]
    return re.sub(r"[^a-z0-9]+", "-", seat.lower()).strip("-")


def _project_name() -> str:
    """A compose project nobody else is in, generated per call.

    Per CALL is the requirement. See the module docstring: a name that is stable
    for a checkout satisfies "not a constant" and collides anyway, because the
    seats that collide share a repo and a commit.
    """
    parts = (COMPOSE_PROJECT_PREFIX, _seat(), uuid.uuid4().hex[:8])
    return "-".join(part for part in parts if part)


def compose_environment() -> dict[str, str]:
    """The environment every compose invocation in this directory runs under.

    Named separately for readability, NOT because the fixture would otherwise be
    ungateable -- the fixture is driven without Docker directly, via its own
    `__wrapped__` generator with the daemon probe stubbed (hy-7j2t corrects an
    earlier claim that the split was what enabled the gate). What the uniqueness
    gate must read is what the compose tests actually run under, which is the
    `compose_env` FIXTURE's yielded env, not this function called on its own:
    testing `_project_name` -- or this function -- alone would pass just as well
    with the fixture pinning a constant, the vacuous version of the check.
    """
    env = os.environ.copy()
    # ONE per-run identity drives both the project name (namespaces containers/networks/
    # volumes) AND the image tag (hy-8uw0). An image TAG is global to the Docker daemon, so a
    # fixed `hyperset/hyperset:dev` lets a concurrent seat/checkout retag it out from under this
    # run and serve another tree's code silently. Keyed to the SAME project name, the tag is
    # unique per run and stable within it; the project name is already a valid docker tag
    # (lowercase alnum + hyphens). `make up-demo` sets neither and keeps the `:dev` default.
    project = _project_name()
    env["COMPOSE_PROJECT_NAME"] = project
    env["HYPERSET_IMAGE_TAG"] = project
    env.setdefault("HYPERSET_DB_PASSWORD", "test-only")
    env.setdefault("SUPERSET_DB_PASSWORD", "test-only")
    env.setdefault("ANALYTICS_DB_PASSWORD", "test-only")
    env.setdefault("SUPERSET_SECRET_KEY", "test-only-not-a-real-secret")
    env.setdefault("SUPERSET_ADMIN_PASSWORD", "test-only")
    env.setdefault("DATAHUB_MYSQL_PASSWORD", "test-only")
    env.setdefault("DATAHUB_TOKEN_SERVICE_SIGNING_KEY", "test-only-not-a-real-secret")
    env.setdefault("DATAHUB_TOKEN_SERVICE_SALT", "test-only-not-a-real-salt")
    # Neutralise EVERY published host port to an OS-assigned ephemeral one. Unique
    # project names (hy-xjch) separate the networks but NOT the host ports -- two
    # worktree suites binding the same fixed `ports:` host port still collide, which
    # is the residual hy-k9yw left and this closes (hy-l09x9). `0` lets the kernel
    # pick a free port, distinct per run, so concurrent runs never overlap; the tests
    # read the actual port back with `docker compose port`.
    #
    # FORCED, not `setdefault`: these vars have real fixed values in a developer's
    # `.env` (55432, 8088, 8000, 8090) for `make up-demo`, and a `setdefault` would
    # let an inherited one through and re-open the exact collision -- the suite would
    # bind the fixed port and defeat its own isolation. The fixture always publishes
    # ephemerally; a demo stack that wants fixed ports is `make up-demo`, not this. One
    # place owns it, and `test_compose_host_ports_are_isolated_for_parallel_runs` ties
    # it to docker-compose.yml so a newly published port cannot skip it.
    for host_port_var in (
        "HYPERSET_DB_PORT",
        "HYPERSET_API_PORT",
        "HYPERSET_MCP_HTTP_PORT",
        "ANALYTICS_DB_PORT_HOST",
        "SUPERSET_PORT_HOST",
        "DATAHUB_GMS_PORT_HOST",
    ):
        env[host_port_var] = "0"
    return env


def require_healthy_service(listing: subprocess.CompletedProcess, service: str, hint: str) -> None:
    """Skip unless `service` is healthy in a `docker compose ps --format json`.

    The return code is read BEFORE the entries are, and that order is the
    whole point (hy-9vb3). A `ps` that could not run -- daemon gone, unknown
    profile, a compose that spells the format flag differently -- writes
    nothing to stdout, and an unread rc turns that into an empty entry list,
    which is byte-for-byte what a stack that is simply not up produces. The
    module then skips, the session is green, and nothing anywhere says the
    probe was blind.

    So the two are separated: a stack that is genuinely absent must still
    SKIP, and only a probe that could not look may fail. Checking the health
    first and the rc afterwards would rebuild the defect, because the skip
    leaves before the check.
    """
    assert listing.returncode == 0, (
        f"`docker compose ps` exited {listing.returncode}, so this probe could not look; "
        f'an unread return code would report that as "{service} is not running" and skip '
        f"green:\n{listing.stderr}"
    )

    entries: list[dict] = []
    for line in listing.stdout.splitlines():
        if not line.strip():
            continue
        parsed = json.loads(line)
        entries.extend(parsed if isinstance(parsed, list) else [parsed])
    if not any(e.get("Service") == service and e.get("Health") == "healthy" for e in entries):
        pytest.skip(hint)


def mcp_tools_list_response(stdout: str) -> dict | None:
    """The `tools/list` (id == 1) response from an MCP stdio run's stdout, or None when it is
    not there yet (hy-l614s).

    The one-shot `docker compose run --rm -T mcp` container can, under host load, exit 0
    before it has emitted the tools/list line, so the response is simply ABSENT. Returning
    None -- rather than letting `next(...)` raise an opaque StopIteration -- lets the caller
    RETRY the handshake and, on final failure, assert with the captured stdout/stderr so the
    flake is DIAGNOSABLE instead of an unlabelled StopIteration. A torn line (the container
    killed mid-flush) is skipped, not fatal, so it cannot mask the readiness signal; the raw
    stdout is still in the caller's diagnostic. A present-but-WRONG surface still returns (so
    the caller's surface assertion catches a real drift deterministically, never retried)."""
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            response = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(response, dict) and response.get("id") == 1:
            return response
    return None


LIVE_SUITES = {
    "tests/compose/test_superset_live_sync.py": ("HYPERSET_COMPOSE_DEMO", "make up-demo"),
    "tests/compose/test_demo_review_queue.py": ("HYPERSET_COMPOSE_DEMO", "make up-demo"),
    "tests/compose/test_datahub_live_sync.py": ("HYPERSET_COMPOSE_DATAHUB", "make up-datahub"),
}
"""Each live module, and the variable whose value `1` claims its stack is up.

THE ARMING VARIABLE IS THE MODULE'S OWN OPT-IN, and that is the decision this
mechanism turns on (hy-kaud). `tests/evals/conftest.py` needed a separate
`HYPERSET_REQUIRE_LIVE` because its arms have no opt-in of their own: they run
whenever Ollama answers, so nothing in the environment distinguished "no server
today" from "the scheduled job that exists to exercise the server". These
modules already carry that statement. `HYPERSET_COMPOSE_DEMO=1` is not a
request to try -- the module docstring spells it "needs an already-running demo
stack (`make up-demo`)" -- so a session that sets it and completes no arm has
been told the stack is up and found otherwise.

A NEW VARIABLE WOULD HAVE BEEN THE SAME GREEN AS NO BACKSTOP. Measured before
choosing: `HYPERSET_REQUIRE_LIVE` is set in exactly one place in this
repository, `.github/workflows/benchmark-live.yml:78`, and that job runs
`tests/evals`. Nothing anywhere runs the compose live suites -- both variables
above appear only in the modules that read them and in the docs describing
them. So a backstop armed by a third variable would be armed by nobody, and
would report the green it was added to prevent. Reusing `HYPERSET_REQUIRE_LIVE`
here would be no better and slightly worse: it is neither necessary (the
per-stack variable is already required for a single arm to run at all) nor
sufficient (setting it alone still skips everything at the first gate), so it
would be a second name for a condition that is already decided."""


def stalled_live_suites(
    collected: set[str], completed: set[str], environ: Mapping[str, str]
) -> list[str]:
    """The live modules this session claimed were up and then never ran.

    Split out of the hook so the verdict can be checked directly against every
    combination, including the ones a subprocess cannot stage: an arm that
    genuinely passed needs a real stack, and a test that could only be written
    with the stack up would not run in the environment where this backstop
    matters.

    `collected` is a precondition and not a formality. Without it,
    `pytest tests/compose/test_core_stack.py` in a shell that exports
    `HYPERSET_COMPOSE_DEMO=1` -- the shell of somebody who does have the demo
    stack up -- goes red over a module the session never selected. A session
    that did not collect a module is not evidence about it either way.

    BOUND, in the same spirit: a caller who deselects the arms with `-k`, or
    excludes the module with `--ignore`, produces an empty `collected` and no
    finding. That is the same hole `scripts/gate.py` documents for a
    collection-time exclusion, and it has the same answer -- the narrowing is
    the caller's own explicit act, so it is disclosed rather than defended
    against.
    """
    return sorted(
        module
        for module in collected
        if module not in completed and environ.get(LIVE_SUITES[module][0]) == "1"
    )


_collected: set[str] = set()
_completed: set[str] = set()


def _live_module(nodeid: str) -> str | None:
    return next((module for module in LIVE_SUITES if nodeid.startswith(module + "::")), None)


def pytest_collection_modifyitems(items) -> None:
    for item in items:
        module = _live_module(item.nodeid)
        if module:
            _collected.add(module)


def pytest_runtest_logreport(report) -> None:
    """An arm COMPLETED, which is a narrower subject than "the stack was up".

    Counted the way `tests/evals/conftest.py` counts, and for its reason: there
    is more than one way to run nothing here -- no Docker, an unset opt-in, an
    unhealthy service, a `ps` that could not look -- and each is a skip raised
    somewhere different. What the caller needs is not a health verdict but one
    number, and it is this one.
    """
    if report.when == "call" and report.passed:
        module = _live_module(report.nodeid)
        if module:
            _completed.add(module)


def pytest_sessionfinish(session, exitstatus) -> None:
    """Turn an all-skipped live session red, when the caller said the stack was up.

    Not on a session that already failed: `exitstatus != 0` means something
    else is being reported and this would only overwrite the reason. Not on
    `--collect-only` either, which runs nothing by design and would otherwise
    be red for doing exactly what it was asked to do.
    """
    if exitstatus != 0 or session.config.getoption("collectonly", False):
        return
    stalled = stalled_live_suites(_collected, _completed, os.environ)
    if not stalled:
        return
    for module in stalled:
        variable, target = LIVE_SUITES[module]
        print(
            f"\nNO LIVE ARM RAN in {module} and {variable}=1: every case was skipped, so this "
            f"session is evidence about nothing. {variable}=1 states that `{target}` is already "
            "running -- it is not a request to try -- so a green here would report the stack as "
            "exercised when nothing reached it (hy-kaud)."
        )
    session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(scope="session")
def compose_env():
    if not _docker_available():
        pytest.skip("Docker is not available; skipping compose platform tests")

    env = compose_environment()
    yield env

    subprocess.run(["docker", "compose", "down", "-v"], cwd=REPO_ROOT, env=env, capture_output=True)
