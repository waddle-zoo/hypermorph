"""The compose health probe must not report a blind look as a stack that is down.

hy-9vb3 F2. Both live-sync modules gate a whole module on
`docker compose ps --format json` and then skip when no healthy entry comes
back. The return code was never read, so a `ps` that could not run at all --
daemon gone, unknown profile, a compose that spells the format flag
differently -- produced an empty stdout, an empty entry list, and a skip whose
stated reason ("the stack is not running") was false. Exit 0, module silently
never ran.

The arm that matters is the first one: it is the one that fails against the
code as it stood, and it fails rather than skipping because `pytest.skip`
raises a BaseException that `pytest.raises` would let through as a skip.
"""

import json
import subprocess

import pytest

from tests.compose.conftest import require_healthy_service

HINT = "the pinned DataHub GMS is not healthy; start it with `make up-datahub`"


def _listing(returncode, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["docker", "compose", "--profile", "datahub", "ps", "--format", "json"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _outcome(listing, service="datahub-gms"):
    """What the probe DID, as a value.

    `Skipped` is a BaseException, so a bare `pytest.raises(AssertionError)`
    around a skipping call marks this test skipped instead of failed -- a
    closing arm that cannot go red. Catching it here turns both outcomes into
    something an assertion can discriminate.
    """
    try:
        require_healthy_service(listing, service, HINT)
    except BaseException as outcome:  # noqa: BLE001 -- classifying, not handling
        return outcome
    return None


def test_a_probe_that_could_not_run_fails_instead_of_reporting_the_stack_down():
    outcome = _outcome(_listing(1, "", "no configuration file provided: not found"))

    assert isinstance(outcome, AssertionError), (
        f"a blind probe must fail, not skip; got {outcome!r}. Skipping here is the "
        "defect: an unread rc makes 'could not look' and 'not running' the same event"
    )
    assert "could not look" in str(outcome)
    assert "no configuration file provided" in str(outcome), str(outcome)


def test_an_absent_stack_still_skips():
    """The other half, and the reason this is not `check=True`: an ordinary
    developer without the opt-in stack must not get a red suite."""
    outcome = _outcome(_listing(0, ""))

    assert isinstance(outcome, pytest.skip.Exception), repr(outcome)
    assert HINT in str(outcome)


def test_an_unhealthy_or_wrongly_named_service_still_skips():
    running_but_starting = json.dumps({"Service": "datahub-gms", "Health": "starting"})
    someone_else = json.dumps({"Service": "postgres", "Health": "healthy"})

    assert isinstance(_outcome(_listing(0, running_but_starting)), pytest.skip.Exception)
    assert isinstance(_outcome(_listing(0, someone_else)), pytest.skip.Exception)


def test_a_healthy_service_returns_without_skipping():
    healthy = json.dumps({"Service": "datahub-gms", "Health": "healthy"})

    assert _outcome(_listing(0, healthy)) is None


def test_the_array_shaped_line_some_compose_versions_emit_is_read():
    """`ps --format json` emits one JSON object per line on some versions and a
    single JSON array on others; the probe accepts both, so this arm keeps the
    rc guard from being credited for a parse that never worked."""
    healthy = json.dumps([{"Service": "datahub-gms", "Health": "healthy"}])

    assert _outcome(_listing(0, healthy)) is None
