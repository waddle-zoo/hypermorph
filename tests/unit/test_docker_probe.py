"""The Docker-availability probe must not silently skip a slow daemon (hy-ev8v).

A `TimeoutExpired` from `docker info` used to be folded in with an absent CLI and
returned False -- a silent skip (exit 0) of the Postgres and compose suites while
Docker was in fact up, which flipped with load. These exercise every branch with
an injected `run` (no real daemon), and pin the invariant: a timeout RAISES, it
does not resolve to "absent".
"""

from __future__ import annotations

import subprocess

import pytest

from tests.docker_probe import (
    DOCKER_INFO_ATTEMPTS,
    DockerProbeTimeout,
    _docker_available,
)


def test_a_reachable_daemon_is_available():
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    assert _docker_available(run=run) is True
    assert calls == [["docker", "info"]]  # succeeds on the first attempt


def test_an_absent_cli_is_genuinely_unavailable():
    # FileNotFoundError == docker not installed. This is the ONE honest skip.
    def run(cmd, **kwargs):
        raise FileNotFoundError("docker")

    assert _docker_available(run=run) is False


def test_a_daemon_that_answers_not_running_is_unavailable():
    # CalledProcessError == the CLI ran and the daemon promptly said "no".
    def run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    assert _docker_available(run=run) is False


def test_a_timed_out_probe_raises_it_does_not_silently_skip():
    # THE GUARD (hy-ev8v): a slow/wedged daemon must NOT read as absent. The old
    # code returned False here -> pytest.skip -> exit 0 while Docker was up.
    attempts = []

    def run(cmd, *, timeout, **kwargs):
        attempts.append(timeout)
        raise subprocess.TimeoutExpired(cmd, timeout)

    with pytest.raises(DockerProbeTimeout):
        _docker_available(run=run)
    # Retried before giving up, and never silently returned a value.
    assert len(attempts) == DOCKER_INFO_ATTEMPTS


def _sequence(*effects):
    """A `run` that raises/returns the given effects in order, one per attempt."""
    calls = iter(effects)

    def run(cmd, **kwargs):
        effect = next(calls)
        if isinstance(effect, BaseException):
            raise effect
        return effect

    return run


def test_a_timeout_then_a_missing_cli_still_raises_never_downgrades_to_skip():
    # hy-ev8v adversary bounce: the retry loop fail-opened. Attempt 1 timed out
    # (CLI present, slow), attempt 2 reported the CLI absent -> the old code
    # returned False = silent skip. Once a timeout occurred it must stay loud.
    run = _sequence(
        subprocess.TimeoutExpired(["docker", "info"], 30.0),
        FileNotFoundError("docker"),
    )
    with pytest.raises(DockerProbeTimeout):
        _docker_available(run=run)


def test_a_timeout_then_a_daemon_error_still_raises_never_downgrades_to_skip():
    # The other downgrade path: timeout then CalledProcessError (daemon says
    # not-running). A later not-running must not erase a prior timeout either.
    run = _sequence(
        subprocess.TimeoutExpired(["docker", "info"], 30.0),
        subprocess.CalledProcessError(1, ["docker", "info"]),
    )
    with pytest.raises(DockerProbeTimeout):
        _docker_available(run=run)


def test_both_docker_gated_conftests_use_the_one_shared_probe():
    # "Others point": if a conftest ever re-copied its own _docker_available, the
    # swallow-the-timeout bug could return module-locally. This fails the moment
    # either stops referencing the canonical probe.
    import tests.compose.conftest as compose_conftest
    import tests.docker_probe as canonical
    import tests.postgres.conftest as postgres_conftest

    assert postgres_conftest._docker_available is canonical._docker_available
    assert compose_conftest._docker_available is canonical._docker_available
