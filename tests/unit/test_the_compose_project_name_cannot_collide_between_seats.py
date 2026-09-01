"""Two seats running `tests/compose` must never share a compose project (hy-xjch).

Lives under `tests/unit` on purpose. The suite this protects skips itself
wherever Docker is absent, so a gate placed beside it would be skipped on every
seat and in CI at exactly the moments a regression could land unseen. Nothing
here needs a daemon: the defect is the NAME, and the name is computable.

WHAT THE ORIGINAL GUARD GOT RIGHT, and why that made this hard to see. The
constant separated DEV from TEST, which is a real hazard and was handled. It was
silent about SEAT vs SEAT, and this rig runs many seats against one daemon by
design. A guard that discriminates on one axis reads as a guard, so the second
axis was not so much rejected as never considered -- and its failures arrive
wearing the clothes of an ordinary flake.

BOTH MUTATIONS MEASURED, not asserted, and the second is the one worth having.

    `_project_name` returns `COMPOSE_PROJECT_PREFIX`         ->  6 of 7 red
    `_project_name` returns prefix + seat + hash(REPO_ROOT)  ->  2 of 7 red

The first is the regression this bead exists to prevent and almost any arm would
catch it. The second is the WRONG FIX, and it is wrong in a way that looks
right: stable per checkout, visibly not a literal, different on every machine,
compose-legal, seat-attributed -- and still colliding on the only configuration
that ever collided, several seats in one repo at one commit. Five of the seven
arms stay GREEN under it. It dies on exactly the two that read the value TWICE,
which is why "differs from a constant" was never the property to gate and why
those two arms are the ones to protect if this file is ever trimmed.

Survivor under the first mutation is `test_an_absent_rig_still_produces_a_usable
_project`, correctly: it gates legality, not uniqueness, and a constant is
legal. Source restored from a copy taken before the mutations and verified
byte-identical (md5 `7c942f314ee74e5181ceb8afbcc1b4c0`), never from `HEAD`.
"""

from __future__ import annotations

import re

from tests.compose.conftest import COMPOSE_PROJECT_PREFIX, _project_name

# Compose derives container, network, and volume names from the project, and
# rejects a project name outside this shape.
COMPOSE_SAFE = re.compile(r"[a-z0-9][a-z0-9_-]*$")


def test_two_runs_in_the_same_repo_at_the_same_commit_get_different_projects():
    """The arm the whole bead turns on, and the one a plausible wrong fix fails.

    "Not a literal constant" is not the requirement. A name derived from the
    checkout path, the branch, or the tree oid would satisfy that reading and
    collide anyway, because the seats that collide are in the SAME repo at
    nearly the same commit -- that is the condition every measured collision was
    taken under, not an edge case.

    SO DO NOT SIMPLIFY THIS TO ONE READ. Comparing a single name against the old
    literal, or asserting it merely contains a random-looking suffix, is the
    version that goes green on the wrong fix -- measured, five of seven arms
    survive it. Calling twice and comparing is the entire discriminator, and it
    reads like redundancy, which is why it is the thing most likely to be tidied
    away by someone who has not seen the mutation table above.

    Reverting the fixture to a module constant fails here.
    """
    assert _project_name() != _project_name()


def test_the_fixture_environment_is_what_carries_the_unique_name(monkeypatch):
    """Uniqueness where it is USED, not merely where it is generated (hy-7j2t).

    Every compose invocation in `tests/compose` runs under the `compose_env` FIXTURE, not under
    a bare `compose_environment()` call. So the value that must be unique is the one the FIXTURE
    yields: a `_project_name` the fixture then pins to a constant -- `env["COMPOSE_PROJECT_NAME"]
    = COMPOSE_PROJECT_PREFIX` after building the env -- would pass an arm that read
    `compose_environment()` directly while every compose test shared one project, the exact
    regression this file exists to prevent. This drives the fixture's own generator
    (`compose_env.__wrapped__`, the undecorated function pytest keeps) and reads what it yields.

    Gated WITHOUT Docker, on any seat: the daemon probe is stubbed True so the fixture builds an
    env instead of skipping, and `subprocess.run` is stubbed so nothing -- probe or teardown --
    touches a daemon.
    """
    from tests.compose import conftest

    monkeypatch.setattr(conftest, "_docker_available", lambda: True)
    monkeypatch.setattr(conftest.subprocess, "run", lambda *args, **kwargs: None)

    def _project_the_fixture_yields() -> str:
        generator = conftest.compose_env.__wrapped__()
        env = next(generator)
        generator.close()  # run past the yield; teardown's subprocess.run is stubbed off
        return env["COMPOSE_PROJECT_NAME"]

    first = _project_the_fixture_yields()
    second = _project_the_fixture_yields()

    assert first != second
    assert first.startswith(COMPOSE_PROJECT_PREFIX)


def test_the_generated_name_is_one_compose_will_accept():
    """The canary on the two arms above.

    Both of them pass on a name Docker rejects outright -- an empty string is
    not unique, but a stray uppercase seat or a slash from `BD_ACTOR` is, and
    would turn every compose test into an invocation error rather than a
    collision. Cheap to state, and it is the difference between a name that
    cannot collide and a name that cannot run.
    """
    for _ in range(8):
        name = _project_name()

        assert COMPOSE_SAFE.fullmatch(name), name
        assert len(name) > len(COMPOSE_PROJECT_PREFIX)


def test_the_name_still_separates_a_developers_own_stack():
    """The original guard, kept. It was never wrong, only incomplete.

    The compose stack a developer runs by hand takes its project name from the
    repository directory, so the prefix is what keeps `docker compose down -v`
    in the fixture teardown off their volumes.
    """
    assert _project_name().startswith(f"{COMPOSE_PROJECT_PREFIX}-")


def test_the_seat_is_named_in_the_project_when_the_rig_sets_one(monkeypatch):
    """Attribution, which is the half that pays for itself after the fix.

    `uuid4` alone makes collision impossible; it does not make an orphan
    readable. The failures that cost the most were the ones naming a network or
    a bare container id, leaving the seat that saw them unable to tell whose run
    was holding it. With the seat in the project name that is a read.
    """
    monkeypatch.setenv("BD_ACTOR", "hyperset/crew/atlas")

    assert "atlas" in _project_name()


def test_a_seat_name_compose_would_reject_is_carried_safely(monkeypatch):
    """The negative control on attribution.

    `BD_ACTOR` is a rig-supplied string, not a literal, and the naive
    interpolation of it is what would fail: slashes and uppercase are both
    present in real seat names and both are illegal in a project name. Asserting
    the sanitiser on a value that never needed sanitising would prove nothing.
    """
    monkeypatch.setenv("BD_ACTOR", "Hyperset/Crew/Atlas_2")

    name = _project_name()

    assert COMPOSE_SAFE.fullmatch(name), name
    assert "atlas-2" in name


def test_an_absent_rig_still_produces_a_usable_project(monkeypatch):
    """Off the rig -- a laptop, or CI -- there is no seat, and the name must
    still be legal rather than carrying an empty segment that leaves a `--`."""
    monkeypatch.delenv("BD_ACTOR", raising=False)

    name = _project_name()

    assert COMPOSE_SAFE.fullmatch(name), name
    assert "--" not in name
