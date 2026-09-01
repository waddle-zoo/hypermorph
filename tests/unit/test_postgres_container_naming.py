"""The Postgres fixture must never remove a container it did not create.

hy-rm3: the fixture used one hardcoded container name and force-removed it at
session start, so a second worktree running `pytest tests/postgres` destroyed
the first one's database mid-run -- a hundred connection errors on a branch
that was fine. These cover the naming and reaping rules that replaced it,
without needing Docker.
"""

import os

import pytest

from tests.postgres.conftest import (
    _abandoned_containers,
    _container_name,
    _list_test_containers,
    _owner_pid,
    _process_is_alive,
)


def test_container_name_is_unique_per_session():
    assert _container_name(101, 54321) != _container_name(102, 54321)
    assert _container_name(101, 54321) != _container_name(101, 54322)


def test_owner_pid_round_trips_the_name_it_generated():
    assert _owner_pid(_container_name(4242, 55000)) == 4242


def test_owner_pid_rejects_containers_this_suite_did_not_name():
    assert _owner_pid("hyperset-pg-test") is None
    assert _owner_pid("hyperset-pg-test-legacy") is None
    assert _owner_pid("some-other-hyperset-pg-test-1-2") is None
    assert _owner_pid("postgres") is None


def test_a_container_owned_by_a_live_session_is_never_reaped():
    """The whole bug: a running neighbour's container must survive."""
    live = _container_name(999, 55001)
    assert _abandoned_containers([live], is_alive=lambda pid: True) == []


def test_a_container_whose_owner_died_is_reaped():
    dead = _container_name(999, 55001)
    assert _abandoned_containers([dead], is_alive=lambda pid: False) == [dead]


def test_only_this_suites_containers_are_considered():
    unrelated = "hyperset-pg-test-manual"
    assert _abandoned_containers([unrelated], is_alive=lambda pid: False) == []


def test_this_process_reads_as_alive():
    assert _process_is_alive(os.getpid())


def _docker_stub(tmp_path, body):
    """A `docker` earlier on PATH than the real one, so the listing call can be
    made to fail without a daemon and without touching anyone's containers."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(body)
    stub.chmod(0o755)
    return bin_dir


def test_a_docker_that_could_not_list_is_not_an_empty_list(tmp_path, monkeypatch):
    """The reaping's own precondition (hy-9vb3).

    `docker ps` failing and `docker ps` finding nothing both arrive as empty
    stdout. Read as "no abandoned containers", the second is an answer and the
    first is silence, and the difference is the hy-rm3 leak resuming with
    nothing anywhere saying so -- the session still gets a fresh unique name
    and still passes.
    """
    bin_dir = _docker_stub(
        tmp_path,
        "#!/usr/bin/env bash\necho 'Cannot connect to the Docker daemon' >&2\nexit 1\n",
    )
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    with pytest.raises(RuntimeError) as refused:
        _list_test_containers()

    assert "docker ps" in str(refused.value)
    assert "Cannot connect to the Docker daemon" in str(refused.value)


def test_a_successful_listing_is_still_read(tmp_path, monkeypatch):
    """The other side, so the guard above cannot be credited for a listing
    that stopped working."""
    mine = _container_name(4242, 55000)
    bin_dir = _docker_stub(tmp_path, f"#!/usr/bin/env bash\nprintf '%s\\n' '{mine}'\nexit 0\n")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    assert _list_test_containers() == [mine]
