"""One robust Docker-availability probe for the Docker-gated suites (hy-ev8v).

Both `tests/postgres/conftest.py` and `tests/compose/conftest.py` import this,
rather than each holding its own copy, so the two cannot drift and there is one
place where "is Docker available" is decided.

WHY THIS EXISTS. The previous copies ran `docker info` with a 5s timeout and
treated `TimeoutExpired` the same as an absent CLI -- a silent skip with exit 0.
Under the seat's 12-container Superset+DataHub load, `docker info` was measured
at 3.2-3.7s against that 5s budget, so the probe FLIPPED with load: three runs on
an unchanged tree gave 4 failed / 4 skipped / 4 errors. A gate that skips its
Postgres and compose suites silently when Docker is in fact up is a false-green
hole -- the whole point of these suites is to run.

THE INVARIANT. A skip must mean Docker is genuinely absent, provably -- never
that the probe was merely slow. So the three failure modes are told apart rather
than folded into one `except`:

- `FileNotFoundError`: the `docker` CLI is not installed. Genuinely absent; a
  skip is honest. Returns False.
- `CalledProcessError`: the CLI ran and the daemon answered "not running"
  (non-zero, promptly). Genuinely absent; a skip is honest. Returns False.
- `TimeoutExpired`: the CLI IS present (no `FileNotFoundError`) and the daemon
  did not answer within a GENEROUS budget across retries. That is not absence --
  it is a daemon under load or wedged -- and returning False here is exactly the
  silent skip this fixes. We RAISE `DockerProbeTimeout` so the gate goes RED
  (loud), never green (silent).

The budget is deliberately generous (not a blind bump of 5 to some other number
that still flips): 30s is ~8x the measured worst case, and a second attempt
covers a one-off stall, so a timeout now means a genuinely unresponsive daemon
worth failing on -- not routine load.
"""

from __future__ import annotations

import subprocess

# ~8x the 3.2-3.7s measured under the seat's full stack. Not "5 bumped a bit":
# large enough that a timeout means a wedged/overloaded daemon, not normal load.
DOCKER_INFO_TIMEOUT = 30.0
DOCKER_INFO_ATTEMPTS = 2


class DockerProbeTimeout(RuntimeError):
    """`docker info` did not respond, though the CLI is present.

    Raised instead of returning "absent" so a slow daemon fails the run LOUDLY
    rather than silently skipping the Docker-gated suites (hy-ev8v). A returned
    False would read as "Docker is not installed" and skip with exit 0; this
    reads as "Docker is up but unresponsive", which is a real, actionable red.
    """


def _docker_available(*, run=subprocess.run) -> bool:
    """True iff Docker is genuinely reachable; False iff genuinely absent.

    Never returns False for a mere timeout -- that raises `DockerProbeTimeout`.
    `run` is injectable so the timeout and absent paths can be exercised without
    a real daemon (see `tests/unit/test_docker_probe.py`).

    ONCE A TIMEOUT OCCURRED, NEVER RETURN False. A timeout means the CLI was
    present and the daemon answered slowly; a LATER attempt raising
    `FileNotFoundError` or `CalledProcessError` must NOT downgrade that prior
    timeout to a silent skip (hy-ev8v adversary bounce -- the retry loop
    fail-opened if the second attempt reported absent after the first timed out).
    So any observed timeout wins: fall through to the loud raise.
    """
    last_timeout: subprocess.TimeoutExpired | None = None
    for _attempt in range(DOCKER_INFO_ATTEMPTS):
        try:
            run(["docker", "info"], capture_output=True, timeout=DOCKER_INFO_TIMEOUT, check=True)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            if last_timeout is None:
                return False
            break  # a prior timeout already stands; absent/not-running must not skip
        except subprocess.TimeoutExpired as timeout:
            last_timeout = timeout
    raise DockerProbeTimeout(
        f"`docker info` did not respond within {DOCKER_INFO_TIMEOUT:.0f}s over "
        f"{DOCKER_INFO_ATTEMPTS} attempts, but the docker CLI is present -- this is a slow or "
        "wedged daemon, not an absent Docker. Refusing to silently skip the Docker-gated suites "
        "(a skip must mean Docker is genuinely absent). Free the daemon or reduce load and re-run."
    ) from last_timeout
