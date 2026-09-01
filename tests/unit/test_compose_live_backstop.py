"""The compose live suites must not report green having run nothing (hy-kaud).

The closing arm here is a REAL pytest session over the REAL live modules, not
an assertion that a variable is respected. `HYPERSET_COMPOSE_DEMO=1` with every
arm skipped is the exact state the backstop exists to redden, and staging it
needs no stack: a stub `docker` earlier on `PATH` answers `compose ps` with a
successful, empty listing, which is what a genuinely absent stack produces.

MEASURED AGAINST THE PRE-FIX TREE, by deleting `pytest_sessionfinish` from
`tests/compose/conftest.py`: `7 skipped in 0.14s`, exit 0 -- the two subprocess
arms below go red on that tree and pass on this one. The two negative arms stay
green either way ON PURPOSE, since their subject is the backstop declining to
fire; they are the control, and the first arm is the measurement.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.compose.conftest import LIVE_SUITES, stalled_live_suites

REPO_ROOT = Path(__file__).resolve().parents[2]
SUPERSET = "tests/compose/test_superset_live_sync.py"
DATAHUB = "tests/compose/test_datahub_live_sync.py"

DOCKER_STUB = """#!/usr/bin/env bash
# `compose ps` succeeded and listed nothing, which is a stack that is down --
# not a probe that failed. Anything else this suite shells out to would mean an
# arm got past the health gate, so it is loud rather than silently fine.
if [[ "$1" == "compose" ]]; then
  for arg in "$@"; do
    if [[ "$arg" == "ps" ]]; then exit 0; fi
  done
fi
echo "the stub docker was asked to $*, which no skipped session should need" >&2
exit 90
"""


def _session(tmp_path: Path, **environ: str) -> subprocess.CompletedProcess:
    """A real pytest run over the real live modules, with a stub `docker`."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "docker"
    stub.write_text(DOCKER_STUB)
    stub.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{stub_dir}{os.pathsep}{env['PATH']}"
    for variable, _ in LIVE_SUITES.values():
        env.pop(variable, None)
    env.update(environ)
    return subprocess.run(
        [sys.executable, "-m", "pytest", SUPERSET, DATAHUB, "-q", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_a_session_that_claims_the_stack_is_up_and_runs_nothing_is_red(tmp_path):
    """The arm this bead exists for, and it fails without the backstop.

    Both opt-ins on, so both stacks were claimed; the stub answers every `ps`
    with a successful empty listing, so every arm skips. Before the fix this
    session was `7 skipped`, exit 0.
    """
    result = _session(tmp_path, HYPERSET_COMPOSE_DEMO="1", HYPERSET_COMPOSE_DATAHUB="1")

    assert result.returncode != 0, (
        "every live arm skipped while both opt-ins claimed a running stack, and the session "
        f"still reported success:\n{result.stdout}"
    )
    # The skip is what makes the failure meaningful: a session that went red by
    # ERRORING would satisfy the return code above while proving nothing about
    # a green all-skip, which is the state under test.
    assert "skipped" in result.stdout and " passed" not in result.stdout, result.stdout
    assert "NO LIVE ARM RAN" in result.stdout, result.stdout
    assert SUPERSET in result.stdout and DATAHUB in result.stdout, result.stdout


def test_a_stack_nobody_claimed_still_skips_green(tmp_path):
    """Off where compose is legitimately absent, which is the common case.

    A developer with no stack up sets neither variable, and must get a skip
    rather than a failure they cannot act on -- the same reason
    `tests/evals/conftest.py` leaves its backstop off by default.
    """
    result = _session(tmp_path)

    assert result.returncode == 0, result.stdout
    assert "NO LIVE ARM RAN" not in result.stdout, result.stdout


def test_only_the_stack_the_caller_claimed_is_named(tmp_path):
    """One opt-in is not a claim about the other stack.

    The two stacks come up independently -- `make up-datahub` says nothing about
    Superset -- so a session with one variable set must go red for that stack
    only. A backstop that fired for both would make the second opt-in unusable
    on its own, and reddening a stack nobody claimed is the mirror of the defect
    this fixes.
    """
    result = _session(tmp_path, HYPERSET_COMPOSE_DATAHUB="1")

    assert result.returncode != 0, result.stdout
    assert DATAHUB in result.stdout, result.stdout
    assert SUPERSET not in result.stdout.split("NO LIVE ARM RAN", 1)[1], result.stdout
    assert "make up-datahub" in result.stdout, result.stdout


def test_an_arm_that_completed_is_the_whole_subject():
    """Staged directly, because the honest version of it needs a live stack.

    A subprocess cannot produce a passing live arm without `make up-demo`
    actually running, and a check that only exists where the stack is up is not
    a check on the machines this backstop protects.
    """
    assert stalled_live_suites({SUPERSET}, {SUPERSET}, {"HYPERSET_COMPOSE_DEMO": "1"}) == []


def test_a_module_the_session_never_collected_is_not_a_finding():
    """`pytest tests/compose/test_core_stack.py` in the shell of somebody who
    does have the demo stack up must not go red over a module that was never
    selected."""
    assert stalled_live_suites(set(), set(), {"HYPERSET_COMPOSE_DEMO": "1"}) == []
    assert stalled_live_suites({SUPERSET}, set(), {"HYPERSET_COMPOSE_DEMO": "1"}) == [SUPERSET]
